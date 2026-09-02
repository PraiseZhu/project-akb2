"""loop2 extract contract: deterministic fill-in, then integrity floor.

Does not retry the LLM (EXTRACT_QUALITY_RETRY_CONCEPT stays 0). Does not
fabricate components or edges that are not grounded in raw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from akb2.quality.distill import (
    CONCEPT_MIN_MECHANISM_COMPONENTS,
    EXTRACT_QUALITY_RETRY_CONCEPT,
    _backfill_component_evidence,
    _enrich_zero_eligible_with_deterministic,
)
from akb2.quality.integrity_floor import (
    count_eligible_edges,
    evaluate_integrity_floor,
)
from akb2.quality.raw_anchors import (
    canonicalize_anchor,
    declared_anchor_set,
    resolve_anchor_ref,
)

# 合同层不重开 LLM 抽取。具名常量只是把 distill 的 0 钉死在 maker2 边界。
assert EXTRACT_QUALITY_RETRY_CONCEPT == 0


@dataclass
class ExtractContractResult:
    extract_data: dict[str, Any]
    floor_passed: bool
    floor_reason: str
    enriched: bool
    components_added: int = 0
    edges_added: int = 0
    backfilled: int = 0
    fabricated: bool = False
    unanchored: list[str] = field(default_factory=list)


def _copy_extract(extract_data: dict[str, Any]) -> dict[str, Any]:
    copied = dict(extract_data)
    for key in (
        "claims",
        "argumentation",
        "mechanism_components",
        "causal_edges",
        "graph_edges",
        "how_steps",
    ):
        value = copied.get(key)
        if isinstance(value, list):
            copied[key] = [dict(item) if isinstance(item, dict) else item for item in value]
    return copied


def _collect_evidence_anchors(extract_data: dict[str, Any]) -> list[str]:
    anchors: list[str] = []
    for edge in extract_data.get("causal_edges") or []:
        if isinstance(edge, dict):
            value = str(edge.get("evidence_anchor", "")).strip()
            if value:
                anchors.append(value)
    for component in extract_data.get("mechanism_components") or []:
        if isinstance(component, dict):
            value = str(component.get("evidence_anchor", "")).strip()
            if value:
                anchors.append(value)
    return anchors


def _drop_floating_interpretations(extract_data: dict[str, Any]) -> int:
    """丢掉缺 traces_to 的 akb_interpretation，不新开 LLM、不造锚。"""
    grounded = {
        str(item.get("anchor", "")).strip()
        for item in (extract_data.get("claims") or [])
        if isinstance(item, dict)
        and item.get("provenance") == "source_grounded"
        and str(item.get("anchor", "")).strip()
    }

    def _keep(item: Any) -> bool:
        if not isinstance(item, dict):
            return True
        if item.get("provenance") != "akb_interpretation":
            return True
        traces = item.get("traces_to")
        trace_set = (
            {str(anchor).strip() for anchor in traces if str(anchor).strip()}
            if isinstance(traces, list)
            else set()
        )
        return bool(trace_set & grounded) if grounded else bool(trace_set)

    dropped = 0
    for key in ("claims", "argumentation"):
        items = extract_data.get(key)
        if not isinstance(items, list):
            continue
        kept = []
        for item in items:
            if _keep(item):
                kept.append(item)
            else:
                dropped += 1
        extract_data[key] = kept
    return dropped


def _resolve_short_evidence_anchors(
    extract_data: dict[str, Any], declared: set[str]
) -> None:
    """LLM 常写 §B，raw 声明 §B_INTRODUCTION。合同层用唯一前缀解析，不造锚。"""
    if not declared:
        return

    def _resolve(ref: str) -> str:
        canon = canonicalize_anchor(str(ref).strip())
        resolved = resolve_anchor_ref(canon, declared)
        return resolved if resolved is not None else canon

    for key in ("causal_edges", "graph_edges"):
        edges = extract_data.get(key)
        if not isinstance(edges, list):
            continue
        for edge in edges:
            if isinstance(edge, dict) and edge.get("evidence_anchor"):
                edge["evidence_anchor"] = _resolve(str(edge["evidence_anchor"]))
    for component in extract_data.get("mechanism_components") or []:
        if isinstance(component, dict) and component.get("evidence_anchor"):
            component["evidence_anchor"] = _resolve(
                str(component["evidence_anchor"])
            )


def fulfill_extract_contract(
    extract_data: dict[str, Any],
    raw_content: str,
    *,
    content_lane: str = "concept",
) -> ExtractContractResult:
    """Deterministic enrichment then floor. Never fabricates off-raw nodes."""
    data = _copy_extract(extract_data)
    declared = declared_anchor_set(raw_content)
    lane = str(content_lane or "").strip().lower() or "concept"

    eligible_before = count_eligible_edges(data, raw_anchors=declared)["eligible"]
    n_components = len(
        [
            item
            for item in (data.get("mechanism_components") or [])
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
    )
    thin = lane == "concept" and (
        n_components < CONCEPT_MIN_MECHANISM_COMPONENTS or eligible_before < 1
    )

    enrich_diag: dict[str, Any] = {
        "enriched": False,
        "components_added": 0,
        "edges_added": 0,
    }
    if thin:
        enrich_diag = _enrich_zero_eligible_with_deterministic(data, raw_content)

    backfill_diag = _backfill_component_evidence(data, raw_content)
    _resolve_short_evidence_anchors(data, declared)
    _drop_floating_interpretations(data)
    floor = evaluate_integrity_floor(data, lane, raw_anchors=declared)

    used_anchors = _collect_evidence_anchors(data)
    unanchored = [
        anchor
        for anchor in used_anchors
        if canonicalize_anchor(anchor) not in declared
    ]
    fabricated = bool(unanchored)

    return ExtractContractResult(
        extract_data=data,
        floor_passed=floor.passed and not fabricated,
        floor_reason=floor.reason if not floor.passed else (
            f"unanchored evidence: {unanchored}" if fabricated else ""
        ),
        enriched=bool(enrich_diag.get("enriched")),
        components_added=int(enrich_diag.get("components_added") or 0),
        edges_added=int(enrich_diag.get("edges_added") or 0),
        backfilled=int(backfill_diag.get("backfilled") or 0),
        fabricated=fabricated,
        unanchored=unanchored,
    )
