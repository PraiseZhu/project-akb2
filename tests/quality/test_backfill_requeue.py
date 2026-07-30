"""test_backfill_requeue.py — 上游 400 误拒批量 backfill requeue 行为测试。

断言绑定可观测行为(不是"能跑通"):
- 指纹筛选:只认 abort、只认 details 里的指纹;非 abort 携带指纹不入选。
- 去重:同 hash 多次误拒只产出一条候选,且 hit_count 记录真实次数。
- 排除:误拒后已 finalize / 已有本 CLI requeue / 当前已在待审队列 → 不重复排队。
- 幂等:--apply 跑第二遍零新增(账本行数不变)。
- 事件结构:reason 前缀 + 原错误摘要 + 可被 runner._is_processed 消费(重开生效)。
- 有界:--limit 生效且截断量可见。
- fail-closed:账本缺失非 0 退出;空 pattern 拒绝。
"""

from __future__ import annotations

import json
from pathlib import Path

import src.akb2.quality.backfill_requeue as backfill_mod
from src.akb2.quality.backfill_requeue import (
    BACKFILL_REASON_PREFIX,
    DEFAULT_PATTERN,
    SKIP_ALREADY_PENDING,
    SKIP_BACKFILL_EXISTS,
    SKIP_FINALIZE_AFTER,
    scan_ledger,
)
from src.akb2.quality.backfill_requeue import main as backfill_main
from src.akb2.quality.runner import _is_processed

_ERR = (
    "车道分类调用失败: LLM API 错误: deepseek-v4-pro — Error code: 400 - "
    '{"error": {"message": "This response_format type is unavailable now"}}'
)


def _ev(event: str, h: str, ts: str = "2026-07-23T03:00:00Z", **details: object) -> dict:
    return {
        "loop_id": "loop1",
        "node": event,
        "event": event,
        "content_hash": h,
        "ts": ts,
        "details": dict(details),
    }


def _infra_abort(h: str, ts: str = "2026-07-23T03:00:00Z") -> dict:
    return _ev("abort", h, ts, reason="准入未通过: topic_relevance", lane_reason=_ERR)


def _content_abort(h: str, ts: str = "2026-07-23T03:00:00Z") -> dict:
    """内容性 abort(质量结论),不带故障指纹。"""
    return _ev("abort", h, ts, reason="准入未通过: 地板不达标")


def _write_ledger(path: Path, events: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestScanFiltering:
    def test_infra_abort_selected(self, tmp_path: Path) -> None:
        led = _write_ledger(tmp_path / "l.jsonl", [_ev("intake_raw", "h1"), _infra_abort("h1")])
        result = scan_ledger(led)
        assert [c.content_hash for c in result.candidates] == ["h1"]

    def test_content_abort_not_selected(self, tmp_path: Path) -> None:
        """内容性死因不是误杀,不得被重排队。"""
        led = _write_ledger(tmp_path / "l.jsonl", [_content_abort("h1")])
        assert scan_ledger(led).candidates == []

    def test_non_abort_event_with_pattern_not_selected(self, tmp_path: Path) -> None:
        """同一故障也会写进 admission 事件;只有 abort 才代表被判死。"""
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [_ev("admission", "h1", lane_reason=_ERR), _ev("finalize", "h1")],
        )
        assert scan_ledger(led).candidates == []

    def test_dedup_multiple_hits_single_candidate(self, tmp_path: Path) -> None:
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _infra_abort("h1", "2026-07-23T03:00:00Z"),
                _ev("requeue", "h1", "2026-07-24T01:00:00Z", reason="人工"),
                _infra_abort("h1", "2026-07-25T03:00:00Z"),
            ],
        )
        result = scan_ledger(led)
        assert len(result.candidates) == 1
        assert result.candidates[0].hit_count == 2
        assert result.candidates[0].first_hit_ts == "2026-07-23T03:00:00Z"

    def test_raw_filename_from_intake_raw(self, tmp_path: Path) -> None:
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _ev("intake_raw", "h1", raw_path="/v/raw/src--aaaa--raw--2026-07-01.md"),
                _infra_abort("h1"),
            ],
        )
        assert scan_ledger(led).candidates[0].raw_filename == "src--aaaa--raw--2026-07-01.md"

    def test_custom_pattern_overrides_default(self, tmp_path: Path) -> None:
        led = _write_ledger(
            tmp_path / "l.jsonl", [_ev("abort", "h1", reason="quota exhausted 429")]
        )
        assert scan_ledger(led, DEFAULT_PATTERN).candidates == []
        assert len(scan_ledger(led, "quota exhausted").candidates) == 1

    def test_corrupt_line_reported_not_silent(self, tmp_path: Path) -> None:
        led = tmp_path / "l.jsonl"
        led.write_text(
            json.dumps(_infra_abort("h1"), ensure_ascii=False) + "\n{ broken\n",
            encoding="utf-8",
        )
        result = scan_ledger(led)
        assert result.corrupt_lines == [2]
        assert len(result.candidates) == 1

    def test_non_dict_details_does_not_crash(self, tmp_path: Path) -> None:
        """账本行由多个模块写入；details 形状异常不得让整次 backfill 崩掉。"""
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                {"loop_id": "loop1", "node": "intake_raw", "event": "intake_raw",
                 "content_hash": "h1", "ts": "2026-07-23T02:00:00Z", "details": "字符串不是 dict"},
                {"loop_id": "loop1", "node": "requeue", "event": "requeue",
                 "content_hash": "h1", "ts": "2026-07-23T02:30:00Z", "details": []},
                _infra_abort("h1", "2026-07-23T03:00:00Z"),
            ],
        )
        result = scan_ledger(led)
        assert [c.content_hash for c in result.candidates] == ["h1"]
        assert result.candidates[0].raw_filename == ""

    def test_candidates_sorted_by_first_hit(self, tmp_path: Path) -> None:
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _infra_abort("late", "2026-07-29T03:00:00Z"),
                _infra_abort("early", "2026-07-23T03:00:00Z"),
            ],
        )
        assert [c.content_hash for c in scan_ledger(led).candidates] == ["early", "late"]


