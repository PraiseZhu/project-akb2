"""test_f10c_relation_verb_component_id.py — F10-C relation 具体动词 + component_id 补 测试。

grader H3 抱怨:'raw_causal 不完整英文短语表述不自然'、组件表空 component_id 行。
F10-C:_build_real_causal_edges 的 connector 命中时写实际因果动词(dispatches/collects/
feeds/triggers...)进 edge.relation(非 raw_causal);叙述用自然句式;组件表空 component_id
补 slug 化生成。
"""

from __future__ import annotations

import re

from src.akb2.quality.distill import (
    _build_body,
    _build_mechanism_evidence_section,
    _build_real_causal_edges,
    _build_tldr_mechanism_sentence,
    _extract_causal_verb,
    _render_mechanism_narrative,
    build_deterministic_extract_result,
)

_RAW = """\
§A_CONTENT_START

## 1. Captain

The Captain dispatches tasks to the Executor Pool for scheduling.

## 2. Executor Pool

The Executor Pool feeds results to the Task Graph.

## 3. Task Graph

The Task Graph tracks state.
"""

_COMPS = [
    {"name": "Captain", "role": "coord"},
    {"name": "Executor Pool", "role": "exec"},
    {"name": "Task Graph", "role": "state"},
]


class TestF10CExtractCausalVerb:
    def test_dispatches_extracted(self):
        assert _extract_causal_verb("The Captain dispatches tasks to the pool") == "dispatches"

    def test_feeds_extracted(self):
        assert _extract_causal_verb("The pool feeds results to the graph") == "feeds"

    def test_triggers_extracted(self):
        assert _extract_causal_verb("The event triggers the action") == "triggers"

    def test_no_connector_returns_raw_causal_fallback(self):
        """无因果连接词→返 raw_causal 兜底(不应达,caller 已 search 过)。"""
        assert _extract_causal_verb("plain sentence no connector") == "raw_causal"


class TestF10CRelationRealVerb:
    def test_edges_have_real_verb_not_raw_causal(self):
        """_build_real_causal_edges 产的 source_causal 边 relation 是具体动词,非 raw_causal。"""
        edges = _build_real_causal_edges(_COMPS, _RAW)
        source_causal = [e for e in edges if e.get("edge_kind") == "source_causal"]
        assert source_causal, "应有 source_causal 边"
        for e in source_causal:
            assert e["relation"] != "raw_causal", (
                f"relation 仍是 raw_causal: {e}"
            )
            assert e["relation"] in ("dispatches", "feeds", "triggers", "collects", "spawn",
                                     "spawns", "leads to", "invokes", "schedules"), (
                f"relation 非预期动词: {e['relation']}"
            )

    def test_inferred_edge_relation_unchanged(self):
        """inferred_from_structure 边 relation 仍是 structural_sequence(非 source_causal)。"""
        edges = _build_real_causal_edges(_COMPS, _RAW)
        inferred = [e for e in edges if e.get("edge_kind") == "inferred_from_structure"]
        for e in inferred:
            assert e["relation"] == "structural_sequence"


class TestF10CNarrativeNoRawCausal:
    def test_narrative_uses_real_verb(self):
        """叙述段落用具体动词,无 'raw_causal' 字样。"""
        data = {
            "mechanism_components": _COMPS,
            "causal_edges": _build_real_causal_edges(_COMPS, _RAW),
            "claims": [{"text": "x", "anchor": "§A", "provenance": "source_grounded",
                        "evidence_gap": "未提供"}],
            "how_steps": [],
        }
        narr = _render_mechanism_narrative(data, _RAW)
        assert narr
        assert "raw_causal" not in narr, f"叙述仍含 raw_causal: {narr}"
        # 含具体动词
        assert any(v in narr for v in ("dispatches", "feeds", "collects")), (
            f"叙述无具体因果动词: {narr}"
        )
        # 不再用 '通过 ... 导致' 机器句式(英文动词)
        assert "通过" not in narr or "导致" not in narr.split("（证据")[0]

    def test_tldr_mechanism_sentence_no_raw_causal(self):
        """TLDR 机制句无 'raw_causal' 字样。"""
        data = {
            "mechanism_components": _COMPS,
            "causal_edges": _build_real_causal_edges(_COMPS, _RAW),
            "claims": [{"text": "x", "anchor": "§A", "provenance": "source_grounded",
                        "evidence_gap": "未提供"}],
            "how_steps": [],
        }
        sent = _build_tldr_mechanism_sentence(data, _RAW)
        assert sent
        assert "raw_causal" not in sent, f"TLDR 机制句含 raw_causal: {sent}"

    def test_full_body_tldr_no_raw_causal(self):
        """完整 body 的 TLDR 节无 'raw_causal' 字样。"""
        res = build_deterministic_extract_result(_RAW, content_lane="concept")
        assert res.success
        body = _build_body(
            res.data, "test.md", None, must_cover=[], raw_content=_RAW, content_lane="concept"
        )
        tldr_match = re.search(r"## TLDR\s+(.+?)(?:\n## )", body, re.S)
        assert tldr_match
        tldr = tldr_match.group(1)
        assert "raw_causal" not in tldr, f"TLDR 含 raw_causal: {tldr}"


class TestF10CComponentIdNoEmpty:
    def test_mechanism_table_no_empty_component_id(self):
        """组件表 component_id 列无空值(空→slug 化组件名补)。"""
        data = {
            "mechanism_components": [
                {"name": "Captain", "role": "x"},  # 无 component_id
                {"name": "Executor Pool", "role": "y", "component_id": "exec_pool"},
                {"name": "Task Graph", "role": "z"},  # 无 component_id
            ],
            "causal_edges": [],
        }
        section = _build_mechanism_evidence_section(data)
        # 解析 component_id 列(每行第一个 cell)
        rows = re.findall(r"^\| ([^|]+) \|", section, re.M)
        # 跳过表头分隔行
        ids = [r.strip() for r in rows if r.strip() and not r.startswith("---")]
        # 第一个非空(Captain → slug 'captain')
        assert all(cid for cid in ids), f"有空 component_id: {ids}"
        assert "captain" in ids  # slug 化补生成
        assert "task_graph" in ids  # slug 化补生成

    def test_fallback_components_have_component_id(self):
        """fallback 路径组件 component_id 非空(_build_deterministic_extract_data 已设 +
        F10-C 渲染侧兜底)。"""
        res = build_deterministic_extract_result(_RAW, content_lane="concept")
        assert res.success
        section = _build_mechanism_evidence_section(res.data)
        rows = re.findall(r"^\| ([^|]+) \|", section, re.M)
        ids = [r.strip() for r in rows if r.strip() and not r.startswith("---")]
        assert all(cid for cid in ids), f"fallback 组件有空 component_id: {ids}"
