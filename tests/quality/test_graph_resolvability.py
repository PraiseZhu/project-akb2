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
