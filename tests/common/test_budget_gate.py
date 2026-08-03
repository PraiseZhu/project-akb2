from __future__ import annotations

import datetime as dt
import json
import os
import stat
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from src.akb2.common import budget_gate
from src.akb2.common.state_paths import state_file_path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
UTC = dt.UTC
UTC_DAILY_EXHAUSTED_AT = dt.datetime(2026, 8, 1, 21, 14, tzinfo=UTC)


def _record_daily_budget_exhausted(state_root: Path) -> None:
    os.environ["AKB2_STATE_ROOT"] = str(state_root)
    budget_gate.record_budget_event(
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.BUDGET_EXCEEDED,
            budget_scope=budget_gate.UTC_DAILY_SCOPE,
            spend=2701.23,
            cap=2700.0,
            budget_date="2026-08-01",
            provider_code=budget_gate.PROVIDER_CODE_429,
            raw_message=(
                "ExceededBudget: exceeded UTC daily cap. "
                "Date=2026-08-01, Spend=2701.23, DailyCap=2700.0"
            ),
        ),
        recorded_at=UTC_DAILY_EXHAUSTED_AT,
        consumer="seed",
    )


def _concurrent_record_and_query(state_root: str, consumer: str) -> dict[str, object]:
    os.environ["AKB2_STATE_ROOT"] = state_root
    budget_gate.record_budget_event(
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.BUDGET_EXCEEDED,
            budget_scope=budget_gate.UTC_DAILY_SCOPE,
            spend=2701.23,
            cap=2700.0,
            budget_date="2026-08-01",
            provider_code=budget_gate.PROVIDER_CODE_429,
            raw_message="ExceededBudget: exceeded UTC daily cap.",
        ),
        recorded_at=UTC_DAILY_EXHAUSTED_AT,
        consumer=consumer,
    )
    decision = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 1, 21, 30, tzinfo=UTC),
        consumer=consumer,
    )
    state = budget_gate.load_state_snapshot()
    return {
        "allowed": decision.allowed,
        "write_count": state["write_count"],
        "state": state,
    }


def _make_stub(path: Path, marker: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env bash\n"
        f"echo stub >> {str(marker)!r}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_utc_daily_boundary_short_circuits_until_next_utc_midnight(monkeypatch, tmp_path):
    monkeypatch.setenv("AKB2_STATE_ROOT", str(tmp_path / "state"))
    _record_daily_budget_exhausted(tmp_path / "state")

    at_2130 = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 1, 21, 30, tzinfo=UTC),
        consumer="github.synthesize",
    )
    at_2330 = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 1, 23, 30, tzinfo=UTC),
        consumer="quality.admission",
    )
    at_0405 = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 2, 4, 5, tzinfo=UTC),
        consumer="quality.distill",
    )

    assert at_2130.allowed is False
    assert at_2330.allowed is False
    assert at_0405.allowed is True


def test_corrupt_or_unreadable_state_degrades_fail_open(monkeypatch, tmp_path, caplog):
    state_root = tmp_path / "state"
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))
    caplog.set_level("WARNING")
    state_path = state_file_path(budget_gate.STATE_FILE_NAME)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"broken": ', encoding="utf-8")

    corrupt_decision = budget_gate.query_budget_gate(consumer="corrupt")
    assert corrupt_decision.allowed is True
    assert corrupt_decision.degraded is True

    state_path.write_text("{}", encoding="utf-8")
    state_path.chmod(0)
    try:
        unreadable_decision = budget_gate.query_budget_gate(consumer="chmod000")
    finally:
        state_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert unreadable_decision.allowed is True
    assert unreadable_decision.degraded is True
    assert "shared budget gate degraded to fail-open" in caplog.text


def test_concurrent_record_keeps_json_valid_and_counts_all_updates(monkeypatch, tmp_path):
    state_root = tmp_path / "state"
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))

    with ProcessPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                _concurrent_record_and_query,
                [str(state_root)] * 8,
                [f"consumer-{index}" for index in range(8)],
            )
        )

    assert all(result["allowed"] is False for result in results)
    snapshot = budget_gate.load_state_snapshot()
    assert snapshot["write_count"] == 8

    parsed = json.loads(
        state_file_path(budget_gate.STATE_FILE_NAME).read_text(encoding="utf-8")
    )
    assert isinstance(parsed, dict)
    assert parsed["write_count"] == 8


def test_daily_scripts_fail_visible_before_pipeline_when_budget_is_exhausted(tmp_path):
    state_root = tmp_path / "state"
    _record_daily_budget_exhausted(state_root)
    marker = tmp_path / "blocked.marker"
    github_stub = _make_stub(tmp_path / "github-stub.sh", marker)
    social_stub = _make_stub(tmp_path / "social-stub.sh", marker)

    common_env = {
        **os.environ,
        "AKB2_STATE_ROOT": str(state_root),
        "AKB2_DISABLE_DEADLINE": "1",
        "VAULT_ROOT": str(tmp_path / "vault"),
        "AKB2_GITHUB_BRIEFING_PIPELINE_BIN": str(github_stub),
        "AKB2_SOCIAL_BRIEFING_PIPELINE_BIN": str(social_stub),
        "AKB2_BUDGET_GATE_NOW": "2026-08-01T21:30:00Z",
    }

    github = subprocess.run(
        ["/bin/bash", str(SCRIPTS_DIR / "daily_github_briefing.sh")],
        cwd=str(PROJECT_ROOT),
        env=common_env,
        capture_output=True,
        text=True,
    )
    social = subprocess.run(
        [
            "/bin/bash",
            str(SCRIPTS_DIR / "daily_briefing.sh"),
            "--vault-root",
            str(tmp_path / "vault"),
        ],
        cwd=str(PROJECT_ROOT),
        env=common_env,
        capture_output=True,
        text=True,
    )

    for result in (github, social):
        combined = f"{result.stdout}\n{result.stderr}"
        assert result.returncode != 0
        assert result.returncode != 124
        assert "blocked_by_shared_budget scope=utc_daily" in combined
    assert not marker.exists()


def test_daily_scripts_allow_pipeline_when_budget_gate_is_clear(tmp_path):
    state_root = tmp_path / "state"
    marker = tmp_path / "allowed.marker"
    github_stub = _make_stub(tmp_path / "github-stub.sh", marker)
    social_stub = _make_stub(tmp_path / "social-stub.sh", marker)

    common_env = {
        **os.environ,
        "AKB2_STATE_ROOT": str(state_root),
        "AKB2_DISABLE_DEADLINE": "1",
        "VAULT_ROOT": str(tmp_path / "vault"),
        "AKB2_GITHUB_BRIEFING_PIPELINE_BIN": str(github_stub),
        "AKB2_SOCIAL_BRIEFING_PIPELINE_BIN": str(social_stub),
    }

    github = subprocess.run(
        ["/bin/bash", str(SCRIPTS_DIR / "daily_github_briefing.sh")],
        cwd=str(PROJECT_ROOT),
        env=common_env,
        capture_output=True,
        text=True,
    )
    social = subprocess.run(
        [
            "/bin/bash",
            str(SCRIPTS_DIR / "daily_briefing.sh"),
            "--vault-root",
            str(tmp_path / "vault"),
        ],
        cwd=str(PROJECT_ROOT),
        env=common_env,
        capture_output=True,
        text=True,
    )

    assert github.returncode == 0, github.stderr
    assert social.returncode == 0, social.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == ["stub", "stub"]
