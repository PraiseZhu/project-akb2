"""test_grader_score_required.py — G3 返工 concept 评分（pass 派生 weighted）验收。

G3 裁决（p0-1 撤销 score required）：gpt-5.4 真机吐 dimensions(含 pass) +
interpretation_entailment，**不吐逐维 0~1 score** → concept weighted 改由逐维 pass
派生（pass=1.0/fail=0.0 × 权重，过阈 6.0 即通过）。

新契约（反转 p0-1 的"score required"）：
  - schema：concept score **不** required、无 0~1 min/max（全车道 optional）
  - parse：concept 缺 score **不** fail（有 pass 即可）；score 若提供且非法
    （bool/非数值/越界 <0 或 >1）仍 fail-closed；缺整维 / pass 非 bool → fail
  - extensions：concept dim_scores 从 pass 派生（1.0/0.0），**不读 score 字段**
  - compute：concept Σ(weight × pass_derived_score)，缺维 fail-closed；
    越界/非数值防御性 fail-closed（pass 派生不触发，防手工/异常输入）
  - procedural 零回归（score 可选，旧口径）
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


def _concept_resp_no_score(pass_map: dict[str, bool]) -> dict:
    """构造 concept 7 维 grader 响应（**无 score**，只有 pass/fail）——模拟 gpt-5.4 真实输出。"""
    return {
        "dimensions": [
            {
                "dimension": d,
                "pass": pass_map[d],
                "defects": (
                    []
                    if pass_map[d]
                    else [{"location": "L", "defect": "d", "suggestion": "s"}]
                ),
            }
            for d in _concept_dims()
        ],
        "interpretation_entailment": True,
    }


def _all_pass_no_score() -> dict:
    return _concept_resp_no_score({d: True for d in _concept_dims()})


def _concept_resp_with_score(score_map: dict[str, float], all_pass: bool = True) -> dict:
    """构造 concept 响应（带 score）——测 score 提供且非法。"""
    return {
        "dimensions": [
            {"dimension": d, "pass": all_pass, "defects": [], "score": score_map[d]}
            for d in _concept_dims()
        ],
        "interpretation_entailment": True,
    }


# ---------------------------------------------------------------------------
# schema 层：score 全车道 optional（concept 撤销 required + min/max）
# ---------------------------------------------------------------------------


class TestSchemaScoreOptional:
    def test_concept_schema_score_not_required(self):
        """G3: concept schema score 不在 required（撤销 p0-1 score required）。"""
        schema = grader._build_grader_schema("concept")
        dim_item = schema["properties"]["dimensions"]["items"]
        assert "score" not in dim_item["required"], "concept score 不应必填（G3 撤销）"

    def test_concept_schema_score_no_min_max(self):
        """G3: concept schema score 无 minimum/maximum（撤销 0~1 钉尺度）。"""
        schema = grader._build_grader_schema("concept")
        score_field = schema["properties"]["dimensions"]["items"]["properties"]["score"]
        assert "minimum" not in score_field
        assert "maximum" not in score_field

    def test_procedural_schema_score_optional(self):
        """procedural schema score 不在 required（零回归）。"""
        schema = grader._build_grader_schema("procedural")
        dim_item = schema["properties"]["dimensions"]["items"]
        assert "score" not in dim_item["required"]


# ---------------------------------------------------------------------------
# parse 层：concept 缺 score 不 fail（pass 派生）；score 提供且非法仍 fail；
#           缺整维 / pass 非 bool → fail
# ---------------------------------------------------------------------------


class TestConceptParsePassBased:
    def test_concept_missing_score_parse_ok(self):
        """G3: concept 缺 score（有 pass）→ parse ok（不 fail，pass 派生 weighted）。"""
        defects, ok = grader._parse_grader_output(_all_pass_no_score(), "concept")
        assert ok is True, "concept 缺 score（有 pass）应 parse ok"
        assert defects == []

    def test_concept_all_pass_no_score_parse_ok(self):
        """G3: concept 全 pass 无 score → parse ok（gpt-5.4 真实输出形态）。"""
        defects, ok = grader._parse_grader_output(_all_pass_no_score(), "concept")
        assert ok is True
        assert defects == []

    def test_concept_missing_dim_parse_fails(self):
        """G3: concept 缺整维（某维没出）→ parse fail-closed（seen_dimensions != 全维）。"""
        dims = _concept_dims()
        data = {
            "dimensions": [
                {"dimension": d, "pass": True, "defects": []} for d in dims[:6]
            ]
        }  # 缺第 7 维
        defects, ok = grader._parse_grader_output(data, "concept")
        assert ok is False, "concept 缺整维应 parse fail"

    def test_concept_pass_non_bool_parse_fails(self):
        """G3: concept pass 非 bool → parse fail-closed。"""
        dims = _concept_dims()
        data = {
            "dimensions": [
                {"dimension": dims[0], "pass": "yes", "defects": []},
                *[{"dimension": d, "pass": True, "defects": []} for d in dims[1:]],
            ]
        }
        defects, ok = grader._parse_grader_output(data, "concept")
        assert ok is False, "concept pass 非 bool 应 parse fail"

    def test_concept_score_provided_above_one_parse_fails(self):
        """G3: concept score 提供 且 >1 → parse fail-closed（score 若提供则校验）。"""
        dims = _concept_dims()
        score_map = {d: 0.5 for d in dims}
        score_map[dims[0]] = 1.5
        defects, ok = grader._parse_grader_output(
            _concept_resp_with_score(score_map), "concept"
        )
        assert ok is False, "concept score>1（提供）应 parse fail"

    def test_concept_score_provided_non_numeric_parse_fails(self):
        """G3: concept score 提供 且 非数值 → parse fail-closed。"""
        dims = _concept_dims()
        score_map = {d: 0.5 for d in dims}
        score_map[dims[0]] = "high"  # type: ignore[assignment]
        defects, ok = grader._parse_grader_output(
            _concept_resp_with_score(score_map), "concept"
        )
        assert ok is False, "concept 非数值 score（提供）应 parse fail"

    def test_concept_score_provided_bool_parse_fails(self):
        """G3: concept score 提供 且 bool → parse fail-closed。"""
        dims = _concept_dims()
        score_map = {d: 0.5 for d in dims}
        score_map[dims[0]] = True  # type: ignore[assignment]
        defects, ok = grader._parse_grader_output(
            _concept_resp_with_score(score_map), "concept"
        )
        assert ok is False, "concept bool score（提供）应 parse fail"


# ---------------------------------------------------------------------------
# procedural 零回归
# ---------------------------------------------------------------------------


class TestProceduralNoRegression:
    def test_procedural_missing_score_parse_ok(self):
        """procedural 缺 score 仍 parse_ok（零回归）。"""
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
# extensions 层：concept dim_scores 从 pass 派生（不读 score 字段）
# ---------------------------------------------------------------------------


class TestConceptExtensionsPassDerived:
    def test_concept_all_pass_no_score_dim_scores_all_one(self):
        """G3: concept 全 pass 无 score → dim_scores 全 1.0（pass 派生）。"""
        ext = grader._parse_grader_extensions(_all_pass_no_score(), "concept")
        expected = {d: 1.0 for d in _concept_dims()}
        assert ext["dimension_scores"] == expected

    def test_concept_partial_pass_dim_scores_pass_derived(self):
        """G3: concept 部分 pass → dim_scores pass=1.0/fail=0.0（从 pass 派生，不读 score）。"""
        dims = _concept_dims()
        pass_map = {d: (d == dims[0]) for d in dims}  # 只 dims[0] pass
        resp = _concept_resp_no_score(pass_map)
        ext = grader._parse_grader_extensions(resp, "concept")
        assert ext["dimension_scores"][dims[0]] == 1.0
        for d in dims[1:]:
            assert ext["dimension_scores"][d] == 0.0

    def test_concept_score_ignored_when_pass_present(self):
        """G3: concept 有 pass 时 score 字段被忽略（pass 派生优先，不读 score）。"""
        dims = _concept_dims()
        # 带 score=0.9 但 pass=True → dim_scores 应=1.0（pass 派生，不读 score）
        resp = {
            "dimensions": [
                {"dimension": d, "pass": True, "defects": [], "score": 0.9}
                for d in dims
            ],
            "interpretation_entailment": True,
        }
        ext = grader._parse_grader_extensions(resp, "concept")
        assert ext["dimension_scores"] == {d: 1.0 for d in dims}


# ---------------------------------------------------------------------------
# compute 层：concept Σ(weight × pass_derived)；缺维 fail-closed；越界防御 fail
# ---------------------------------------------------------------------------


class TestComputeConceptPassDerived:
    def test_compute_concept_all_pass_full_score(self):
        """G3: concept 全 pass（1.0）→ weighted=10.0 ≥ 6.0 → pass。"""
        w = _lane_weight_map(_load_rubric_spec(), "concept")
        scores = {name: 1.0 for name in w}  # 全 pass 派生
        r = compute_weighted_score(scores, "concept")
        assert abs(r.score - 10.0) < 1e-6
        assert r.threshold == 6.0
        assert r.passed is True

    def test_compute_concept_partial_pass_below_threshold(self):
        """G3: concept 只 H3 pass（1.0）其它 fail（0.0）→ weighted=3.5 < 6.0 → reject。"""
        w = _lane_weight_map(_load_rubric_spec(), "concept")
        scores = {name: (1.0 if "H3" in name else 0.0) for name in w}
        r = compute_weighted_score(scores, "concept")
        assert abs(r.score - 3.5) < 1e-6
        assert r.passed is False

    def test_compute_concept_missing_dim_fail_closed(self):
        """G3: concept 缺整维 → fail-closed（score=0/passed=False）。"""
        w = _lane_weight_map(_load_rubric_spec(), "concept")
        scores = {name: 1.0 for name in list(w)[:5]}  # 缺 2 维
        r = compute_weighted_score(scores, "concept")
        assert r.passed is False
        assert r.score == 0.0

    def test_compute_concept_score_above_one_defensive_fail(self):
        """G3: compute 收越界 score（>1）→ 防御性 fail-closed（pass 派生不触发，防手工）。"""
        w = _lane_weight_map(_load_rubric_spec(), "concept")
        scores = {name: 1.5 for name in w}
        r = compute_weighted_score(scores, "concept")
        assert r.passed is False
        assert r.score == 0.0

    def test_compute_concept_non_numeric_defensive_fail(self):
        """G3: compute 收非数值 score → 防御性 fail-closed。"""
        w = _lane_weight_map(_load_rubric_spec(), "concept")
        scores = {name: "high" for name in w}  # type: ignore[dict-item]
        r = compute_weighted_score(scores, "concept")
        assert r.passed is False
        assert r.score == 0.0
