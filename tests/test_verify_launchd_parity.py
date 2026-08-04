"""verify-launchd-parity.sh 的反证测试（R1 审查 M6/F10：新门禁此前零测试覆盖）。

为什么这个文件必须存在：脚本自己留了 `AKB2_PARITY_MANIFEST` 覆写口，注释写得很清楚 ——

    manifest 路径可覆写：仅为让「检查真的会咬人」这件事可被端到端反证
    （一个扫真实目录、啥也没找到就绿的检查，与一个坏掉的检查在结果上无法区分）。

但全仓 grep `AKB2_PARITY_MANIFEST` 只命中脚本自己：**接缝留了、反证没写**。
327 行门控 + 145 行 manifest 零测试，与 CLAUDE.md「门控改动必须带测试」直接冲突，
而 3481 条测试里没有一条能发现 manifest↔模板不自洽（那正是本轮的 blocker）。

测试方法：全部经 `AKB2_PARITY_MANIFEST` 喂**自造 manifest**，`installed` 指向 tmp 里的
假 plist，`launchctl` 用 PATH stub 假冒 —— 不读真 `~/Library/LaunchAgents`、不跑真 launchctl。
"""

from __future__ import annotations

import os
import plistlib
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
PARITY_SH = REPO / "scripts" / "verify-launchd-parity.sh"

# 与脚本头部的退出码语义对齐（不同语义不得共用一个码）
EXIT_MATCH = 0
EXIT_DRIFT = 1
EXIT_EXPECTED_PENDING = 3

VAULT_ROOT_BASE = "/Volumes/AKB2/Obsidian"


def _write_executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _template_text(
    label: str, *, hours: list[tuple[int, int]], repo_placeholder: bool = True
) -> str:
    """一个最小但**合法**的 plist 模板（带占位符，供 parity 自行渲染）。"""
    slots = "".join(
        f"      <dict><key>Hour</key><integer>{hour}</integer>"
        f"<key>Minute</key><integer>{minute}</integer></dict>\n"
        for hour, minute in hours
    )
    repo_root = "__AKB2_REPO_ROOT__" if repo_placeholder else "/somewhere/else"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"  <key>Label</key><string>{label}</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        "    <string>/bin/bash</string>\n"
        f"    <string>{repo_root}/scripts/daily_briefing.sh</string>\n"
        "    <string>--vault-root</string>\n"
        "    <string>__AKB2_VAULT_ROOT__</string>\n"
        "  </array>\n"
        "  <key>StartCalendarInterval</key>\n"
        "  <array>\n"
        f"{slots}"
        "  </array>\n"
        "  <key>EnvironmentVariables</key>\n"
        "  <dict><key>PATH</key><string>/usr/bin:/bin</string></dict>\n"
        "</dict>\n"
        "</plist>\n"
    )


def _render(text: str, *, repo_root: Path, vault_root: str) -> bytes:
    return (
        text.replace("__AKB2_REPO_ROOT__", str(repo_root))
        .replace("__AKB2_VAULT_ROOT__", vault_root)
        .replace("__AKB2_HOME__", str(Path.home()))
        .encode("utf-8")
    )