class TestScanExclusions:
    def test_finalize_after_abort_excluded(self, tmp_path: Path) -> None:
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _infra_abort("h1", "2026-07-23T03:00:00Z"),
                _ev("requeue", "h1", "2026-07-26T01:00:00Z", reason="人工"),
                _ev("finalize", "h1", "2026-07-26T02:00:00Z"),
            ],
        )
        result = scan_ledger(led)
        assert result.candidates == []
        assert result.skipped[SKIP_FINALIZE_AFTER] == 1

    def test_finalize_before_abort_does_not_exclude(self, tmp_path: Path) -> None:
        """旧的 finalize 不能豁免之后的误拒(顺序敏感)。"""
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _ev("finalize", "h1", "2026-07-01T00:00:00Z"),
                _ev("requeue", "h1", "2026-07-22T00:00:00Z", reason="人工"),
                _infra_abort("h1", "2026-07-23T03:00:00Z"),
            ],
        )
        result = scan_ledger(led)
        assert [c.content_hash for c in result.candidates] == ["h1"]
        assert result.skipped[SKIP_FINALIZE_AFTER] == 0

    def test_existing_backfill_requeue_excluded(self, tmp_path: Path) -> None:
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _infra_abort("h1", "2026-07-23T03:00:00Z"),
                _ev(
                    "requeue",
                    "h1",
                    "2026-07-30T01:00:00Z",
                    reason=f"{BACKFILL_REASON_PREFIX}: {_ERR}",
                ),
                # 重审后又因别的原因判死 → 当前是终态,但本 CLI 的预算已用完
                _content_abort("h1", "2026-07-30T02:00:00Z"),
            ],
        )
        result = scan_ledger(led)
        assert result.candidates == []
        assert result.skipped[SKIP_BACKFILL_EXISTS] == 1

    def test_manual_requeue_does_not_consume_budget(self, tmp_path: Path) -> None:
        """他人写的 requeue(非本 CLI 前缀)不占本 CLI 的一次预算。"""
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _infra_abort("h1", "2026-07-23T03:00:00Z"),
                _ev("requeue", "h1", "2026-07-24T01:00:00Z", reason="F25 校准后重审"),
                _content_abort("h1", "2026-07-24T02:00:00Z"),
                _infra_abort("h1", "2026-07-25T03:00:00Z"),
            ],
        )
        assert [c.content_hash for c in scan_ledger(led).candidates] == ["h1"]

    def test_already_pending_excluded(self, tmp_path: Path) -> None:
        """已有未消费的 requeue → 已在待审队列,再追加是空操作。"""
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _infra_abort("h1", "2026-07-23T03:00:00Z"),
                _ev("requeue", "h1", "2026-07-24T01:00:00Z", reason="人工"),
            ],
        )
        result = scan_ledger(led)
        assert result.candidates == []
        assert result.skipped[SKIP_ALREADY_PENDING] == 1

    def test_needs_repair_residency_still_candidate(self, tmp_path: Path) -> None:
        """驻留态(needs_repair)在 runner 眼里是已处理,误拒后仍需重开。"""
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [_infra_abort("h1", "2026-07-23T03:00:00Z"),
             _ev("needs_repair", "h1", "2026-07-23T04:00:00Z")],
        )
        assert [c.content_hash for c in scan_ledger(led).candidates] == ["h1"]


