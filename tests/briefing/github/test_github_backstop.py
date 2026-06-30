"""Tests for github_briefing_backstop.sh — SC-2: github链路补跑守卫。"""

import os
import subprocess
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
    args = ["/bin/bash", str(BACKSTOP_SH), "--vault-root", vault_root]
    if extra_args:
        args += extra_args
    env = {
        **os.environ,
        "AKB2_GITHUB_DEADLINE_HOUR": "99",
        "AKB2_GITHUB_DEADLINE_MINUTE": "0",
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

    def test_log_written_to_logs_dir(self, tmp_path):
        """Each run appends one line to logs/github-backstop.log."""
        vault = tmp_path / "vault"
        out_dir = vault / "Praise Space/AKB2.0/AI TRENDS/GitHub"
        out_dir.mkdir(parents=True)
        today = _today()
        (out_dir / f"{today} GitHub Briefing.md").write_text("# existing")

        _run_backstop(str(vault), ["--dry-run"])
        log_file = PROJECT_ROOT / "logs" / "github-backstop.log"
        assert log_file.exists(), "github-backstop.log should be created"
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
