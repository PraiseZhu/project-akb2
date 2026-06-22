"""Tests for github_briefing_backstop.sh — SC-2: github链路补跑守卫。"""

import subprocess
import os
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
