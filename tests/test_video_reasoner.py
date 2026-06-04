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


# ---------------------------------------------------------------------------
# Test: _determine_relationship 规则分支
# ---------------------------------------------------------------------------


def test_relationship_extends(concept_store):
    """上下文含 '基于/扩展' + 概念名 → extends。"""
    reasoner = Reasoner(concept_store=concept_store)
    result = reasoner.reason(["DeepSeek"], "DeepSeek 基于 Transformer 架构扩展而来")
    assert result.related_concept[0].relationship_type == "extends"


def test_relationship_implements(concept_store):
    """上下文含 '实现' → implements。"""
    reasoner = Reasoner(concept_store=concept_store)
    result = reasoner.reason(["LangGraph"], "本文用 LangGraph 实现了一个 agent 编排系统")
    assert result.related_concept[0].relationship_type == "implements"


def test_relationship_alternatives(concept_store):
    """上下文含 '替代' → alternatives_to。"""
    reasoner = Reasoner(concept_store=concept_store)
    result = reasoner.reason(["GPT-4"], "新模型旨在替代 GPT-4 的市场地位")
    assert result.related_concept[0].relationship_type == "alternatives_to"


# ---------------------------------------------------------------------------
# Test: LLM 辅助分类路径（规则未命中时）
# ---------------------------------------------------------------------------

# 规则关键词全不命中的中性上下文，确保走到 LLM 分类分支
_NEUTRAL_CTX = "这份报告聚焦 DeepSeek 的整体设计哲学与社区生态"


def test_llm_classify_used_when_rules_miss(concept_store):
    """规则未命中 → 调 LLM 分类，返回合法类型被采用。"""
    llm = MockLLMAdapter(relationship_type="integrates_with")
    reasoner = Reasoner(concept_store=concept_store, llm_adapter=llm)
    result = reasoner.reason(["DeepSeek"], _NEUTRAL_CTX)

    assert result.related_concept[0].relationship_type == "integrates_with"
    assert len(llm.calls) == 1
    assert llm.calls[0]["task"] == "classify"
    assert llm.calls[0]["temperature"] == 0.1


def test_llm_classify_invalid_json_falls_back_references(concept_store):
    """LLM 返回非 JSON → extract 失败 → 回落 references。"""

    class BadJSONLLM:
        def chat(self, task, system, user, temperature=0.3):
            return "not json at all"

    reasoner = Reasoner(concept_store=concept_store, llm_adapter=BadJSONLLM())
    result = reasoner.reason(["DeepSeek"], _NEUTRAL_CTX)
    assert result.related_concept[0].relationship_type == "references"


def test_llm_classify_illegal_type_falls_back_references(concept_store):
    """LLM 返回非法 relationship_type → 回落 references。"""
    llm = MockLLMAdapter(relationship_type="totally_invalid_type")
    reasoner = Reasoner(concept_store=concept_store, llm_adapter=llm)
    result = reasoner.reason(["DeepSeek"], _NEUTRAL_CTX)
    assert result.related_concept[0].relationship_type == "references"


def test_llm_classify_exception_caught(concept_store):
    """LLM.chat 抛异常 → _determine_relationship 捕获 → references。"""

    class RaisingLLM:
        def chat(self, task, system, user, temperature=0.3):
            raise RuntimeError("llm down")

    reasoner = Reasoner(concept_store=concept_store, llm_adapter=RaisingLLM())
    result = reasoner.reason(["DeepSeek"], _NEUTRAL_CTX)
    assert result.related_concept[0].relationship_type == "references"


# ---------------------------------------------------------------------------
# Test: _infer_node_type 推断分支
# ---------------------------------------------------------------------------


def test_infer_node_type_tool():
    """含工具关键词（cli）→ tool。"""
    reasoner = Reasoner(concept_store=ConceptStore())
    result = reasoner.reason(["SomeCLI"])
    assert result.proposed_graph_nodes[0].type == "tool"


def test_infer_node_type_entity():
    """含实体关键词（inc）→ entity。"""
    reasoner = Reasoner(concept_store=ConceptStore())
    result = reasoner.reason(["OpenAI Inc"])
    assert result.proposed_graph_nodes[0].type == "entity"


def test_infer_node_type_pattern():
    """含模式关键词（pattern）→ pattern。"""
    reasoner = Reasoner(concept_store=ConceptStore())
    result = reasoner.reason(["Pipeline Pattern"])
    assert result.proposed_graph_nodes[0].type == "pattern"


def test_infer_node_type_default_concept():
    """无任何关键词 → 默认 concept。"""
    reasoner = Reasoner(concept_store=ConceptStore())
    result = reasoner.reason(["Quantum"])
    assert result.proposed_graph_nodes[0].type == "concept"


# ---------------------------------------------------------------------------
# Test: ConceptStore 模糊匹配 + reason 边界
# ---------------------------------------------------------------------------


def test_concept_store_fuzzy_match():
    """查询字符串包含已存概念名 → 模糊命中。"""
    store = ConceptStore()
    store.add_concept("DeepSeek", "concept")
    match = store.lookup("DeepSeek V4 Pro")
    assert match is not None
    assert match["id"] == "concept:deepseek"


def test_reason_skips_empty_concepts(concept_store):
    """空白概念名被跳过，只处理有效概念。"""
    reasoner = Reasoner(concept_store=concept_store)
    result = reasoner.reason(["", "   ", "DeepSeek"], "报告聚焦 DeepSeek")
    total = len(result.related_concept) + len(result.proposed_graph_nodes)
    assert total == 1


def test_build_evidence_fallback_when_concept_absent(concept_store):
    """概念命中但上下文无含该名的行 → evidence 走兜底文案。"""
    reasoner = Reasoner(concept_store=concept_store)
    result = reasoner.reason(["DeepSeek"], "一段与概念名完全无关的上下文文本")
    assert result.related_concept[0].evidence.startswith("报告中将")