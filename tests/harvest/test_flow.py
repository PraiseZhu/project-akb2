from __future__ import annotations

import json
from pathlib import Path

from akb2.harvest.flow import (
    AssemblyRejectedError,
    HarvestState,
    run_harvest_flow,
)


def _payload() -> dict:
    return {
        "title": "修 gate",
        "lane_hint": "procedural",
        "atoms": [{"id": "a1", "kind": "command", "text": "pytest -q"}],
        "how_steps": [{"anchor": "a1", "provenance": "session", "cmd": "pytest -q", "expect": "pass"}],
        "components": ["gate"],
    }


def test_rejected_skips_assembler_and_ledger(tmp_path: Path) -> None:
    called = False

    def assembler(payload: dict, *, harvest_run_id: str) -> str:
        nonlocal called
        called = True
        return "unused.md"

    outcome = run_harvest_flow(
        _payload(),
        harvest_run_id="run-1",
        raw_dir=tmp_path / "raw",
        ledger_path=tmp_path / "ledger.jsonl",
        confirm=lambda rendered: False,
        assembler=assembler,
    )

    assert outcome.state is HarvestState.REJECTED
    assert outcome.submitted is False
    assert called is False
    assert not (tmp_path / "ledger.jsonl").exists()


def test_aborted_skips_assembler_and_ledger(tmp_path: Path) -> None:
    called = False

    def assembler(payload: dict, *, harvest_run_id: str) -> str:
        nonlocal called
        called = True
        return "unused.md"

    outcome = run_harvest_flow(
        _payload(),
        harvest_run_id="run-2",
        raw_dir=tmp_path / "raw",
        ledger_path=tmp_path / "ledger.jsonl",
        confirm=lambda rendered: None,
        assembler=assembler,
    )

    assert outcome.state is HarvestState.ABORTED
    assert outcome.submitted is False
    assert called is False
    assert not (tmp_path / "ledger.jsonl").exists()


def test_submitted_appends_ledger_once(tmp_path: Path) -> None:
    outcome = run_harvest_flow(
        _payload(),
        harvest_run_id="run-3",
        raw_dir=tmp_path / "raw",
        ledger_path=tmp_path / "ledger.jsonl",
        confirm=lambda rendered: True,
        assembler=lambda payload, *, harvest_run_id: "ok.md",
    )

    assert outcome.state is HarvestState.SUBMITTED
    assert outcome.submitted is True

    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["loop_id"] == "akb2-harvest"
    assert event["node"] == "submit"
    assert event["event"] == "committed"
    assert event["harvest_run_id"] == "run-3"


def test_bad_payload_does_not_crash_before_assembler_rejects(tmp_path: Path) -> None:
    payload = {
        "title": "坏 payload",
        "lane_hint": "procedural",
        "atoms": 1,
        "how_steps": None,
        "components": 7,
    }

    def assembler(payload: dict, *, harvest_run_id: str) -> str:
        raise AssemblyRejectedError("schema invalid")

    outcome = run_harvest_flow(
        payload,
        harvest_run_id="run-4",
        raw_dir=tmp_path / "raw",
        ledger_path=tmp_path / "ledger.jsonl",
        confirm=lambda rendered: True,
        assembler=assembler,
    )

    assert outcome.state is HarvestState.PROPOSED
    assert outcome.submitted is False
    assert "atoms: 0 段 / how_steps: 0 条 / components: 0 个" in outcome.rendered
    assert not (tmp_path / "ledger.jsonl").exists()
