#!/usr/bin/env python3
"""Harvest 反馈统计脚本 — wave9/g7/sc-fb-stats。

输入恰三源：
1. harvest-ledger（committed 记录）
2. trial journal（reconcile 事件）
3. quality-ledger（质量事件）

折叠器复用 fold_reconcile。除此三源外不进任何输入。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 身份断言：此 fold_reconcile 与 akb2.harvest.trial_verify.fold_reconcile 是同一函数对象
from akb2.harvest.trial_verify import fold_reconcile

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read_jsonl(path: str) -> tuple[list[dict], int]:
    """读取 JSONL 文件，返回 (dict 列表, 损坏行数)。损坏行被跳过但显式计数，不静默丢弃。"""
    p = Path(path)
    if not p.exists():
        return [], 0
    rows: list[dict] = []
    corrupted = 0
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                corrupted += 1
                continue
    return rows, corrupted


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------


def harvest_feedback(
    harvest_ledger_path: str,
    trial_journal_path: str,
    quality_ledger_path: str,
) -> dict:
    """生成 harvest 反馈统计。

    Args:
        harvest_ledger_path: harvest-ledger.jsonl 路径（committed 记录源）。
        trial_journal_path: trial journal reconcile 事件 JSONL 路径。
        quality_ledger_path: quality-ledger.jsonl 路径。

    Returns:
        dict: 反馈统计结果，含 fold_state、各源计数、quality 摘要、交叉引用。
    """
    # 1. 读三源
    harvest_rows, harvest_corrupted = _read_jsonl(harvest_ledger_path)
    trial_events, trial_corrupted = _read_jsonl(trial_journal_path)
    quality_rows, quality_corrupted = _read_jsonl(quality_ledger_path)

    # 2. 从 harvest-ledger 提取 committed 记录
    committed_records = [r for r in harvest_rows if r.get("event") == "committed"]

    # 3. 折叠 trial 事件
    try:
        state = fold_reconcile(trial_events, committed_records=committed_records)
    except Exception as exc:
        return {
            "fold_state": {"error": str(exc)},
            "counts": {
                "harvest_committed": len(committed_records),
                "trial_events": len(trial_events),
                "quality_entries": len(quality_rows),
                "corrupted_lines": {
                    "harvest_ledger": harvest_corrupted,
                    "trial_journal": trial_corrupted,
                    "quality_ledger": quality_corrupted,
                },
            },
            "quality_summary": {},
            "cross_reference": {},
        }

    # 4. quality 摘要：按 event 类型分组
    quality_by_event: dict[str, int] = {}
    for row in quality_rows:
        ev = row.get("event", "unknown")
        quality_by_event[ev] = quality_by_event.get(ev, 0) + 1

    # 5. 交叉引用：trial 事件与 quality 条目的 content_hash 重叠分析
    trial_content_hashes: set[str] = set()
    for ev in trial_events:
        ch = ev.get("content_hash", "")
        if ch:
            trial_content_hashes.add(ch)
    quality_content_hashes: set[str] = set()
    for row in quality_rows:
        ch = row.get("content_hash", "")
        if ch:
            quality_content_hashes.add(ch)
    overlapped = trial_content_hashes & quality_content_hashes
    trial_only = trial_content_hashes - quality_content_hashes
    quality_only = quality_content_hashes - trial_content_hashes

    return {
        "fold_state": {
            "phase": state.phase.name,
            "resolved_hash": state.resolved_hash,
            "resolved_details": state.resolved_details,
        },
        "counts": {
            "harvest_committed": len(committed_records),
            "trial_events": len(trial_events),
            "quality_entries": len(quality_rows),
            "corrupted_lines": {
                "harvest_ledger": harvest_corrupted,
                "trial_journal": trial_corrupted,
                "quality_ledger": quality_corrupted,
            },
        },
        "quality_summary": {
            "total": len(quality_rows),
            "by_event": quality_by_event,
        },
        "cross_reference": {
            "overlapped_content_hashes": len(overlapped),
            "trial_only_content_hashes": len(trial_only),
            "quality_only_content_hashes": len(quality_only),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：读三源 → 产出反馈统计 → 打印 JSON。"""
    args = argv or sys.argv
    if len(args) != 4:
        print(
            f"用法: python3 {__file__} <harvest-ledger> <trial-journal> <quality-ledger>",
            file=sys.stderr,
        )
        return 2

    result = harvest_feedback(args[1], args[2], args[3])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
