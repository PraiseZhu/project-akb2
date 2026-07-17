"""test_f46_needs_repair_residency.py — F46 needs_repair 驻留通路测试。

覆盖 6 项可观测行为(lead 硬要求):
    ① 不收敛 → needs_repair 事件(非 abort)
    ② 3 掷耗尽 → expired abort
    ③ 收敛 → 正常 promote 链(mock 到 promote 入口)
    ④ 间隔守卫(MIN_INTERVAL_H)
    ⑤ _is_processed 三态语义(终态/驻留/未处理)
    ⑥ 预算具名(MAX_RETAKES/MIN_INTERVAL_H/PER_CYCLE)

判定链零动:驻留只给"再掷一轮"入口,不碰 grader/floor/A层/五闸。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.akb2.quality import runner
from src.akb2.quality.runner import (
    NEEDS_REPAIR_MAX_RETAKES,
    NEEDS_REPAIR_MIN_INTERVAL_H,
    NEEDS_REPAIR_PER_CYCLE,
    _is_processed,
    _is_resident,
    _needs_repair_dir,
    _needs_repair_event_count,
    _persist_resident,
    _route_to_residency,
    _run_residency_cycle,
    _run_residency_rethrow,
    _scan_residency,
)


def _mk_config(tmp_path: Path):
    """Stub config: staging_dir + ledger_path + grader_result_dir 全指向 tmp_path。"""
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "needs-repair").mkdir(parents=True, exist_ok=True)
    ledger = staging / "quality-ledger.jsonl"
    ledger.write_text("", encoding="utf-8")

    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.staging_dir = str(staging)
    cfg.ledger_path = str(ledger)
    cfg.grader_result_dir = str(staging / "grader-results")
    return cfg, ledger


def _append_event(ledger: Path, content_hash: str, event: str,
                  ts: str = "2026-07-12T00:00:00Z", **details) -> None:
    entry = {
        "loop_id": "loop1",
        "node": event if event in ("admission", "repair_loop", "promote",
                                   "finalize", "integrity_floor") else "repair_loop",
        "event": event,
        "content_hash": content_hash,
        "ts": ts,
        "details": details,
    }
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# ⑥ 预算具名(宪法 P1)
# ---------------------------------------------------------------------------


class TestBudget:
    def test_named_constants(self) -> None:
        assert NEEDS_REPAIR_MAX_RETAKES == 3
        assert NEEDS_REPAIR_MIN_INTERVAL_H == 20
        assert NEEDS_REPAIR_PER_CYCLE == 4  # F49 提速 2→4


# ---------------------------------------------------------------------------
# ⑤ _is_processed 三态语义
# ---------------------------------------------------------------------------


class TestIsProcessedThreeState:
    def test_resident_is_processed_and_resident(self, tmp_path: Path) -> None:
        cfg, ledger = _mk_config(tmp_path)
        _append_event(ledger, "h1", "needs_repair")
        # 驻留中:主扫描跳过(_is_processed=True),由驻留扫描接管(_is_resident=True)
        assert _is_processed("h1", str(ledger)) is True
        assert _is_resident("h1", str(ledger)) is True

    def test_terminal_is_processed_not_resident(self, tmp_path: Path) -> None:
        cfg, ledger = _mk_config(tmp_path)
        _append_event(ledger, "h1", "finalize")
        assert _is_processed("h1", str(ledger)) is True
        assert _is_resident("h1", str(ledger)) is False

    def test_expired_abort_not_resident(self, tmp_path: Path) -> None:
        cfg, ledger = _mk_config(tmp_path)
        _append_event(ledger, "h1", "needs_repair")
        _append_event(ledger, "h1", "abort", reason="needs_repair_expired")
        # 超龄 abort 后:终态(_is_processed=True),不再是驻留(_is_resident=False)
        # → 恢复可被 F26 requeue 手动重开(语义不变)
        assert _is_processed("h1", str(ledger)) is True
        assert _is_resident("h1", str(ledger)) is False

    def test_unprocessed(self, tmp_path: Path) -> None:
        cfg, ledger = _mk_config(tmp_path)
        _append_event(ledger, "h1", "intake_raw")
        assert _is_processed("h1", str(ledger)) is False
        assert _is_resident("h1", str(ledger)) is False

    def test_requeue_after_needs_repair_reopens(self, tmp_path: Path) -> None:
        """F26 requeue 手动重开驻留 → 重新 intake 全链(_is_processed=False)。"""
        cfg, ledger = _mk_config(tmp_path)
        _append_event(ledger, "h1", "needs_repair")
        _append_event(ledger, "h1", "requeue")
        assert _is_processed("h1", str(ledger)) is False
        assert _is_resident("h1", str(ledger)) is False


# ---------------------------------------------------------------------------
# ① 不收敛 → needs_repair(非 abort)+ ② 3 掷耗尽 → expired abort
# ---------------------------------------------------------------------------


def _quality_meta_nonconverge(final_text: str) -> dict:
    return {
        "final_text": final_text,
        "repair_rounds": 2,
        "final_defects_summary": [{"dimension": "mechanism", "defect": "组件罗列"}],
        "repair_trace_paths": [],
    }


class TestRouteToResidency:
    def test_content_nonconverge_routes_to_needs_repair_not_abort(
        self, tmp_path: Path
    ) -> None:
        """① 不收敛 + 预算内 → needs_repair 事件(非 abort),final_text 持久化。"""
        cfg, ledger = _mk_config(tmp_path)
        result = _route_to_residency(
            config=cfg, content_hash="h1", raw_path="/raw/x.md",
            candidate_path="/cand/x.md", content_lane="concept", slug="x",
            rounds=2,
            final_defects_summary=[{"dimension": "mechanism", "defect": "组件罗列"}],
            quality_meta=_quality_meta_nonconverge("final-text-body"),
            error_class="distill_no_converge",
            error_detail="修复不收敛: 1 项缺陷残留",
        )
        # 断言:final_status=needs_repair(非 failed/abort)
        assert result["final_status"] == "needs_repair"
        assert result["needs_repair_retake"] == 1
        # 账本:needs_repair 事件(非 abort)
        events = [json.loads(x) for x in ledger.read_text().splitlines()]
        assert events[-1]["event"] == "needs_repair"
        assert events[-1]["node"] == "repair_loop"
        assert events[-1]["details"]["retake_count"] == 1
        assert events[-1]["details"]["max_retakes"] == NEEDS_REPAIR_MAX_RETAKES
        # final_text 持久化(不丢进度硬要求)
        text_path = _needs_repair_dir(cfg) / "h1.md"
        meta_path = _needs_repair_dir(cfg) / "h1.json"
        assert text_path.read_text(encoding="utf-8") == "final-text-body"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["content_lane"] == "concept"
        assert meta["raw_path"] == "/raw/x.md"

    def test_budget_exhausted_routes_to_expired_abort(self, tmp_path: Path) -> None:
        """② 3 掷耗尽 → expired abort(reason=needs_repair_expired)+ 清理驻留文件。"""
        cfg, ledger = _mk_config(tmp_path)
        # 预写 3 次 needs_rerepair(预算耗尽)
        for i in range(NEEDS_REPAIR_MAX_RETAKES):
            _append_event(ledger, "h1", "needs_repair", ts=f"2026-07-{10+i}T00:00:00Z")
        assert _needs_repair_event_count("h1", str(ledger)) == NEEDS_REPAIR_MAX_RETAKES
        # 预存驻留文件(应被 expired 路径清理)
        _persist_resident(cfg, "h1", "/raw/x.md", "/cand/x.md", "final-text",
                          "concept", "x", [], 2)
        text_path = _needs_repair_dir(cfg) / "h1.md"
        assert text_path.exists()
        result = _route_to_residency(
            config=cfg, content_hash="h1", raw_path="/raw/x.md",
            candidate_path="/cand/x.md", content_lane="concept", slug="x",
            rounds=2, final_defects_summary=[],
            quality_meta=_quality_meta_nonconverge("final-text"),
            error_class="distill_no_converge", error_detail="修复不收敛",
        )
        # 断言:expired abort
        assert result["final_status"] == "failed"
        assert result["needs_repair_expired"] is True
        events = [json.loads(x) for x in ledger.read_text().splitlines()]
        aborts = [e for e in events if e["event"] == "abort"]
        assert aborts and aborts[-1]["details"]["reason"] == "needs_repair_expired"
        assert aborts[-1]["details"]["retake_count"] == NEEDS_REPAIR_MAX_RETAKES
        # 驻留文件清理(防目录膨胀)
        assert not text_path.exists()


# ---------------------------------------------------------------------------
# ③ 收敛 → 正常 promote 链(mock 到 promote 入口)
# ---------------------------------------------------------------------------


class TestResidencyRethrowConverge:
    def test_converge_rethrow_runs_promote_chain(self, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
        """③ 重掷收敛 → 走完全链下游(promote 五闸 → finalize),同函数同判定不开旁门。"""
        cfg, ledger = _mk_config(tmp_path)
        # 预存驻留候选
        _persist_resident(cfg, "h1", str(tmp_path / "raw.md"),
                          str(tmp_path / "cand.md"), "final-text", "concept",
                          "x", [{"dimension": "mechanism"}], 2)
        (tmp_path / "raw.md").write_text("raw-content", encoding="utf-8")
        _append_event(ledger, "h1", "needs_repair", ts="2026-07-10T00:00:00Z")

        # mock 重函数:不跑真机 LLM
        monkeypatch.setattr(runner, "_load_vault_concept_index",
                            lambda *a, **k: {"known_concepts": [], "pending_queue": []})
        called: dict = {}

        def fake_quality_segments(candidate_text, candidate_path, raw_content,
                                  config, grader_result_path, content_lane="",
                                  floor_result=None, known_concepts=None,
                                  pending_queue=None):
            called["quality"] = True
            assert candidate_text == "final-text"  # 从 final_text 续,不重新 intake
            return True, "", "", {
                "repair_rounds": 1, "final_defects_summary": [],
                "repair_trace_paths": [], "final_text": "final-text",
            }

        monkeypatch.setattr(runner, "_run_quality_segments", fake_quality_segments)

        def fake_promote(candidate_path, config, grader_result_path=None):
            called["promote"] = True
            return True, "active", "ok"

        monkeypatch.setattr(runner, "_run_promote_segment", fake_promote)

        result = _run_residency_rethrow(cfg, "h1")
        # 断言:收敛 → promoted,走 promote 链
        assert result["final_status"] == "promoted"
        assert result["needs_repair_resolved"] is True
        assert called.get("quality") is True
        assert called.get("promote") is True
        # 账本:repair_loop(converged, needs_repair_resolved=True) + promote + finalize
        events = [json.loads(x) for x in ledger.read_text().splitlines()]
        assert any(e["event"] == "repair_loop" and e["details"].get("needs_repair_resolved")
                    for e in events)
        assert any(e["event"] == "promote" for e in events)
        assert any(e["event"] == "finalize" for e in events)
        # 收敛后清理驻留文件
        assert not (_needs_repair_dir(cfg) / "h1.md").exists()


# ---------------------------------------------------------------------------
# ④ 间隔守卫(MIN_INTERVAL_H)
# ---------------------------------------------------------------------------


class TestIntervalGuard:
    def test_recent_needs_repair_not_scannable(self, tmp_path: Path) -> None:
        """④ 间隔 < MIN_INTERVAL_H → 不重掷(让 grader 骰子跨会话)。"""
        cfg, ledger = _mk_config(tmp_path)
        # 1h 前刚驻留 → 间隔未到
        _persist_resident(cfg, "h1", "/raw.md", "/cand.md", "ft", "concept", "x", [], 2)
        _append_event(ledger, "h1", "needs_repair", ts="2026-07-12T00:00:00Z")
        # _now() 是真实当前时间(2026-07-12),1h 前的事件间隔不足 20h
        # 用一个明确的近期 ts(相对当前)构造:写一个 1h 前的 ts
        from src.akb2.quality.runner import _now
        recent = _now().strftime("%Y-%m-%dT%H:00:00Z")  # 当天,间隔 < 20h
        # 重写 ledger 用近期 ts
        ledger.write_text("", encoding="utf-8")
        _append_event(ledger, "h1", "needs_repair", ts=recent)
        assert _scan_residency(cfg) == []

    def test_old_needs_repair_scannable(self, tmp_path: Path) -> None:
        """间隔 ≥ MIN_INTERVAL_H → 可重掷。"""
        cfg, ledger = _mk_config(tmp_path)
        _persist_resident(cfg, "h1", "/raw.md", "/cand.md", "ft", "concept", "x", [], 2)
        # 25h 前(≥ 20h 间隔)
        old_ts = "2026-07-10T00:00:00Z"
        _append_event(ledger, "h1", "needs_repair", ts=old_ts)
        scanned = _scan_residency(cfg)
        assert "h1" in scanned

    def test_per_cycle_cap(self, tmp_path: Path) -> None:
        """每轮最多 PER_CYCLE 篇。"""
        cfg, ledger = _mk_config(tmp_path)
        for i in range(NEEDS_REPAIR_PER_CYCLE + 2):
            ch = f"h{i}"
            _persist_resident(cfg, ch, "/raw.md", "/cand.md", "ft", "concept", "x", [], 2)
            _append_event(ledger, ch, "needs_repair", ts="2026-07-10T00:00:00Z")
        scanned = _scan_residency(cfg)
        assert len(scanned) == NEEDS_REPAIR_PER_CYCLE


# ---------------------------------------------------------------------------
# 异常兜底:驻留扫描抛错不得杀死主循环
# ---------------------------------------------------------------------------


class TestResidencyScanErrorHandling:
    def test_rethrow_error_caught_and_fail_visible(self, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
        """主循环 7×24 生命线:驻留扫描异常不杀主循环(catch + ledger fail-visible)。"""
        cfg, ledger = _mk_config(tmp_path)
        _persist_resident(cfg, "h1", "/raw.md", "/cand.md", "ft", "concept", "x", [], 2)
        _append_event(ledger, "h1", "needs_repair", ts="2026-07-10T00:00:00Z")

        def boom(*a, **k):
            raise RuntimeError("simulated residency explosion")

        monkeypatch.setattr(runner, "_run_residency_rethrow", boom)
        # _run_residency_cycle 必须 catch,不抛
        results = _run_residency_cycle(cfg)
        assert len(results) == 1
        assert results[0]["final_status"] == "error"
        # fail-visible:ledger 记 abort needs_repair reason=residency_rethrow_error
        events = [json.loads(x) for x in ledger.read_text().splitlines()]
        aborts = [e for e in events if e["event"] == "abort"
                  and e["details"].get("reason") == "residency_rethrow_error"]
        assert aborts
