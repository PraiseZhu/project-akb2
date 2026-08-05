"""Tests for github_briefing_backstop.sh — SC-2: github链路补跑守卫。"""

import fcntl
import os
import re
import subprocess
import tempfile
import time
from datetime import date
from pathlib import Path

import pytest

from tests.briefing.backstop_lock_test_support import (
    assert_flock_interleaving_matrix,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BACKSTOP_SH = PROJECT_ROOT / "scripts" / "github_briefing_backstop.sh"

# backstop **主动放弃** 的退出码。124 现在只属于「run_with_deadline.py 终止
# pipeline」这一种来源 —— 二者共用一码时 launchd 的 "last exit code 124" 分不出
# 是"pipeline 跑超时"还是"守卫压根没启动"，而这两件事的处置完全不同。
GIVE_UP_EXIT_CODE = 125
DEADLINE_EXIT_CODE = 124

def _today() -> str:
    """Return today's date in YYYY-MM-DD (mirrors pipeline.py today_str)."""
    return date.today().isoformat()


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def _stub_pgrep_probe(tmp_path, monkeypatch):
    """Keep every test independent of the machine-global process table."""
    stub_bin = tmp_path / "hermetic-pgrep-bin"
    stub_bin.mkdir()
    _write_exec(stub_bin / "pgrep", "exit 1")
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.environ['PATH']}")


