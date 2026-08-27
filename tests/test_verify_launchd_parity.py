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


# 假 launchctl：按 label 分别回答"是否在册"。
#
# 为什么必须**按 label**而不是一刀切 exit 0/113：SC-R2-04 要判的四态里有
# 「pending 的 job 未装但仍在册」与「另一个 job 正常在册」共存的形态，一刀切的
# stub 表达不出来 —— 而旧版正是 `exit 0`（全部在册）+ `_Harness(launchctl_loaded=True)`
# 默认值，把 pending+loaded 这个异常态当成了 pending 用例的**期望环境**。
_FAKE_LAUNCHCTL = r"""#!/usr/bin/env bash
# 只认 `launchctl print gui/<uid>/<label>`：取末位参数的 label 段查 loaded 清单。
for arg in "$@"; do last="$arg"; done
label="${last##*/}"
if [ -n "${FAKE_LAUNCHCTL_LOADED_FILE:-}" ] \
   && [ -f "$FAKE_LAUNCHCTL_LOADED_FILE" ] \
   && grep -qxF "$label" "$FAKE_LAUNCHCTL_LOADED_FILE"; then
  exit 0
fi
echo "Could not find service \"$label\" in domain" >&2
exit 113
"""

# 挂死的假 launchctl：记录自身 pid 后 exec sleep（exec 让 pid 不变，
# 于是"父被 kill、sleep 变孤儿"这种残余无法混进判定）。
_HANG_LAUNCHCTL = r"""#!/usr/bin/env bash
printf '%s\n' "$$" >> "${FAKE_LAUNCHCTL_PIDFILE:?FAKE_LAUNCHCTL_PIDFILE 未设}"
exec sleep 600
"""

# 解析咽喉必须进假仓：parity 用 $ROOT/src 导入 akb2.common.plist_parse。
_CHOKEPOINT_RELS = (
    "src/akb2/__init__.py",
    "src/akb2/common/__init__.py",
    "src/akb2/common/plist_parse.py",
)


class _Harness:
    """自造 manifest + 假 installed plist + stub launchctl 的隔离运行环境。

    `loaded_labels=None` 时按 `launchctl_loaded` 兜全部 label（保持旧用例语义）；
    传显式集合则逐 label 控制 —— pending 相关用例**必须**显式给出 loaded 态，
    见 `_FAKE_LAUNCHCTL` 上方注释。
    """

    def __init__(
        self,
        tmp_path: Path,
        *,
        launchctl_loaded: bool = True,
        loaded_labels: set[str] | None = None,
        launchctl_hangs: bool = False,
    ):
        self.tmp = tmp_path
        self.repo = tmp_path / "repo"
        (self.repo / "launchd").mkdir(parents=True)
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "src" / "akb2" / "common").mkdir(parents=True)
        # parity 脚本从 $0/.. 推 ROOT，所以脚本要住在假仓里
        (self.repo / "scripts" / "verify-launchd-parity.sh").write_text(
            PARITY_SH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (self.repo / "scripts" / "verify-launchd-parity.sh").chmod(0o755)
        for rel in _CHOKEPOINT_RELS:
            (self.repo / rel).write_text(
                (REPO / rel).read_text(encoding="utf-8"), encoding="utf-8"
            )
        self.installed_dir = tmp_path / "LaunchAgents"
        self.installed_dir.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        _write_executable(
            self.bin / "launchctl",
            _HANG_LAUNCHCTL if launchctl_hangs else _FAKE_LAUNCHCTL,
        )
        self.launchctl_loaded = launchctl_loaded
        self.loaded_labels = loaded_labels
        self.loaded_file = tmp_path / "launchctl-loaded-labels"
        self.pidfile = tmp_path / "launchctl-hang-pids"
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
        template_comment: str | None = None,
        ignore_env: list[Any] | None = None,
    ) -> None:
        template = self.repo / "launchd" / f"{label}.plist"
        template_body = _template_text(label, hours=template_hours)
        if template_comment is not None:
            template_body = template_body.replace(
                "<dict>\n", f"<!--\n{template_comment}\n-->\n<dict>\n", 1
            )
        template.write_text(template_body, encoding="utf-8")
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
        if ignore_env is not None:
            job["ignore_installed_env_keys"] = ignore_env
        self.jobs.append(job)

    def _loaded_now(self) -> set[str]:
        if self.loaded_labels is not None:
            return set(self.loaded_labels)
        return {job["label"] for job in self.jobs} if self.launchctl_loaded else set()

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

    def run(self, *, timeout: float = 120, probe_timeout_sec: str | None = None):
        manifest = self.write_manifest()
        self.loaded_file.write_text(
            "".join(f"{label}\n" for label in sorted(self._loaded_now())),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env["AKB2_PARITY_MANIFEST"] = str(manifest)
        env["FAKE_LAUNCHCTL_LOADED_FILE"] = str(self.loaded_file)
        env["FAKE_LAUNCHCTL_PIDFILE"] = str(self.pidfile)
        if probe_timeout_sec is not None:
            env["AKB2_LAUNCHCTL_TIMEOUT_SEC"] = probe_timeout_sec
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / "verify-launchd-parity.sh")],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )

    def hung_pids(self) -> list[int]:
        if not self.pidfile.exists():
            return []
        return [
            int(line) for line in self.pidfile.read_text(encoding="utf-8").split()
            if line.strip()
        ]


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
    #    loaded_labels 显式给：pending 的 job **必须确认未加载**才配得上"预期的未安装"。
    #    旧版这里用 _Harness 的默认 launchctl_loaded=True（全部在册），于是断言的其实是
    #    「plist 未装、job 仍驻留 → exit 3」，把 write gate fail-open 焊成了期望值。
    pending_only = _Harness(
        tmp_path / "pending-only", loaded_labels={"com.akb2.ok"}
    )
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
    both = _Harness(
        tmp_path / "pending-plus-drift", loaded_labels={"com.akb2.drifted"}
    )
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


