"""test_f48_residency_gate_signature.py — F46 驻留门签名匹配修复(F48)。

账本实锤(2026-07-13):repair 的 _fail_closed 落账的是 record_failure 完整签名
"loop1/repair_loop/<class>",而 F46 驻留门只认裸 "distill_no_converge" →
上线后驻留通路 0 触发:62 例 repair_idle_early_stop 内容性不收敛全部误走
终态 abort(9 例仅 1 项缺陷残留),needs_repair 事件全库 0 条。

修复:门改签名族匹配(_is_content_no_converge)——distill_no_converge(轮数
耗尽)与 repair_idle_early_stop(空转早停)同为内容性不收敛(文本完好、
final_text/final_defects 俱全)均适格驻留;repair_route_failed / grader_all_down
等过程性死亡仍走 abort + F39。判定链零动,只修路由。
"""

from __future__ import annotations

import inspect

from src.akb2.quality import runner
from src.akb2.quality.runner import _is_content_no_converge


class TestContentNoConvergeMatch:
    def test_full_signature_matches(self) -> None:
        """账本实际形态:loop/node/class 全签名必须命中(修复的核心)。"""
        assert _is_content_no_converge("loop1/repair_loop/distill_no_converge")
        assert _is_content_no_converge("loop1/repair_loop/repair_idle_early_stop")

    def test_bare_class_matches(self) -> None:
        """裸类名(runner 1790 行 .get 默认值路径)同样命中。"""
        assert _is_content_no_converge("distill_no_converge")
        assert _is_content_no_converge("repair_idle_early_stop")

    def test_process_deaths_excluded(self) -> None:
        """过程性死亡不适格驻留(仍走 abort + F39 全链重跑)。"""
        assert not _is_content_no_converge("loop1/repair_loop/repair_route_failed")
        assert not _is_content_no_converge("loop1/repair_loop/grader_all_down")
        assert not _is_content_no_converge("grader_fallback_not_allowed")
        assert not _is_content_no_converge("integrity_floor_failed")

    def test_none_and_empty_excluded(self) -> None:
        """fail-closed:异常输入不适格(宁 abort 不误驻留)。"""
        assert not _is_content_no_converge(None)
        assert not _is_content_no_converge("")


class TestGateUsesHelper:
    def _block(self) -> str:
        src = inspect.getsource(runner)
        i = src.index("F46/F48: repair 不收敛分流")
        return src[i:i + 900]

    def test_gate_calls_helper(self) -> None:
        blk = self._block()
        assert "if not _is_content_no_converge(error_class):" in blk, (
            "F48: 驻留门必须走签名族匹配(兼容全签名前缀)")

    def test_old_bare_equality_removed(self) -> None:
        src = inspect.getsource(runner)
        assert 'error_class != "distill_no_converge"' not in src, (
            "旧裸串等值门必须移除(它对 _fail_closed 全签名永不命中)")

    def test_eligible_classes_bounded(self) -> None:
        """适格族有界:只有两类内容性不收敛,不得悄悄扩到过程性死亡。"""
        assert runner._RESIDENCY_ELIGIBLE_CLASSES == frozenset(
            {"distill_no_converge", "repair_idle_early_stop"}
        )