def _run_backstop(
    vault_root: str,
    extra_args: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    # SC4: 默认把日志隔离到 tmp。历史上本脚本没有日志覆写口（只有 briefing 侧有
    # AKB2_BACKSTOP_LOG），于是每跑一次测试就往生产 logs/github-backstop.log 追加
    # 一整套场景行 —— 生产日志里那些"同一秒内 FAILED(rc=0)/FAILED(rc=2)/recovered
    # 交替多轮"就是测试留下的，不是线上重试。
    isolated_log = tempfile.NamedTemporaryFile(
        prefix="akb2-github-backstop-test-", suffix=".log", delete=False
    )
    isolated_log.close()
    # 锁同样要隔离：默认锁落机器本地状态根（~/Library/Application Support/akb2/
    # locks），测试不该碰真用户目录，也不该让并行用例互相抢同一把锁。
    isolated_lock = tempfile.NamedTemporaryFile(
        prefix="akb2-github-backstop-test-lock-", suffix=".pid", delete=False
    )
    isolated_lock.close()
    os.unlink(isolated_lock.name)
    args = ["/bin/bash", str(BACKSTOP_SH), "--vault-root", vault_root]
    if extra_args:
        args += extra_args
    env = {
        **os.environ,
        "AKB2_GITHUB_DEADLINE_HOUR": "99",
        "AKB2_GITHUB_DEADLINE_MINUTE": "0",
        "AKB2_GITHUB_BACKSTOP_LOG": isolated_log.name,
        "AKB2_GITHUB_BACKSTOP_LOCK_FILE": isolated_lock.name,
        "AKB2_GITHUB_RERUN_BACKOFF": "0",
        **(env_overrides or {}),
    }
    return subprocess.run(args, capture_output=True, text=True, env=env)


class TestGithubBackstop:
    """SC-2: github_briefing_backstop.sh — idempotent,補跑, pgrep guards."""

    def test_script_exists_and_is_executable(self):
        assert BACKSTOP_SH.exists(), f"Missing: {BACKSTOP_SH}"
        assert BACKSTOP_SH.stat().st_mode & 0o111, "script not executable"

    def test_requires_vault_root(self):
        result = subprocess.run(
            ["/bin/bash", str(BACKSTOP_SH)],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "vault-root" in result.stderr

    def test_dry_run_skip_when_briefing_exists(self, tmp_path):
        """Idempotent guard: today's GitHub Briefing already exists → skip."""
        vault = tmp_path / "vault"
        out_dir = vault / "Praise Space/AKB2.0/AI TRENDS/GitHub"
        out_dir.mkdir(parents=True)
        today = _today()
        (out_dir / f"{today} GitHub Briefing.md").write_text("# existing")

        result = _run_backstop(str(vault), ["--dry-run"])
        assert "skip" in result.stderr
        assert "已产出" in result.stderr

    def test_dry_run_would_trigger_when_briefing_missing(self, tmp_path):
        """When briefing is absent, dry-run reports it would trigger a re-run."""
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)

        result = _run_backstop(str(vault), ["--dry-run"])
        assert result.returncode == 0
        assert "dry-run" in result.stderr
        assert "补跑" in result.stderr

    def test_deadline_missed_blocks_late_rerun(self, tmp_path):
        """过 deadline 拒绝补跑 = backstop **主动放弃** → 125，不是 124。

        124/125 的分工（L3-5，别再把这条改回 124）：
          124  只表示「pipeline 被 scripts/run_with_deadline.py 到点终止」。它同时是
               GNU timeout(1) 的超时约定码，通用监控读到它会理解成"某个子进程超时"——
               只有这一种来源时那个理解才是对的。
          125  backstop 自己决定不启动/不继续补跑。
        两者共用一码时，launchd 的 `LastExitStatus 124` 完全分不出是"pipeline 跑超时"
        还是"守卫压根没启动"，而这两件事的处置完全不同。
        运行时的另一半由 test_pipeline_deadline_kill_propagates_124_without_retry 钉住。
        """
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)

        result = _run_backstop(
            str(vault),
            ["--dry-run"],
            env_overrides={
                "AKB2_GITHUB_DEADLINE_HOUR": "0",
                "AKB2_GITHUB_DEADLINE_MINUTE": "0",
            },
        )
        # backstop 主动放弃 → 125；124 留给 run_with_deadline 终止 pipeline
        assert result.returncode == GIVE_UP_EXIT_CODE
        assert "deadline" in result.stderr

    def test_log_written_to_configured_log(self, tmp_path):
        """Each run appends one line to the configured backstop log."""
        vault = tmp_path / "vault"
        out_dir = vault / "Praise Space/AKB2.0/AI TRENDS/GitHub"
        out_dir.mkdir(parents=True)
        today = _today()
        (out_dir / f"{today} GitHub Briefing.md").write_text("# existing")

        log_file = tmp_path / "github-backstop.log"
        _run_backstop(
            str(vault), ["--dry-run"],
            env_overrides={"AKB2_GITHUB_BACKSTOP_LOG": str(log_file)},
        )
        assert log_file.exists(), "configured github-backstop log should be created"
        content = log_file.read_text()
        assert "github-backstop" in content

    def test_plist_exists_with_correct_schedule(self):
        """LaunchAgent plist for github backstop exists and schedules at 07:30."""
        plist = PROJECT_ROOT / "launchd" / "com.akb2.github_briefing.backstop.plist"
        assert plist.exists(), f"Missing plist: {plist}"
        content = plist.read_text()
        assert "com.akb2.github_briefing.backstop" in content
        assert "<integer>7</integer>" in content   # Hour = 7
        assert "<integer>30</integer>" in content  # Minute = 30
        assert "github_briefing_backstop.sh" in content

    # ── SC-5: 补跑环境对齐 + recovered 须复查文件 ───────────────────────────

    def _stub(self, tmp_path, body: str) -> str:
        stub = tmp_path / "stub_rerun.sh"
        stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
        stub.chmod(0o755)
        return str(stub)

    def test_recovered_requires_file(self, tmp_path):
        """SC-5: stub 返 0 但不写文件 → backstop 非0 且日志不含 recovered（假成功纠正）。"""
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        stub = self._stub(tmp_path, "exit 0")
        result = _run_backstop(
            str(vault),
            env_overrides={
                "AKB2_GITHUB_RERUN_SCRIPT": stub,
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
            },
        )
        assert result.returncode != 0, "rc==0 但无文件必须 fail-visible 非0"
        assert "recovered" not in result.stderr, "无文件不应记 recovered"
        assert "FAILED" in result.stderr

    def test_missing_output_fails_visible(self, tmp_path):
        """SC-5: stub 返非0 → backstop 非0 且日志不含 recovered。"""
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        stub = self._stub(tmp_path, "exit 2")
        result = _run_backstop(
            str(vault),
            env_overrides={
                "AKB2_GITHUB_RERUN_SCRIPT": stub,
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
            },
        )
        assert result.returncode != 0
        assert "recovered" not in result.stderr
        assert "FAILED" in result.stderr

    def test_sources_env_before_rerun(self, tmp_path):
        """SC-5: 补跑前 source env — stub 仅在 env marker 存在时写文件 → recovered exit 0。

        断言绑定可观测行为：env 被.source 后 marker 注入子进程，stub 据此落盘文件，
        backstop 复查文件存在才记 recovered；env 未 source 时 stub 不写文件 → FAIL。
        """
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)

        env_file = tmp_path / "fake.env"
        env_file.write_text("export AKB2_TEST_ENV_MARKER=loaded\n", encoding="utf-8")

        stub_body = (
            'if [[ "${AKB2_TEST_ENV_MARKER:-}" == "loaded" ]]; then\n'
            '  mkdir -p "$(dirname "$GITHUB_FILE")"\n'
            '  echo "# briefing" > "$GITHUB_FILE"\n'
            "fi\n"
            "exit 0"
        )
        stub = self._stub(tmp_path, stub_body)

        result = _run_backstop(
            str(vault),
            env_overrides={
                "AKB2_GITHUB_RERUN_SCRIPT": stub,
                "AKB2_GITHUB_ENV_FILE": str(env_file),
            },
        )
        assert result.returncode == 0, f"env 已 source + 文件落盘应 recovered: {result.stderr}"
        assert "recovered" in result.stderr


