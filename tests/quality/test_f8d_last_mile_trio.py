"""test_f8d_last_mile_trio.py — F8-D 最后一公里三件套测试。

覆盖:
    1. 命名归一:边端点 from/to 改写为组件表 canonical name(消 'claudemd_files vs
       CLAUDE.md files' 类命名分裂)
    2. 机制叙述段落:eligible 边确定性生成连贯段落,每句含 [raw §X] 且锚∈声明集
    3. TLDR 去重:≥80% 相似 claim 去重;要素覆盖(核心机制 + 组件全景 + how/边界)
"""

from __future__ import annotations

import re

from src.akb2.quality.distill import (
    _build_tldr_component_sentence,
    _build_tldr_mechanism_sentence,
    _dedup_claims_for_tldr,
    _normalize_edge_endpoints_to_canonical,
    _render_mechanism_narrative,
    _text_similarity,
)
from src.akb2.quality.raw_anchors import declared_anchor_set

_RAW = """\
§A_CONTENT_START

The Captain agent holds the system-level goal.

§B_INTRODUCTION

The Executor Pool dispatches tasks to the Task Graph for scheduling.
"""


class TestF8DItem1NormalizeEdgeEndpoints:
    def _data(self) -> dict:
        return {
            "mechanism_components": [
                {"name": "Captain", "role": "coord", "aliases": ["captain"]},
                {"name": "Executor Pool", "role": "exec", "aliases": ["executor_pool"]},
                {"name": "Task Graph", "role": "state"},
            ],
            "causal_edges": [
                # lowercase + alias 形式 → 应改写为 canonical
                {"from": "captain", "to": "executor_pool", "relation": "dispatches",
                 "edge_kind": "source_causal", "evidence": "x", "evidence_anchor": "§A"},
                {"from": "Executor Pool", "to": "task graph", "relation": "feeds",
                 "edge_kind": "source_causal", "evidence": "y", "evidence_anchor": "§B"},
            ],
            "graph_edges": [],
            "claims": [],
            "how_steps": [],
        }

    def test_lowercase_alias_endpoints_rewritten_to_canonical(self):
        """'captain'/'executor_pool'(alias/lowercase)→ 'Captain'/'Executor Pool'(canonical)。"""
        data = self._data()
        rewritten = _normalize_edge_endpoints_to_canonical(data)
        assert rewritten >= 2  # 至少 2 个端点被改写
        names = {c["name"] for c in data["mechanism_components"]}
        for e in data["causal_edges"]:
            assert e["from"] in names, f"from {e['from']} 不在组件名集合"
            assert e["to"] in names, f"to {e['to']} 不在组件名集合"
        # 具体值
        assert data["causal_edges"][0]["from"] == "Captain"
        assert data["causal_edges"][0]["to"] == "Executor Pool"
        assert data["causal_edges"][1]["to"] == "Task Graph"

    def test_already_canonical_not_rewritten(self):
        """已是 canonical name 的端点不计 rewritten(幂等)。"""
        data = {
            "mechanism_components": [{"name": "Captain", "role": "x"}],
            "causal_edges": [
                {"from": "Captain", "to": "Captain", "relation": "r",
                 "edge_kind": "source_causal", "evidence": "x", "evidence_anchor": "§A"},
            ],
            "graph_edges": [],
            "claims": [],
            "how_steps": [],
        }
        assert _normalize_edge_endpoints_to_canonical(data) == 0
        assert data["causal_edges"][0]["from"] == "Captain"

    def test_no_components_noop(self):
        data = {"mechanism_components": [], "causal_edges": [], "graph_edges": []}
        assert _normalize_edge_endpoints_to_canonical(data) == 0


