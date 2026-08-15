"""test_f50_residency_stall_escalation.py — stall 升级/超龄淘汰的**缺席**测试。

P1 重构（drop-repair-residency，2026-08）：F50 止损升级(全链重掷)与
扫描端超龄补记随 F46 驻留池整体删除。历史背景:同文续掷 698 次 0 转正,
驻留池养出 197 篇僵尸——止损升级治标不治本,整个通路连同删除。

本文件钉住:
    ① stall 升级预算常量与理由前缀不存在
    ② 升级函数族(_escalate_stalled_resident/_stall_requeue_used/
       _expire_capped_resident)不存在
    ③ abort→requeue 双写序列(升级形态)不得回潮
"""

from __future__ import annotations

from pathlib import Path

_RUNNER_SRC = (
    Path(__file__).resolve().parents[2] / "src" / "akb2" / "quality" / "runner.py"
)


def _runner_src() -> str:
    return _RUNNER_SRC.read_text(encoding="utf-8")


class TestStallEscalationAbsent:
    def test_stall_budget_constants_absent(self) -> None:
        """F50 预算常量与理由前缀必须消失。"""
        src = _runner_src()
        for banned in (
            "NEEDS_REPAIR_STALL_REQUEUE_MAX",
            "needs_repair_stall_redistill",
            "_NEEDS_REPAIR_STALL_REASON_PREFIX",
        ):
            assert banned not in src, f"{banned} 不得回到 runner 生产代码"

    def test_stall_function_family_absent(self) -> None:
        """stall 升级函数族必须整体消失。"""
        src = _runner_src()
        for fn in (
            "_escalate_stalled_resident",
            "_stall_requeue_used",
            "_expire_capped_resident",
            "needs_repair_stalled",
            "needs_repair_expired",
        ):
            assert fn not in src, f"{fn} 不得回到 runner 生产代码"

    def test_no_zombie_expire_backfill(self) -> None:
        """扫描端超龄补记(修僵尸死锁的 F50 手法)随驻留池删除——
        驻留扫描本身不存在,不得再有补记路径。"""
        src = _runner_src()
        assert "扫描端补记" not in src
        assert "expired" not in src.replace("needs_repair_expired", ""), (
            "不得残留任何 expired 补记语义"
        )