class _Harness:
    """自造 manifest + 假 installed plist + stub launchctl 的隔离运行环境。"""

    def __init__(self, tmp_path: Path, *, launchctl_loaded: bool = True):
        self.tmp = tmp_path
        self.repo = tmp_path / "repo"
        (self.repo / "launchd").mkdir(parents=True)
        (self.repo / "scripts").mkdir(parents=True)
        # parity 脚本从 $0/.. 推 ROOT，所以脚本要住在假仓里
        (self.repo / "scripts" / "verify-launchd-parity.sh").write_text(
            PARITY_SH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (self.repo / "scripts" / "verify-launchd-parity.sh").chmod(0o755)
        self.installed_dir = tmp_path / "LaunchAgents"
        self.installed_dir.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        _write_executable(
            self.bin / "launchctl",
            f"#!/usr/bin/env bash\nexit {0 if launchctl_loaded else 113}\n",
        )
        self.jobs: list[dict[str, Any]] = []

    def add_job(
        self,
        label: str,
        *,
        template_hours: list[tuple[int, int]],
        manifest_hours: list[tuple[int, int]],
        installed_hours: list[tuple[int, int]] | None,
        should_load: bool = True,
        pending: bool = False,
        pending_since: str = "2026-07-30",
        installed_vault_root: str | None = None,
        installed_repo_root: Path | None = None,
    ) -> None:
        template = self.repo / "launchd" / f"{label}.plist"
        template.write_text(
            _template_text(label, hours=template_hours), encoding="utf-8"
        )
        installed_path = self.installed_dir / f"{label}.plist"
        if installed_hours is not None:
            installed_path.write_bytes(
                _render(
                    _template_text(label, hours=installed_hours),
                    repo_root=installed_repo_root or self.repo,
                    vault_root=installed_vault_root or VAULT_ROOT_BASE,
                )
            )
        job: dict[str, Any] = {
            "label": label,
            "template": f"launchd/{label}.plist",
            "installed": str(installed_path),
            "installer": "manual_existing_runtime",
            "should_load": should_load,
            "pending_human_install": pending,
            "expected_schedule": {
                "kind": "calendar",
                "entries": [{"Hour": h, "Minute": m} for h, m in manifest_hours],
            },
            "expected_env_keys": ["PATH"],
        }
        if pending:
            job["pending_since"] = pending_since
        self.jobs.append(job)

    def write_manifest(self) -> Path:
        import yaml

        manifest = {
            "generated_at": "2026-08-04",
            "defaults": {
                "launch_agents_dir": str(self.installed_dir),
                "repo_root_placeholder": "__AKB2_REPO_ROOT__",
                "home_placeholder": "__AKB2_HOME__",
                "vault_root_placeholder": "__AKB2_VAULT_ROOT__",
                "vault_root_base": VAULT_ROOT_BASE,
            },
            "jobs": self.jobs,
        }
        path = self.tmp / "manifest.yaml"
        path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        return path

    def run(self) -> subprocess.CompletedProcess:
        manifest = self.write_manifest()
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env["AKB2_PARITY_MANIFEST"] = str(manifest)
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / "verify-launchd-parity.sh")],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )


# ═══════════════════════════════════════════════════════════════════════════
# SC-R1-26 / SC-R1-27 —— 反证接缝真的被用：喂坏 manifest 必须咬人并指认条目
# ═══════════════════════════════════════════════════════════════════════════


def test_parity_catches_planted_bad_manifest(tmp_path: Path) -> None:
    """种入排期错误的 manifest → exit 1 且**点名**该 label；干净 manifest → exit 0。

    这条同时是整个 parity 检查的「非空过」证明：一个扫真实目录、啥也没找到就绿的检查，
    与一个坏掉的检查在结果上无法区分。
    """
    # ① 干净：三方一致 → MATCH / exit 0
    clean = _Harness(tmp_path / "clean")
    clean.add_job(
        "com.akb2.probe",
        template_hours=[(8, 0), (11, 0)],
        manifest_hours=[(8, 0), (11, 0)],
        installed_hours=[(8, 0), (11, 0)],
    )
    ok = clean.run()
    assert ok.returncode == EXIT_MATCH, f"{ok.stdout}\n{ok.stderr}"
    assert "result=MATCH" in ok.stdout, ok.stdout
    assert "drift_failures=0" in ok.stdout, ok.stdout

    # ② 种坏 manifest（模板与运行态都是 11:00，manifest 写 12:05）→ 必须咬人
    planted = _Harness(tmp_path / "planted")
    planted.add_job(
        "com.akb2.probe",
        template_hours=[(8, 0), (11, 0)],
        manifest_hours=[(8, 0), (12, 5)],
        installed_hours=[(8, 0), (11, 0)],
    )
    planted.add_job(
        "com.akb2.other",
        template_hours=[(5, 30)],
        manifest_hours=[(5, 30)],
        installed_hours=[(5, 30)],
    )
    bad = planted.run()
    assert bad.returncode == EXIT_DRIFT, f"坏 manifest 没被咬:\n{bad.stdout}\n{bad.stderr}"
    # 指认的是出问题的那条，不是无辜的那条
    probe_line = next(
        line for line in bad.stdout.splitlines() if "label=com.akb2.probe" in line
    )
    other_line = next(
        line for line in bad.stdout.splitlines() if "label=com.akb2.other" in line
    )
    assert "result=DRIFT" in probe_line, probe_line
    assert "result=MATCH" in other_line, other_line
    assert "drift_failures=1" in bad.stdout, bad.stdout