# ── 缺陷 C 回归：配置与日志噪音 ────────────────────────────────────────────
#
# 生产实证（logs/github-backstop.log，07-28 13:55:44 / 14:57:49 / 14:58:43）：
# 同一秒内反复出现 `warn — env file /dev/null 缺失` + FAILED(rc=0) + FAILED(rc=2)
# + recovered 交替。三处根因：
#   1. 本脚本没有日志覆写口 → 测试直接往生产日志追加（408 条 /dev/null warn 全是
#      测试留下的），把测试场景伪装成线上重试轮次；
#   2. `AKB2_GITHUB_ENV_FILE=/dev/null` 是测试用来"显式不加载 env"的标记，却被
#      当成"配置缺失"记 warn —— 误导性告警（宪法 P4：失败可见但不得误导）；
#   3. 日志行不带 run_id/host，多进程 + Syncthing 双机共享 logs/ 时无法归属，
#      单次 run 的终局也没有机器可判的标记。


class TestGithubBackstopLogHygiene:
    def _vault_without_briefing(self, tmp_path) -> Path:
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        return vault

    def _vault_with_briefing(self, tmp_path) -> Path:
        vault = tmp_path / "vault"
        out_dir = vault / "Praise Space/AKB2.0/AI TRENDS/GitHub"
        out_dir.mkdir(parents=True)
        (out_dir / f"{_today()} GitHub Briefing.md").write_text("# existing")
        return vault

    def test_repo_github_backstop_log_not_modified(self, tmp_path):
        """SC4: 测试运行绝不改动生产 logs/github-backstop.log。"""
        repo_log = PROJECT_ROOT / "logs" / "github-backstop.log"
        repo_log.parent.mkdir(parents=True, exist_ok=True)
        if not repo_log.exists():
            repo_log.write_text("", encoding="utf-8")
        before = repo_log.stat().st_mtime_ns

        _run_backstop(str(self._vault_with_briefing(tmp_path)), ["--dry-run"])

        assert repo_log.stat().st_mtime_ns == before, "测试不应修改仓库 logs/github-backstop.log"

    def test_log_path_arg_overrides_env(self, tmp_path):
        """--log-path 优先级高于 AKB2_GITHUB_BACKSTOP_LOG（与 briefing 侧对齐）。"""
        arg_log = tmp_path / "arg.log"
        env_log = tmp_path / "env.log"
        result = _run_backstop(
            str(self._vault_with_briefing(tmp_path)),
            ["--dry-run", "--log-path", str(arg_log)],
            env_overrides={"AKB2_GITHUB_BACKSTOP_LOG": str(env_log)},
        )
        assert result.returncode == 0, result.stderr
        assert arg_log.exists() and arg_log.read_text(encoding="utf-8").strip()
        assert not env_log.exists()

    def test_devnull_env_file_is_explicit_skip_not_warn(self, tmp_path):
        """SC4: /dev/null 是"显式不加载 env"，必须记为显式跳过而非配置缺失告警。"""
        log = tmp_path / "gh.log"
        stub = tmp_path / "stub.sh"
        stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)

        _run_backstop(
            str(self._vault_without_briefing(tmp_path)),
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub),
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
            },
        )

        content = log.read_text(encoding="utf-8")
        assert "env file" not in content, f"/dev/null 不是配置缺失:\n{content}"
        # 断言范围限定在 env 归因上：本用例守的是"显式跳过不得被记成配置缺失告警"，
        # 不是"整份日志一个 warn 都不能有"（补跑本身失败时记 warn 是正确的
        # fail-visible 行为，把它一并禁掉会让这条断言反过来压制真告警）。
        env_warns = [
            ln for ln in content.splitlines() if "warn" in ln and "env" in ln
        ]
        assert not env_warns, f"显式跳过不得记 env 相关 warn:\n{env_warns}"
        assert "env_load=missing" not in content, content
        assert "env_load=skipped_explicit" in content, content

    def test_real_env_file_absence_still_warns(self, tmp_path):
        """真·配置缺失（指向不存在的路径）仍必须 warn —— 别把告警一并关掉。"""
        log = tmp_path / "gh.log"
        stub = tmp_path / "stub.sh"
        stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)

        _run_backstop(
            str(self._vault_without_briefing(tmp_path)),
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub),
                "AKB2_GITHUB_ENV_FILE": str(tmp_path / "nope.env"),
            },
        )

        content = log.read_text(encoding="utf-8")
        assert "warn" in content and "缺失" in content, content
        assert "env_load=missing" in content, content

    def test_single_terminal_state_per_run_with_round_marker(self, tmp_path):
        """SC4: 单次 run 的日志行同 run_id，终态唯一，补跑带轮次标记。"""
        log = tmp_path / "gh.log"
        stub = tmp_path / "stub.sh"
        stub.write_text("#!/usr/bin/env bash\nexit 2\n", encoding="utf-8")
        stub.chmod(0o755)

        result = _run_backstop(
            str(self._vault_without_briefing(tmp_path)),
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub),
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
            },
        )
        assert result.returncode == 2

        lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        run_ids = {ln.split("run_id=")[1].split()[0] for ln in lines}
        assert len(run_ids) == 1, f"单次调用必须共用一个 run_id: {run_ids}"
        terminals = [ln for ln in lines if "terminal=" in ln]
        assert len(terminals) == 1, f"单 run 终态必须唯一:\n{chr(10).join(lines)}"
        assert "terminal=failed" in terminals[0]
        assert any("attempt=" in ln for ln in lines), "补跑轮次必须带 attempt 标记"

    def test_log_lines_carry_run_id_and_host(self, tmp_path):
        """logs/ 经 Syncthing 双机共享 → 每行必须能归属到 run 与机器。"""
        log = tmp_path / "gh.log"
        _run_backstop(
            str(self._vault_with_briefing(tmp_path)), ["--dry-run"],
            env_overrides={"AKB2_GITHUB_BACKSTOP_LOG": str(log)},
        )
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                assert "run_id=" in line and "host=" in line, f"缺归属字段: {line}"

    def test_deadline_exit_code_is_named_and_logged(self, tmp_path):
        """SC3 对齐: 放弃码必须自证来源且是具名常量。"""
        log = tmp_path / "gh.log"
        result = _run_backstop(
            str(self._vault_without_briefing(tmp_path)), ["--dry-run"],
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_DEADLINE_HOUR": "0",
                "AKB2_GITHUB_DEADLINE_MINUTE": "0",
            },
        )
        assert result.returncode == GIVE_UP_EXIT_CODE
        content = log.read_text(encoding="utf-8")
        assert f"exit_code={GIVE_UP_EXIT_CODE}" in content, content
        assert "reason=deadline_missed" in content, content
        script = BACKSTOP_SH.read_text(encoding="utf-8")
        assert "DEADLINE_EXIT_CODE=124" in script, "124 必须是具名常量"
        assert "GIVE_UP_EXIT_CODE=125" in script, "主动放弃码必须是具名常量"
        assert "RERUN_ATTEMPT_BUDGET" in script, "补跑重试预算必须是具名常量"


