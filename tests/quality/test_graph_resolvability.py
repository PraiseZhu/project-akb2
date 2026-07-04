"""test_graph_resolvability.py — step-3 SC-3e 图谱 A 层 resolve 验收。

A3-8：check_a4_graph_edges 增 known_concepts/pending_queue 形参；run_structural_checks
把它们喂 check_a4；边 resolve 到真实节点或 pending ≥3 共指才计图谱分；假边/未 resolve 报缺陷。
  - _resolve_graph_node 纯代码确定性（不调 LLM）——monkeypatch router raise 后仍正确。
  - import allowlist：_resolve_graph_node 所在模块不 import llm_router。
  - 两处 self-gate（compose 第一闸 + runner 第二闸）都传 known/pending 实参（非 None）。
"""

from __future__ import annotations

from unittest.mock import patch

from src.akb2.quality.checks_structural import (
    _resolve_graph_node,
    check_a4_graph_edges,
    check_a5_wikilinks,
    run_candidate_self_gate,
    run_structural_checks,
)

# ----------------------------- _resolve_graph_node ----------------------------- #


class TestResolveGraphNode:
    def test_known_concept_hit(self):
        assert _resolve_graph_node("概念A", ["概念a", "概念b"], None) is True

    def test_pending_coref_at_threshold(self):
        """pending ≥ GRAPH_PENDING_COREF_MIN 个共指 → resolve（待立条目累积到阈值视作真实）。"""
        # GRAPH_PENDING_COREF_MIN 默认 3
        assert _resolve_graph_node("概念X", None, ["概念x", "概念x", "概念x"]) is True

    def test_pending_below_threshold(self):
        assert _resolve_graph_node("概念X", None, ["概念x", "概念x"]) is False

    def test_unknown_not_in_known_or_pending(self):
        assert _resolve_graph_node("假概念", ["概念a"], ["概念x", "概念x"]) is False

    def test_empty_target(self):
        assert _resolve_graph_node("", ["a"], None) is False

    def test_case_insensitive(self):
        assert _resolve_graph_node("ConceptA", ["concepta"], None) is True


# ----------------------------- 纯代码不调 LLM ----------------------------- #


class TestPureCodeNoLlm:
    def test_resolve_still_correct_when_router_raises(self, monkeypatch):
        """monkeypatch llm_router.route_llm raise → _resolve_graph_node 仍正确（纯代码硬证）。"""
        import src.akb2.quality.llm_router as router

        def _raise(**kw):
            raise AssertionError("图谱 resolve 不应调 LLM（A3-8 纯代码）")

        monkeypatch.setattr(router, "route_llm", _raise)
        # 即便 router 入口 raise，resolve 仍确定性返回
        assert _resolve_graph_node("概念A", ["概念a"], None) is True
        assert _resolve_graph_node("假概念", ["概念a"], None) is False

    def test_checks_structural_not_import_llm_router(self):
        """import allowlist：_resolve_graph_node 所在模块不 import llm_router。"""
        import src.akb2.quality.checks_structural as mod

        # 模块级 globals 不应含 llm_router / route_llm（防 LLM 猜测渗入确定性 resolve）
        mod_globals = dir(mod)
        assert "llm_router" not in mod_globals, "checks_structural 不应 import llm_router"
        assert "route_llm" not in mod_globals, "checks_structural 不应 import route_llm"


# ----------------------------- check_a4_graph_edges ----------------------------- #


_GRAPH_CANDIDATE = """---
title: 图谱 resolve 测试
---

## 图谱关系

| target | relation | strength | provenance | evidence |
|--------|----------|----------|------------|----------|
| 真实概念A | relates_to | strong | source_grounded | [raw §A] 原文证据这里 |
| 假概念Z | relates_to | strong | source_grounded | [raw §B] 假边无对应节点 |
"""