# ═══════════════════════════════════════════════════════════════════════════
# SC-R1-13 / SC-R1-14 —— 三方归因：三个值都打出来 + 方向词
# ═══════════════════════════════════════════════════════════════════════════


def test_direction_attribution_repo_vs_installed(tmp_path: Path) -> None:
    """repo=11:00 manifest=11:00 installed=12:05 → `installed_stale`，不是指责仓库。

    旧实现只贴 `repo_schedule_mismatch` / `installed_schedule_mismatch` 两个标签，
    且一旦 file_status 变 DRIFT 就跳过真实 diff 的计算。于是「模板已改、运行态还旧」
    这种情况下 installed 与 manifest 相符 → installed_schedule_mismatch 不触发；
    repo_schedule_mismatch 触发 → 操作员看到的字面结论是「**仓库模板错了**」；
    真正要看的 `repo=11:00 installed=12:05` 永远不被打印。
    照它的提示"修好" = 把模板改回 12:05 = 撤销事故修复。
    """
    harness = _Harness(tmp_path / "installed-stale")
    harness.add_job(
        "com.akb2.probe",
        template_hours=[(8, 0), (11, 0)],
        manifest_hours=[(8, 0), (11, 0)],
        installed_hours=[(8, 0), (12, 5)],
    )
    result = harness.run()

    assert result.returncode == EXIT_DRIFT, f"{result.stdout}\n{result.stderr}"
    line = next(
        ln for ln in result.stdout.splitlines() if "label=com.akb2.probe" in ln
    )
    assert "schedule_drift=installed_stale" in line, (
        f"方向词错了（这会把归因指向仓库）:\n{line}"
    )
    # 三个值全打出来，运维一眼能看到差在哪
    assert "repo=[{'Hour': 8, 'Minute': 0}, {'Hour': 11, 'Minute': 0}]" in line, line
    assert "manifest=[{'Hour': 8, 'Minute': 0}, {'Hour': 11, 'Minute': 0}]" in line, line
    assert "installed=[{'Hour': 8, 'Minute': 0}, {'Hour': 12, 'Minute': 5}]" in line, line
    # 真实 diff 不再被 file_status 短路吞掉
    assert "StartCalendarInterval repo=" in line, f"真实 diff 又被吞了:\n{line}"


@pytest.mark.parametrize(
    ("template", "manifest", "installed", "direction"),
    [
        ([(11, 0)], [(11, 0)], [(12, 5)], "installed_stale"),
        ([(11, 0)], [(12, 5)], [(11, 0)], "manifest_stale"),
        ([(11, 0)], [(12, 5)], [(12, 5)], "repo_ahead"),
        ([(11, 0)], [(12, 5)], [(9, 0)], "all_diverged"),
    ],
    ids=["installed_stale", "manifest_stale", "repo_ahead", "all_diverged"],
)
def test_direction_word_covers_every_divergence_shape(
    tmp_path: Path,
    template: list[tuple[int, int]],
    manifest: list[tuple[int, int]],
    installed: list[tuple[int, int]],
    direction: str,
) -> None:
    """四种分叉形态各自给出正确方向词（方向词是给人看的处置指引，不能只对一种）。"""
    harness = _Harness(tmp_path / direction)
    harness.add_job(
        "com.akb2.probe",
        template_hours=template,
        manifest_hours=manifest,
        installed_hours=installed,
    )
    result = harness.run()
    assert result.returncode == EXIT_DRIFT, result.stdout
    assert f"schedule_drift={direction}" in result.stdout, result.stdout