# ── L3-3 / L3-4 / L3-5 回归：重试维度、互斥锁、退出码语义 ─────────────────────
#
# L3-3 反证（2026-08-02 实测）：synthesis 内部已有 3 次重试，主跑 05:59 用掉 3 次、
# backstop 07:30 又 3 次 —— **共 6 次同族尝试全 429**。所以要变的是**模型族**，
# 不是次数；预算取 2，第 2 轮换族（COMMON.md 契约 1）。
# L3-4：`attempt=1/1` 曾是**字面量字符串**，全脚本唯一具名的重试预算给了读盘 ——
# 最不需要它的环节；而真正会双跑的补跑环节连锁都没有。


def _counting_rerun_stub(
    tmp_path: Path, succeed_from: int, *, write_file: bool = True
) -> Path:
    """补跑 stub：第 succeed_from 轮起成功；每轮记录轮次与收到的模型族开关。"""
    counter = tmp_path / "rerun-attempts.txt"
    env_log = tmp_path / "rerun-env.txt"
    marker = tmp_path / "rerun-marker.txt"
    body_write = (
        'mkdir -p "$(dirname "$GITHUB_FILE")"\necho "# briefing" > "$GITHUB_FILE"'
        if write_file
        else ":"
    )
    body = f"""
n=$(cat "{counter}" 2>/dev/null || echo 0)
n=$(( n + 1 ))
echo "$n" > "{counter}"
echo "attempt=$n AKB2_LLM_FAMILY_FALLBACK=${{AKB2_LLM_FAMILY_FALLBACK:-<unset>}}" >> "{env_log}"
echo "invoked" >> "{marker}"
if [ "$n" -lt {succeed_from} ]; then
  exit 1
fi
{body_write}
exit 0
"""
    return _write_exec(tmp_path / "counting-rerun-stub.sh", body.strip())


