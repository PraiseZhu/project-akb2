"""loop2 bounded deterministic fixpoint. Zero LLM, zero residency pool."""

from __future__ import annotations

from dataclasses import dataclass, field

from akb2.quality.checks_structural import Defect, run_candidate_self_gate
from akb2.quality.repair import (
    _apply_deterministic_repairs,
    _defect_identity,
    _is_deterministic_repair_defect,
)

MAKER2_FIXPOINT_MAX_ROUNDS = 2
MAKER2_FIXPOINT_IDLE_STOP = 1


@dataclass
class FixpointResult:
    text: str
    success: bool
    rounds: int
    idle_stops: int
    remaining_defects: list[Defect] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)
    aborted: bool = False
    reason: str = ""


def run_fixpoint(
    text: str,
    *,
    raw_content: str | None = None,
    content_lane: str = "",
    promotion_mode: str = "proposal",
    pending_queue: list[str] | None = None,
    known_concepts: list[str] | None = None,
) -> FixpointResult:
    """At most 2 deterministic repair rounds. Stop on idle or empty det set."""
    current = text
    actions: list[dict[str, str]] = []
    prev_identity: set[str] | None = None
    idle = 0

    for round_i in range(1, MAKER2_FIXPOINT_MAX_ROUNDS + 1):
        defects = run_candidate_self_gate(
            current,
            raw_content=raw_content,
            pending_queue=pending_queue,
            known_concepts=known_concepts,
            content_lane=content_lane,
            promotion_mode=promotion_mode,
        )
        det = [item for item in defects if _is_deterministic_repair_defect(item)]
        if not det:
            return FixpointResult(
                text=current,
                success=True,
                rounds=round_i - 1,
                idle_stops=idle,
                remaining_defects=defects,
                actions=actions,
                reason="no_deterministic_defects",
            )

        identity = _defect_identity(det)
        if prev_identity is not None and not identity < prev_identity:
            idle += 1
            if idle >= MAKER2_FIXPOINT_IDLE_STOP:
                return FixpointResult(
                    text=current,
                    success=False,
                    rounds=round_i,
                    idle_stops=idle,
                    remaining_defects=defects,
                    actions=actions,
                    aborted=True,
                    reason="idle_stop",
                )

        repaired, round_actions = _apply_deterministic_repairs(
            current, det, raw_text=raw_content
        )
        actions.extend(round_actions)
        if repaired == current:
            idle += 1
            if idle >= MAKER2_FIXPOINT_IDLE_STOP:
                return FixpointResult(
                    text=current,
                    success=False,
                    rounds=round_i,
                    idle_stops=idle,
                    remaining_defects=defects,
                    actions=actions,
                    aborted=True,
                    reason="idle_stop",
                )
        current = repaired
        prev_identity = identity

    final_defects = run_candidate_self_gate(
        current,
        raw_content=raw_content,
        pending_queue=pending_queue,
        known_concepts=known_concepts,
        content_lane=content_lane,
        promotion_mode=promotion_mode,
    )
    det_left = [item for item in final_defects if _is_deterministic_repair_defect(item)]
    return FixpointResult(
        text=current,
        success=not det_left,
        rounds=MAKER2_FIXPOINT_MAX_ROUNDS,
        idle_stops=idle,
        remaining_defects=final_defects,
        actions=actions,
        aborted=bool(det_left),
        reason="" if not det_left else "max_rounds",
    )
