"""test_f51_extract_timeout_infra_requeue.py — extract 超时进 F39 + retake 代际重置(F51)。

账本实锤(2026-07-18~21):84/84 extract_failed 全部是 LLM 超时(调用超时 3/3 /
extract_worker_timeout),≈21 篇已过准入的合格候选每天白死。根因两个:
1. maker 超时后确定性兜底 fail-closed,abort 现场只把兜底的"0 组件"内容样
   文本传给 _maybe_auto_infra_requeue,infra 信号埋在 maker_primary_error
   里没进探测 → F39 永不触发。
2. 指纹表缺 "调用超时" / "extract_worker_timeout" 两种账本实际形态。

另修 F50 半生效:_needs_repair_event_count 数全史,requeue(全链重掷出新文本)
后不清零 → 重掷者新文本只拿 1/3 驻留额度。改为代际计数(requeue 处清零);
有界性不破——每 hash 的 requeue 来源各有一生一次预算(F39/F50)。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from src.akb2.quality import runner
from src.akb2.quality.runner import (
    _is_infra_error,
    _maybe_auto_infra_requeue,
    _needs_repair_event_count,
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


def _append_event(ledger: Path, content_hash: str, event: str, **details) -> None:
    entry = {"loop_id": "loop1", "node": "repair_loop", "event": event,
             "content_hash": content_hash, "ts": "2026-07-18T00:00:00Z",
             "details": details}
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


class TestInfraMarkers:
    def test_timeout_variants_are_infra(self) -> None:
        """账本两种实际超时形态必须命中(F51 修复核心)。"""
        assert _is_infra_error("抽取调用失败（3/3）: LLM 调用超时 (60.0s): deepseek-v4-pro")
        assert _is_infra_error("extract_worker_timeout: 抽取子进程超过总时限 210.0s")

    def test_fallback_content_text_not_infra(self) -> None:
        """兜底 fail-closed 的内容样文本单独出现不触发(内容死因不重审)。"""
        assert not _is_infra_error(
            "concept deterministic fallback fail-closed: 0 组件 / 0 eligible 因果边"
        )

    def test_probe_with_maker_error_joined(self) -> None:
        """extract abort 的探测串 = error_detail + maker_primary_error 拼接后命中。"""
        probe = (
            "concept deterministic fallback fail-closed: 0 组件 | "
            "抽取调用失败（3/3）: LLM 调用超时 (60.0s): deepseek-v4-pro"
        )
        assert _is_infra_error(probe)


class TestExtractAbortWiring:
    """挂点形态锁(inspect):extract abort 现场必须把 maker_primary_error 并入探测。"""

    def test_infra_probe_includes_maker_primary_error(self) -> None:
        src = inspect.getsource(runner)
        i = src.index("infra_probe = ")
        blk = src[i:i + 400]
        assert 'distill_meta.get("maker_primary_error")' in blk
        assert "_maybe_auto_infra_requeue(config, content_hash, infra_probe)" in src

    def test_old_detail_only_probe_removed_at_extract_site(self) -> None:
        """收窄到 extract abort 段:grader 死亡现场(error_detail 本身即 infra
        文本)仍可用 str(error_detail),不在本断言范围。"""
        src = inspect.getsource(runner)
        i = src.index("details = _distill_abort_details(")
        blk = src[i:i + 1200]
        assert (
            "_maybe_auto_infra_requeue(config, content_hash, str(error_detail))"
            not in blk
        ), "extract abort 现场不得只探测 error_detail(infra 信号在 maker_primary_error)"
        assert "_maybe_auto_infra_requeue(config, content_hash, infra_probe)" in blk


class TestAutoRequeueBudget:
    def test_timeout_death_requeued_once(self, tmp_path: Path) -> None:
        cfg, ledger = _mk_config(tmp_path)
        h = "timeout000000001"
        _append_event(ledger, h, "intake_raw")
        probe = "0 组件 fail-closed | 抽取调用失败（3/3）: LLM 调用超时 (60.0s)"

        first = _maybe_auto_infra_requeue(cfg, h, probe)
        second = _maybe_auto_infra_requeue(cfg, h, probe)

        assert first is True and second is False, "F39 预算一生一次不变"
        requeues = [e for e in _events(ledger, h) if e["event"] == "requeue"]
        assert len(requeues) == 1
        assert requeues[0]["details"]["reason"].startswith("auto_infra_retry(F39)")


class TestRetakeGenerationReset:
    def test_count_resets_after_requeue(self, tmp_path: Path) -> None:
        """F51: requeue = 新一代文本,驻留额度重新计数。"""
        cfg, ledger = _mk_config(tmp_path)
        h = "gen0000000000001"
        for _ in range(3):
            _append_event(ledger, h, "needs_repair")
        assert _needs_repair_event_count(h, cfg.ledger_path) == 3

        _append_event(ledger, h, "requeue")
        assert _needs_repair_event_count(h, cfg.ledger_path) == 0, "重掷后清零"

        _append_event(ledger, h, "needs_repair")
        assert _needs_repair_event_count(h, cfg.ledger_path) == 1

    def test_no_requeue_keeps_lifetime_count(self, tmp_path: Path) -> None:
        """无 requeue 时语义与 F46 原版一致(僵尸 expired 判定不受影响)。"""
        cfg, ledger = _mk_config(tmp_path)
        h = "nogen00000000001"
        _append_event(ledger, h, "intake_raw")
        _append_event(ledger, h, "needs_repair")
        _append_event(ledger, h, "abort")
        _append_event(ledger, h, "needs_repair")
        assert _needs_repair_event_count(h, cfg.ledger_path) == 2
