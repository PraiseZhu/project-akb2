"""Tests for github_briefing_backstop.sh — SC-2: github链路补跑守卫。"""

import os
import subprocess
import tempfile
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BACKSTOP_SH = PROJECT_ROOT / "scripts" / "github_briefing_backstop.sh"


def _today() -> str:
    """Return today's date in YYYY-MM-DD (mirrors pipeline.py today_str)."""
    return date.today().isoformat()


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
    args = ["/bin/bash", str(BACKSTOP_SH), "--vault-root", vault_root]
    if extra_args:
        args += extra_args
    env = {
        **os.environ,
        "AKB2_GITHUB_DEADLINE_HOUR": "99",
        "AKB2_GITHUB_DEADLINE_MINUTE": "0",
        "AKB2_GITHUB_BACKSTOP_LOG": isolated_log.name,
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
        """After the noon deadline, a missing report must fail visibly, not rerun late."""
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
        assert result.returncode == 124
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
        assert "warn" not in content, f"显式跳过不得记 warn:\n{content}"
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
        """SC3 对齐: 124 必须自证来源且是具名常量。"""
        log = tmp_path / "gh.log"
        result = _run_backstop(
            str(self._vault_without_briefing(tmp_path)), ["--dry-run"],
            env_overrides={
                "AKB2_GITHUB_BACKSTOP_LOG": str(log),
                "AKB2_GITHUB_DEADLINE_HOUR": "0",
                "AKB2_GITHUB_DEADLINE_MINUTE": "0",
            },
        )
        assert result.returncode == 124
        content = log.read_text(encoding="utf-8")
        assert "exit_code=124" in content and "reason=deadline_missed" in content, content
        script = BACKSTOP_SH.read_text(encoding="utf-8")
        assert "DEADLINE_EXIT_CODE=124" in script, "124 必须是具名常量"
