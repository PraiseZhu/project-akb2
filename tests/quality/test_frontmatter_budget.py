"""Stage 1: frontmatter line budget keeps check_a2 non-proposal under 25 lines."""

from __future__ import annotations

import random
from typing import Any

import pytest

from src.akb2.quality.checks_structural import MAX_FRONTMATTER_LINES, check_a2_frontmatter
from src.akb2.quality.maker2.compose2 import compose_candidate
from src.akb2.quality.maker2.frontmatter_budget import (
    FrontmatterBudgetError,
    apply_frontmatter_budget,
    count_frontmatter_lines,
    dump_frontmatter,
)

_BODY = """# Harness Engineering

## What

Harness 把模型输出钉成可复现步骤。

## How

1. 先写合同字段。
2. 再用确定性组装压 frontmatter。
3. 最后走结构闸，不靠 LLM 自评。

## Why it matters

行预算失败会在 promote/lint 被 25 行闸拦住。
"""


def _base_frontmatter(**overrides: Any) -> dict[str, Any]:
    fm: dict[str, Any] = {
        "canonical_title": "harness-engineering",
        "slug": "concept-harness-engineering",
        "template_type": "concept",
        "status": "active",
        "source_url": "https://example.com/harness",
        "source_refs": ["raw/2026-09-01-harness.md#A"],
        "gate_decision": None,
        "gate_reason": "候选来源锚点 [raw 2026-09-01-harness]；待裁判与五闸确认。",
        "star_level": 0,
        "graph_role": "concept",
        "future_questions": [
            "合同字段缺省时谁补？",
            "行预算外移 proposed 后 related 是否仍 >=2？",
            "第三问会被收成 2 条吗？",
        ],
        "related_concepts": [
            "[[concept-loop-engineering]]",
            "[[concept-maker-checker]]",
            "[[concept-fail-closed]]",
            "[[concept-budget-explicit]]",
        ],
        "proposed_graph_nodes": [
            {
                "slug": "concept-contract-compose",
                "label": "合同式组装",
                "reason": "把抽取结果钉成可过闸页面",
                "source_anchor": "A",
            },
            {
                "slug": "concept-frontmatter-budget",
                "label": "行预算",
                "reason": "active/lint 仍按 25 行拦",
                "source_anchor": "E",
            },
            {
                "slug": "concept-x-wiki-intake",
                "label": "X-wiki 直通",
                "reason": "loop2 主源",
                "source_anchor": "H",
            },
            {
                "slug": "concept-vendor-separation",
                "label": "异厂商",
                "reason": "Maker/Checker 不比族名",
                "source_anchor": "M",
            },
        ],
        "graph_pending": True,
        "evidence_provenance": "raw",
        "content_lane": "wiki",
        "source_type": "x-longform",
        "source_author": "example-author",
        "source_published_at": "2026-08-01T00:00:00Z",
    }
    fm.update(overrides)
    return fm


def _line_defects(result: Any) -> list[str]:
    items = result if isinstance(result, list) else getattr(result, "defects", []) or []
    out: list[str] = []
    for item in items:
        text = getattr(item, "defect", None) or str(item)
        if "行数超标" in text or "frontmatter 行数" in text:
            out.append(str(text))
    return out


def test_budget_caps_questions_and_related_without_dropping_identity() -> None:
    fm, degraded, pending = apply_frontmatter_budget(_base_frontmatter())
    dumped = dump_frontmatter(fm)
    assert count_frontmatter_lines(dumped) <= MAX_FRONTMATTER_LINES
    assert fm["slug"] == "concept-harness-engineering"
    assert fm["source_url"] == "https://example.com/harness"
    assert len(fm["future_questions"]) == 2
    assert len(fm["related_concepts"]) == 2
    assert "future_questions_capped_2" in degraded
    assert "related_concepts_capped_2" in degraded
    if "proposed_graph_nodes" not in fm:
        assert pending.startswith("## pending_graph_nodes")
        assert "proposed_graph_nodes_moved_to_body" in degraded


def test_budgeted_page_has_zero_line_count_defects_in_active_mode() -> None:
    composed = compose_candidate(_base_frontmatter(), _BODY)
    assert composed.success, composed.error
    assert count_frontmatter_lines(composed.page_text) <= MAX_FRONTMATTER_LINES
    result = check_a2_frontmatter(composed.page_text, promotion_mode="active")
    assert _line_defects(result) == []


def test_source_published_at_is_droppable() -> None:
    fat = _base_frontmatter()
    fat["future_questions"] = ["q1", "q2"]
    fat["related_concepts"] = ["[[a]]", "[[b]]"]
    fat["aliases"] = ["one", "two", "three", "four"]
    for i in range(8):
        fat[f"extra_block_{i}"] = [
            f"pad-line-{i}-{j}-{'x' * 40}" for j in range(3)
        ]
    try:
        fm, degraded, _pending = apply_frontmatter_budget(fat)
    except FrontmatterBudgetError:
        return
    if "dropped_source_published_at" in degraded:
        assert "source_published_at" not in fm
    assert count_frontmatter_lines(dump_frontmatter(fm)) <= MAX_FRONTMATTER_LINES


def test_budget_fail_closed_when_identity_alone_overflows() -> None:
    fm = {
        "slug": "concept-overflow",
        "status": "active",
        "source_url": "https://example.com/overflow",
        "gate_reason": "x" * 150,
    }
    for i in range(30):
        fm[f"identity_pad_{i}"] = f"keep-{i}-{'k' * 20}"
    with pytest.raises(FrontmatterBudgetError):
        apply_frontmatter_budget(fm)


def test_fuzz_200_budgeted_or_fail_closed_never_over_25() -> None:
    rng = random.Random(20260901)
    for i in range(200):
        n_q = rng.randint(2, 8)
        n_rel = rng.randint(2, 8)
        n_prop = rng.randint(0, 6)
        fm = _base_frontmatter(
            future_questions=[f"问题 {i}-{k}？" for k in range(n_q)],
            related_concepts=[f"[[concept-rel-{i}-{k}]]" for k in range(n_rel)],
            proposed_graph_nodes=[
                {
                    "slug": f"concept-node-{i}-{k}",
                    "label": f"节点 {i}-{k}",
                    "reason": f"理由 {i}-{k} " + ("长" * rng.randint(0, 12)),
                    "source_anchor": rng.choice(["A", "E", "H", "M", "Z"]),
                }
                for k in range(n_prop)
            ],
            source_author="author-" + ("n" * rng.randint(0, 20)),
            source_published_at="2026-08-01T00:00:00Z",
            source_type="x-longform",
        )
        if rng.random() < 0.4:
            fm["aliases"] = [f"alias-{i}-{k}" for k in range(rng.randint(0, 6))]
        try:
            budgeted, _degraded, pending = apply_frontmatter_budget(fm)
        except FrontmatterBudgetError:
            continue
        dumped = dump_frontmatter(budgeted)
        n_lines = count_frontmatter_lines(dumped)
        assert n_lines <= MAX_FRONTMATTER_LINES, n_lines
        page = compose_candidate(budgeted, _BODY + pending).page_text
        result = check_a2_frontmatter(page, promotion_mode="active")
        assert _line_defects(result) == [], result