# ═══════════════════════════════════════════════════════════════════════════
# SC-R1-24 —— 已批准的 pending 不得让无关 write gate 永久无解
# ═══════════════════════════════════════════════════════════════════════════


def test_pending_does_not_block_write_gate(tmp_path: Path) -> None:
    """仅 pending 存在 → 专用退出码（非失败）；真 DRIFT 仍 exit 1。

    manifest 把 inflow-sentinel 登记为 pending_human_install，docs/allowed-residuals.md
    也批准了它未安装；而 post-change-pipeline.sh 在 --write 模式把 parity 当硬门。
    旧实现对 pending 无条件 `result=MISSING` 并计 failure → **任何**无关的 write pipeline
    都结构性失败，合法状态下 gate 无解（CADV-G4）。
    """
    # ① 只有已批准 pending → 专用码 3（可诊断但不判失败）
    pending_only = _Harness(tmp_path / "pending-only")
    pending_only.add_job(
        "com.akb2.ok",
        template_hours=[(5, 30)],
        manifest_hours=[(5, 30)],
        installed_hours=[(5, 30)],
    )
    pending_only.add_job(
        "com.akb2.sentinel",
        template_hours=[(9, 0)],
        manifest_hours=[(9, 0)],
        installed_hours=None,          # 未安装
        should_load=False,
        pending=True,
    )
    result = pending_only.run()
    assert result.returncode == EXIT_EXPECTED_PENDING, (
        f"仅已批准 pending 却不是专用码:\n{result.stdout}\n{result.stderr}"
    )
    assert "result=EXPECTED_PENDING" in result.stdout, result.stdout
    assert "drift_failures=0 expected_pending=1" in result.stdout, result.stdout
    # 仍然可诊断（不是静默放过）
    assert "pending_human_install" in result.stdout, result.stdout
    assert "pending_days=" in result.stdout, result.stdout

    # ② 未登记 pending 的缺失仍是失败（别把闸门放宽成"缺文件都不算事"）
    unexpected = _Harness(tmp_path / "unexpected-missing")
    unexpected.add_job(
        "com.akb2.gone",
        template_hours=[(5, 30)],
        manifest_hours=[(5, 30)],
        installed_hours=None,
        pending=False,
    )
    missing = unexpected.run()
    assert missing.returncode == EXIT_DRIFT, missing.stdout
    assert "result=UNEXPECTED_MISSING" in missing.stdout, missing.stdout

    # ③ pending + 真 DRIFT 共存 → 仍 exit 1（pending 不掩盖真问题）
    both = _Harness(tmp_path / "pending-plus-drift")
    both.add_job(
        "com.akb2.sentinel",
        template_hours=[(9, 0)],
        manifest_hours=[(9, 0)],
        installed_hours=None,
        should_load=False,
        pending=True,
    )
    both.add_job(
        "com.akb2.drifted",
        template_hours=[(11, 0)],
        manifest_hours=[(11, 0)],
        installed_hours=[(12, 5)],
    )
    mixed = both.run()
    assert mixed.returncode == EXIT_DRIFT, mixed.stdout
    assert "drift_failures=1 expected_pending=1" in mixed.stdout, mixed.stdout


