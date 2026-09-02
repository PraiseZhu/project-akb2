"""Stage 4: bounded deterministic fixpoint, no LLM, no residency."""

from __future__ import annotations

from src.akb2.quality.checks_structural import Defect
from src.akb2.quality.maker2.fixpoint import (
    MAKER2_FIXPOINT_IDLE_STOP,
    MAKER2_FIXPOINT_MAX_ROUNDS,
    run_fixpoint,
)
from src.akb2.quality.repair import REPAIR_IDLE_EARLY_STOP


def _must_cover(location: str = "正文") -> Defect:
    return Defect(
        dimension="SG-must-cover",
        location=location,
        defect="缺 must-cover 块",
        suggestion="补 must-cover",
    )


def test_fixpoint_budget_is_two_rounds() -> None:
    assert MAKER2_FIXPOINT_MAX_ROUNDS == 2
    assert MAKER2_FIXPOINT_IDLE_STOP == 1
    assert REPAIR_IDLE_EARLY_STOP >= 1


def test_no_llm_call_during_fixpoint(monkeypatch) -> None:
    calls: list[str] = []

    def _forbidden(*_args, **_kwargs):
        calls.append("route_llm")
        raise AssertionError("fixpoint must not call route_llm")

    monkeypatch.setattr("src.akb2.quality.llm_router.route_llm", _forbidden)
    monkeypatch.setattr(
        "src.akb2.quality.maker2.fixpoint.run_candidate_self_gate",
        lambda *_args, **_kwargs: [],
    )
    result = run_fixpoint("# ok\n")
    assert result.success is True
    assert result.rounds == 0
    assert calls == []


def test_idle_stop_when_identity_does_not_shrink(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.akb2.quality.maker2.fixpoint.run_candidate_self_gate",
        lambda *_args, **_kwargs: [_must_cover()],
    )
    monkeypatch.setattr(
        "src.akb2.quality.maker2.fixpoint._apply_deterministic_repairs",
        lambda text, defects, raw_text=None: (text, []),
    )
    result = run_fixpoint("same")
    assert result.aborted is True
    assert result.reason == "idle_stop"
    assert result.rounds == 1
    assert result.idle_stops >= 1


def test_max_two_rounds_then_abort(monkeypatch) -> None:
    n = {"round": 0}

    def _gate(*_args, **_kwargs):
        n["round"] += 1
        if n["round"] == 1:
            return [_must_cover("a"), _must_cover("b")]
        return [_must_cover("a")]

    monkeypatch.setattr(
        "src.akb2.quality.maker2.fixpoint.run_candidate_self_gate",
        _gate,
    )
    monkeypatch.setattr(
        "src.akb2.quality.maker2.fixpoint._apply_deterministic_repairs",
        lambda text, defects, raw_text=None: (text + ".", [{"section": "must-cover"}]),
    )
    result = run_fixpoint("seed")
    assert result.rounds == MAKER2_FIXPOINT_MAX_ROUNDS
    assert result.aborted is True
    assert result.reason == "max_rounds"
    assert result.text.count(".") == 2


def test_must_cover_repaired_in_one_round(monkeypatch) -> None:
    state = {"calls": 0}

    def _gate(*_args, **_kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            return [_must_cover()]
        return []

    monkeypatch.setattr(
        "src.akb2.quality.maker2.fixpoint.run_candidate_self_gate",
        _gate,
    )
    monkeypatch.setattr(
        "src.akb2.quality.maker2.fixpoint._apply_deterministic_repairs",
        lambda text, defects, raw_text=None: (text + "\ncovered", [{"section": "must-cover"}]),
    )
    result = run_fixpoint("seed")
    assert result.success is True
    assert result.rounds == 1
    assert result.aborted is False
    assert "covered" in result.text


def test_fixpoint_has_no_residency_state(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "src.akb2.quality.maker2.fixpoint.run_candidate_self_gate",
        lambda *_args, **_kwargs: [],
    )
    run_fixpoint("clean")
    banned = {
        "repair-pool",
        "residency",
        "needs_repair",
        "fixpoint-state.json",
    }
    leftover = {path.name for path in tmp_path.iterdir()}
    assert not (leftover & banned)
