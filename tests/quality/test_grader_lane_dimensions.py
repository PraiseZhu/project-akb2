"""test_grader_lane_dimensions.py — step-3 SC-3f grader 按 lane 选维度集验收。

A3-6：RUBRIC_DIMENSIONS_BY_LANE 从 _load_rubric_spec() 动态派生（不在 .py 手写维名）。
  - concept grader B 层维度集 == rubric.yaml lanes.concept.dimensions 7 维全名（逐字含后缀）。
  - concept 不含 How 两维（How 密度/How 可执行性）、不含地板维。
  - procedural 逐字 == 现有 10 维。
  - AST 负断言：grader.py 非测试/fixture 字符串常量不含 concept 7 维任一字面量（防硬编码回流）。
  - 行为测试：tmp rubric 改名 → _rubric_dimension_names_by_lane 派生集随之变化。
"""

from __future__ import annotations

import ast
import copy
import pathlib

from src.akb2.quality.grader import (
    RUBRIC_DIMENSIONS,
    RUBRIC_DIMENSIONS_BY_LANE,
    _dimensions_for_lane,
    _load_rubric_spec,
    _rubric_dimension_names_by_lane,
)

_CONCEPT_DIMS = {
    "概念机制清晰度(H3)", "实现机制说明(H2)", "图谱可调用性",
    "适用边界", "TLDR密度", "Future Questions质量", "新颖增量",
}


class TestGraderLaneDimensions:
    def test_concept_dims_match_rubric_yaml(self):
        """concept grader 维度集 == rubric.yaml lanes.concept 7 维全名。"""
        assert set(RUBRIC_DIMENSIONS_BY_LANE["concept"]) == _CONCEPT_DIMS
        assert len(RUBRIC_DIMENSIONS_BY_LANE["concept"]) == 7

    def test_concept_excludes_how_and_floor_dims(self):
        """concept 不含 How 两维、不含地板维（锚定/诚实/F3/F4 是 A 层地板，不进 grader B 层 enum）。"""
        concept = set(RUBRIC_DIMENSIONS_BY_LANE["concept"])
        how_dims = {"How 密度", "How 可执行性"}
        assert not (concept & how_dims), f"concept 含 How 维: {concept & how_dims}"
        # 地板维名（integrity_floor 四成员）不在 concept grader enum
        floor_dims = {"论断锚定率", "证据诚实", "解读分离"}
        assert not (concept & floor_dims), f"concept 含地板维: {concept & floor_dims}"

    def test_procedural_dims_match_existing_10(self):
        """procedural 逐字 == 现有 10 维（零回归）。"""
        assert RUBRIC_DIMENSIONS_BY_LANE["procedural"] == RUBRIC_DIMENSIONS
        assert len(RUBRIC_DIMENSIONS_BY_LANE["procedural"]) == 10

    def test_dimensions_for_lane_defaults_to_procedural(self):
        """未知/缺省 lane → procedural 旧行为（零回归）。"""
        assert _dimensions_for_lane("") == RUBRIC_DIMENSIONS
        assert _dimensions_for_lane("unknown") == RUBRIC_DIMENSIONS
        assert _dimensions_for_lane("concept") == RUBRIC_DIMENSIONS_BY_LANE["concept"]

    def test_grader_py_no_hardcoded_concept_dim_constants(self):
        """AST 负断言：grader.py 字符串常量不含 concept 7 维任一字面量（防硬编码回流，A3-6）。

        维度集必须从 rubric.yaml 动态派生，不在 .py 写死维名字符串。
        """
        grader_path = pathlib.Path(__import__("src.akb2.quality.grader", fromlist=["x"]).__file__)
        tree = ast.parse(grader_path.read_text(encoding="utf-8"))
        str_consts = {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        leaked = _CONCEPT_DIMS & str_consts
        assert not leaked, (
            f"grader.py 含 concept 维名硬编码字面量（违反 A3-6 动态派生）: {sorted(leaked)}"
        )

    def test_tmp_rubric_rename_changes_derived_dims(self):
        """tmp rubric 改名 → _rubric_dimension_names_by_lane 派生集随之变化（动态派生不硬编码）。"""
        spec = _load_rubric_spec()
        tmp = copy.deepcopy(spec)
        tmp["lanes"]["concept"]["dimensions"][0]["name"] = "改名的H3维度(X)"
        by_lane = _rubric_dimension_names_by_lane(tmp)
        assert "改名的H3维度(X)" in by_lane["concept"]
        assert "概念机制清晰度(H3)" not in by_lane["concept"]
        # procedural 不受 concept 改名影响
        assert by_lane["procedural"] == RUBRIC_DIMENSIONS


# ---------------------------------------------------------------------------
# §0.2/D6：grader concept prompt 含 inferred_from_structure / edge evidence / H3 fail
# ---------------------------------------------------------------------------


class TestD6GraderConceptPromptH3:
    """§0.2/D6：concept grader prompt 显式含 H3 fail 条件 + inferred + edge evidence。"""

    def test_concept_prompt_contains_inferred_from_structure(self):
        from src.akb2.quality.grader import _build_grader_prompt, _load_rubric_text

        msgs = _build_grader_prompt("候选页正文", _load_rubric_text(), content_lane="concept")
        system = msgs[0]["content"]
        assert "inferred_from_structure" in system, (
            "concept grader prompt 须含 inferred_from_structure（H3 fail 条件）"
        )

    def test_concept_prompt_contains_edge_evidence_and_h3_fail(self):
        from src.akb2.quality.grader import _build_grader_prompt, _load_rubric_text

        msgs = _build_grader_prompt("候选页正文", _load_rubric_text(), content_lane="concept")
        system = msgs[0]["content"]
        assert "evidence" in system and "evidence_anchor" in system, (
            "concept prompt 须含 edge evidence / evidence_anchor"
        )
        assert "H3 fail" in system or "H3（概念机制清晰度）fail" in system, (
            "concept prompt 须显式含 H3 fail 条件"
        )
        assert "机制组件与因果边证据" in system, (
            "concept prompt 须指向 ## 机制组件与因果边证据 两表"
        )

    def test_procedural_prompt_has_no_h3_fail_note(self):
        """procedural 不注 H3 fail 条件（H3 是 concept 维度）。"""
        from src.akb2.quality.grader import _build_grader_prompt, _load_rubric_text

        msgs = _build_grader_prompt("候选页正文", _load_rubric_text(), content_lane="procedural")
        system = msgs[0]["content"]
        assert "H3 fail" not in system, "procedural prompt 不应含 concept H3 fail 条件"

    def test_rubric_yaml_h3_note_contains_d6_keywords(self):
        """rubric.yaml concept H3 note 含 inferred_from_structure + eligible edge。"""
        from src.akb2.quality.grader import _load_rubric_spec

        spec = _load_rubric_spec()
        concept_dims = spec["lanes"]["concept"]["dimensions"]
        h3 = next(d for d in concept_dims if "H3" in d["name"])
        note = h3["note"]
        assert "inferred_from_structure" in note
        assert "evidence_anchor" in note
        assert "eligible" in note or "source_causal" in note