class TestCheckA4GraphEdges:
    def test_concept_real_node_edge_no_defect(self):
        """concept：边 target 命中 known_concepts → 无 resolve 缺陷。"""
        defects = check_a4_graph_edges(
            _GRAPH_CANDIDATE,
            known_concepts=["真实概念a"],
            pending_queue=None,
            content_lane="concept",
        )
        resolve_defects = [d for d in defects if "resolve" in getattr(d, "dimension", "").lower()
                           or "resolve" in getattr(d, "defect", "").lower()]
        # 真实概念A 命中 known → 不报 resolve 缺陷（假概念Z 会报）
        # 只验真实概念A 那条不产生 resolve 缺陷
        assert not any("真实概念A" in getattr(d, "defect", "") for d in resolve_defects)

    def test_concept_fake_edge_reported(self):
        """concept：假概念Z 不在 known/pending → 报 resolve 缺陷。"""
        defects = check_a4_graph_edges(
            _GRAPH_CANDIDATE,
            known_concepts=["真实概念a"],
            pending_queue=None,
            content_lane="concept",
        )
        # 假概念Z 未 resolve → 至少一个缺陷提及它
        fake_defects = [d for d in defects if "假概念Z" in getattr(d, "defect", "")
                        or "假概念z" in getattr(d, "defect", "").lower()]
        assert len(fake_defects) >= 1

    def test_procedural_no_resolve(self):
        """procedural 不启用 resolve → 假边也不报 resolve 缺陷（零回归）。"""
        defects = check_a4_graph_edges(
            _GRAPH_CANDIDATE,
            known_concepts=None,
            pending_queue=None,
            content_lane="procedural",
        )
        resolve_defects = [d for d in defects if "resolve" in getattr(d, "dimension", "").lower()
                           or "resolve" in getattr(d, "defect", "").lower()]
        assert resolve_defects == []

    def test_pending_coref_resolves(self):
        """concept：假概念Z 在 pending ≥3 共指 → resolve（不报缺陷）。"""
        defects = check_a4_graph_edges(
            _GRAPH_CANDIDATE,
            known_concepts=None,
            pending_queue=["假概念z", "假概念z", "假概念z"],
            content_lane="concept",
        )
        resolve_defects = [d for d in defects if "假概念Z" in getattr(d, "defect", "")
                           or "假概念z" in getattr(d, "defect", "").lower()]
        assert resolve_defects == []


# ----------------------------- self-gate 透传 known/pending ----------------------------- #


class TestSelfGateThreading:
    def test_run_structural_checks_threads_known_pending_to_check_a4(self):
        """run_structural_checks 把 known/pending 喂给 check_a4（A3-8 接线）。"""
        with patch("src.akb2.quality.checks_structural.check_a4_graph_edges") as mock_a4:
            mock_a4.return_value = []
            run_structural_checks(
                "some text",
                raw_content="raw",
                pending_queue=["概念x"],
                known_concepts=["概念a"],
                content_lane="concept",
            )
            assert mock_a4.call_count == 1
            _, kwargs = mock_a4.call_args
            assert kwargs.get("known_concepts") == ["概念a"]
            assert kwargs.get("pending_queue") == ["概念x"]
            assert kwargs.get("content_lane") == "concept"

    def test_run_candidate_self_gate_threads_known_pending(self):
        """run_candidate_self_gate 把 known/pending 透传到 run_structural_checks→check_a4。"""
        with patch("src.akb2.quality.checks_structural.check_a4_graph_edges") as mock_a4:
            mock_a4.return_value = []
            # run_candidate_self_gate 内部调 run_structural_checks，known/pending 须透传
            run_candidate_self_gate(
                "some text",
                raw_content="raw",
                pending_queue=["概念x"],
                known_concepts=["概念a"],
                content_lane="concept",
            )
            assert mock_a4.call_count == 1
            _, kwargs = mock_a4.call_args
            assert kwargs.get("known_concepts") == ["概念a"]
            assert kwargs.get("pending_queue") == ["概念x"]


# ----------------------------- SC-2a 前缀归一化 ----------------------------- #