class TestF8DItem2MechanismNarrative:
    def _data(self) -> dict:
        return {
            "mechanism_components": [
                {"name": "Captain", "role": "x"},
                {"name": "Executor Pool", "role": "y"},
                {"name": "Task Graph", "role": "z"},
            ],
            "causal_edges": [
                {"from": "Captain", "to": "Executor Pool", "relation": "dispatches",
                 "edge_kind": "source_causal", "evidence": "Captain dispatches to executors",
                 "evidence_anchor": "§A"},
                {"from": "Executor Pool", "to": "Task Graph", "relation": "feeds",
                 "edge_kind": "source_causal", "evidence": "Pool feeds the task graph",
                 "evidence_anchor": "§B"},
                # inferred 边不计入叙述
                {"from": "Captain", "to": "Task Graph", "relation": "r",
                 "edge_kind": "inferred_from_structure"},
            ],
            "claims": [],
            "how_steps": [],
        }

    def test_narrative_each_sentence_has_anchor_in_declared(self):
        """叙述段落每句含 [raw §X] 且锚∈声明集。"""
        narrative = _render_mechanism_narrative(self._data(), _RAW)
        assert narrative  # 非空
        assert "机制叙述" in narrative
        decl = declared_anchor_set(_RAW)
        # 提取所有 [raw §X] 锚
        anchors = re.findall(r"\[raw (§[A-Z_]+)\]", narrative)
        assert anchors, "叙述段落无 [raw §X] 锚"
        for a in anchors:
            assert a in decl, f"锚 {a} 不在声明集"

    def test_narrative_uses_causal_phrasing(self):
        """叙述用 relation 动词 + 证据句式(非 'A raw_causal B' 机器味,F10-C 改)。"""
        narrative = _render_mechanism_narrative(self._data(), _RAW)
        assert "证据" in narrative
        assert "raw_causal" not in narrative  # F10-C: 不再输出 raw_causal 机器形态
        # 含具体因果动词(dispatches/feeds)
        assert any(v in narrative for v in ("dispatches", "feeds", "collects", "spawn")), (
            f"叙述无具体因果动词: {narrative}"
        )

    def test_narrative_sorted_by_anchor_position(self):
        """叙述按 anchor 在 raw 中出现顺序排序(§A 在 §B 前)。"""
        narrative = _render_mechanism_narrative(self._data(), _RAW)
        pos_a = narrative.find("§A")
        pos_b = narrative.find("§B")
        assert 0 <= pos_a < pos_b, "§A 应在 §B 前(按 raw 顺序)"

    def test_narrative_excludes_inferred_edges(self):
        """inferred_from_structure 边不计入叙述(无 evidence/anchor)。"""
        narrative = _render_mechanism_narrative(self._data(), _RAW)
        # Captain→Task Graph 是 inferred,不应作为独立因果句
        # 每条 eligible 边产 1 个 '（证据' 句;inferred 不产。F10-C 后模板无 '通过',
        # 改计 '（证据' 出现次数(== eligible 边数)。
        assert narrative.count("（证据") == 2  # 只 2 条 eligible

    def test_narrative_no_eligible_returns_empty(self):
        """无 eligible 边 → 空串(不硬凑)。"""
        data = {
            "mechanism_components": [{"name": "A", "role": "x"}],
            "causal_edges": [],
            "claims": [],
            "how_steps": [],
        }
        assert _render_mechanism_narrative(data, _RAW) == ""

    def test_narrative_anchor_not_in_declared_skipped(self):
        """evidence_anchor 不在声明集 → 该边跳过(不造假 anchor)。"""
        data = {
            "mechanism_components": [{"name": "A", "role": "x"}, {"name": "B", "role": "y"}],
            "causal_edges": [
                {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
                 "evidence": "ev", "evidence_anchor": "§FAKE_ANCHOR"},
            ],
            "claims": [],
            "how_steps": [],
        }
        assert _render_mechanism_narrative(data, _RAW) == ""