class TestProcessedSemanticsMatchRunner:
    """本模块复刻了 runner._is_processed（为避免每 hash 重扫账本）。

    复刻会漂移：runner 改语义而这边不改 → 排除规则静默失准。这些用例把两份实现
    在同一批序列上钉成等价，runner 一改就红。
    """

    SEQUENCES = [
        ["intake_raw"],
        ["abort"],
        ["finalize"],
        ["abort", "requeue"],
        ["abort", "requeue", "abort"],
        ["needs_repair"],
        ["abort", "needs_repair"],
        ["needs_repair", "requeue"],
        ["intake_raw", "admission", "distill_extract"],
        ["finalize", "requeue", "needs_repair"],
    ]

    def test_equivalent_to_runner_is_processed(self, tmp_path: Path) -> None:
        for index, sequence in enumerate(self.SEQUENCES):
            led = _write_ledger(
                tmp_path / f"l{index}.jsonl", [_ev(ev, "h1") for ev in sequence]
            )
            mine = backfill_mod._is_processed(
                [(line, ev, False) for line, ev in enumerate(sequence, start=1)]
            )
            theirs = _is_processed("h1", str(led))
            assert mine is theirs, f"序列 {sequence} 判定分歧: 本模块={mine} runner={theirs}"


class TestCliDryRun:
    def test_dry_run_writes_nothing(self, tmp_path: Path, capsys) -> None:
        led = _write_ledger(tmp_path / "l.jsonl", [_infra_abort("h1"), _infra_abort("h2")])
        before = led.read_text(encoding="utf-8")
        assert backfill_main(["--ledger", str(led)]) == 0
        assert led.read_text(encoding="utf-8") == before
        out = capsys.readouterr().out
        assert "命中候选(可 requeue): 2" in out
        assert "[dry-run] 未写入任何事件" in out
        assert "2026-07-23: 2" in out  # 按日分布
        # 本机有两处账本，mtime 必须可见，否则操作者无法判断是否选错账本
        assert "账本更新时间: " in out

    def test_missing_ledger_fails_closed(self, tmp_path: Path, capsys) -> None:
        assert backfill_main(["--ledger", str(tmp_path / "nope.jsonl")]) == 1
        assert "账本不存在" in capsys.readouterr().err

    def test_empty_pattern_rejected(self, tmp_path: Path, capsys) -> None:
        led = _write_ledger(tmp_path / "l.jsonl", [_infra_abort("h1")])
        assert backfill_main(["--ledger", str(led), "--pattern", "  "]) == 1
        assert "不可为空" in capsys.readouterr().err

    def test_non_positive_limit_rejected(self, tmp_path: Path, capsys) -> None:
        led = _write_ledger(tmp_path / "l.jsonl", [_infra_abort("h1")])
        assert backfill_main(["--ledger", str(led), "--limit", "0"]) == 1
        assert "正整数" in capsys.readouterr().err


