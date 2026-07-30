"""test_inflow_sentinel.py — 入库断流哨兵行为测试。

覆盖(断言绑定可观测行为,不是"能跑通"):
  - 判定三态:有进无出→stalled / 有 finalize→healthy / 无进料→no_intake(降级文案)
  - 窗口边界:恰好在边界上计入,早 1 秒不计入
  - 幂等抑制:同一断流窗口第二次被抑制;--force 强制重发;恢复入库后再断→重新告警
  - fail-visible:发送失败→退出码 1 且文案落 stderr;dry-run 不发送不写状态
  - 数据质量:损坏行 / ts 不可解析被计数并出现在文案里,不静默丢弃
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from akb2.quality.inflow_sentinel import (
    DEFAULT_WINDOW_HOURS,
    MAX_STATE_HISTORY,
    VERDICT_HEALTHY,
    VERDICT_NO_INTAKE,
    VERDICT_STALLED,
    build_alert_text,
    evaluate_ledger,
    main,
    resolve_state_path,
    run_check,
)

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ev(event: str, ts: datetime, *, details: dict | None = None) -> dict:
    return {
        "loop_id": "loop1",
        "node": event,
        "event": event,
        "content_hash": "deadbeef",
        "ts": ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "details": details or {},
    }


def _write_ledger(path: Path, events: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def _hours_ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


class _Recorder:
    """假发送器:记录被发送的文案。"""

    def __init__(self, fail: Exception | None = None):
        self.sent: list[str] = []
        self.fail = fail

    def __call__(self, text: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append(text)


# ---------------------------------------------------------------------------
# 判定三态
# ---------------------------------------------------------------------------


class TestVerdict:
    def test_intake_without_finalize_is_stalled(self, tmp_path):
        """有进无出 = 断流(这正是 2026-07-24 事故形态:进料 + 全 abort + 零 finalize)。"""
        ledger = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _ev("intake_raw", _hours_ago(10)),
                _ev("admission", _hours_ago(10)),
                _ev("abort", _hours_ago(10), details={"reason": "准入未通过: topic_relevance"}),
                _ev("intake_raw", _hours_ago(5)),
                _ev("abort", _hours_ago(5), details={"reason": "准入未通过: topic_relevance"}),
            ],
        )
        v = evaluate_ledger(ledger, now=NOW)
        assert v.verdict == VERDICT_STALLED
        assert v.intake_count == 2
        assert v.finalize_count == 0
        # 中止原因聚合进文案,免得收到告警还得自己去翻账本
        assert v.top_abort_reasons == [("准入未通过: topic_relevance", 2)]

    def test_finalize_in_window_is_healthy(self, tmp_path):
        """窗口内有入库 → 不告警(即使同期也有大量 abort)。"""
        ledger = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _ev("intake_raw", _hours_ago(20)),
                _ev("abort", _hours_ago(19)),
                _ev("intake_raw", _hours_ago(3)),
                _ev("finalize", _hours_ago(2)),
            ],
        )
        v = evaluate_ledger(ledger, now=NOW)
        assert v.verdict == VERDICT_HEALTHY
        assert v.is_alerting is False

    def test_no_intake_is_degraded_verdict(self, tmp_path):
        """零 intake_raw ≠ 入库断流:排查现场不同,文案与排查入口都必须不同。

        且 intake_raw 是 runner 落账的 → 零 intake_raw 既可能是采集停摆、也可能是
        runner 死了,降级文案必须同时给出两个现场(只指 intake 会把人引错方向)。
        """
        ledger = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _ev("intake_raw", _hours_ago(100)),
                _ev("finalize", _hours_ago(99)),
            ],
        )
        v = evaluate_ledger(ledger, now=NOW)
        assert v.verdict == VERDICT_NO_INTAKE
        assert v.is_alerting is True

        stalled_ledger = _write_ledger(
            tmp_path / "s.jsonl", [_ev("intake_raw", _hours_ago(5))]
        )
        stalled = evaluate_ledger(stalled_ledger, now=NOW)

        no_intake_text = build_alert_text(v)
        stalled_text = build_alert_text(stalled)
        assert "取件停滞" in no_intake_text
        assert "入库断流告警" in stalled_text
        # stalled 现场是 runner/LLM;零事件的两个可能现场都要给,否则把人引错方向
        assert "logs/akb2-quality-runner.log" in stalled_text
        assert "logs/intake.log" not in stalled_text
        assert "logs/intake.log" in no_intake_text
        assert "logs/akb2-quality-runner.log" in no_intake_text

    def test_no_intake_text_does_not_claim_zero_events(self, tmp_path):
        """窗口内还有在飞的 repair/grade 事件时,文案不得谎称"账本零事件"。"""
        ledger = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _ev("intake_raw", _hours_ago(200)),
                _ev("grade_llm", _hours_ago(6)),
                _ev("repair_loop", _hours_ago(4)),
            ],
        )
        v = evaluate_ledger(ledger, now=NOW)
        assert v.verdict == VERDICT_NO_INTAKE
        text = build_alert_text(v)
        assert "零事件" not in text
        assert "无新进料且零入库" in text

    def test_missing_ledger_reports_no_intake(self, tmp_path):
        """账本还不存在 → 无进料判定,不抛异常(首次部署形态)。"""
        v = evaluate_ledger(tmp_path / "nope.jsonl", now=NOW)
        assert v.verdict == VERDICT_NO_INTAKE
        assert v.window_counts == {}

    def test_healthy_has_no_alert_text(self, tmp_path):
        ledger = _write_ledger(tmp_path / "l.jsonl", [_ev("finalize", _hours_ago(1))])
        v = evaluate_ledger(ledger, now=NOW)
        with pytest.raises(ValueError):
            build_alert_text(v)


# ---------------------------------------------------------------------------
# 窗口边界
# ---------------------------------------------------------------------------


class TestWindowBoundary:
    def test_event_exactly_on_boundary_counts_in(self, tmp_path):
        ledger = _write_ledger(
            tmp_path / "l.jsonl", [_ev("intake_raw", _hours_ago(DEFAULT_WINDOW_HOURS))]
        )
        v = evaluate_ledger(ledger, now=NOW)
        assert v.intake_count == 1
        assert v.verdict == VERDICT_STALLED

    def test_event_one_second_before_boundary_is_excluded(self, tmp_path):
        ts = NOW - timedelta(hours=DEFAULT_WINDOW_HOURS, seconds=1)
        ledger = _write_ledger(tmp_path / "l.jsonl", [_ev("intake_raw", ts)])
        v = evaluate_ledger(ledger, now=NOW)
        assert v.intake_count == 0
        assert v.verdict == VERDICT_NO_INTAKE

    def test_finalize_just_outside_window_still_stalls(self, tmp_path):
        """窗口外的 finalize 不救场,但会作为"上次入库"进文案。"""
        ledger = _write_ledger(
            tmp_path / "l.jsonl",
            [
                _ev("finalize", _hours_ago(DEFAULT_WINDOW_HOURS + 1)),
                _ev("intake_raw", _hours_ago(2)),
            ],
        )
        v = evaluate_ledger(ledger, now=NOW)
        assert v.verdict == VERDICT_STALLED
        assert v.last_finalize_ts == _hours_ago(DEFAULT_WINDOW_HOURS + 1)
        assert "上次入库: 2026-07-28T11:00:00Z(距今 49.0h)" in build_alert_text(v)

    def test_custom_window_changes_verdict(self, tmp_path):
        """--window-hours 可覆盖:同一账本在 12h 窗口是断流,在 48h 窗口是健康。"""
        ledger = _write_ledger(
            tmp_path / "l.jsonl",
            [_ev("finalize", _hours_ago(20)), _ev("intake_raw", _hours_ago(1))],
        )
        assert evaluate_ledger(ledger, window_hours=48, now=NOW).verdict == VERDICT_HEALTHY
        assert evaluate_ledger(ledger, window_hours=12, now=NOW).verdict == VERDICT_STALLED

    def test_non_positive_window_rejected(self, tmp_path):
        ledger = _write_ledger(tmp_path / "l.jsonl", [_ev("intake_raw", _hours_ago(1))])
        with pytest.raises(ValueError):
            evaluate_ledger(ledger, window_hours=0, now=NOW)


# ---------------------------------------------------------------------------
# 数据质量可见(宪法 P4)
# ---------------------------------------------------------------------------


class TestDataQualityVisible:
    def test_corrupt_and_unparseable_lines_counted_and_surfaced(self, tmp_path):
        path = tmp_path / "l.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(_ev("intake_raw", _hours_ago(1)), ensure_ascii=False) + "\n")
            fh.write("{not json\n")
            fh.write("\n")  # 空行不算损坏
            fh.write(json.dumps({"event": "finalize", "ts": "昨天"}, ensure_ascii=False) + "\n")
        v = evaluate_ledger(path, now=NOW)
        assert v.corrupt_lines == 1
        assert v.unparseable_ts == 1
        # ts 不可解析的 finalize 不被当成"有入库"(否则真断流会被误判健康)
        assert v.verdict == VERDICT_STALLED
        text = build_alert_text(v)
        assert "损坏行 1" in text
        assert "ts 不可解析 1" in text


# ---------------------------------------------------------------------------
# 幂等抑制
# ---------------------------------------------------------------------------


class TestSuppression:
    def _stalled_ledger(self, tmp_path: Path) -> Path:
        return _write_ledger(
            tmp_path / "l.jsonl",
            [_ev("finalize", _hours_ago(80)), _ev("intake_raw", _hours_ago(3))],
        )

    def test_second_check_in_same_episode_is_suppressed(self, tmp_path):
        ledger = self._stalled_ledger(tmp_path)
        state = tmp_path / "state.json"
        rec = _Recorder()

        assert run_check(
            ledger_path=ledger, state_path=state, now=NOW, sender=rec
        ) == 0
        assert len(rec.sent) == 1

        # 6 小时后再查:仍是同一次断流(没有新 finalize)→ 不重复轰炸
        assert run_check(
            ledger_path=ledger,
            state_path=state,
            now=NOW + timedelta(hours=6),
            sender=rec,
        ) == 0
        assert len(rec.sent) == 1

    def test_force_bypasses_suppression(self, tmp_path):
        ledger = self._stalled_ledger(tmp_path)
        state = tmp_path / "state.json"
        rec = _Recorder()
        run_check(ledger_path=ledger, state_path=state, now=NOW, sender=rec)
        run_check(ledger_path=ledger, state_path=state, now=NOW, sender=rec, force=True)
        assert len(rec.sent) == 2

    def test_new_stall_after_recovery_alerts_again(self, tmp_path):
        """恢复入库后再次断流 = 新事件,必须重新告警(否则第二次故障永久静默)。"""
        ledger = self._stalled_ledger(tmp_path)
        state = tmp_path / "state.json"
        rec = _Recorder()
        run_check(ledger_path=ledger, state_path=state, now=NOW, sender=rec)
        assert len(rec.sent) == 1

        # 管线恢复:一条新的 finalize 落账 → 此刻判定健康,不告警
        later = NOW + timedelta(hours=10)
        _write_ledger(
            ledger,
            [
                _ev("finalize", _hours_ago(80)),
                _ev("intake_raw", _hours_ago(3)),
                _ev("finalize", NOW + timedelta(hours=1)),
            ],
        )
        run_check(ledger_path=ledger, state_path=state, now=later, sender=rec)
        assert len(rec.sent) == 1

        # 再过 60 小时又断了 → 参照的"上次入库"变了 → 重新告警
        much_later = NOW + timedelta(hours=60)
        _write_ledger(
            ledger,
            [
                _ev("finalize", NOW + timedelta(hours=1)),
                _ev("intake_raw", NOW + timedelta(hours=20)),
            ],
        )
        run_check(ledger_path=ledger, state_path=state, now=much_later, sender=rec)
        assert len(rec.sent) == 2

    def test_verdict_switch_is_a_new_episode(self, tmp_path):
        """stalled → no_intake 是不同故障(上游也停了),不该被前者的抑制吞掉。"""
        state = tmp_path / "state.json"
        rec = _Recorder()
        stalled = _write_ledger(tmp_path / "a.jsonl", [_ev("intake_raw", _hours_ago(3))])
        run_check(ledger_path=stalled, state_path=state, now=NOW, sender=rec)
        no_intake = _write_ledger(tmp_path / "b.jsonl", [_ev("intake_raw", _hours_ago(300))])
        run_check(ledger_path=no_intake, state_path=state, now=NOW, sender=rec)
        assert len(rec.sent) == 2

    def test_state_history_is_bounded(self, tmp_path):
        state = tmp_path / "state.json"
        rec = _Recorder()
        ledger = tmp_path / "l.jsonl"
        for i in range(MAX_STATE_HISTORY + 5):
            # 每轮换一个"上次入库"时刻 → 每轮都是新 episode,必发
            _write_ledger(
                ledger,
                [
                    _ev("finalize", _hours_ago(80 + i)),
                    _ev("intake_raw", _hours_ago(3)),
                ],
            )
            run_check(ledger_path=ledger, state_path=state, now=NOW, sender=rec)
        assert len(rec.sent) == MAX_STATE_HISTORY + 5
        saved = json.loads(state.read_text(encoding="utf-8"))
        assert len(saved["history"]) == MAX_STATE_HISTORY

    def test_corrupt_state_file_does_not_silence_alert(self, tmp_path):
        """抑制状态损坏时宁可多发一次,也不能因为读不到状态而漏报。"""
        ledger = self._stalled_ledger(tmp_path)
        state = tmp_path / "state.json"
        state.write_text("{broken", encoding="utf-8")
        rec = _Recorder()
        assert run_check(ledger_path=ledger, state_path=state, now=NOW, sender=rec) == 0
        assert len(rec.sent) == 1


# ---------------------------------------------------------------------------
# 发送与退出码(fail-visible)
# ---------------------------------------------------------------------------


class TestSendAndExitCode:
    def test_dry_run_sends_nothing_and_writes_no_state(self, tmp_path, capsys):
        ledger = _write_ledger(tmp_path / "l.jsonl", [_ev("intake_raw", _hours_ago(3))])
        state = tmp_path / "state.json"
        rec = _Recorder()
        rc = run_check(
            ledger_path=ledger, state_path=state, now=NOW, sender=rec, dry_run=True
        )
        assert rc == 0
        assert rec.sent == []
        assert not state.exists()
        assert "入库断流告警" in capsys.readouterr().out

    def test_send_failure_returns_1_and_surfaces_text(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(
            "akb2.quality.inflow_sentinel._local_notify", lambda text: None
        )
        ledger = _write_ledger(tmp_path / "l.jsonl", [_ev("intake_raw", _hours_ago(3))])
        state = tmp_path / "state.json"
        rec = _Recorder(fail=RuntimeError("飞书发送失败 (code=9499)"))
        rc = run_check(ledger_path=ledger, state_path=state, now=NOW, sender=rec)
        assert rc == 1
        err = capsys.readouterr().err
        assert "飞书告警发送失败" in err
        # 发送挂了也要把告警内容留在日志里,否则这条断流就彻底消失了
        assert "入库断流告警" in err
        # 没发出去就不能记抑制状态,否则下次会被"已告警"吞掉
        assert not state.exists()

    def test_healthy_returns_0_without_touching_state(self, tmp_path):
        ledger = _write_ledger(tmp_path / "l.jsonl", [_ev("finalize", _hours_ago(1))])
        state = tmp_path / "state.json"
        rec = _Recorder()
        assert run_check(ledger_path=ledger, state_path=state, now=NOW, sender=rec) == 0
        assert rec.sent == []
        assert not state.exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_dry_run_cli_on_explicit_ledger(self, tmp_path, capsys):
        ledger = _write_ledger(tmp_path / "l.jsonl", [_ev("intake_raw", _hours_ago(3))])
        rc = main(["--ledger", str(ledger), "--dry-run"])
        out = capsys.readouterr().out
        assert rc == 0
        assert f"verdict={VERDICT_STALLED}" in out
        assert "--dry-run" in out

    def test_bad_window_hours_returns_1(self, tmp_path, capsys):
        ledger = _write_ledger(tmp_path / "l.jsonl", [_ev("intake_raw", _hours_ago(3))])
        rc = main(["--ledger", str(ledger), "--window-hours", "0", "--dry-run"])
        assert rc == 1
        assert "断流判定失败" in capsys.readouterr().err

    def test_state_path_lands_outside_synced_dirs(self, tmp_path, monkeypatch):
        """抑制状态必须落机器本地状态根:落同步区会被两台机器互相覆盖。"""
        monkeypatch.setenv("AKB2_STATE_ROOT", str(tmp_path / "state-root"))
        path = resolve_state_path()
        assert path == tmp_path / "state-root" / "inflow-sentinel-state.json"

    def test_state_root_in_synced_dir_is_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AKB2_STATE_ROOT", str(Path.home() / "AI-Agent" / "akb2-state"))
        ledger = _write_ledger(tmp_path / "l.jsonl", [_ev("intake_raw", _hours_ago(3))])
        rec = _Recorder()
        rc = run_check(ledger_path=ledger, now=NOW, sender=rec)
        assert rc == 1
        assert rec.sent == []