def _marker_calls(tmp_path: Path) -> int:
    marker = tmp_path / "rerun-marker.txt"
    if not marker.exists():
        return 0
    return len([ln for ln in marker.read_text(encoding="utf-8").splitlines() if ln.strip()])


class TestGithubBackstopRerunBudget:
    def _vault(self, tmp_path) -> Path:
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        return vault

    def test_second_attempt_switches_model_family(self, tmp_path):
        """SC-5: 第 1 轮 exit 1、第 2 轮 exit 0 并写出文件 → recovered，且第 2 轮换了族。

        断言绑可观测行为：不是"循环跑了两次"，而是"第二次带上了
        AKB2_LLM_FAMILY_FALLBACK=1"（契约 1 的生产端）。同族第 7/8/9 次只会多烧额度。
        """
        log = tmp_path / "gh.log"
        stub = _counting_rerun_stub(tmp_path, succeed_from=2)
        result = _run_backstop(
            str(self._vault(tmp_path)),
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub),
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
                "AKB2_GITHUB_RERUN_ATTEMPTS": "2",
            },
        )
        content = log.read_text(encoding="utf-8")
        assert result.returncode == 0, content
        assert "terminal=recovered" in content, content
        assert "attempt=1/2" in content and "attempt=2/2" in content, content
        assert "attempt=3/2" not in content, f"预算之外的轮次不得出现:\n{content}"

        env_lines = (tmp_path / "rerun-env.txt").read_text(encoding="utf-8").splitlines()
        assert len(env_lines) == 2, f"应恰好补跑 2 轮:\n{env_lines}"
        assert "AKB2_LLM_FAMILY_FALLBACK=0" in env_lines[0], f"第 1 轮不该换族: {env_lines[0]}"
        assert "AKB2_LLM_FAMILY_FALLBACK=1" in env_lines[1], f"第 2 轮必须换族: {env_lines[1]}"

    def test_rerun_budget_is_bounded_and_named(self, tmp_path):
        """SC-3 有界: 恒失败的 stub 只被调用 RERUN_ATTEMPT_BUDGET 次，一次不多。"""
        log = tmp_path / "gh.log"
        stub = _counting_rerun_stub(tmp_path, succeed_from=99, write_file=False)
        result = _run_backstop(
            str(self._vault(tmp_path)),
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub),
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
                "AKB2_GITHUB_RERUN_ATTEMPTS": "3",
            },
        )
        content = log.read_text(encoding="utf-8")
        assert result.returncode != 0
        assert _marker_calls(tmp_path) == 3, f"补跑轮次必须严格等于预算:\n{content}"
        assert "attempt=4/3" not in content, content
        assert "terminal=failed" in content, content

    def test_deadline_stops_retry_loop_before_next_attempt(self, tmp_path):
        """SC-7: 预算给到 9 也不能越过 deadline —— 第一次 deadline 检查即停，退 125。

        `AKB2_GITHUB_DEADLINE_HOUR=0` 让 deadline 恒为过界：循环开跑前就被拦下，
        日志里 attempt 不超过 1，且补跑 stub 一次都没被调用。
        """
        log = tmp_path / "gh.log"
        stub = _counting_rerun_stub(tmp_path, succeed_from=99, write_file=False)
        result = _run_backstop(
            str(self._vault(tmp_path)),
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub),
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
                "AKB2_GITHUB_RERUN_ATTEMPTS": "9",
                "AKB2_GITHUB_DEADLINE_HOUR": "0",
                "AKB2_GITHUB_DEADLINE_MINUTE": "0",
            },
        )
        content = log.read_text(encoding="utf-8")
        assert result.returncode == GIVE_UP_EXIT_CODE, (
            f"主动放弃必须是 {GIVE_UP_EXIT_CODE}，实际 {result.returncode}:\n{content}"
        )
        assert "attempt=2/" not in content, f"deadline 后不得进入第 2 轮:\n{content}"
        assert "reason=deadline_missed" in content, content
        assert _marker_calls(tmp_path) == 0, "过 deadline 时补跑一次都不该启动"

    def test_invalid_rerun_budget_fails_loudly(self, tmp_path):
        """预算配错（0 / 非数字）必须硬失败，不得静默退化成"跑一次"或死循环。"""
        vault = str(self._vault(tmp_path))
        for bad in ("0", "abc", "-1"):
            result = _run_backstop(
                vault,
                ["--dry-run"],
                env_overrides={"AKB2_GITHUB_RERUN_ATTEMPTS": bad},
            )
            assert result.returncode != 0, f"AKB2_GITHUB_RERUN_ATTEMPTS={bad} 应被拒"
            assert "AKB2_GITHUB_RERUN_ATTEMPTS" in result.stderr, result.stderr


