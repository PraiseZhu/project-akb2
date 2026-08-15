"""test_runner_concept_gates.py — step-3 SC-3c/SC-3d 生产 runner 接入验收。

codex cross-review P1 修复验证：_run_quality_segments 在 concept 车道 grader 无 defects 后，
必须判 F3 层② interpretation_entailment + concept 加权分过阈——防 poisoned-trace/低分被放行。

  - poisoned-trace（entailment=false）→ runner reject（error_class=poisoned_trace_entailment）。
  - entailment=None（grader 未评）→ concept fail-closed reject（entailment_not_evaluated）。
  - 低加权分（<6.0）→ runner reject（weighted_score_below_threshold）。
  - entailment=true + 高分 → runner converged=True（pass）。
  - procedural 不启 concept 门（entailment=None 仍过，零回归）。
"""

from __future__ import annotations

from pathlib import Path
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
        high_scores = {d: 1.0 for d in _CONCEPT_DIMS}  # 全 1.0 → 10.0 ≥6.0
        grader = _grader(defects=[], entailment=True, dim_scores=high_scores)
        converged, error_class, _, _ = _run(grader)
        assert converged
        assert error_class == ""


class TestRunnerWeightedScoreGate:
    def test_low_score_rejected(self):
        """concept 加权分 <6.0 → runner reject（weighted_score_below_threshold）。"""
        # 只 H3=1.0，其它 0 → 3.5 <6.0
        low = {d: (1.0 if "H3" in d else 0.0) for d in _CONCEPT_DIMS}
        grader = _grader(defects=[], entailment=True, dim_scores=low)
        converged, error_class, _, meta = _run(grader)
        assert not converged
        assert error_class == "weighted_score_below_threshold"
        assert meta["weighted_score"] == 3.5
        assert meta["weighted_threshold"] == 6.0

    def test_high_score_passes(self):
        """concept 全维满分 → 加权分 10.0 ≥6.0 → runner converged=True。"""
        high = {d: 1.0 for d in _CONCEPT_DIMS}
        grader = _grader(defects=[], entailment=True, dim_scores=high)
        converged, _, _, _ = _run(grader)
        assert converged

    def test_threshold_boundary(self):
        """总分 6.5（>= threshold 6.0 → pass；pass meta 不含 weighted_score，只有 reject 才含）。"""
        # H3=0 + 其它满分 → 10-3.5=6.5
        boundary = {d: (0.0 if "H3" in d else 1.0) for d in _CONCEPT_DIMS}
        grader = _grader(defects=[], entailment=True, dim_scores=boundary)
        converged, _, _, _ = _run(grader)
        # 6.5 >= 6.0 → pass（pass meta 不带 weighted_score，故不在此断言该 key）
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


class TestRepairPathAbsent:
    """P1 重构（drop-repair-residency）：repair 收敛路径已从 runner 删除。

    原 TestRepairConvergedConceptGates 验证「repair 收敛后复查 concept 硬门，
    防 poisoned-trace/低分被 repair 绕过」——repair 循环删除后该绕过路径本身
    不复存在。本类改钉三件事：
      ① runner 不再 import/调用 repair 循环入口（源码级）
      ② initial defect → 直接终态（blocking_defects_present），无 repair 可言
      ③ 无 defects 路径的 concept 硬门不受本次重构影响（entailment 门照常拒）
    """

    def test_runner_does_not_call_repair_loop(self):
        """源码级：runner.py 不得 import/调用 run_repair_loop。"""
        runner_src = (
            Path(__file__).resolve().parents[2]
            / "src" / "akb2" / "quality" / "runner.py"
        ).read_text(encoding="utf-8")
        assert "from src.akb2.quality.repair import run_repair_loop" not in runner_src
        assert "run_repair_loop(" not in runner_src

    def test_initial_defect_is_terminal_no_repair_bypass(self):
        """initial defect（concept 车道）→ converged=False + blocking_defects_present。

        旧语义「defect → repair → 收敛后复查硬门」已删；新语义 defect 即终态，
        poisoned-trace/低分不再可能经 repair 绕过（绕过路径不存在本身即防线）。
        """
        from src.akb2.quality.checks_structural import Defect
        from src.akb2.quality.runner import _run_quality_segments

        defect = Defect(dimension="x", location="L", defect="d", suggestion="s")
        initial_grader = _grader(defects=[defect], entailment=False, dim_scores={})
        with patch(
            "src.akb2.quality.checks_structural.run_candidate_self_gate", return_value=[]
        ), patch(
            "src.akb2.quality.grader.run_grader_detailed", return_value=initial_grader
        ), patch(
            "src.akb2.quality.repair.run_repair_loop"
        ) as mock_repair:
            converged, error_class, _, _ = _run_quality_segments(
                "candidate", "/tmp/x.md", "raw", config=MagicMock(),
                content_lane="concept", floor_result=_pass_floor(),
            )
        assert not converged
        assert error_class == "blocking_defects_present"
        mock_repair.assert_not_called()

    def test_no_defects_concept_gates_still_enforced(self):
        """无 defects → concept 硬门照常（entailment=false → poisoned_trace 拒）。

        锁「本次重构只删 repair 路径，不动无缺陷路径的 concept 硬门」。
        """
        grader = _grader(defects=[], entailment=False)
        converged, error_class, _, _ = _run(grader)
        assert not converged
        assert error_class == "poisoned_trace_entailment"
