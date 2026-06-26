"""test_runner_concept_gates.py — step-3 SC-3c/SC-3d 生产 runner 接入验收。

codex cross-review P1 修复验证：_run_quality_segments 在 concept 车道 grader 无 defects 后，
必须判 F3 层② interpretation_entailment + concept 加权分过阈——防 poisoned-trace/低分被放行。

  - poisoned-trace（entailment=false）→ runner reject（error_class=poisoned_trace_entailment）。
  - entailment=None（grader 未评）→ concept fail-closed reject（entailment_not_evaluated）。
  - 低加权分（<6.5）→ runner reject（weighted_score_below_threshold）。
  - entailment=true + 高分 → runner converged=True（pass）。
  - procedural 不启 concept 门（entailment=None 仍过，零回归）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.akb2.quality.grader import (
    PRIMARY_GRADER_MODEL,
    RUBRIC_DIMENSIONS_BY_LANE,
    GraderResult,
)
from src.akb2.quality.integrity_floor import FloorResult


def _pass_floor() -> FloorResult:
    return FloorResult(passed=True, failed_floors=[], reason="", content_lane="concept")


def _grader(
    defects=None,
    entailment=True,
    dim_scores=None,
    suspended=False,
    fallback_used=False,
) -> GraderResult:
    return GraderResult(
        defects=defects or [],
        model_used="gpt-5.4",
        suspended=suspended,
        primary_model=PRIMARY_GRADER_MODEL,
        fallback_used=fallback_used,
        content_lane="concept",
        interpretation_entailment=entailment,
        dimension_scores=dim_scores or {},
    )


def _run(candidate, content_lane="concept", floor=None):
    """跑 _run_quality_segments，mock A 层 self-gate（通过）+ grader。"""
    from src.akb2.quality.runner import _run_quality_segments

    with patch(
        "src.akb2.quality.checks_structural.run_candidate_self_gate", return_value=[]
    ), patch(
        "src.akb2.quality.grader.run_grader_detailed", return_value=candidate
    ):
        return _run_quality_segments(
            "candidate text",
            "/tmp/x.md",
            "raw",
            config=None,
            content_lane=content_lane,
            floor_result=floor or _pass_floor(),
        )


_CONCEPT_DIMS = RUBRIC_DIMENSIONS_BY_LANE["concept"]


class TestRunnerEntailmentGate:
    def test_poisoned_trace_rejected(self):
        """entailment=false + 无 defects → runner reject（poisoned_trace_entailment）。"""
        grader = _grader(defects=[], entailment=False)
        converged, error_class, _, meta = _run(grader)
        assert not converged
        assert error_class == "poisoned_trace_entailment"
        assert meta["interpretation_entailment"] is False
        assert meta["weighted_score_not_computed"] is True

    def test_entailment_none_fail_closed_for_concept(self):
        """concept 必填 entailment；None（grader 未评）→ fail-closed reject。"""
        grader = _grader(defects=[], entailment=None)
        converged, error_class, _, meta = _run(grader)
        assert not converged
        assert error_class == "entailment_not_evaluated"
        assert meta["interpretation_entailment"] is None

    def test_entailment_true_proceeds_to_weighted_score(self):
        """entailment=true → 进入加权分判定（不在此拒）。"""
        # 高分 → 过；这里验 entailment=true 不在 entailment 门拒
        high_scores = {d: 1.0 for d in _CONCEPT_DIMS}  # 全 1.0 → 10.0 ≥6.5
        grader = _grader(defects=[], entailment=True, dim_scores=high_scores)
        converged, error_class, _, _ = _run(grader)
        assert converged
        assert error_class == ""


class TestRunnerWeightedScoreGate:
    def test_low_score_rejected(self):
        """concept 加权分 <6.5 → runner reject（weighted_score_below_threshold）。"""
        # 只 H3=1.0，其它 0 → 3.5 <6.5
        low = {d: (1.0 if "H3" in d else 0.0) for d in _CONCEPT_DIMS}
        grader = _grader(defects=[], entailment=True, dim_scores=low)
        converged, error_class, _, meta = _run(grader)
        assert not converged
        assert error_class == "weighted_score_below_threshold"
        assert meta["weighted_score"] == 3.5
        assert meta["weighted_threshold"] == 6.5

    def test_high_score_passes(self):
        """concept 全维满分 → 加权分 10.0 ≥6.5 → runner converged=True。"""
        high = {d: 1.0 for d in _CONCEPT_DIMS}
        grader = _grader(defects=[], entailment=True, dim_scores=high)
        converged, _, _, _ = _run(grader)
        assert converged

    def test_threshold_boundary(self):
        """总分恰 6.5（边界）→ >= threshold → pass（pass meta 不含 weighted_score，只有 reject 才含）。"""
        # H3=0 + 其它满分 → 10-3.5=6.5
        boundary = {d: (0.0 if "H3" in d else 1.0) for d in _CONCEPT_DIMS}
        grader = _grader(defects=[], entailment=True, dim_scores=boundary)
        converged, _, _, _ = _run(grader)
        # 6.5 >= 6.5 → pass（pass meta 不带 weighted_score，故不在此断言该 key）
        assert converged


class TestProceduralNoConceptGates:
    def test_procedural_skips_entailment_and_weighted_score(self):
        """procedural 不启 concept entailment/加权分门（零回归）。

        entailment=None + 无 defects → procedural 不应在 concept 门拒（converged=True）。
        """
        grader = _grader(defects=[], entailment=None, dim_scores={})
        # 改 content_lane 为 procedural
        from src.akb2.quality.runner import _run_quality_segments

        with patch(
            "src.akb2.quality.checks_structural.run_candidate_self_gate", return_value=[]
        ), patch(
            "src.akb2.quality.grader.run_grader_detailed", return_value=grader
        ):
            converged, error_class, _, _ = _run_quality_segments(
                "candidate", "/tmp/x.md", "raw", config=None,
                content_lane="procedural",
                floor_result=FloorResult(passed=True, failed_floors=[], reason="", content_lane="procedural"),
            )
        assert converged  # procedural 不查 entailment/score
        assert error_class == ""


# 注：grader 有 defects → 进 repair（既有行为，非 P1 修复内容），不进 concept 门。
# 该路径需完整 RunnerConfig（failures_path 等），不在本单测范围；P1 修复（无 defects 时的
# entailment/加权分硬门）由上面 TestRunnerEntailmentGate/TestRunnerWeightedScoreGate 覆盖。


# ----------------------------- repair 收敛后 concept 门（codex P1 修复核心） ----------------------------- #


def _repair_result(entailment, dim_scores):
    """构造 repair 收敛的 repair_result（final_grader_meta 携 entailment/scores）。"""
    return {
        "converged": True,
        "rounds": 1,
        "final_defects": [],
        "final_text": None,  # 不写文件（避免测试副作用）
        "final_grader_meta": {
            "grader_model_used": "gpt-5.4",
            "grader_suspended": False,
            "interpretation_entailment": entailment,
            "dimension_scores": dict(dim_scores),
        },
        "trace_paths": [],
    }


class TestRepairConvergedConceptGates:
    """codex P1：repair 收敛后必须复查 concept 硬门（之前直接写 PASS 绕过）。"""

    def test_repair_converged_poisoned_trace_rejected(self):
        """initial defect → repair 收敛 → final entailment=false → reject（不写 PASS）。"""
        from src.akb2.quality.checks_structural import Defect
        from src.akb2.quality.runner import _run_quality_segments

        defect = Defect(dimension="x", location="L", defect="d", suggestion="s")
        initial_grader = _grader(defects=[defect], entailment=False, dim_scores={})
        repair = _repair_result(entailment=False, dim_scores={})
        with patch("src.akb2.quality.checks_structural.run_candidate_self_gate", return_value=[]), \
             patch("src.akb2.quality.grader.run_grader_detailed", return_value=initial_grader), \
             patch("src.akb2.quality.runner._load_golden_sample", return_value="golden"), \
             patch("src.akb2.quality.repair.run_repair_loop", return_value=repair):
            converged, error_class, _, meta = _run_quality_segments(
                "candidate", "/tmp/x.md", "raw", config=MagicMock(),
                content_lane="concept", floor_result=_pass_floor(),
            )
        assert not converged
        assert error_class == "poisoned_trace_entailment"
        assert meta["interpretation_entailment"] is False

    def test_repair_converged_low_score_rejected(self):
        """initial defect → repair 收敛 → final 低分 → reject（加权分不能靠 repair 绕过）。"""
        from src.akb2.quality.checks_structural import Defect
        from src.akb2.quality.runner import _run_quality_segments

        defect = Defect(dimension="x", location="L", defect="d", suggestion="s")
        initial_grader = _grader(defects=[defect], entailment=True, dim_scores={})
        # final 只 H3=1.0 → 3.5 <6.5
        low = {d: (1.0 if "H3" in d else 0.0) for d in _CONCEPT_DIMS}
        repair = _repair_result(entailment=True, dim_scores=low)
        with patch("src.akb2.quality.checks_structural.run_candidate_self_gate", return_value=[]), \
             patch("src.akb2.quality.grader.run_grader_detailed", return_value=initial_grader), \
             patch("src.akb2.quality.runner._load_golden_sample", return_value="golden"), \
             patch("src.akb2.quality.repair.run_repair_loop", return_value=repair):
            converged, error_class, _, meta = _run_quality_segments(
                "candidate", "/tmp/x.md", "raw", config=MagicMock(),
                content_lane="concept", floor_result=_pass_floor(),
            )
        assert not converged
        assert error_class == "weighted_score_below_threshold"
        assert meta["weighted_score"] == 3.5

    def test_repair_converged_entailment_none_fail_closed(self):
        """repair 收敛但 final entailment=None（grader 未评）→ concept fail-closed reject。"""
        from src.akb2.quality.checks_structural import Defect
        from src.akb2.quality.runner import _run_quality_segments

        defect = Defect(dimension="x", location="L", defect="d", suggestion="s")
        initial_grader = _grader(defects=[defect], entailment=None, dim_scores={})
        repair = _repair_result(entailment=None, dim_scores={})
        with patch("src.akb2.quality.checks_structural.run_candidate_self_gate", return_value=[]), \
             patch("src.akb2.quality.grader.run_grader_detailed", return_value=initial_grader), \
             patch("src.akb2.quality.runner._load_golden_sample", return_value="golden"), \
             patch("src.akb2.quality.repair.run_repair_loop", return_value=repair):
            converged, error_class, _, _ = _run_quality_segments(
                "candidate", "/tmp/x.md", "raw", config=MagicMock(),
                content_lane="concept", floor_result=_pass_floor(),
            )
        assert not converged
        assert error_class == "entailment_not_evaluated"

    def test_repair_converged_high_score_entailment_true_passes(self):
        """repair 收敛 + final entailment=true + 高分 → PASS（concept 门通过）。"""
        from src.akb2.quality.checks_structural import Defect
        from src.akb2.quality.runner import _run_quality_segments

        defect = Defect(dimension="x", location="L", defect="d", suggestion="s")
        initial_grader = _grader(defects=[defect], entailment=True, dim_scores={})
        high = {d: 1.0 for d in _CONCEPT_DIMS}  # 全 1.0 → 10.0
        repair = _repair_result(entailment=True, dim_scores=high)
        with patch("src.akb2.quality.checks_structural.run_candidate_self_gate", return_value=[]), \
             patch("src.akb2.quality.grader.run_grader_detailed", return_value=initial_grader), \
             patch("src.akb2.quality.runner._load_golden_sample", return_value="golden"), \
             patch("src.akb2.quality.repair.run_repair_loop", return_value=repair):
            converged, _, _, _ = _run_quality_segments(
                "candidate", "/tmp/x.md", "raw", config=MagicMock(),
                content_lane="concept", floor_result=_pass_floor(),
            )
        assert converged
