"""test_grader_score_required.py — p0-1 SC-1 concept 评分诚实化验收。

锁：concept 车道逐维 score 必填 + 0~1 尺度（强制产出，拒缺/非数值/越界 fail-closed，
不静默当 0 放行）；procedural 车道保持可选零回归。覆盖三层：
  - schema 层（_build_grader_schema）：concept score required + minimum:0/maximum:1
  - parse 层（_parse_grader_output）：concept 缺/越界/非数值/bool score → parse fail
  - compute 层（compute_weighted_score）：concept 非数值/越界 score → fail-closed
  - extensions 层（_parse_grader_extensions）：concept 越界 score 防御性跳过 → 缺维兜底
"""

from __future__ import annotations

from src.akb2.quality import grader
from src.akb2.quality.grader import (
    RUBRIC_DIMENSIONS,
    RUBRIC_DIMENSIONS_BY_LANE,
    _load_rubric_spec,
)
from src.akb2.quality.weighted_score import (
    _lane_weight_map,
    compute_weighted_score,
)


def _concept_dims() -> list[str]:
    return list(RUBRIC_DIMENSIONS_BY_LANE["concept"])


def _concept_resp(score_map: dict[str, float], all_pass: bool = True) -> dict:
    """构造 concept 7 维 grader 响应（每维带指定 score）。"""
    return {
        "dimensions": [
            {"dimension": d, "pass": all_pass, "defects": [], "score": score_map[d]}
            for d in _concept_dims()
        ],
        "interpretation_entailment": True,
    }


def _uniform_scores(value: float) -> dict[str, float]:
    return {d: value for d in _concept_dims()}


# ---------------------------------------------------------------------------
# schema 层：concept score required + 0~1 边界；procedural 可选无边界
# ---------------------------------------------------------------------------


class TestSchemaScoreRequired:
    def test_concept_schema_score_required_and_bounded(self):
        """concept schema：score 在 required + minimum:0/maximum:1（强制产出 + 钉尺度）。"""
        schema = grader._build_grader_schema("concept")
        dim_item = schema["properties"]["dimensions"]["items"]
        assert "score" in dim_item["required"], "concept 车道 score 应必填"
        score_field = dim_item["properties"]["score"]
        assert score_field.get("minimum") == 0
        assert score_field.get("maximum") == 1

    def test_procedural_schema_score_optional_unbounded(self):
        """procedural schema：score 不在 required、无 0~1 边界（零回归）。"""
        schema = grader._build_grader_schema("procedural")
        dim_item = schema["properties"]["dimensions"]["items"]
        assert "score" not in dim_item["required"], "procedural score 不应必填"
        score_field = dim_item["properties"]["score"]
        assert "minimum" not in score_field
        assert "maximum" not in score_field


# ---------------------------------------------------------------------------
# parse 层：concept 缺/越界/非数值/bool score → parse fail-closed
# ---------------------------------------------------------------------------


class TestConceptScoreParseFailClosed:
    def test_concept_missing_score_per_dim_parse_fails(self):
        """concept 每维缺 score → parse fail-closed（不静默当 0 过）。"""
        data = {
            "dimensions": [
                {"dimension": d, "pass": True, "defects": []} for d in _concept_dims()
            ]
        }
        defects, ok = grader._parse_grader_output(data, "concept")
        assert ok is False, "concept 缺逐维 score 应 parse 失败"
        assert defects == []

    def test_concept_score_above_one_parse_fails(self):
        """concept score>1（越界）→ parse fail-closed。"""
        dims = _concept_dims()
        score_map = _uniform_scores(0.5)
        score_map[dims[0]] = 1.5  # 越界
        defects, ok = grader._parse_grader_output(_concept_resp(score_map), "concept")
        assert ok is False, "concept score>1 应 parse 失败"

    def test_concept_score_below_zero_parse_fails(self):
        """concept score<0（越界）→ parse fail-closed。"""
        dims = _concept_dims()
        score_map = _uniform_scores(0.5)
        score_map[dims[0]] = -0.1  # 越界
        defects, ok = grader._parse_grader_output(_concept_resp(score_map), "concept")
        assert ok is False, "concept score<0 应 parse 失败"

    def test_concept_score_non_numeric_parse_fails(self):
        """concept score 非数值（字符串）→ parse fail-closed。"""
        dims = _concept_dims()
        score_map = _uniform_scores(0.5)
        score_map[dims[0]] = "high"  # type: ignore[assignment]
        defects, ok = grader._parse_grader_output(_concept_resp(score_map), "concept")
        assert ok is False, "concept 非数值 score 应 parse 失败"

    def test_concept_score_bool_parse_fails(self):
        """concept score=bool（True/False）→ parse fail-closed（bool 非 0~1 数值）。"""
        dims = _concept_dims()
        score_map = _uniform_scores(0.5)
        score_map[dims[0]] = True  # type: ignore[assignment]
        defects, ok = grader._parse_grader_output(_concept_resp(score_map), "concept")
        assert ok is False, "concept bool score 应 parse 失败"

    def test_concept_valid_scores_parse_ok_and_collected(self):
        """concept 全维合法 0~1 score → parse_ok + extensions 收集逐维 score。"""
        score_map = _uniform_scores(0.8)
        defects, ok = grader._parse_grader_output(_concept_resp(score_map), "concept")
        assert ok is True
        assert defects == []
        ext = grader._parse_grader_extensions(_concept_resp(score_map), "concept")
        assert ext["dimension_scores"] == score_map


