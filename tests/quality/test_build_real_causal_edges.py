"""test_build_real_causal_edges.py — P1b-3 alias/复数/跨句因果边测试。

覆盖:
    - alias/朴素单复数匹配:'executors' 匹配 'Executor Pool' 组件
    - 多词名长 token 匹配:'Executor'(token)匹配 'Executor Pool'
    - [N1] a 歧义弃用:variant 被 ≥2 组件认领 → 弃用
    - [N1] b domain stoplist:agent/context/model/... 单泛词不作 variant
    - 跨句(保守):单句 0 边时,相邻句对 + 定向连接词 + 同 §-anchor block → 边
    - 同 block 并列两句(无定向连接词)→ 不抽边
"""

from __future__ import annotations

from src.akb2.quality.distill import (
    _build_real_causal_edges,
    _component_match_variants,
)


def _comps(*names: str) -> list[dict[str, str]]:
    return [{"name": n, "role": "x"} for n in names]


# ---------------------------------------------------------------------------
# alias / 朴素单复数 / 多词名长 token
# ---------------------------------------------------------------------------


class TestAliasPluralMatching:
    def test_plural_form_matches_component(self):
        """'executors'(复数)匹配 'Executor Pool' 组件 → 边。"""
        raw = "## §A\n\nThe executors dispatch to the task queue for scheduling.\n"
        edges = _build_real_causal_edges(_comps("Executor Pool", "Task Queue"), raw)
        causal = [e for e in edges if e["edge_kind"] == "source_causal"]
        assert len(causal) == 1
        assert causal[0]["from"] == "Executor Pool"
        assert causal[0]["to"] == "Task Queue"

    def test_multiword_long_token_matches(self):
        """'Executor'(Executor Pool 的长 token 变体)匹配 → 边。"""
        raw = "## §A\n\nThe Executor dispatches to the Task Queue.\n"
        edges = _build_real_causal_edges(_comps("Executor Pool", "Task Queue"), raw)
        causal = [e for e in edges if e["edge_kind"] == "source_causal"]
        assert len(causal) == 1
        assert causal[0]["from"] == "Executor Pool"


# ---------------------------------------------------------------------------
# [N1] 双护栏
# ---------------------------------------------------------------------------


class TestDualGuardrail:
    def test_ambiguous_variant_discarded(self):
        """[N1] a:'planner' 被 Task Planner + Route Planner 双认领 → 弃用(variant_map 无 'planner')。"""
        vm = _component_match_variants(_comps("Task Planner", "Route Planner"))
        assert "planner" not in vm  # 歧义弃用
        assert "task planner" in vm  # 全名唯一保留
        assert "route planner" in vm

    def test_stoplist_single_word_not_variant(self):
        """[N1] b:'agent'(domain stoplist 单泛词)不作 variant;'agent runtime' 全名保留。"""
        vm = _component_match_variants(_comps("Agent Runtime"))
        assert "agent" not in vm  # stoplist 弃用
        assert "agent runtime" in vm  # 全名保留

    def test_stoplist_word_does_not_trigger_edge(self):
        """'agent' 单独出现不匹配 Agent Runtime → 0 组件 → 无 source_causal 边。"""
        raw = "## §A\n\nThe agent dispatches to the broker.\n"
        edges = _build_real_causal_edges(_comps("Agent Runtime", "Broker"), raw)
        # "agent" stoplist 不匹配;"broker" stoplist 不匹配 → 0 组件 → 无 causal 边
        assert not any(e["edge_kind"] == "source_causal" for e in edges)


# ---------------------------------------------------------------------------
# 跨句(保守)
# ---------------------------------------------------------------------------


class TestCrossSentence:
    def test_cross_sentence_edge_when_single_sentence_zero(self):
        """单句 0 边 + 相邻句对有定向连接词 + 同 §-anchor block → 跨句边。

        句1:'The Executor Pool is fast.'(无连接词,Executor Pool 出现)
        句2:'It dispatches to the Task Queue for scheduling.'(连接词 dispatches to,Task Queue 出现)
        单句:句1 无连接词;句2 仅 1 组件 → 0 边 → 跨句触发,pair 有连接词 + 2 组件 + 同 §A → 边。
        """
        raw = (
            "## §A\n\n"
            "The Executor Pool is fast. It dispatches to the Task Queue for scheduling.\n"
        )
        edges = _build_real_causal_edges(_comps("Executor Pool", "Task Queue"), raw)
        causal = [e for e in edges if e["edge_kind"] == "source_causal"]
        assert len(causal) == 1
        assert causal[0]["from"] == "Executor Pool"
        assert causal[0]["to"] == "Task Queue"
        assert "Executor Pool" in causal[0]["evidence"]  # evidence 写两句
        assert "Task Queue" in causal[0]["evidence"]
        assert causal[0]["evidence_anchor"]  # 有真实 anchor

    def test_parallel_sentences_no_connector_no_edge(self):
        """同 block 并列两句(无定向连接词)→ 不抽边。"""
        raw = (
            "## §A\n\n"
            "The Executor Pool is fast. The Task Queue is reliable.\n"
        )
        edges = _build_real_causal_edges(_comps("Executor Pool", "Task Queue"), raw)
        assert not any(e["edge_kind"] == "source_causal" for e in edges)

    def test_cross_sentence_different_anchor_blocks_no_edge(self):
        """跨句两句在不同 §-anchor block → 不连(防跨 block 误连)。"""
        raw = (
            "## §A\n\nThe Executor Pool is fast.\n\n"
            "## §B\n\nIt dispatches to the Task Queue for scheduling.\n"
        )
        edges = _build_real_causal_edges(_comps("Executor Pool", "Task Queue"), raw)
        # 单句 0 边;跨句 pair 有连接词 + 2 组件,但 §A vs §B 不同 block → 不连
        assert not any(e["edge_kind"] == "source_causal" for e in edges)

    def test_cross_sentence_cap_at_3(self):
        """跨句上限 3 条(有界预算)。每对(句1 Executor Pool{i} 无连接词 + 句2 dispatches to
        Task Queue{i})→ 单句每句≤1 组件 0 边,跨句每对产 1 边,5 对 → 截到 3。"""
        parts = []
        for i in range(5):
            parts.append(f"Executor Pool{i} is ready.")
            parts.append(f"It dispatches to Task Queue{i}.")
        raw = f"## §A\n\n" + " ".join(parts) + "\n"
        comps = [f"Executor Pool{i}" for i in range(5)] + [
            f"Task Queue{i}" for i in range(5)
        ]
        edges = _build_real_causal_edges(_comps(*comps), raw)
        causal = [e for e in edges if e["edge_kind"] == "source_causal"]
        assert len(causal) == 3  # 截到 3
