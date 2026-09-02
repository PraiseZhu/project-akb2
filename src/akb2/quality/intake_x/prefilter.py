"""loop2 X 预筛：grok-4.6, illegal JSON 整批丢弃, borderline 只 medium 复检一次。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.akb2.quality.llm_router import (
    build_schema_prompt_messages,
    route_llm,
    validate_response_against_schema,
)

logger = logging.getLogger(__name__)

X_PREFILTER_MODEL = "grok-4.6"
X_PREFILTER_EFFORT = "low"
X_PREFILTER_BORDERLINE_EFFORT = "medium"
X_PREFILTER_BATCH_SIZE = 20
X_PREFILTER_MAX_BATCHES_PER_RUN = 8
X_PREFILTER_TIMEOUT_S = 90.0
X_PREFILTER_MIN_CONFIDENCE = 0.6
X_PREFILTER_BORDERLINE_BAND = (0.45, 0.6)
X_PREFILTER_BORDERLINE_MAX_RECHECK = 1

PREFILTER_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["keep", "lane_hint", "topic", "why", "confidence"],
    "properties": {
        "keep": {"type": "boolean"},
        "lane_hint": {"type": "string"},
        "topic": {"type": "string"},
        "why": {"type": "string"},
        "confidence": {"type": "number"},
    },
}

PREFILTER_BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": PREFILTER_ITEM_SCHEMA,
        }
    },
}

RouteLLM = Callable[..., Any]


@dataclass
class PrefilterDecision:
    keep: bool
    lane_hint: str = ""
    topic: str = ""
    why: str = ""
    confidence: float = 0.0


@dataclass
class PrefilterBatchResult:
    kept: list[tuple[dict[str, Any], PrefilterDecision]] = field(default_factory=list)
    dropped: list[tuple[dict[str, Any], PrefilterDecision]] = field(default_factory=list)
    dropped_batch: bool = False
    reason: str = ""
    rechecked: int = 0


def _in_borderline(confidence: float) -> bool:
    lo, hi = X_PREFILTER_BORDERLINE_BAND
    return lo <= float(confidence) < hi


def _parse_batch_payload(content: Any, expected: int) -> list[dict[str, Any]] | None:
    payload = content
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None
    try:
        validate_response_against_schema(payload, PREFILTER_BATCH_SCHEMA)
    except Exception:
        return None
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or len(items) != expected:
        return None
    return items


def _item_to_decision(item: dict[str, Any]) -> PrefilterDecision:
    return PrefilterDecision(
        keep=bool(item.get("keep")),
        lane_hint=str(item.get("lane_hint") or ""),
        topic=str(item.get("topic") or ""),
        why=str(item.get("why") or ""),
        confidence=float(item.get("confidence") or 0.0),
    )


def _summarize_item(item: dict[str, Any]) -> dict[str, str]:
    text = str(item.get("text") or item.get("articleTitle") or item.get("title") or "")
    url = str(item.get("url") or "")
    tweet_id = str(item.get("id") or "")
    return {
        "id": tweet_id,
        "url": url[:240],
        "text": text[:400],
    }


def _call_prefilter(
    items: list[dict[str, Any]],
    *,
    effort: str,
    route_llm_fn: RouteLLM,
    loop_id: str,
) -> list[dict[str, Any]] | None:
    summaries = [_summarize_item(item) for item in items]
    messages = [
        {
            "role": "system",
            "content": (
                "你是 AKB2 loop2 X 预筛器。只保留可入库的长文/外链知识。"
                "对每条输入按顺序输出 keep/lane_hint/topic/why/confidence。"
                "lane_hint 只能是 concept/tutorial/pattern/design/reject。"
                "confidence 是 0-1 的小数。非法 JSON 会导致整批丢弃。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"items": summaries}, ensure_ascii=False),
        },
    ]
    outbound = build_schema_prompt_messages(messages, PREFILTER_BATCH_SCHEMA)
    try:
        response = route_llm_fn(
            X_PREFILTER_MODEL,
            outbound,
            response_schema=PREFILTER_BATCH_SCHEMA,
            reasoning_effort=effort,
            timeout_s=X_PREFILTER_TIMEOUT_S,
            loop_id=loop_id,
        )
    except TypeError:
        response = route_llm_fn(
            X_PREFILTER_MODEL,
            outbound,
            response_schema=PREFILTER_BATCH_SCHEMA,
            reasoning_effort=effort,
            timeout_s=X_PREFILTER_TIMEOUT_S,
        )
    except Exception as exc:
        logger.warning("prefilter call failed: %s", exc)
        return None
    return _parse_batch_payload(getattr(response, "content", response), len(items))


def prefilter_batch(
    items: list[dict[str, Any]],
    *,
    route_llm_fn: RouteLLM | None = None,
    loop_id: str = "loop2",
) -> PrefilterBatchResult:
    """筛一批条目。非法 JSON / 长度不对 / 复检后仍 borderline → 整批丢弃。"""
    if not items:
        return PrefilterBatchResult()
    if route_llm_fn is None:
        route_llm_fn = route_llm

    parsed = _call_prefilter(
        items, effort=X_PREFILTER_EFFORT, route_llm_fn=route_llm_fn, loop_id=loop_id
    )
    if parsed is None:
        return PrefilterBatchResult(
            dropped_batch=True,
            reason="illegal_json",
        )

    decisions = [_item_to_decision(row) for row in parsed]
    borderline_idx = [
        i for i, decision in enumerate(decisions) if _in_borderline(decision.confidence)
    ]
    rechecked = 0
    if borderline_idx:
        subset = [items[i] for i in borderline_idx]
        reparsed = _call_prefilter(
            subset,
            effort=X_PREFILTER_BORDERLINE_EFFORT,
            route_llm_fn=route_llm_fn,
            loop_id=loop_id,
        )
        rechecked = 1
        if reparsed is None:
            return PrefilterBatchResult(
                dropped_batch=True,
                reason="borderline_recheck_illegal_json",
                rechecked=rechecked,
            )
        for offset, row in zip(borderline_idx, reparsed, strict=True):
            decisions[offset] = _item_to_decision(row)
        if any(_in_borderline(decisions[i].confidence) for i in borderline_idx):
            return PrefilterBatchResult(
                dropped_batch=True,
                reason="borderline_unresolved",
                rechecked=rechecked,
            )

    kept: list[tuple[dict[str, Any], PrefilterDecision]] = []
    dropped: list[tuple[dict[str, Any], PrefilterDecision]] = []
    for item, decision in zip(items, decisions, strict=True):
        if decision.keep and decision.confidence >= X_PREFILTER_MIN_CONFIDENCE:
            kept.append((item, decision))
        else:
            dropped.append((item, decision))
    return PrefilterBatchResult(
        kept=kept,
        dropped=dropped,
        dropped_batch=False,
        rechecked=rechecked,
    )


def iter_prefilter_batches(
    items: list[dict[str, Any]],
    *,
    route_llm_fn: RouteLLM | None = None,
    loop_id: str = "loop2",
    batch_size: int = X_PREFILTER_BATCH_SIZE,
    max_batches: int = X_PREFILTER_MAX_BATCHES_PER_RUN,
) -> list[PrefilterBatchResult]:
    results: list[PrefilterBatchResult] = []
    for index in range(min(max_batches, (len(items) + batch_size - 1) // batch_size or 0)):
        start = index * batch_size
        chunk = items[start : start + batch_size]
        if not chunk:
            break
        results.append(
            prefilter_batch(chunk, route_llm_fn=route_llm_fn, loop_id=loop_id)
        )
    return results