# ---------------------------------------------------------------------------
# procedural 零回归：缺 score / 越界 score 仍 parse_ok（不收紧 procedural）
# ---------------------------------------------------------------------------


class TestProceduralNoRegression:
    def test_procedural_missing_score_parse_ok(self):
        """procedural 缺 score 仍 parse_ok（零回归，score 仅 concept 必填）。"""
        data = {
            "dimensions": [
                {"dimension": d, "pass": True, "defects": []} for d in RUBRIC_DIMENSIONS
            ]
        }
        defects, ok = grader._parse_grader_output(data, "procedural")
        assert ok is True
        assert defects == []

    def test_procedural_score_out_of_range_parse_ok(self):
        """procedural score 越界（2.0）仍 parse_ok（procedural 不校验 0~1，零回归）。"""
        data = {
            "dimensions": [
                {"dimension": d, "pass": True, "defects": [], "score": 2.0}
                for d in RUBRIC_DIMENSIONS
            ]
        }
        defects, ok = grader._parse_grader_output(data, "procedural")
        assert ok is True


# ---------------------------------------------------------------------------
# compute 层：concept 非数值/越界 score → fail-closed（不静默当 0 放行）
# ---------------------------------------------------------------------------


class TestComputeConceptScoreFailClosed:
    def test_compute_concept_score_above_one_fail_closed(self):
        """compute_weighted_score：concept score>1 → fail-closed（score=0/passed=False）。"""
        w = _lane_weight_map(_load_rubric_spec(), "concept")
        scores = {name: 1.5 for name in w}
        r = compute_weighted_score(scores, "concept")
        assert r.passed is False
        assert r.score == 0.0

    def test_compute_concept_score_below_zero_fail_closed(self):
        """compute_weighted_score：concept score<0 → fail-closed。"""
        w = _lane_weight_map(_load_rubric_spec(), "concept")
        scores = {name: -0.5 for name in w}
        r = compute_weighted_score(scores, "concept")
        assert r.passed is False
        assert r.score == 0.0

    def test_compute_concept_non_numeric_fail_closed(self):
        """compute_weighted_score：concept 非数值 score → fail-closed（不静默当 0 放行）。"""
        w = _lane_weight_map(_load_rubric_spec(), "concept")
        scores = {name: "high" for name in w}  # type: ignore[dict-item]
        r = compute_weighted_score(scores, "concept")
        assert r.passed is False
        assert r.score == 0.0

    def test_compute_concept_valid_scores_aggregate(self):
        """compute_weighted_score：concept 全维 0.8 → 总分 8.0 ≥ 6.0 → pass（复算正确）。"""
        w = _lane_weight_map(_load_rubric_spec(), "concept")
        scores = {name: 0.8 for name in w}
        r = compute_weighted_score(scores, "concept")
        assert abs(r.score - 8.0) < 1e-6
        assert r.threshold == 6.0
        assert r.passed is True


# ---------------------------------------------------------------------------
# extensions 层：concept 越界 score 防御性跳过 → 缺维 fail-closed 兜底
# ---------------------------------------------------------------------------


class TestExtensionsConceptOutOfBoundsSkipped:
    def test_concept_extensions_out_of_range_skipped(self):
        """_parse_grader_extensions：concept 越界 score 不进 dim_scores（防御性兜底）。"""
        score_map = _uniform_scores(1.5)
        ext = grader._parse_grader_extensions(_concept_resp(score_map), "concept")
        assert ext["dimension_scores"] == {}, "越界 score 不应进 dim_scores"
