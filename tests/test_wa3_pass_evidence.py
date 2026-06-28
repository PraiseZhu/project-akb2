"""wA#3: concept 通过时 PASS artifact 必须带异族硬判证据(非 null)。

旧逻辑 _check_concept_hard_gates 通过时返回 None、丢弃已算出的 weighted_score；
runner 用不含证据的 grader_meta 落盘 → PASS JSON 的 interpretation_entailment /
weighted_score / weighted_threshold 全是 null(硬判没被绕过,但证据链断,护栏⑥)。

修法: 硬门通过时把证据写回传入 meta；runner 两条 pass 路径并回落盘 meta。
本测试锁: 通过时 base_meta 被回填非 null 证据；reject 路径不变；procedural 不被动。
"""

from __future__ import annotations

from src.akb2.quality.grader import _dimensions_for_lane
from src.akb2.quality.runner import _check_concept_hard_gates


def test_concept_pass_populates_evidence_in_meta():
    """concept 全维高分 + entailment True → 通过, 且 base_meta 拿到非 null 证据。"""
    dims = _dimensions_for_lane("concept")
    dim_scores = {d: 0.9 for d in dims}  # p0-1: 0~1 标度（旧 9.0 越界被 fail-closed 拦）
    base: dict = {"grader_model_used": "gpt-5.4"}
    result = _check_concept_hard_gates(True, dim_scores, "concept", base)
    assert result is None  # 通过
    assert base["interpretation_entailment"] is True
    assert base["weighted_score"] is not None
    assert base["weighted_threshold"] is not None
    assert base["weighted_score"] >= base["weighted_threshold"]


def test_concept_entailment_false_still_rejects():
    """entailment False → poisoned-trace reject (硬判不变)。"""
    base: dict = {}
    r = _check_concept_hard_gates(False, {}, "concept", base)
    assert r is not None
    assert r[0] == "poisoned_trace_entailment"


def test_concept_entailment_none_still_rejects():
    """entailment None(未评) → fail-closed reject (硬判不变)。"""
    r = _check_concept_hard_gates(None, {}, "concept", {})
    assert r is not None
    assert r[0] == "entailment_not_evaluated"


def test_procedural_lane_not_mutated():
    """非 concept 车道 → 直接 None, 不回填证据 (零回归)。"""
    base: dict = {}
    r = _check_concept_hard_gates(None, {}, "procedural", base)
    assert r is None
    assert "interpretation_entailment" not in base
    assert "weighted_score" not in base
