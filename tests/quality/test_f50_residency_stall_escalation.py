"""test_f50_residency_stall_escalation.py — F46 驻留通路止损升级 + 僵尸死锁修复(F50)。

账本数据裁决(2026-07-20):
- 同文续掷 698 次(retake 1/2/3 = 269/232/197)转正 0 篇;62%(143/232)跨 retake
  缺陷数不降;残留缺陷 57% 为"概念机制清晰度 H3"(提炼欠抽病,同文补丁修不出
  原文没抽到的证据)。对照 F26 全链重掷(重新提炼)33% 转正(F49 刀4 批次 4/12)。
- F46 死锁:打满 MAX_RETAKES 的驻留被 _scan_residency 静默跳过,原设想"由
  rethrow 端记 expired abort"不可达(被跳过的永不再进 rethrow)→ 197 篇僵尸
  永驻池,违反宪法 P4 fail-visible。

修复(判定链零动,只改路由):
1. 扫描端补记 expired:打满预算 → 当场 abort(needs_repair_expired) + 清理。
2. 止损升级:续掷缺陷数不降 → 一次性升级全链重掷(先 abort 后 requeue,镜像
   F39 一生一次预算);有进展仍走 F46 续掷。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from src.akb2.quality import runner
from src.akb2.quality.runner import (
    _NEEDS_REPAIR_STALL_REASON_PREFIX,
    NEEDS_REPAIR_MAX_RETAKES,
    NEEDS_REPAIR_STALL_REQUEUE_MAX,
    _escalate_stalled_resident,
    _is_resident,
    _persist_resident,
    _scan_residency,
    _stall_requeue_used,
)


def _mk_config(tmp_path: Path):
    staging = tmp_path / "staging"
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
                  ts: str = "2026-07-18T00:00:00Z", **details) -> None:
    entry = {"loop_id": "loop1", "node": "repair_loop", "event": event,
             "content_hash": content_hash, "ts": ts, "details": details}
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _events(ledger: Path, h: str) -> list[dict]:
    out = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("content_hash") == h:
            out.append(e)
    return out


class TestBudgetNamed:
    def test_constants(self) -> None:
        assert NEEDS_REPAIR_STALL_REQUEUE_MAX == 1, "止损升级一生一次(宪法 P1)"
        assert NEEDS_REPAIR_MAX_RETAKES == 3, "F46 续掷预算不动"


class TestZombieExpire:
    """死锁修复:打满预算的驻留由扫描端当场 expired + 清理。"""

    def test_capped_resident_expired_and_purged(self, tmp_path: Path) -> None:
        cfg, ledger = _mk_config(tmp_path)
        h = "zombie0000000001"
        for i in range(NEEDS_REPAIR_MAX_RETAKES):
            _append_event(ledger, h, "intake_raw" if i == 0 else "needs_repair",
                          ts=f"2026-07-1{5 + i}T00:00:00Z")
        # intake + 3 needs_repair(上面循环首个是 intake,再补齐到 3 个 needs_repair)
        _append_event(ledger, h, "needs_repair", ts="2026-07-18T01:00:00Z")
        _append_event(ledger, h, "needs_repair", ts="2026-07-18T02:00:00Z")
        _persist_resident(cfg, h, "/tmp/raw.md", "/tmp/cand.md", "text",
                          "concept", "slug-x", [{"d": 1}], 2)
        assert _is_resident(h, cfg.ledger_path)

        picked = _scan_residency(cfg)

        assert h not in picked, "打满预算不得再进重掷清单"
        evs = _events(ledger, h)
        assert evs[-1]["event"] == "abort", "扫描端必须当场补记终态"
        assert evs[-1]["details"]["error_class"] == "needs_repair_expired"
        nr_dir = Path(cfg.staging_dir) / "needs-repair"
        assert not list(nr_dir.glob(f"{h}*")), "驻留文件必须清理(防僵尸)"
        assert not _is_resident(h, cfg.ledger_path), "expired 后不再是驻留"

    def test_under_budget_resident_not_expired(self, tmp_path: Path) -> None:
        cfg, ledger = _mk_config(tmp_path)
        h = "alive00000000001"
        _append_event(ledger, h, "intake_raw", ts="2026-07-15T00:00:00Z")
        _append_event(ledger, h, "needs_repair", ts="2026-07-15T01:00:00Z")
        _persist_resident(cfg, h, "/tmp/raw.md", "/tmp/cand.md", "text",
                          "concept", "slug-y", [{"d": 1}], 2)
        picked = _scan_residency(cfg)
        assert h in picked, "预算内且过间隔的驻留应可重掷"
        assert _events(ledger, h)[-1]["event"] == "needs_repair", "不得误 expired"


class TestStallEscalation:
    def test_stall_appends_abort_then_requeue_and_purges(self, tmp_path: Path) -> None:
        cfg, ledger = _mk_config(tmp_path)
        h = "stall00000000001"
        _append_event(ledger, h, "intake_raw")
        _append_event(ledger, h, "needs_repair")
        _persist_resident(cfg, h, "/tmp/raw.md", "/tmp/cand.md", "text",
                          "concept", "slug-z", [{"d": 1}] * 4, 2)

        ok = _escalate_stalled_resident(
            cfg, h, prior_defect_count=4, new_defect_count=4,
            retake_count=1, error_class="loop1/repair_loop/repair_idle_early_stop",
            error_detail="修复不收敛: 4 项缺陷残留",
        )

        assert ok is True
        evs = _events(ledger, h)
        assert [e["event"] for e in evs[-2:]] == ["abort", "requeue"], (
            "事件序必须 abort→requeue(最后 requeue 才会被视为未处理重跑)")
        assert evs[-1]["details"]["reason"].startswith(
            _NEEDS_REPAIR_STALL_REASON_PREFIX)
        assert evs[-2]["details"]["prior_defect_count"] == 4, "fail-visible 带对比"
        nr_dir = Path(cfg.staging_dir) / "needs-repair"
        assert not list(nr_dir.glob(f"{h}*")), "升级后驻留文件清理"
        # 升级后:非驻留、且 _is_processed=False(可被主扫描全链重跑)
        assert not _is_resident(h, cfg.ledger_path)
        assert not runner._is_processed(h, cfg.ledger_path), (
            "requeue 收尾必须使 hash 回到可处理状态(全链重掷)")

    def test_budget_once_per_lifetime(self, tmp_path: Path) -> None:
        cfg, ledger = _mk_config(tmp_path)
        h = "budget0000000001"
        _append_event(ledger, h, "intake_raw")
        first = _escalate_stalled_resident(
            cfg, h, prior_defect_count=3, new_defect_count=3,
            retake_count=1, error_class="x", error_detail="d")
        second = _escalate_stalled_resident(
            cfg, h, prior_defect_count=3, new_defect_count=5,
            retake_count=2, error_class="x", error_detail="d")
        assert first is True and second is False, "一生一次(镜像 F39)"
        assert _stall_requeue_used(h, cfg.ledger_path)
        requeues = [e for e in _events(ledger, h) if e["event"] == "requeue"]
        assert len(requeues) == NEEDS_REPAIR_STALL_REQUEUE_MAX


class TestRethrowWiring:
    """rethrow 挂点形态锁(inspect):止损在 _route_to_residency 之前判。"""

    def _block(self) -> str:
        src = inspect.getsource(runner._run_residency_rethrow)
        return src

    def test_stall_check_before_route(self) -> None:
        src = self._block()
        assert "F50 止损" in src
        i_stall = src.index("_escalate_stalled_resident(")
        i_route = src.index("return _route_to_residency(")
        assert i_stall < i_route, "止损判定必须先于 F46 正常驻留路由"

    def test_progress_path_preserved(self) -> None:
        """有进展(缺陷数下降)仍走 F46 续掷——prior_n>0 且 new_n>=prior_n 才升级。"""
        src = self._block()
        assert "prior_n > 0 and new_n >= prior_n" in src
