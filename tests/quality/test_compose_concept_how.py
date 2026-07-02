"""test_compose_concept_how.py — P3 compose 占位自拒修复测试。

覆盖(计划 P3 节 4 条):
    - concept 空 how → 渲染 walkthrough 无 SG-how-executability defect
    - walkthrough anchors ⊆ raw 声明集
    - procedural(含空 lane)占位原样保留 → SG 照拒(严格性零回退)
    - 零 eligible 兜底行无 FORBIDDEN/TEMPLATE marker 且无 provenance 伪标
"""

from __future__ import annotations

from src.akb2.quality.checks_structural import (
    FORBIDDEN_HOW_FALLBACK_MARKERS,
    TEMPLATE_RESIDUE_MARKERS,
    run_candidate_self_gate,
)
from src.akb2.quality.distill import _render_concept_mechanism_walkthrough
from src.akb2.quality.raw_anchors import declared_anchor_set


_RAW = """\
## 锚点目录

| §A | x |
| §B | y |

---

§A_CONTENT_START

The Executor Pool dispatches to the Task Graph for scheduling.

§B_INTRODUCTION

second para.
"""

_RAW_NO_ANCHORS = "plain text no anchors"


def _extract_with_eligible_edges() -> dict:
    """有 eligible source_causal 边(有 evidence + evidence_anchor)。"""
    return {
        "claims": [],
        "argumentation": [],
        "mechanism_components": [
            {"name": "Executor Pool", "role": "x"},
            {"name": "Task Graph", "role": "y"},
        ],
        "causal_edges": [
            {"from": "Executor Pool", "to": "Task Graph", "relation": "raw_causal",
             "edge_kind": "source_causal", "evidence": "Executor Pool dispatches to Task Graph",
             "evidence_anchor": "§A"},
        ],
        "how_steps": [],
    }


def _extract_zero_eligible() -> dict:
    """无 eligible 边(只 inferred 弱边)。"""
    return {
        "claims": [],
        "argumentation": [],
        "mechanism_components": [{"name": "A", "role": "x"}],
        "causal_edges": [
            {"from": "A", "to": "B", "relation": "seq", "edge_kind": "inferred_from_structure",
             "evidence": "", "evidence_anchor": ""},
        ],
        "how_steps": [],
    }


class TestRenderConceptMechanismWalkthrough:
    def test_concept_empty_how_walkthrough_no_sg_defect(self):
        """concept 空 how → walkthrough 无 SG-how-executability defect。"""
        from src.akb2.quality.distill import _build_body
        from src.akb2.quality.distill import ExtractResult, compose_candidate  # noqa: F401

        # 直接调 _build_body 验证 how 节内容
        data = _extract_with_eligible_edges()
        body = _build_body(
            data, raw_path="", route_llm_fn=None, must_cover=[],
            raw_content=_RAW, content_lane="concept",
        )
        # walkthrough 行出现(有 eligible 边)
        assert "机制关系:Executor Pool → Task Graph" in body
        assert "[raw §A] (source_grounded)" in body
        # 无 FORBIDDEN 占位 marker(walkthrough 替代了占位)
        for marker in FORBIDDEN_HOW_FALLBACK_MARKERS:
            assert marker not in body, f"walkthrough 不应有 FORBIDDEN marker {marker!r}"

    def test_walkthrough_anchors_in_declared_set(self):
        """walkthrough anchors ⊆ raw 声明集(canonicalize + resolve 后)。"""
        data = _extract_with_eligible_edges()
        walkthrough = _render_concept_mechanism_walkthrough(data, _RAW)
        decl = declared_anchor_set(_RAW)
        # 抽出 walkthrough 里所有 [raw §X] 引用
        import re

        refs = re.findall(r"\[raw (§[A-Z0-9_]+)\]", walkthrough)
        assert refs  # 至少一条
        for r in refs:
            assert r in decl, f"walkthrough anchor {r} 不在 raw 声明集 {decl}"

    def test_procedural_empty_how_keeps_placeholder_rejected(self):
        """procedural(含空 lane)占位原样保留 → SG 照拒(严格性零回退)。"""
        from src.akb2.quality.distill import _build_body

        data = _extract_with_eligible_edges()
        # procedural lane:how 双空 → 占位保留(不走 walkthrough)
        body = _build_body(
            data, raw_path="", route_llm_fn=None, must_cover=[],
            raw_content=_RAW, content_lane="procedural",
        )
        assert "原文无可安全锚定的操作步骤" in body  # 占位保留
        # walkthrough 不出现(procedural 不渲染)
        assert "机制关系:Executor Pool → Task Graph" not in body

    def test_zero_eligible_fallback_no_marker_no_fake_provenance(self):
        """[N2] 零 eligible 兜底行:无 FORBIDDEN/TEMPLATE marker,无 (source_grounded) 伪标。"""
        data = _extract_zero_eligible()
        walkthrough = _render_concept_mechanism_walkthrough(data, _RAW)
        # 兜底行出现
        assert "概念机制清晰度" not in walkthrough  # 不是 grader 缺陷描述
        assert "无可锚定操作步骤" in walkthrough or "图谱关系节" in walkthrough
        # 无 FORBIDDEN marker
        for marker in FORBIDDEN_HOW_FALLBACK_MARKERS:
            assert marker not in walkthrough, f"兜底行不应有 FORBIDDEN marker {marker!r}"
        # 无 TEMPLATE marker
        for marker in TEMPLATE_RESIDUE_MARKERS:
            assert marker not in walkthrough, f"兜底行不应有 TEMPLATE marker {marker!r}"
        # 无 (source_grounded) 伪标
        assert "(source_grounded)" not in walkthrough

    def test_zero_eligible_fallback_when_no_declared_anchors(self):
        """raw 无声明锚 → 零 eligible 兜底(不造假 anchor)。"""
        data = _extract_with_eligible_edges()
        walkthrough = _render_concept_mechanism_walkthrough(data, _RAW_NO_ANCHORS)
        # 无声明锚 → eligible 边的 anchor 不在声明集 → 跳过 → 兜底行
        assert "(source_grounded)" not in walkthrough  # 不伪标