# ═══════════════════════════════════════════════════════════════════════════
# SC-R2-04 —— pending 分支必须保留真实 loaded 态：四态互斥，write gate 不被穿过
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("case", "installed", "loaded", "extra_drift", "expected_exit", "expected_reason"),
    [
        ("pending_unloaded", False, False, False, EXIT_EXPECTED_PENDING, "pending_human_install"),
        ("pending_loaded", False, True, False, EXIT_DRIFT, "pending_but_still_loaded"),
        ("pending_installed", True, False, False, EXIT_DRIFT, "pending_but_installed_exists"),
        ("pending_plus_other_drift", False, False, True, EXIT_DRIFT, "schedule_drift"),
    ],
    ids=[
        "pending+unloaded→3",
        "pending+loaded→1",
        "pending+installed→1",
        "pending+其他drift→1",
    ],
)
def test_pending_loaded_states_are_mutually_exclusive(
    tmp_path: Path,
    case: str,
    installed: bool,
    loaded: bool,
    extra_drift: bool,
    expected_exit: int,
    expected_reason: str,
) -> None:
    """已批准的 pending 只有一种态配得上 exit 3：文件缺失 **且** 确认未加载。

    R2 blocker（write gate fail-open）：旧实现算出了 `loaded = _launchctl_loaded(label)`
    却在 pending 分支无条件 `loaded_status="PENDING"` 并保持 EXPECTED_PENDING —— 于是
    「plist 已从 LaunchAgents 删掉、job 却仍驻留 launchd」这个异常运行态被伪装成
    「已批准未安装」：parity 出 exit 3，post-change-pipeline --write 把 3 当通过放行，
    而**旧配置的 job 继续按老参数跑、继续花额度**，输出里连"它其实还在册"都看不到
    （唯一能报出这件事的诊断字段被自己盖掉了）。

    这条在全量 3575 passed 下存活，因为 `_Harness.__init__` 默认
    `launchctl_loaded=True` + 旧 `test_pending_does_not_block_write_gate`
    正好断言了 pending+loaded → exit 3 —— 错语义被焊成了期望值。
    所以四态必须**逐态**钉死，且 loaded 态一律显式给出。
    """
    harness = _Harness(
        tmp_path / case,
        loaded_labels=({"com.akb2.sentinel"} if loaded else set()),
    )
    harness.add_job(
        "com.akb2.sentinel",
        template_hours=[(9, 0)],
        manifest_hours=[(9, 0)],
        installed_hours=[(9, 0)] if installed else None,
        should_load=False,
        pending=True,
    )
    if extra_drift:
        harness.add_job(
            "com.akb2.drifted",
            template_hours=[(11, 0)],
            manifest_hours=[(11, 0)],
            installed_hours=[(12, 5)],
        )
        harness.loaded_labels = {"com.akb2.drifted"}

    result = harness.run()
    combined = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == expected_exit, (
        f"{case}: 期望 exit={expected_exit}，实际 {result.returncode}\n{combined}"
    )
    assert expected_reason in result.stdout, f"{case}: 缺归因 {expected_reason}\n{combined}"

    sentinel_line = next(
        ln for ln in result.stdout.splitlines() if "label=com.akb2.sentinel" in ln
    )
    # 真实 loaded 态必须留在输出里（诊断价值），不得被 "PENDING" 盖掉
    assert "loaded=PENDING" not in sentinel_line, (
        f"{case}: pending 分支又把真实 loaded 态盖成了 PENDING:\n{sentinel_line}"
    )
    assert f"loaded={'YES' if loaded else 'NO'}" in sentinel_line, sentinel_line
    # pending 本身始终可诊断（不是静默放过 / 也不是丢掉登记信息）
    assert "pending_days=" in sentinel_line, sentinel_line


