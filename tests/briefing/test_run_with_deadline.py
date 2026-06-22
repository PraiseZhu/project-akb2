"""Tests for the same-day deadline wrapper."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = PROJECT_ROOT / "scripts" / "run_with_deadline.py"


def test_deadline_already_missed_does_not_start_child(tmp_path):
    marker = tmp_path / "started"
    result = subprocess.run(
        [
            sys.executable,
            str(WRAPPER),
            "--name",
            "test-deadline",
            "--date",
            date.today().isoformat(),
            "--deadline-hour",
            "0",
            "--deadline-minute",
            "0",
            "--",
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('started')",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 124
    assert "deadline already missed" in result.stderr
    assert not marker.exists()
