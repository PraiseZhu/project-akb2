"""SC-s3: 故障签名计数 + 第 3 次升级测试。

约束：
- 同签名出现时 count 累加
- count 达到 3 时写入 escalation 标记行
- 前两次记录不丢弃
- 不同签名独立计数
"""

from __future__ import annotations

import json
from pathlib import Path

from akb2.loops.signatures import ESCALATION_THRESHOLD, record_failure


def _read_all(path: Path) -> list[dict]:
    """读取 failures.jsonl 全部行。"""
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _count_signature_rows(path: Path, error_class: str, node: str) -> int:
    """统计某签名的普通签名行数（不含 escalation 行）。"""
    rows = _read_all(path)
    return sum(
        1
        for r in rows
        if r.get("error_class") == error_class
        and r.get("node") == node
        and "escalation" not in r
    )


# ---------------------------------------------------------------------------
# 基础记录测试
# ---------------------------------------------------------------------------


def test_first_record_count_is_one(tmp_path: Path) -> None:
    """首次记录故障签名，count=1，不升级。"""
    path = tmp_path / "failures.jsonl"
    result = record_failure(str(path), "fetch_fail", "intake_raw")
    assert result["count"] == 1
    assert result["escalated"] is False
    assert _count_signature_rows(path, "fetch_fail", "intake_raw") == 1


def test_second_record_count_is_two(tmp_path: Path) -> None:
    """同签名再次记录，count=2，不升级。"""
    path = tmp_path / "failures.jsonl"
    record_failure(str(path), "fetch_fail", "intake_raw")
    result = record_failure(str(path), "fetch_fail", "intake_raw")
    assert result["count"] == 2
    assert result["escalated"] is False
    assert _count_signature_rows(path, "fetch_fail", "intake_raw") == 2


def test_third_record_triggers_escalation(tmp_path: Path) -> None:
    """第 3 次同签名记录触发升级，count=3，escalated=True。"""
    path = tmp_path / "failures.jsonl"
    record_failure(str(path), "fetch_fail", "intake_raw")
    record_failure(str(path), "fetch_fail", "intake_raw")
    result = record_failure(str(path), "fetch_fail", "intake_raw")
    assert result["count"] == 3
    assert result["escalated"] is True

    # 文件中共 4 行：3 条签名记录 + 1 条 escalation 标记
    rows = _read_all(path)
    assert len(rows) == 4

    # 前三行是普通签名行，不含 escalation
    sigs = [r for r in rows if "escalation" not in r]
    assert len(sigs) == 3
    assert sigs[0]["count"] == 1
    assert sigs[1]["count"] == 2
    assert sigs[2]["count"] == 3

    # 第四行是 escalation 标记行
    esc_rows = [r for r in rows if "escalation" in r]
    assert len(esc_rows) == 1
    esc = esc_rows[0]
    assert esc["escalation"]["escalated"] is True
    assert esc["escalation"]["reason"] is not None


def test_previous_records_preserved_on_escalation(tmp_path: Path) -> None:
    """升级后前两次记录完整保留，不丢弃。"""
    path = tmp_path / "failures.jsonl"
    record_failure(str(path), "fetch_fail", "intake_raw")
    record_failure(str(path), "fetch_fail", "intake_raw")
    record_failure(str(path), "fetch_fail", "intake_raw")

    sigs = [r for r in _read_all(path) if "escalation" not in r]
    assert len(sigs) == 3  # 三条记录全在
    assert sigs[0]["count"] == 1
    assert sigs[1]["count"] == 2
    assert sigs[2]["count"] == 3


# ---------------------------------------------------------------------------
# 不同签名独立计数测试
# ---------------------------------------------------------------------------


def test_different_signatures_independent(tmp_path: Path) -> None:
    """不同签名的计数独立，互不干扰。"""
    path = tmp_path / "failures.jsonl"

    # 签名 A 出现 2 次
    _r1 = record_failure(str(path), "fetch_fail", "intake_raw")
    r2 = record_failure(str(path), "fetch_fail", "intake_raw")
    assert r2["count"] == 2
    assert r2["escalated"] is False

    # 签名 B 出现 3 次
    record_failure(str(path), "anchor_mismatch", "distill_extract")
    record_failure(str(path), "anchor_mismatch", "distill_extract")
    r3 = record_failure(str(path), "anchor_mismatch", "distill_extract")
    assert r3["count"] == 3
    assert r3["escalated"] is True

    # 签名 A 仍不受影响
    assert _count_signature_rows(path, "fetch_fail", "intake_raw") == 2


def test_fourth_record_still_escalates(tmp_path: Path) -> None:
    """第 4 次同签名记录仍触发 escalation。"""
    path = tmp_path / "failures.jsonl"
    for _ in range(3):
        record_failure(str(path), "fetch_fail", "intake_raw")
    result = record_failure(str(path), "fetch_fail", "intake_raw")
    assert result["count"] == 4
    assert result["escalated"] is True


def test_result_contains_signature_string(tmp_path: Path) -> None:
    """返回值含 signature 字段：loop/node/error_class 格式。"""
    path = tmp_path / "failures.jsonl"
    result = record_failure(str(path), "grader_all_down", "grade_llm", loop="loop3")
    assert result["signature"] == "loop3/grade_llm/grader_all_down"


# ---------------------------------------------------------------------------
# 常量测试
# ---------------------------------------------------------------------------

def test_escalation_threshold_is_three() -> None:
    """升级阈值为 3（设计权威 docs/loops-design-decisions.md §7 定）。"""
    assert ESCALATION_THRESHOLD == 3