# ═══════════════════════════════════════════════════════════════════════════
# SC-R2-05 —— launchctl 探针必须有 wall-clock 上限，超时按 fail-closed 处理
# ═══════════════════════════════════════════════════════════════════════════


def test_launchctl_probe_is_wall_clock_bounded(tmp_path: Path) -> None:
    """挂死的 launchctl（sleep 600）→ parity 在具名上限内非零返回，且不留残余子进程。

    旧实现是裸 `subprocess.run(["launchctl", "print", ...])`，无 `timeout=`：
    一次挂死的探针就让 parity 永不返回，而 post-change-pipeline --write 把 parity
    当硬门 —— 门不返回 = 整条 write 流程无声悬挂，没有任何超时留痕。

    「超时」还必须与「未加载」区分：静默当 false 正是 SC-R2-04 那个 fail-open 的形状
    （问不出来 → 判"未加载" → pending 被当成已批准未安装 → exit 3 放行）。
    所以超时走 DRIFT + 具名留痕。

    subprocess 侧也给 timeout：没有上限时本用例会**挂住**而不是打红，
    挂住的测试等于没有测试。
    """
    import time

    harness = _Harness(tmp_path / "hang", launchctl_hangs=True)
    harness.add_job(
        "com.akb2.probe",
        template_hours=[(5, 30)],
        manifest_hours=[(5, 30)],
        installed_hours=[(5, 30)],
    )

    wall_clock_budget_s = 60
    started = time.monotonic()
    try:
        result = harness.run(timeout=wall_clock_budget_s, probe_timeout_sec="1")
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"launchctl 探针没有 wall-clock 上限：parity 在 {wall_clock_budget_s}s 内没返回"
            f"（write gate 会无声悬挂）。stdout={exc.stdout!r}"
        ) from exc
    elapsed = time.monotonic() - started
    combined = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == EXIT_DRIFT, f"超时没进 fail-closed:\n{combined}"
    assert "launchctl_probe_unknown=1" in result.stdout, f"超时没具名留痕:\n{combined}"
    assert "loaded=UNKNOWN" in result.stdout, (
        f"超时被折叠成了 YES/NO（问不出来 ≠ 已知状态）:\n{combined}"
    )
    assert elapsed < wall_clock_budget_s, f"耗时 {elapsed:.1f}s"

    # 非空过：stub 真的被调到了（否则"超时"可能只是别的原因）
    pids = harness.hung_pids()
    assert pids, f"挂死的 stub 一次都没被调用，用例证明不了上限:\n{combined}"
    # 不留残余：subprocess.run 超时后应已 kill + reap 子进程
    leftover = []
    for pid in pids:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            continue
        leftover.append(pid)
    assert not leftover, f"超时后残留子进程未被回收: {leftover}"


# ═══════════════════════════════════════════════════════════════════════════
# SC-R2-02 —— 带注释的模板不得被判成 parse_error（并吞掉该 job 的真实 diff）
# ═══════════════════════════════════════════════════════════════════════════

# 真实成因样本：模板的文档注释里含裸 `--`（旗标名）。XML 规范禁注释内 `--`，
# strict expat 拒解，但 plutil 与 launchd 自身的加载器都接受 —— 实测 launchd/ 下
# 9 个模板 `plutil -lint` 全 OK。逐字取自 launchd/com.akb2.feishu-sync.plist。
_BARE_DASH_COMMENT = (
    "  feishu-briefing-sync 每日 11:15 调度（prod：发 XD WEB 群）。\n"
    "  注意：本 plist 仅在 merge 部署后才 launchctl load；dev 期手动跑 wrapper"
    "（默认 --target dm）。"
)