class TestGithubBackstopLock:
    """SC-8: 补跑必须有 flock —— pgrep 在 backstop 触发时必然扑空。"""

    def test_concurrent_backstop_blocked_by_pid_lock(self, tmp_path):
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        lock = tmp_path / "shared.lock"
        marker = tmp_path / "pipeline-marker.txt"
        release = tmp_path / "release"
        holder = _write_exec(
            tmp_path / "holder.sh",
            f'''echo "invoked" >> "{marker}"
i=0
while [ ! -f "{release}" ] && [ "$i" -lt 100 ]; do sleep 0.2; i=$(( i + 1 )); done
exit 1''',
        )
        second = _write_exec(
            tmp_path / "second.sh", f'echo "invoked" >> "{marker}"\nexit 0'
        )
        common = {
            **os.environ,
            "AKB2_GITHUB_DEADLINE_HOUR": "99",
            "AKB2_GITHUB_DEADLINE_MINUTE": "0",
            "AKB2_GITHUB_BACKSTOP_LOCK_FILE": str(lock),
            "AKB2_GITHUB_ENV_FILE": "/dev/null",
            "AKB2_GITHUB_RERUN_ATTEMPTS": "1",
            "AKB2_GITHUB_RERUN_BACKOFF": "0",
        }
        first = subprocess.Popen(
            ["/bin/bash", str(BACKSTOP_SH), "--vault-root", str(vault)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**common,
                 "AKB2_GITHUB_BACKSTOP_LOG": str(tmp_path / "first.log"),
                 "AKB2_GITHUB_RERUN_SCRIPT": str(holder)},
        )
        try:
            deadline = time.time() + 20
            while time.time() < deadline:
                if lock.exists() and marker.exists():
                    break
                time.sleep(0.1)
            assert lock.exists(), "第一个 backstop 应已建立锁文件"
            assert lock.exists(), "flock 的共享 inode 必须在持锁期间存在"

            blocked = subprocess.run(
                ["/bin/bash", str(BACKSTOP_SH), "--vault-root", str(vault)],
                capture_output=True, text=True,
                env={**common,
                     "AKB2_GITHUB_BACKSTOP_LOG": str(tmp_path / "second.log"),
                     "AKB2_GITHUB_RERUN_SCRIPT": str(second)},
            )
        finally:
            release.write_text("go", encoding="utf-8")
            try:
                first.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                first.kill()
                first.communicate()

        second_log = (tmp_path / "second.log").read_text(encoding="utf-8")
        assert blocked.returncode == 0, f"被锁挡住是正常早退:\n{second_log}"
        assert "already-running" in second_log, second_log
        assert "terminal=skip" in second_log, second_log
        calls = len(
            [ln for ln in marker.read_text(encoding="utf-8").splitlines() if ln.strip()]
        )
        assert calls == 1, f"补跑只应被调用 1 次（双跑防线），实际 {calls} 次"

    def test_lock_released_on_normal_exit(self, tmp_path):
        """锁必须在正常退出时释放，否则一次补跑就把后续所有 backstop 永久锁死。"""
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        lock = tmp_path / "release.lock"
        stub = _counting_rerun_stub(tmp_path, succeed_from=1)
        result = _run_backstop(
            str(vault),
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOCK_FILE": str(lock),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub),
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
            },
        )
        assert result.returncode == 0, result.stderr
        assert lock.exists(), "共享 flock inode 必须保留，禁止 unlink/replace"
        with lock.open("a+") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def test_stale_takeover_interleaving_matrix(tmp_path):
    """Flock has no rename takeover: stale inode, N-way start, and owner death are safe."""
    assert_flock_interleaving_matrix(tmp_path, "github", PROJECT_ROOT)


