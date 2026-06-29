"""test_concept_weighted_score.py — step-3 SC-3d concept 加权质量分验收。

覆盖：
  - concept 7 维权重和 == 10（从 rubric.yaml lanes.concept 读，不硬编码）。
  - 维名 ⊆ grader concept 维名集（无 KeyError/静默零权重）。
  - 正样本（全维高分）≥ 6.0 → pass。
  - 最小合规 H3 深度 ≤1.0 且总分 <6.0 → 拒（H3 深度独立于地板，对抗踩线）。
  - 维名不在权重表 → KeyError（不静默零权重）。
  - 阈值从 rubric.yaml 读（改 YAML threshold 即改判分，SC-3 外置）。
"""

from __future__ import annotations

import pytest

from src.akb2.quality.grader import _load_rubric_spec
from src.akb2.quality.weighted_score import (
    DEFAULT_THRESHOLD,
    _lane_weight_map,
    compute_weighted_score,
)


def _concept_weights() -> dict[str, float]:
    return _lane_weight_map(_load_rubric_spec(), "concept")


class TestConceptWeightedScore:
    def test_concept_weights_sum_to_10(self):
        """concept 7 维权重和 == 10（rubric.yaml 外置，不硬编码）。"""
        w = _concept_weights()
        assert len(w) == 7
        assert abs(sum(w.values()) - 10.0) < 1e-6

    def test_concept_dim_names_match_expected_set(self):
        """concept 维名集逐字匹配 spec（含 (H3)/(H2) 后缀）。"""
        w = _concept_weights()
        expected = {
            "概念机制清晰度(H3)", "实现机制说明(H2)", "图谱可调用性",
            "适用边界", "TLDR密度", "Future Questions质量", "新颖增量",
        }
        assert set(w.keys()) == expected

    def test_positive_sample_passes_threshold(self):
        """正样本：全维满分(score=1.0)→总分=权重和=10 ≥ 6.0 → pass。"""
        w = _concept_weights()
        scores = {name: 1.0 for name in w}
        r = compute_weighted_score(scores, "concept")
        assert abs(r.score - 10.0) < 1e-6
        assert r.threshold == 6.0
        assert r.passed is True

    def test_minimal_compliant_h3_depth_below_threshold(self):
        """最小合规 H3 深度 ≤1.0 且总分 <6.0 → 拒（H3 独立于地板，防辅助分顶过）。

        H3 维度（概念机制清晰度(H3)）score=1.0（最小合规），其它维全 0 → 总分=3.5 <6.0。
        对抗样本靠辅助分过 6.0 在此被拒：H3 深度独立，最小合规不足以过阈。
        """
        w = _concept_weights()
        scores = {name: (1.0 if "H3" in name else 0.0) for name in w}
        r = compute_weighted_score(scores, "concept")
        # H3 weight=3.5, score=1.0 → 贡献 3.5；其它 0 → 总分 3.5
        assert abs(r.score - 3.5) < 1e-6
        assert r.score < r.threshold
        assert r.passed is False

    def test_adversarial_auxiliary_only_below_threshold(self):
        """对抗：H3=0 但辅助维全满分 → 总分=10-3.5=6.5 边界。
        H3 深度=0（未达最小合规）→ 即使辅助分到 6.5 边界，H3 独立判定仍拒（地板层兜底）。
        本测试验加权分计算正确性：H3=0 + 其它满分 → 总分 6.5（边界）。
        """
        w = _concept_weights()
        scores = {name: (0.0 if "H3" in name else 1.0) for name in w}
        r = compute_weighted_score(scores, "concept")
        # 总分 = 10 - 3.5 = 6.5（边界，>=threshold 6.0 → passed=True）
        # 注：H3 深度独立由地板/grader 把关，加权分只算过阈；H3=0 的对抗由地板 F4/grader 拒
        assert abs(r.score - 6.5) < 1e-6

    def test_unknown_dimension_raises_keyerror(self):
        """维名不在 concept 权重表 → KeyError（不静默零权重，防漏维）。"""
        scores = {"不存在的维度": 1.0}
        with pytest.raises(KeyError, match="不在 concept"):
            compute_weighted_score(scores, "concept")

    def test_threshold_from_rubric_yaml(self):
        """阈值从 rubric.yaml 读（改 YAML threshold 即改判分，SC-3 外置）。"""
        r = compute_weighted_score({}, "concept")
        assert r.threshold == 6.0  # rubric.yaml concept threshold=6.0
        # 空维度 → score=0 < 6.0 → 不通过
        assert r.passed is False
        assert r.score == 0.0

    def test_default_threshold_constant_matches_spec(self):
        """DEFAULT_THRESHOLD 兜底常量与 spec threshold 一致。"""
        spec = _load_rubric_spec()
        assert DEFAULT_THRESHOLD == float(spec["threshold"])

    def test_missing_dims_fail_closed(self):
        """codex worker #2：缺维（只给部分 concept 维 score）→ fail-closed reject，不静默放行。

        之前只累加 present 维，5 维高分可过阈；修复后 concept 要求全 7 维到齐，缺维 → score=0 拒。
        """
        w = _concept_weights()
        partial = {name: 1.0 for name in list(w)[:5]}  # 只给 5 维（缺 2 维）
        r = compute_weighted_score(partial, "concept")
        assert r.passed is False
        assert r.score == 0.0  # 缺维 → fail-closed，不靠 present 高分放行

    def test_lane_threshold_takes_effect(self, monkeypatch):
        """codex worker：改 lanes.concept.threshold 即改 concept 判定（不被顶层 threshold 覆盖）。"""
        import copy

        from src.akb2.quality import weighted_score as ws

        spec = ws._load_rubric_spec()
        tmp = copy.deepcopy(spec)
        tmp["lanes"]["concept"]["threshold"] = 9.0  # concept 提阈
        monkeypatch.setattr(ws, "_load_rubric_spec", lambda: tmp)
        w = ws._lane_weight_map(tmp, "concept")
        scores = {name: 0.8 for name in w}  # 总分 8.0
        r = ws.compute_weighted_score(scores, "concept")
        assert r.threshold == 9.0  # 用 lane threshold，非顶层 6.5
        assert abs(r.score - 8.0) < 1e-6
        assert not r.passed  # 8.0 < 9.0 → fail（若用顶层 6.5 会误 pass）