def test_post_change_pipeline_treats_pending_exit_code_as_pass() -> None:
    """post-change-pipeline.sh 必须认这个专用码，否则 SC-R1-24 只修了一半。

    静态断言：write 模式的 parity 步骤必须走能识别专用码的封装，
    而不是直接丢给恒把非 0 当 FAIL 的通用 run_step。
    """
    text = (REPO / "scripts" / "post-change-pipeline.sh").read_text(encoding="utf-8")
    assert "PARITY_PENDING_EXIT_CODE=3" in text, "post-change 侧没有具名的专用码常量"
    assert "run_step_parity" in text, "parity 仍走恒把非 0 当 FAIL 的通用 run_step"
    offenders = [
        line.strip()
        for line in text.splitlines()
        if "run_step " in line and "verify-launchd-parity" in line
    ]
    assert not offenders, f"parity 又被接回通用 run_step: {offenders}"


# ═══════════════════════════════════════════════════════════════════════════
# SC-R1-25 —— 期望根来自 manifest/仓库配置，不由待审 installed plist 自举
# ═══════════════════════════════════════════════════════════════════════════


def test_expected_roots_from_manifest_not_installed(tmp_path: Path) -> None:
    """installed 里塞伪造的 vault/repo 根 → 期望值仍来自 manifest，判定不被牵引。

    旧实现先从 installed plist 反推 repo/vault 根，再用它渲染 repo 模板去和 installed 比
    —— 于是一个把 vault 指到 mktemp 目录、或 repo 指到已删 worktree 的危险 installed
    可以**自证 MATCH**（拿自己的值和自己比）。parity 的信任方向必须单向（CADV-G3）。
    """
    forged_vault = "/private/var/folders/xx/forged-vault"
    harness = _Harness(tmp_path / "forged-vault")
    harness.add_job(
        "com.akb2.probe",
        template_hours=[(5, 30)],
        manifest_hours=[(5, 30)],
        installed_hours=[(5, 30)],
        installed_vault_root=forged_vault,
    )
    result = harness.run()

    assert result.returncode == EXIT_DRIFT, (
        f"伪造 vault 根的 installed 自证 MATCH 了:\n{result.stdout}\n{result.stderr}"
    )
    assert f"installed_vault_root={forged_vault}" in result.stdout, result.stdout
    assert f"expected={VAULT_ROOT_BASE}" in result.stdout, result.stdout

    # repo 根同理：installed 指向别处 → 必须点名，而不是拿它当期望值
    forged_repo = tmp_path / "deleted-worktree"
    (forged_repo / "scripts").mkdir(parents=True)
    harness2 = _Harness(tmp_path / "forged-repo")
    harness2.add_job(
        "com.akb2.probe",
        template_hours=[(5, 30)],
        manifest_hours=[(5, 30)],
        installed_hours=[(5, 30)],
        installed_repo_root=forged_repo,
    )
    result2 = harness2.run()
    assert result2.returncode == EXIT_DRIFT, result2.stdout
    assert f"installed_repo_root={forged_repo}" in result2.stdout, result2.stdout
    assert "期望来自仓库配置" in result2.stdout, result2.stdout


def test_escape_hatch_in_installed_env_is_flagged(tmp_path: Path) -> None:
    """运行态 env 里出现逃逸口 → DRIFT（fail-closed 门对生产已失效）。

    仓库侧的静态检查只看模板；本轮故障的母题恰恰是「运行态与仓库不一致且无人发现」，
    所以运行态这一侧必须由 parity 覆盖。
    """
    harness = _Harness(tmp_path / "hatch")
    harness.add_job(
        "com.akb2.probe",
        template_hours=[(5, 30)],
        manifest_hours=[(5, 30)],
        installed_hours=[(5, 30)],
    )
    installed = harness.installed_dir / "com.akb2.probe.plist"
    data = plistlib.loads(installed.read_bytes())
    data["EnvironmentVariables"]["AKB2_SOCIAL_BRIEFING_PIPELINE_BIN"] = "/tmp/evil.sh"
    installed.write_bytes(plistlib.dumps(data))

    result = harness.run()

    assert result.returncode == EXIT_DRIFT, result.stdout
    assert "escape_hatch_in_installed_env=AKB2_SOCIAL_BRIEFING_PIPELINE_BIN" in result.stdout, (
        f"运行态里的强逃逸口没被抓到:\n{result.stdout}"
    )
