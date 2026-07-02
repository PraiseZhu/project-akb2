"""test_scan_plain_title_components.py — F-B fallback 普通标题组件抽取测试。

覆盖:
    - 正:title-case 命名短语标题(Tool Registration/Loop Structure)→ 组件
    - 正:含机制实体关键词标题(AGENTS.md 含 agent)→ 组件
    - 负:Introduction/Conclusion/FAQ/Overview/Summary 泛节名 → 拒
    - 负:祈使句/疑问句标题(How it works/Choose where...)→ 拒
    - 负:含收缩词(isn't/it's)或小写助动词(is/are)的句子标题 → 拒
    - 集成:title 型 raw → ≥3 组件 + P1b-3 ≥1 eligible 边
"""

from __future__ import annotations

from src.akb2.quality.distill import (
    _build_real_causal_edges,
    _is_plain_title_component,
    _scan_plain_title_components,
    _scan_real_mechanism_components,
)
from src.akb2.quality.raw_anchors import declared_anchor_set


class TestIsPlainTitleComponent:
    def test_title_case_naming_phrase_accepted(self):
        assert _is_plain_title_component("Tool Registration")
        assert _is_plain_title_component("Loop Structure")
        assert _is_plain_title_component("Storage location")  # 首词大写,命名短语

    def test_entity_keyword_accepted(self):
        """含机制实体关键词(AGENTS.md 含 agent)→ 接受(无论 title-case)。"""
        assert _is_plain_title_component("AGENTS.md")  # 'agent' keyword
        assert _is_plain_title_component("Step counter module")  # step/module

    def test_generic_section_names_rejected(self):
        """Introduction/Conclusion/FAQ/Overview/Summary 泛节名 → 拒。"""
        for name in ("Introduction", "Conclusion", "FAQ", "Overview", "Summary",
                     "Background", "References", "Appendix"):
            assert not _is_plain_title_component(name), f"{name} 应被拒"

    def test_imperative_sentence_rejected(self):
        """祈使句/疑问句标题(How/When/Choose/Write 开头)→ 拒。"""
        for name in ("How it works", "When to add to CLAUDE.md",
                     "Choose where to put files", "Write effective instructions",
                     "Manage CLAUDE.md for large teams", "Troubleshoot memory issues"):
            assert not _is_plain_title_component(name), f"{name!r} 应被拒(句子)"

    def test_contraction_rejected(self):
        """含收缩词(isn't/it's,含 curly ')→ 拒。"""
        assert not _is_plain_title_component("Claude isn't following my CLAUDE.md")
        assert not _is_plain_title_component("It's not working")

    def test_lowercase_aux_verb_rejected(self):
        """含小写助动词(is/are/seem)的句子 → 拒。"""
        assert not _is_plain_title_component("My CLAUDE.md is too large")
        assert not _is_plain_title_component("Instructions seem lost after compact")

    def test_lowercase_first_word_rejected(self):
        """首词小写且无实体关键词(foo bar)→ 拒(非 title-case 命名短语)。"""
        assert not _is_plain_title_component("foo bar budget")
        # 注:'maxSteps Budget' 因含 'step' 实体关键词被接受(entity-keyword 路径优先)


class TestScanPlainTitleComponents:
    def test_title_type_raw_extracts_components(self):
        """title-case 标题型 raw → 抽出组件(去重保序,跳过 §-锚/编号/表格)。"""
        raw = (
            "## 锚点目录\n\n- §A: x\n\n---\n\n"
            "## Tool Registration\n\nreg body.\n\n"
            "## Tool Invocation Flow\n\nflow body.\n\n"
            "### Loop Structure\n\nloop body.\n\n"
            "## Introduction\n\nintro.\n\n"
            "## How it works\n\nhow.\n"
        )
        names = _scan_plain_title_components(raw)
        assert "Tool Registration" in names
        assert "Tool Invocation Flow" in names
        assert "Loop Structure" in names
        assert "Introduction" not in names  # 泛节名拒
        assert "How it works" not in names  # 疑问句拒
        assert "锚点目录" not in names  # 目录标题拒

    def test_skips_anchor_and_numbered_headings(self):
        """§-锚标题与编号标题不重复扫(别处处理)。"""
        raw = "## §A Captain\n\n### 1. Executor\n\n## Real Plain Title\n"
        names = _scan_plain_title_components(raw)
        assert names == ["Real Plain Title"]


class TestIntegrationWithCausalEdges:
    def test_title_raw_yields_components_and_eligible_edge(self):
        """集成:title 型 raw → ≥3 组件 + P1b-3 ≥1 eligible edge(evidence_anchor ∈ 声明集)。"""
        raw = (
            "## 锚点目录\n\n- §A: START\n- §B: FLOW\n\n---\n\n"
            "§A_CONTENT_START\n\n"
            "## Tool Registration\n\nThe Tool Registration feeds into the Tool Invocation Flow.\n\n"
            "## Tool Invocation Flow\n\nThe flow dispatches to the Loop Structure.\n\n"
            "## Loop Structure\n\nloop body.\n"
        )
        comps = _scan_real_mechanism_components(raw)
        assert len(comps) >= 3
        decl = declared_anchor_set(raw)
        edges = _build_real_causal_edges(comps, raw)
        causal = [e for e in edges if e["edge_kind"] == "source_causal"]
        eligible = [e for e in causal if e.get("evidence_anchor") in decl]
        assert len(eligible) >= 1, f"应 ≥1 eligible 边,实际 {len(eligible)}"