def test_exit_codes_are_distinct_named_constants():
    """SC-9 静态: DEADLINE_EXIT_CODE 与 GIVE_UP_EXIT_CODE 都具名且取值不同。"""
    text = BACKSTOP_SH.read_text(encoding="utf-8")
    deadline = re.search(r"^readonly DEADLINE_EXIT_CODE=(\d+)$", text, re.M)
    give_up = re.search(r"^readonly GIVE_UP_EXIT_CODE=(\d+)$", text, re.M)
    assert deadline and give_up, "两个退出码都必须是具名常量"
    assert deadline.group(1) == str(DEADLINE_EXIT_CODE)
    assert give_up.group(1) == str(GIVE_UP_EXIT_CODE)
    assert deadline.group(1) != give_up.group(1), (
        "同值 = launchd 的 'last exit code' 又不可分辨（L3-5 的整个修法就白做了）"
    )


# ── 124 / 125 分工的运行时锁死 + 非重试码（集成裁决后补）────────────────────
#
# 集成时 fix-feishu-recovery 报 `expected rc 124, actual rc 125`，裁决为「实现对、
# 测试改」。为免日后有人再把期望改回 124，这里用**成对**用例把两种来源都钉住：
#   本脚本主动放弃            → 125（test_deadline_missed_blocks_late_rerun）
#   pipeline 被 deadline 终止 → 124（下面这条，原样透传，且不再重试）


class TestGithubBackstopExitCodeProvenance:
    def _vault(self, tmp_path) -> Path:
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        return vault

    def test_pipeline_deadline_kill_propagates_124_without_retry(self, tmp_path):
        """补跑子进程返 124（run_with_deadline 终止）→ backstop 原样退 124，且不再试第 2 轮。

        为什么不重试：124 的含义是"这一轮跑不完"，再跑一轮同样跑不完，只会把 backstop
        自己的运行时间推过 deadline —— 那正是 L3-3 要消掉的"在明知无望处烧额度"。
        """
        log = tmp_path / "gh.log"
        stub = _counting_rerun_stub(tmp_path, succeed_from=99, write_file=False)
        result = _run_backstop(
            str(self._vault(tmp_path)),
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub),
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
                "AKB2_GITHUB_RERUN_ATTEMPTS": "2",
            },
        )
        content = log.read_text(encoding="utf-8")
        # stub 恒失败但退的是 1，先确认基线：可重试码会用满 2 轮
        assert _marker_calls(tmp_path) == 2, f"可重试码应用满预算:\n{content}"

        # 换成 124：必须只跑 1 轮并原样透传 124
        log2 = tmp_path / "gh2.log"
        stub124 = _write_exec(
            tmp_path / "stub124.sh",
            f'echo invoked >> "{tmp_path / "marker124.txt"}"\nexit {DEADLINE_EXIT_CODE}',
        )
        result = _run_backstop(
            str(tmp_path / "vault"),
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOG": str(log2),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub124),
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
                "AKB2_GITHUB_RERUN_ATTEMPTS": "2",
            },
        )
        content2 = log2.read_text(encoding="utf-8")
        calls124 = len(
            [ln for ln in (tmp_path / "marker124.txt").read_text().splitlines() if ln.strip()]
        )
        assert result.returncode == DEADLINE_EXIT_CODE, (
            f"pipeline 被 deadline 终止时必须原样传出 124（不能变成 125）:\n{content2}"
        )
        assert result.returncode != GIVE_UP_EXIT_CODE
        assert calls124 == 1, f"124 是非重试码，只应跑 1 轮，实际 {calls124}"
        assert "rerun_stop=nonretryable_exit" in content2, content2
        assert "attempt=1/2" in content2 and "attempt=2/2" not in content2, content2

    def test_retryable_code_still_uses_full_budget(self, tmp_path):
        """反面：非重试码清单不得过宽 —— rc=1 仍必须用满预算并换族。"""
        log = tmp_path / "gh.log"
        stub = _counting_rerun_stub(tmp_path, succeed_from=99, write_file=False)
        _run_backstop(
            str(self._vault(tmp_path)),
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub),
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
                "AKB2_GITHUB_RERUN_ATTEMPTS": "2",
            },
        )
        content = log.read_text(encoding="utf-8")
        assert _marker_calls(tmp_path) == 2, content
        assert "rerun_stop=nonretryable_exit" not in content, "rc=1 不该被当成非重试码"
        env_lines = (tmp_path / "rerun-env.txt").read_text(encoding="utf-8").splitlines()
        assert "AKB2_LLM_FAMILY_FALLBACK=1" in env_lines[1], env_lines


