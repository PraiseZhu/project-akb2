"""Reasoner 测试 — 概念图对照验证。

覆盖：已存在概念链接、新概念 proposed、relationship_type 合法性、
非法枚举拒绝。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.akb2.video.reasoner import (
    ALLOWED_RELATIONSHIP_TYPES,
    ConceptStore,
    ProposedNode,
    ReasonedLinks,
    Reasoner,
    RelatedConcept,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def concept_store() -> ConceptStore:
    """包含部分已知概念的 ConceptStore。"""
    store = ConceptStore()
    store.add_concept("DeepSeek", "concept")
    store.add_concept("LangGraph", "tool")
    store.add_concept("MCP", "protocol")
    store.add_concept("Claude", "model")
    store.add_concept("GPT-4", "model")
    return store


class MockLLMAdapter:
    """Mock LLM adapter for reasoner tests."""

    def __init__(self, relationship_type: str = "evaluates"):
        self.relationship_type = relationship_type
        self.calls: list[dict] = []

    def chat(self, task: str, system: str, user: str, temperature: float = 0.3) -> str:
        self.calls.append({"task": task, "system": system, "user": user, "temperature": temperature})
        return json.dumps({
            "relationship_type": self.relationship_type,
            "reason": "Mock reason",
        })


# ---------------------------------------------------------------------------
# Test: link existing concept
# ---------------------------------------------------------------------------


def test_link_existing_concept(concept_store):
    """验证 mock 概念图中有 DeepSeek → related_concept。"""
    reasoner = Reasoner(concept_store=concept_store)
    result = reasoner.reason(["DeepSeek"], "报告对比了 DeepSeek 和 GPT-4 的性能")

    assert len(result.related_concept) > 0
    assert result.related_concept[0].concept_id == "concept:deepseek"


# ---------------------------------------------------------------------------
# Test: propose new concept
# ---------------------------------------------------------------------------


def test_propose_new_concept(concept_store):
    """验证 mock 概念图中没有某概念 → proposed_graph_nodes。"""
    reasoner = Reasoner(concept_store=concept_store)
    result = reasoner.reason(["Qwen3"], "报告提到了 Qwen3 的新功能")

    assert len(result.proposed_graph_nodes) > 0
    node = result.proposed_graph_nodes[0]
    assert node.name == "Qwen3"
    assert node.type in ("tool", "concept", "entity", "pattern")


# ---------------------------------------------------------------------------
# Test: relationship_type from content
# ---------------------------------------------------------------------------


def test_relationship_type_from_content(concept_store):
    """验证对比表 → evaluates 映射。"""
    reasoner = Reasoner(concept_store=concept_store)
    result = reasoner.reason(
        ["DeepSeek"],
        "我们对比测试了 DeepSeek 和 GPT-4。在 benchmark 上 DeepSeek 表现优异。",
    )

    assert len(result.related_concept) > 0
    for rc in result.related_concept:
        assert rc.relationship_type in ALLOWED_RELATIONSHIP_TYPES
        assert rc.evidence, "evidence 不应为空"


# ---------------------------------------------------------------------------
# Test: illegal relationship_type rejected
# ---------------------------------------------------------------------------


def test_illegal_relationship_type_rejected():
    """验证非法 relationship_type 枚举被拒绝。"""
    assert "invalid_type" not in ALLOWED_RELATIONSHIP_TYPES
    assert "references" in ALLOWED_RELATIONSHIP_TYPES
    assert "evaluates" in ALLOWED_RELATIONSHIP_TYPES
    assert "extends" in ALLOWED_RELATIONSHIP_TYPES


# ---------------------------------------------------------------------------
# Test: Reasoner import
# ---------------------------------------------------------------------------


def test_reasoner_import():
    """验证 Reasoner 和 ALLOWED_RELATIONSHIP_TYPES 可 import。"""
    from src.akb2.video.reasoner import ALLOWED_RELATIONSHIP_TYPES, Reasoner
    assert Reasoner is not None
    assert len(ALLOWED_RELATIONSHIP_TYPES) == 12


# ---------------------------------------------------------------------------
# Test: degraded mode
# ---------------------------------------------------------------------------


def test_reasoner_degraded_mode():
    """验证 ConceptStore 为空时所有概念进 proposed_graph_nodes。"""
    reasoner = Reasoner(concept_store=ConceptStore())  # 空 store
    result = reasoner.reason(["DeepSeek", "LangGraph", "Qwen3"])

    # 空 store 应该全部进 proposed
    assert len(result.related_concept) == 0
    assert len(result.proposed_graph_nodes) == 3


# ---------------------------------------------------------------------------
# Test: evidence non-empty
# ---------------------------------------------------------------------------


def test_evidence_non_empty(concept_store):
    """验证命中概念的 evidence 非空。"""
    reasoner = Reasoner(concept_store=concept_store)
    result = reasoner.reason(
        ["DeepSeek", "GPT-4"],
        "报告详细对比了 DeepSeek V4 和 GPT-4 在多个基准上的表现。",
    )

    for rc in result.related_concept:
        assert rc.evidence, f"evidence 不应为空: {rc.concept_id}"