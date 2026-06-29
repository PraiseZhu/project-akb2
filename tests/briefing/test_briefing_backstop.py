"""Tests for social briefing backstop deadline and frontmatter behavior."""

from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKSTOP_SH = PROJECT_ROOT / "scripts" / "briefing_backstop.sh"


def _today() -> str:
    return date.today().isoformat()


def _run_backstop(
    vault_root: Path,
    extra_args: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    args = ["/bin/bash", str(BACKSTOP_SH), "--vault-root", str(vault_root)]
    if extra_args:
        args += extra_args
    env = {
        **os.environ,
        "AKB2_BRIEFING_DEADLINE_HOUR": "99",
        "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        **(env_overrides or {}),
    }
    return subprocess.run(args, capture_output=True, text=True, env=env)


def test_existing_pass_report_skips_even_after_deadline(tmp_path):
    """macOS-compatible frontmatter parsing must recognize audit_verdict=pass."""
    vault = tmp_path / "vault"
    out_dir = vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social"
    out_dir.mkdir(parents=True)
    (out_dir / f"{_today()} Daily Briefing.md").write_text(
        "---\naudit_verdict: pass\n---\n# briefing\n",
        encoding="utf-8",
    )

    result = _run_backstop(
        vault,
        ["--dry-run"],
        env_overrides={
            "AKB2_BRIEFING_DEADLINE_HOUR": "0",
            "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        },
    )

    assert result.returncode == 0
    assert "audit_verdict=pass" in result.stderr
    assert "无需补跑" in result.stderr


def test_missing_report_after_deadline_fails_visibly(tmp_path):
    """After the noon deadline, missing Social output must not start a late rerun."""
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    result = _run_backstop(
        vault,
        ["--dry-run"],
        env_overrides={
            "AKB2_BRIEFING_DEADLINE_HOUR": "0",
            "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        },
    )

    assert result.returncode == 124
    assert "deadline" in result.stderr
    assert "不再启动补跑" in result.stderr


def test_morning_deadline_compare_uses_decimal_time(tmp_path):
    """08:00 must not trip bash's octal parsing when comparing HHMM values."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text(
        """#!/usr/bin/env bash
case "${1:-}" in
  +%H%M) echo 0800 ;;
  +%Y-%m-%d) echo 2026-06-19 ;;
  *) echo "2026-06-19 08:00:00" ;;
esac
""",
        encoding="utf-8",
    )
    fake_date.chmod(0o755)
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    result = _run_backstop(
        vault,
        ["--dry-run"],
        env_overrides={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "AKB2_BRIEFING_DEADLINE_HOUR": "12",
            "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        },
    )

    assert result.returncode == 0
    assert "dry-run" in result.stderr
    assert "value too great for base" not in result.stderr