class TestGithubBackstopBackoffProjection:
    """睡前 deadline 投影：github 侧与 social 侧共用同一套逻辑，必须同样锁住。

    两个脚本是手工保持一致的（没有共享 shell 库），只测一边等于放任另一边漂移。
    """

    def test_gives_up_instead_of_sleeping_past_deadline(self, tmp_path):
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        stub = _counting_rerun_stub(tmp_path, succeed_from=99, write_file=False)
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_exec(
            fake_bin / "date",
            f"""case "${{1:-}}" in
  +%H%M) echo 1150 ;;
  +%Y-%m-%d) echo {_today()} ;;
  *) echo "{_today()} 11:50:00" ;;
esac""",
        )
        log = tmp_path / "gh.log"
        started = time.monotonic()
        result = _run_backstop(
            str(vault),
            env_overrides={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_BACKSTOP_LOCK_FILE": str(tmp_path / "gh.lock"),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub),
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
                "AKB2_GITHUB_RERUN_ATTEMPTS": "2",
                "AKB2_GITHUB_RERUN_BACKOFF": "600",   # 真实生产值
                "AKB2_GITHUB_DEADLINE_HOUR": "12",
                "AKB2_GITHUB_DEADLINE_MINUTE": "0",
            },
        )
        elapsed = time.monotonic() - started
        content = log.read_text(encoding="utf-8")
        assert result.returncode == GIVE_UP_EXIT_CODE, content
        assert "terminal=give_up" in content, content
        assert "reason=deadline_would_pass" in content, content
        assert _marker_calls(tmp_path) == 1, f"只应补跑 1 轮:\n{content}"
        assert elapsed < 60, f"不得先睡再放弃，实测 {elapsed:.1f}s"
        lock = tmp_path / "gh.lock"
        assert lock.exists(), "共享 flock inode 不得删除"
        with lock.open("a+") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def test_still_sleeps_when_margin_sufficient(self, tmp_path):
        """反面：余量充足时照常退避并跑第 2 轮（投影判据不得过宽）。"""
        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        stub = _counting_rerun_stub(tmp_path, succeed_from=99, write_file=False)
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_exec(
            fake_bin / "date",
            f"""case "${{1:-}}" in
  +%H%M) echo 0800 ;;
  +%Y-%m-%d) echo {_today()} ;;
  *) echo "{_today()} 08:00:00" ;;
esac""",
        )
        log = tmp_path / "gh.log"
        _run_backstop(
            str(vault),
            env_overrides={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_RERUN_SCRIPT": str(stub),
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
                "AKB2_GITHUB_RERUN_ATTEMPTS": "2",
                "AKB2_GITHUB_RERUN_BACKOFF": "1",
                "AKB2_GITHUB_DEADLINE_HOUR": "12",
                "AKB2_GITHUB_DEADLINE_MINUTE": "0",
            },
        )
        content = log.read_text(encoding="utf-8")
        assert "reason=deadline_would_pass" not in content, content
        assert _marker_calls(tmp_path) == 2, f"应跑满 2 轮:\n{content}"
