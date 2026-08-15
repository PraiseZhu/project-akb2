"""test_f48_residency_gate_signature.py — 驻留门签名匹配的**缺席**测试。

P1 重构（drop-repair-residency，2026-08）：F48 驻留签名族门
(_is_content_no_converge / _RESIDENCY_ELIGIBLE_CLASSES)随 F46 驻留池整体删除。
历史背景:F48 曾把"裸串等值门"修成"签名族匹配门"让驻留通路真正触发;
如今驻留通路本身已删,该门及其适格族必须从 runner 生产代码消失。

本文件钉住:
    ① _is_content_no_converge 门函数与适格族常量不存在
    ② 分流点不再存在——内容性失败与过程性死亡统一 abort 终态
    ③ 旧门形态(裸串等值/签名族匹配)都不得回潮
"""

from __future__ import annotations

from pathlib import Path

_RUNNER_SRC = (
    Path(__file__).resolve().parents[2] / "src" / "akb2" / "quality" / "runner.py"
)


def _runner_src() -> str:
    return _RUNNER_SRC.read_text(encoding="utf-8")


class TestResidencyGateAbsent:
    def test_gate_helper_absent(self) -> None:
        """F48 门函数 _is_content_no_converge 必须从生产代码消失。"""
        src = _runner_src()
        assert "_is_content_no_converge" not in src

    def test_eligible_classes_frozenset_absent(self) -> None:
        """适格族常量 _RESIDENCY_ELIGIBLE_CLASSES 必须消失。"""
        src = _runner_src()
        assert "_RESIDENCY_ELIGIBLE_CLASSES" not in src

    def test_no_bare_or_family_gate_forms(self) -> None:
        """旧门两种形态都不得回潮:裸串等值 与 签名族匹配。"""
        src = _runner_src()
        assert 'error_class != "distill_no_converge"' not in src, (
            "旧裸串等值门不得回来(它属于已删除的驻留分流)"
        )
        assert "if not _is_content_no_converge(error_class):" not in src, (
            "签名族驻留分流不得回来——内容性失败直接 abort"
        )

    def test_unified_abort_is_terminal(self) -> None:
        """分流删除后:质量失败统一走 abort(repair_loop)终态 + F39 infra 探测。

        源码级形态锁:abort 落账点存在且后随 _maybe_auto_infra_requeue。
        """
        src = _runner_src()
        i = src.index('质量不达标: ')
        blk = src[max(0, i - 1600):i]
        assert '_log_event(config, "abort", "repair_loop"' in blk
        assert "_maybe_auto_infra_requeue(config, content_hash" in blk