class TestPrefixNormalization:
    """SC-2a：concept-<name> 与裸名 <name> 互为等价（vault 概念页规范为带前缀）。

    实测失败谱：生成器输出裸名 context-engineering / 源 slug，而 vault 概念页
    命名为 concept-context-engineering，精确匹配下裸名被判死链。归一化后等价。
    """

    def test_bare_target_resolves_prefixed_known(self):
        """裸名 target 命中 concept- 前缀 known（SC-2a 验收主例）。"""
        assert _resolve_graph_node(
            "context-engineering", ["concept-context-engineering"], None
        ) is True

    def test_prefixed_target_resolves_bare_known(self):
        """反向：concept- 前缀 target 命中裸名 known。"""
        assert _resolve_graph_node(
            "concept-context-engineering", ["context-engineering"], None
        ) is True

    def test_bare_target_prefixed_pending_coref(self):
        """pending 以 concept- 前缀累积共指，裸名 target 也能 resolve。"""
        assert _resolve_graph_node(
            "loop-engineering", None, ["concept-loop-engineering"] * 3
        ) is True

    def test_distinct_slugs_still_unresolved(self):
        """归一化不产生误报：target 与 known 是不同概念 → 仍不 resolve。"""
        assert _resolve_graph_node(
            "loop-engineering", ["concept-context-engineering"], None
        ) is False

    def test_a5_bare_wikilink_against_prefixed_known_no_defect(self):
        """A5：[[context-engineering]] 在 known=[concept-context-engineering] 下不报死链。"""
        text = "正文 [[context-engineering]] 结尾"
        defects = check_a5_wikilinks(
            text, pending_queue=None, known_concepts=["concept-context-engineering"]
        )
        assert defects == []

    def test_a5_prefixed_wikilink_against_bare_known_no_defect(self):
        """A5 反向：[[concept-context-engineering]] 在 known=[context-engineering] 下不报。"""
        text = "正文 [[concept-context-engineering]] 结尾"
        defects = check_a5_wikilinks(
            text, pending_queue=None, known_concepts=["context-engineering"]
        )
        assert defects == []

    def test_a5_empty_allowed_still_skips(self):
        """SC-2a 不破坏空索引跳过（无 known/pending → 不报死链）。"""
        text = "正文 [[whatever]] 结尾"
        defects = check_a5_wikilinks(text, pending_queue=None, known_concepts=None)
        assert defects == []


# ----------------------------- SC-2b-b2 渲染前过滤悬空边 ----------------------------- #


class TestFilterUnresolvableEdges:
    """SC-2b-b2：compose 渲染前过滤 target 不在 known∪sources∪pending 的图谱边/概念。"""

    def test_drops_invented_slug_keeps_known_and_source(self):
        """loop-engineering（既非概念也非源）被 drop；concept-x 与源 slug 保留。"""
        from src.akb2.quality.distill import _filter_resolvable_graph_edges

        edges = [
            {"concept": "concept-context-engineering", "relation_type": "related"},
            {"concept": "loop-engineering", "relation_type": "related"},
            {"target": "anthropic-effective-context-engineering", "relation_type": "related"},
        ]
        concepts = ["concept-context-engineering", "loop-engineering", "mcp-spec"]
        # known 含 concept + source（b1 折叠后 loader 喂两者）
        known = [
            "concept-context-engineering",
            "anthropic-effective-context-engineering",
            "mcp-spec",
        ]
        fe, fc, _dropped, _de = _filter_resolvable_graph_edges(edges, concepts, known, None)
        kept_targets = [e.get("concept") or e.get("target") for e in fe]
        assert "loop-engineering" not in kept_targets, "发明 slug 须被过滤"
        assert "concept-context-engineering" in kept_targets
        assert "anthropic-effective-context-engineering" in kept_targets, (
            "源 slug 须保留（b1 折叠后 known 含源）"
        )
        assert "loop-engineering" not in fc
        assert "concept-context-engineering" in fc
        assert "mcp-spec" in fc

    def test_none_known_no_filter_for_compat(self):
        """known/pending 均 None 时不过滤（兼容 _build_body 旧调用，默认不传）。"""
        from src.akb2.quality.distill import _filter_resolvable_graph_edges

        edges = [{"concept": "whatever-invented"}]
        fe, fc, _dropped, _de = _filter_resolvable_graph_edges(edges, ["whatever-invented"], None, None)
        assert fe == edges, "known/pending 均 None 时须原样返回（兼容）"
        assert fc == ["whatever-invented"]

    def test_bare_concept_target_resolves_via_prefix_norm(self):
        """SC-2a 协同：裸名 target 经前缀归一化命中 concept- 前缀 known。"""
        from src.akb2.quality.distill import _filter_resolvable_graph_edges

        edges = [{"concept": "context-engineering", "relation_type": "related"}]
        fe, _, _dropped, _de = _filter_resolvable_graph_edges(
            edges, [], ["concept-context-engineering"], None
        )
        assert fe == edges, "裸名 target 须经 SC-2a 前缀归一化保留"