def test_commented_template_is_not_parse_error(tmp_path: Path) -> None:
    """模板注释含裸 `--` → 不得出 parse_error，且该 job 的真实 diff 必须照常打印。

    R2 blocker：`_load_plist`（读 installed）有剥注释回退，`_render_template`
    （渲染仓库模板）是裸 plistlib —— 同一个文件在两处得出相反结论。于是
    feishu-sync 被判 `DRIFT reason=parse_error=not well-formed (line 6, column 64)`，
    整个 `_diff_dicts` 差异被 except 分支吞掉：drift_failures 计数虚高（4 而非真实的 3），
    而该 job **真正**的排期/env 漂移一行都看不到 —— 一个专为「运行态与仓库不一致
    且无人发现」而造的检测器，把自己要报的东西吞了。

    断言绑两件可观测的事：① 没有 parse_error；② 真实 diff 被打出来。
    只断 ① 会被"整条 job 静默跳过"骗过（那也没有 parse_error）。
    """
    harness = _Harness(tmp_path / "commented", loaded_labels={"com.akb2.commented"})
    harness.add_job(
        "com.akb2.commented",
        template_hours=[(10, 0)],
        manifest_hours=[(10, 0)],
        installed_hours=[(12, 5)],          # 种一个真实漂移，必须被看见
        template_comment=_BARE_DASH_COMMENT,
    )

    result = harness.run()
    combined = f"{result.stdout}\n{result.stderr}"

    line = next(
        ln for ln in result.stdout.splitlines() if "label=com.akb2.commented" in ln
    )
    assert "parse_error" not in line, (
        f"带注释的模板被判成解析失败（假 DRIFT，且真实 diff 被吞）:\n{line}"
    )
    # 真实漂移必须原样报出来：方向词 + 三方值 + 逐键 diff
    assert result.returncode == EXIT_DRIFT, combined
    assert "schedule_drift=installed_stale" in line, line
    assert "StartCalendarInterval repo=" in line, (
        f"真实 diff 又被 except 分支吞掉了:\n{line}"
    )

    # 反向守卫：真正不良构的模板仍必须被判 parse_error（别把咽喉做成"全放过"）
    broken = _Harness(tmp_path / "broken", loaded_labels={"com.akb2.broken"})
    broken.add_job(
        "com.akb2.broken",
        template_hours=[(10, 0)],
        manifest_hours=[(10, 0)],
        installed_hours=[(10, 0)],
    )
    template = broken.repo / "launchd" / "com.akb2.broken.plist"
    template.write_text(
        template.read_text(encoding="utf-8").replace("</dict>\n</plist>", ""),
        encoding="utf-8",
    )
    broken_result = broken.run()
    assert broken_result.returncode == EXIT_DRIFT, broken_result.stdout
    assert "parse_error" in broken_result.stdout, (
        f"真正不良构的模板被放行了（咽喉宽成了全放过）:\n{broken_result.stdout}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# SC-R2-16 —— 抑制项缺 reason 时 parity 必须 fail-closed（数据侧断言见
#             tests/test_launchd_manifest_suppressions.py）
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("case", "ignore_env"),
    [
        ("裸字符串列表（旧 schema）", ["AKB2_TRANSLATE_TIMEOUT"]),
        ("reason 缺失", [{"key": "AKB2_TRANSLATE_TIMEOUT"}]),
        ("reason 空白", [{"key": "AKB2_TRANSLATE_TIMEOUT", "reason": "   "}]),
    ],
    ids=["bare-string", "no-reason", "blank-reason"],
)
def test_parity_rejects_suppression_without_reason(
    tmp_path: Path, case: str, ignore_env: list[Any]
) -> None:
    """无理由的抑制不得被当成合法抑制放行 —— 静默忽略等于默许永久债。"""
    harness = _Harness(tmp_path / f"sup-{len(case)}", loaded_labels={"com.akb2.probe"})
    harness.add_job(
        "com.akb2.probe",
        template_hours=[(5, 30)],
        manifest_hours=[(5, 30)],
        installed_hours=[(5, 30)],
        ignore_env=ignore_env,
    )
    result = harness.run()
    combined = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0, f"{case}: 无理由抑制被放行了:\n{combined}"
    assert "reason" in combined, f"{case}: 拒绝了但没说清缺什么:\n{combined}"

    # 反向守卫：带非空 reason 的抑制必须正常工作（抑制掉 installed 侧多出的键）
    ok = _Harness(tmp_path / "sup-ok", loaded_labels={"com.akb2.probe"})
    ok.add_job(
        "com.akb2.probe",
        template_hours=[(5, 30)],
        manifest_hours=[(5, 30)],
        installed_hours=[(5, 30)],
        ignore_env=[{"key": "AKB2_EXTRA", "reason": "运行态多出的 no-op 键，理由见 manifest"}],
    )
    installed = ok.installed_dir / "com.akb2.probe.plist"
    data = plistlib.loads(installed.read_bytes())
    data["EnvironmentVariables"]["AKB2_EXTRA"] = "1"
    installed.write_bytes(plistlib.dumps(data))
    ok_result = ok.run()
    assert ok_result.returncode == EXIT_MATCH, (
        f"带 reason 的抑制没生效（installed 侧多出的键仍判 DRIFT）:\n"
        f"{ok_result.stdout}{ok_result.stderr}"
    )


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
