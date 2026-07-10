"""test_f42_retake_gate_widening.py — F2-B retake 触发门放宽(F42)。

glm 全链路审计(2026-07-11):retake 触发 15 次救回 15 次(100%),但旧门挡掉
12 例可救案例——① lane 门只认 concept(4 例 procedural interp-only 白死);
② 单一 fail 门要求 failed_floors 恰等于 [traceability](8 例 trace+F4 同 fail
不触发)。放宽为 (concept|procedural) + `traceability in failed_floors`。

floor 零放宽:retake 只补 traces_to 材料(逐字引文/真锚校验不变),之后
全量重评地板——F4 等其余 floor 仍 fail 则照死(fail-closed)。
"""

from __future__ import annotations

import inspect

from src.akb2.quality import runner


class TestRetakeGateWidened:
    def _block(self) -> str:
        src = inspect.getsource(runner)
        i = src.index("F2-B: traceability 有界 retake")
        return src[i:i + 1600]

    def test_lane_gate_includes_procedural(self) -> None:
        blk = self._block()
        assert 'in ("concept", "procedural")' in blk, (
            "F42①: procedural 的 traceability fail 必须可触发 retake")

    def test_membership_not_exact_match(self) -> None:
        blk = self._block()
        assert "FLOOR_INTERPRETATION_TRACEABILITY in floor_result.failed_floors" in blk, (
            "F42②: trace+F4 同 fail 须先补 trace 再全量重评")
        assert "failed_floors == [FLOOR_INTERPRETATION_TRACEABILITY]" not in blk, (
            "旧单一 fail 门必须移除")

    def test_budget_guard_retained(self) -> None:
        """有界不变:traceability_retake_applied 防重入守卫保留。"""
        blk = self._block()
        assert 'not floor_result.meta.get("traceability_retake_applied")' in blk

    def test_full_floor_reeval_after_retake(self) -> None:
        """fail-closed 链保留:retake 后全量重评地板(F4 仍 fail 则照死)。"""
        blk = self._block()
        assert "evaluate_integrity_floor" in blk
