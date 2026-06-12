"""SC-s2: 账本幂等追加 + 七段生命线字段测试。

约束：
- 同内容重复写入不产生重复行（幂等，SHA-256 去重）
- 七段生命线字段齐全
- 新事件必增行（不同 ts/event 视为不同内容）
"""

from __future__ import annotations

import json
from pathlib import Path

from akb2.loops.ledger import MAX_LEDGER_RETRIES, append_event

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _count_lines(path: Path) -> int:
    """统计有效行数（跳过空行）。"""
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def _read_events(path: Path) -> list[dict]:
    """读取账本全部事件。"""
    if not path.exists():
        return []
    events: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def _make_event(**overrides) -> dict:
    """构造一个最小合法事件。"""
    base = {
        "loop_id": "loop1",
        "node": "intake_raw",
        "event": "intake_raw",
        "content_hash": "abc123",
        "ts": "2026-06-12T10:00:00Z",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 幂等追加测试
# ---------------------------------------------------------------------------


def test_append_new_event_returns_true(tmp_path: Path) -> None:
    """新事件写入返回 True，文件有 1 行。"""
    ledger = tmp_path / "test.jsonl"
    event = _make_event()
    assert append_event(str(ledger), event) is True
    assert _count_lines(ledger) == 1


def test_duplicate_event_returns_false(tmp_path: Path) -> None:
    """同内容重复写入返回 False，文件仍只有 1 行。"""
    ledger = tmp_path / "test.jsonl"
    event = _make_event()
    assert append_event(str(ledger), event) is True
    assert append_event(str(ledger), event) is False  # 幂等
    assert _count_lines(ledger) == 1


def test_new_event_with_different_ts_returns_true(tmp_path: Path) -> None:
    """不同 ts（不同内容 hash）的事件应视为新事件，追加新行。"""
    ledger = tmp_path / "test.jsonl"
    e1 = _make_event(ts="2026-06-12T10:00:00Z")
    e2 = _make_event(ts="2026-06-12T10:01:00Z")
    assert append_event(str(ledger), e1) is True
    assert append_event(str(ledger), e2) is True
    assert _count_lines(ledger) == 2


def test_new_event_with_different_event_type_returns_true(tmp_path: Path) -> None:
    """不同 event 类型应视为新事件，追加新行。"""
    ledger = tmp_path / "test.jsonl"
    e1 = _make_event(event="intake_raw")
    e2 = _make_event(event="admission")
    assert append_event(str(ledger), e1) is True
    assert append_event(str(ledger), e2) is True
    assert _count_lines(ledger) == 2


def test_multiple_events_no_duplicates(tmp_path: Path) -> None:
    """多次追加不同事件，每行唯一。"""
    ledger = tmp_path / "test.jsonl"
    events_seven = [
        _make_event(node="intake_raw", event="intake_raw"),
        _make_event(node="admission", event="admission"),
        _make_event(node="distill_extract", event="distill_extract"),
        _make_event(node="distill_compose", event="distill_compose"),
        _make_event(node="grade_structural", event="grade_structural"),
        _make_event(node="grade_llm", event="grade_llm"),
        _make_event(node="finalize", event="finalize"),
    ]
    for e in events_seven:
        assert append_event(str(ledger), e) is True
    assert _count_lines(ledger) == 7

    # 重复写入全拒绝
    for e in events_seven:
        assert append_event(str(ledger), e) is False
    assert _count_lines(ledger) == 7


# ---------------------------------------------------------------------------
# 七段生命线字段测试
# ---------------------------------------------------------------------------


def test_seven_lifecycle_events_present(tmp_path: Path) -> None:
    """账本中包含完整的七段事件 + abort。"""
    ledger = tmp_path / "test.jsonl"
    lifecycle = [
        ("intake_raw", "intake_raw"),
        ("admission", "admission"),
        ("distill_extract", "distill_extract"),
        ("distill_compose", "distill_compose"),
        ("grade_structural", "grade_structural"),
        ("grade_llm", "grade_llm"),
        ("repair_loop", "repair_loop"),
        ("promote", "promote"),
        ("finalize", "finalize"),
        ("abort", "abort"),
    ]
    for node, event in lifecycle:
        e = _make_event(node=node, event=event)
        assert append_event(str(ledger), e) is True

    events = _read_events(ledger)
    event_names = {(evt["node"], evt["event"]) for evt in events}
    for node, event in lifecycle:
        assert (node, event) in event_names, f"missing ({node}, {event})"


def test_events_have_required_fields(tmp_path: Path) -> None:
    """每条写入事件必须包含 loop_id/node/event/content_hash/ts。"""
    ledger = tmp_path / "test.jsonl"
    e = _make_event()
    append_event(str(ledger), e)

    written = _read_events(ledger)[0]
    for field in ["loop_id", "node", "event", "content_hash", "ts"]:
        assert field in written, f"missing field: {field}"


def test_budget_field_is_preserved(tmp_path: Path) -> None:
    """budget 字段（可选）在写入后保留。"""
    ledger = tmp_path / "test.jsonl"
    e = _make_event(budget={"token_count": 1500, "elapsed_sec": 2.5})
    append_event(str(ledger), e)
    written = _read_events(ledger)[0]
    assert written.get("budget", {}).get("token_count") == 1500
    assert written.get("budget", {}).get("elapsed_sec") == 2.5


# ---------------------------------------------------------------------------
# 重试常量测试
# ---------------------------------------------------------------------------

def test_max_ledger_retries_is_named_constant() -> None:
    """MAX_LEDGER_RETRIES 是具名常量（宪法 P4）。"""
    assert isinstance(MAX_LEDGER_RETRIES, int)
    assert MAX_LEDGER_RETRIES >= 1
    assert MAX_LEDGER_RETRIES <= 5  # 合理上限