# ----------------------------- SC-2 占位符协议 ----------------------------- #


_GRAPH_PARAGRAPH_CANDIDATE = """---
title: 空图谱段落测试
---

## TLDR

概念正文。

## 图谱关系

图谱边按 evidence 的 provenance 判定。

（本候选暂无可证实的图谱边——所有引用概念在 resolve 阶段未命中 known/pending。）
"""

_GRAPH_PLACEHOLDER_TABLE_CANDIDATE = """---
title: 占位符测试
---

## 图谱关系

| target | relation | strength | provenance | evidence |
|--------|----------|----------|------------|----------|
| （无相关概念） | — | weak | akb_interpretation | 无可证实图谱边。 |
"""


class TestSC2PlaceholderProtocol:
    """SC-2（worker round-4）：空图谱不出占位符表格行（A4 会把占位当 edge 拒）。

    compose 全过滤空时输出普通段落（非 table），A4 无 table 行可解析→不拒。
    若占位符 table 行出现（compose 回退），A4 仍拒（锁防回退）。
    """

    def test_paragraph_graph_no_a4_resolve_defect(self):
        """空图谱出段落 → A4 无 table 行 → 不报 resolve 缺陷（SC-2 主验收）。"""
        defects = check_a4_graph_edges(
            _GRAPH_PARAGRAPH_CANDIDATE,
            known_concepts=["concept-x"],
            pending_queue=None,
            content_lane="concept",
        )
        assert not any(
            "resolve" in getattr(d, "dimension", "").lower() for d in defects
        ), "段落图谱不应触发 A4-图谱resolve（SC-2）"

    def test_placeholder_table_row_still_rejected(self):
        """回归锁：占位符 `| （无相关概念） |` 出现在 table → A4 仍拒（防 compose 回退）。"""
        defects = check_a4_graph_edges(
            _GRAPH_PLACEHOLDER_TABLE_CANDIDATE,
            known_concepts=["concept-x"],
            pending_queue=None,
            content_lane="concept",
        )
        assert any(
            "无相关概念" in getattr(d, "defect", "") for d in defects
        ), "占位符 table 行应被 A4 拒（锁防 compose 回退到占位符）"

    def test_filter_returns_dropped_targets(self):
        """SC-2：filter 返回 dropped_targets（治静默失败——grader/ledger 可见）。"""
        from src.akb2.quality.distill import _filter_resolvable_graph_edges

        edges = [
            {"concept": "concept-x", "relation_type": "related"},
            {"concept": "loop-engineering", "relation_type": "related"},
        ]
        kept_e, kept_c, dropped, _de = _filter_resolvable_graph_edges(
            edges, ["loop-engineering"], ["concept-x"], None
        )
        assert "loop-engineering" in dropped, "被 drop 的 target 须进 dropped 列表"
        assert len(kept_e) == 1, "concept-x 须保留"