class TestF8DItem3TLDRDedup:
    def test_jaccard_similarity(self):
        """overlap coefficient 相似度基本计算(子串包含给 1.0)。"""
        assert _text_similarity("same text here", "same text here") == 1.0
        assert _text_similarity("a b c", "x y z") == 0.0
        s = _text_similarity("the captain holds the goal", "the captain holds the system goal")
        assert s >= 0.8  # 高度相似(短串全含于长串 → overlap 1.0)
        # 子串包含:短串全含于长串 → overlap 1.0(Jaccard 会给 0.7 漏判)
        s2 = _text_similarity(
            "The Captain agent holds the system-level goal.",
            "The Captain agent holds the system-level goal and maintains tasks.",
        )
        assert s2 >= 0.8

    def test_dedup_removes_near_duplicate(self):
        """≥80% 相似的 claim 去重,保留首个。"""
        claims = [
            {"text": "The Captain agent holds the system-level goal.", "anchor": "§A"},
            {"text": "The Captain agent holds the system-level goal and maintains tasks.", "anchor": "§A"},
            {"text": "Executor Pool dispatches tasks.", "anchor": "§B"},
        ]
        deduped = _dedup_claims_for_tldr(claims)
        # 第 1、2 条高度相似 → 第 2 条去重
        assert len(deduped) == 2
        assert deduped[0]["text"] == claims[0]["text"]
        assert deduped[1]["text"] == claims[2]["text"]

    def test_dedup_keeps_distinct(self):
        """不同论点全保留。"""
        claims = [
            {"text": "Captain coordinates.", "anchor": "§A"},
            {"text": "Executor Pool runs tasks.", "anchor": "§B"},
            {"text": "Task Graph tracks state.", "anchor": "§B"},
        ]
        deduped = _dedup_claims_for_tldr(claims)
        assert len(deduped) == 3

    def test_dedup_empty(self):
        assert _dedup_claims_for_tldr([]) == []


class TestF8DItem3TLDRElementCoverage:
    def _data(self) -> dict:
        return {
            "mechanism_components": [
                {"name": "Captain", "role": "x", "evidence_anchor": "§A"},
                {"name": "Executor Pool", "role": "y", "evidence_anchor": "§B"},
                {"name": "Task Graph", "role": "z", "evidence_anchor": "§B"},
            ],
            "causal_edges": [
                {"from": "Captain", "to": "Executor Pool", "relation": "dispatches",
                 "edge_kind": "source_causal", "evidence": "ev",
                 "evidence_anchor": "§A"},
            ],
            "claims": [],
            "how_steps": [],
        }

    def test_mechanism_sentence_has_anchor_in_declared(self):
        """核心机制句含 [raw §X] 且锚∈声明集。"""
        sent = _build_tldr_mechanism_sentence(self._data(), _RAW)
        assert sent
        assert "[raw §" in sent
        decl = declared_anchor_set(_RAW)
        anchors = re.findall(r"\[raw (§[A-Z_]+)\]", sent)
        for a in anchors:
            assert a in decl

    def test_component_sentence_has_count_and_names(self):
        """组件全景句含组件数 + 关键名 + 锚∈声明集。"""
        sent = _build_tldr_component_sentence(self._data(), _RAW)
        assert sent
        assert "机制组件" in sent
        assert "Captain" in sent
        assert "[raw §" in sent
        decl = declared_anchor_set(_RAW)
        anchors = re.findall(r"\[raw (§[A-Z_]+)\]", sent)
        for a in anchors:
            assert a in decl

    def test_no_eligible_mechanism_sentence_empty(self):
        data = {
            "mechanism_components": [{"name": "A", "role": "x"}],
            "causal_edges": [],
            "claims": [],
            "how_steps": [],
        }
        assert _build_tldr_mechanism_sentence(data, _RAW) == ""

    def test_no_components_component_sentence_empty(self):
        data = {"mechanism_components": [], "causal_edges": [], "claims": [], "how_steps": []}
        assert _build_tldr_component_sentence(data, _RAW) == ""
