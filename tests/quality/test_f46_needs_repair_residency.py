"""test_f46_needs_repair_residency.py — needs_repair 驻留通路**缺席**测试。

P1 重构（drop-repair-residency，2026-08）：needs_repair 驻留池已从 runner
生产代码删除。历史背景:驻留池生还率约 6%(358 篇约 20 篇生还),却养出
197 篇僵尸和 62 篇误判终态——内容性失败被反复补丁拖死,入库率被拖住。

本文件钉住「机制不存在」:
    ① 驻留常量/目录/函数族从 runner 生产代码消失(absent 断言,防回退)
    ② needs_repair 事件写入路径从生产代码消失(源码级)
    ③ 历史账本读容忍:旧 needs_repair 事件(无终态)不触发主扫描重跑
       (防 197 篇僵尸无界重启);F26 requeue 仍可手动重开
    ④ 终态语义:blocking 缺陷直接终态(repair 循环已删,无中间态)

突变验证:把 NEEDS_REPAIR_SUBDIR / needs_repair 驻留写入加回 runner 必红。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.akb2.quality.runner import _is_processed

_RUNNER_SRC = (
    Path(__file__).resolve().parents[2] / "src" / "akb2" / "quality" / "runner.py"
)


def _runner_src() -> str:
    return _RUNNER_SRC.read_text(encoding="utf-8")


def _mk_ledger(tmp_path: Path) -> Path:
    ledger = tmp_path / "quality-ledger.jsonl"
    ledger.write_text("", encoding="utf-8")
    return ledger


def _append_event(ledger: Path, content_hash: str, event: str, **details) -> None:
    entry = {
        "loop_id": "loop1",
        "node": "repair_loop",
        "event": event,
        "content_hash": content_hash,
        "ts": "2026-07-12T00:00:00Z",
        "details": details,
    }
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# ① 驻留机制缺席:常量 / 子目录 / 函数族全部从生产代码消失
# ---------------------------------------------------------------------------


class TestResidencyMechanismAbsent:
    def test_needs_repair_constants_absent(self) -> None:
        """驻留预算常量族必须不存在(写回即红——SC mutation 防线)。"""
        src = _runner_src()
        for banned in (
            "NEEDS_REPAIR_MAX_RETAKES",
            "NEEDS_REPAIR_SUBDIR",
            "NEEDS_REPAIR_MIN_INTERVAL_H",
            "NEEDS_REPAIR_PER_CYCLE",
            "NEEDS_REPAIR_STALL_REQUEUE_MAX",
            "_NEEDS_REPAIR_STALL_REASON_PREFIX",
        ):
            assert banned not in src, f"驻留常量 {banned} 不得回到 runner 生产代码"

    def test_needs_repair_subdir_literal_absent(self) -> None:
        """驻留目录字面量 needs-repair 不得出现在 runner(写入路径已删)。"""
        src = _runner_src()
        assert "needs-repair" not in src, "驻留子目录字面量不得回到 runner"

    def test_residency_function_family_absent(self) -> None:
        """驻留函数族(路由/持久化/扫描/重掷/升级/超龄)必须整体消失。"""
        src = _runner_src()
        for fn in (
            "_route_to_residency",
            "_run_residency_rethrow",
            "_run_residency_cycle",
            "_scan_residency",
            "_persist_resident",
            "_load_resident",
            "_purge_resident",
            "_escalate_stalled_resident",
            "_expire_capped_resident",
            "_stall_requeue_used",
            "_needs_repair_event_count",
            "_is_resident",
            "_is_content_no_converge",
        ):
            assert fn not in src, f"驻留函数 {fn} 不得回到 runner 生产代码"


# ---------------------------------------------------------------------------
# ② needs_repair 事件写入路径消失(源码级)
# ---------------------------------------------------------------------------


class TestNeedsRepairEventWritePathAbsent:
    def test_no_needs_repair_event_emission(self) -> None:
        """生产代码不得再**写** needs_repair 事件。

        精确匹配 _log_event(..., "needs_repair", ...) 事件名形态;
        _is_processed 的历史读容忍(elif ev == "needs_repair")不在此列。
        """
        src = _runner_src()
        import re

        emissions = re.findall(r'_log_event\([^)]*?"needs_repair"', src, re.DOTALL)
        assert not emissions, (
            "runner 不得再落 needs_repair 事件——终态只剩 finalize/abort(或 requeue)"
        )

    def test_no_stall_escalation_requeue_reason(self) -> None:
        """F50 stall 升级的 requeue 理由前缀必须消失。"""
        src = _runner_src()
        assert "needs_repair_stall_redistill" not in src


# ---------------------------------------------------------------------------
# ③ 历史账本读容忍(防僵尸无界重启) + F26 requeue 重开
# ---------------------------------------------------------------------------


class TestHistoricalNeedsRepairTolerance:
    def test_legacy_needs_repair_event_keeps_processed(self, tmp_path: Path) -> None:
        """旧机制时代的 needs_repair 事件(无终态)→ 视为已处理,不重跑。

        反证:若无读容忍,删除驻留扫描后 197 篇僵尸会被主扫描当新文章
        全链重掷——无界重启,违反宪法 P1 有界。
        """
        ledger = _mk_ledger(tmp_path)
        _append_event(ledger, "h1", "intake_raw")
        _append_event(ledger, "h1", "needs_repair")
        assert _is_processed("h1", str(ledger)) is True

    def test_manual_requeue_reopens_legacy_needs_repair(self, tmp_path: Path) -> None:
        """F26 手动 requeue 在 needs_repair 事件之后 → 重开全链(可处理)。"""
        ledger = _mk_ledger(tmp_path)
        _append_event(ledger, "h1", "needs_repair")
        _append_event(ledger, "h1", "requeue")
        assert _is_processed("h1", str(ledger)) is False

    def test_terminal_after_needs_repair_stays_processed(self, tmp_path: Path) -> None:
        """needs_repair 后有终态(finish/abort)→ 终态语义不变。"""
        ledger = _mk_ledger(tmp_path)
        _append_event(ledger, "h1", "needs_repair")
        _append_event(ledger, "h1", "abort")
        assert _is_processed("h1", str(ledger)) is True


# ---------------------------------------------------------------------------
# ④ 终态语义:blocking 缺陷直接终态,无中间态
# ---------------------------------------------------------------------------


class TestNoIntermediateState:
    def test_blocking_defects_are_terminal(self) -> None:
        """blocking 缺陷存在 → 直接 converged=False + blocking_defects_present,
        repair 循环入口不再被调用(SC1 核心断言)。"""
        from src.akb2.quality.checks_structural import Defect
        from src.akb2.quality.grader import PRIMARY_GRADER_MODEL, GraderResult
        from src.akb2.quality.integrity_floor import FloorResult
        from src.akb2.quality.runner import _run_quality_segments

        defect = Defect(
            dimension="A7-图谱证据",
            location="图谱关系/evidence/x",
            defect="图谱 evidence 缺少 provenance",
            suggestion="补 provenance",
        )
        grader = GraderResult(
            defects=[],
            model_used="gpt-5.4",
            suspended=False,
            primary_model=PRIMARY_GRADER_MODEL,
            content_lane="concept",
            interpretation_entailment=True,
            dimension_scores={},
        )
        with patch(
            "src.akb2.quality.checks_structural.run_candidate_self_gate",
            return_value=[defect],
        ), patch(
            "src.akb2.quality.grader.run_grader_detailed", return_value=grader
        ), patch(
            "src.akb2.quality.repair.run_repair_loop"
        ) as mock_repair:
            converged, error_class, error_detail, meta = _run_quality_segments(
                "candidate",
                "/tmp/x.md",
                "raw",
                config=MagicMock(),
                content_lane="concept",
                floor_result=FloorResult(
                    passed=True, failed_floors=[], reason="", content_lane="concept"
                ),
            )

        assert converged is False
        assert error_class == "blocking_defects_present"
        assert "blocking 缺陷" in error_detail
        assert meta["repair_rounds"] == 0
        assert meta["final_defects_summary"][0]["dimension"] == "A7-图谱证据"
        mock_repair.assert_not_called()