class TestCliApply:
    def test_apply_appends_valid_requeue_event(self, tmp_path: Path) -> None:
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _ev("intake_raw", "h1", raw_path="/v/raw/src--aaaa--raw--2026-07-01.md"),
                _infra_abort("h1", "2026-07-23T03:00:00Z"),
            ],
        )
        assert backfill_main(["--ledger", str(led), "--apply"]) == 0
        events = _read_events(led)
        assert len(events) == 3
        appended = events[-1]
        assert appended["event"] == "requeue"
        assert appended["node"] == "requeue"
        assert appended["loop_id"] == "loop1"
        assert appended["content_hash"] == "h1"
        assert appended["ts"].endswith("Z")
        details = appended["details"]
        assert details["reason"].startswith(f"{BACKFILL_REASON_PREFIX}: ")
        assert DEFAULT_PATTERN in details["reason"]  # 携带原错误摘要(宪法 P4)
        assert details["by"] == "backfill-requeue-cli"
        assert details["first_rejected_at"] == "2026-07-23T03:00:00Z"
        assert details["infra_abort_count"] == 1
        assert details["raw_filename"] == "src--aaaa--raw--2026-07-01.md"

    def test_apply_reopens_for_runner(self, tmp_path: Path) -> None:
        """写入后 runner._is_processed 转 False —— 真正被 daemon 重扫(端到端语义)。"""
        led = _write_ledger(tmp_path / "l.jsonl", [_infra_abort("h1")])
        assert _is_processed("h1", str(led)) is True
        assert backfill_main(["--ledger", str(led), "--apply"]) == 0
        assert _is_processed("h1", str(led)) is False

    def test_apply_is_idempotent(self, tmp_path: Path, capsys) -> None:
        """重跑零新增(每 hash 至多一条 requeue)。"""
        led = _write_ledger(tmp_path / "l.jsonl", [_infra_abort("h1"), _infra_abort("h2")])
        assert backfill_main(["--ledger", str(led), "--apply"]) == 0
        after_first = _read_events(led)
        assert len(after_first) == 4
        capsys.readouterr()

        assert backfill_main(["--ledger", str(led), "--apply"]) == 0
        assert _read_events(led) == after_first
        out = capsys.readouterr().out
        assert "命中候选(可 requeue): 0" in out
        assert f"{SKIP_BACKFILL_EXISTS}=2" in out

    def test_limit_bounds_writes_and_reports_truncation(self, tmp_path: Path, capsys) -> None:
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _infra_abort("h1", "2026-07-23T03:00:00Z"),
                _infra_abort("h2", "2026-07-24T03:00:00Z"),
                _infra_abort("h3", "2026-07-25T03:00:00Z"),
            ],
        )
        assert backfill_main(["--ledger", str(led), "--apply", "--limit", "2"]) == 0
        events = _read_events(led)
        requeued = {e["content_hash"] for e in events if e["event"] == "requeue"}
        assert requeued == {"h1", "h2"}  # 按首次误拒时间取最早两条
        out = capsys.readouterr().out
        assert "--limit 截断" in out  # 截断可见,不静默省略
        assert "[apply] 已写入 requeue 事件 2 条" in out

    def test_apply_with_no_candidates_is_noop(self, tmp_path: Path, capsys) -> None:
        led = _write_ledger(tmp_path / "l.jsonl", [_content_abort("h1")])
        before = led.read_text(encoding="utf-8")
        assert backfill_main(["--ledger", str(led), "--apply"]) == 0
        assert led.read_text(encoding="utf-8") == before
        assert "[apply] 无候选" in capsys.readouterr().out

    def test_interrupt_reports_partial_write_and_fails(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Ctrl-C 时必须说清写了多少条并非 0 退出（部分失败可见，宪法 P4）。"""
        import src.akb2.loops.ledger as ledger_mod

        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _infra_abort("h1", "2026-07-23T03:00:00Z"),
                _infra_abort("h2", "2026-07-24T03:00:00Z"),
                _infra_abort("h3", "2026-07-25T03:00:00Z"),
            ],
        )
        real_append = ledger_mod.append_event
        calls: list[int] = []

        def flaky_append(path: str, event: dict) -> bool:
            calls.append(1)
            if len(calls) > 2:
                raise KeyboardInterrupt
            return real_append(path, event)

        monkeypatch.setattr(ledger_mod, "append_event", flaky_append)
        assert backfill_main(["--ledger", str(led), "--apply"]) == 1

        requeued = {e["content_hash"] for e in _read_events(led) if e["event"] == "requeue"}
        assert requeued == {"h1", "h2"}  # 中断前写入的部分保留有效
        captured = capsys.readouterr()
        assert "已写入 requeue 事件 2 条" in captured.out
        assert "已中断：2/3" in captured.err

    def test_apply_prints_progress_for_large_batch(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """长时间无输出会被误判卡死 → 每 APPLY_PROGRESS_INTERVAL 条须报进度。"""
        monkeypatch.setattr(backfill_mod, "APPLY_PROGRESS_INTERVAL", 2)
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _infra_abort("h1", "2026-07-23T03:00:00Z"),
                _infra_abort("h2", "2026-07-24T03:00:00Z"),
                _infra_abort("h3", "2026-07-25T03:00:00Z"),
            ],
        )
        assert backfill_main(["--ledger", str(led), "--apply"]) == 0
        assert "[apply] 进度 2/3" in capsys.readouterr().out

    def test_apply_does_not_touch_other_hashes(self, tmp_path: Path) -> None:
        led = _write_ledger(
            tmp_path / "l.jsonl",
            [_infra_abort("h1"), _content_abort("h2"), _ev("finalize", "h3")],
        )
        assert backfill_main(["--ledger", str(led), "--apply"]) == 0
        requeued = {e["content_hash"] for e in _read_events(led) if e["event"] == "requeue"}
        assert requeued == {"h1"}
