#!/usr/bin/env python3
"""Harvest 反馈统计脚本 — wave9/g7/sc-fb-stats + wave1/g4/p2a 联结与复扫。

输入恰三源：
1. harvest-ledger（committed 记录；raw 数据源钉死为其 ``raw_abspath`` 字段）
2. trial journal（reconcile 事件）
3. quality-ledger（质量事件）

折叠器复用 fold_reconcile。除此三源外不进任何输入。

p2a-sc1-runid-join：输出按 harvest_run_id exact-key 联结 raw——
trial/quality 行先按 ``harvest_run_id`` 精确匹配 committed 行；缺 run_id
的行经 ``content_key`` 二级映射兜底；两者皆不可联结时计入顶层
``raw_join.unmatched``（含其自带 run_id 清单，供排障），不静默丢弃。
不做前缀/子串匹配。

p2a-sc2-output-rescan：CLI 输出前对 json.dumps 全文复扫 redaction——
与 assembler 落盘扫描共用同一编译模式族（经 ``redaction.scan_text``
薄封装复用，本脚本零模式定义、零替换实现）。默认 Reject：命中即
exit 3，消息只点名位置与模式类、不回显命中值；``--redact`` 时替换
``[REDACTED]`` 后输出。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 复扫与 assembler 同一规则集：只经薄封装，不复制任何 pattern
from akb2.harvest.redaction import RedactionPolicy, RedactionRejectedError, scan_text

# 身份断言：此 fold_reconcile 与 akb2.harvest.trial_verify.fold_reconcile 是同一函数对象
from akb2.harvest.trial_verify import ResolverError, fold_reconcile

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
                parsed = json.loads(line)
            except json.JSONDecodeError:
                corrupted += 1
                continue
            if not isinstance(parsed, dict):
                corrupted += 1
                continue
            rows.append(parsed)
    return rows, corrupted


# ---------------------------------------------------------------------------
# run_id 联结(p2a-sc1)
# ---------------------------------------------------------------------------


def join_raw_by_run_id(
    committed_records: list[dict],
    trial_events: list[dict],
    quality_rows: list[dict],
) -> dict:
    """按 harvest_run_id exact-key 联结 raw 数据源。

    raw 数据源钉死为 committed 行的 ``raw_abspath`` 字段,不从任何其他
    路径推导。联结顺序:

    1. 行自带 ``harvest_run_id`` 且与某 committed 行**逐字相等** → 联结
       (via=run_id;不做前缀/子串匹配);
    2. 行**缺 run_id**(键缺失/None/空串)时,若 ``content_key`` 与某 committed
       行相等 → 二级映射联结(via=content_key)。键在且值不是 None/空串
       (包括 0、False 等非空 falsy)即视为自带 run_id,未逐字命中
       committed 时**不得**走 content_key 兜底——否则 run-B 的反馈会经共享
       content_key 错记到 run-A 名下;
    3. 两者皆不可联结 → unmatched,计数与其自带 run_id 一并进顶层
       ``raw_join.unmatched``,不静默丢弃。

    Returns:
        dict: ``by_run_id``(每个 committed run_id 的 raw 路径与两源联结
        计数) + 联结途径计数 + unmatched 明细。
    """
    # exact-key 表:run_id -> raw_abspath(数据源钉死 committed 行)
    raw_by_run_id: dict[str, str] = {}
    run_id_by_content_key: dict[str, str] = {}
    for rec in committed_records:
        rid = str(rec.get("harvest_run_id", "") or "")
        if not rid:
            continue
        raw_by_run_id[rid] = str(rec.get("raw_abspath", "") or "")
        ck = str(rec.get("content_key", "") or "")
        if ck:
            run_id_by_content_key[ck] = rid

    def _present_rid(row: dict) -> str | None:
        """行自带 run_id 的规范化。缺键 / None / "" → None(视为缺 run_id,
        允许 content_key 兜底);其余值——含 0、False 等非空 falsy——一律
        返回 str(value),视为自带 run_id(禁止兜底)。"""
        raw = row.get("harvest_run_id")
        if raw is None or raw == "":
            return None
        return str(raw)

    def resolve(row: dict) -> tuple[str, str] | None:
        """单行联结裁定:返回 (run_id, via);不可联结返回 None。

        「缺 run_id」仅指键缺失、值为 None 或空串——只有这三种情形允许
        content_key 兜底。键在且值不是 None/""(包括 0、False 等非空
        falsy 值)一律视为自带 run_id:逐字命中 committed → via=run_id;
        未命中 → unmatched,不得走 content_key,防止跨 run 错记
        (0/False 曾被 ``or ""` 吞成缺 run_id 而错走兜底)。
        """
        rid = _present_rid(row)
        if rid is not None:
            if rid in raw_by_run_id:
                return rid, "run_id"
            return None
        ck = str(row.get("content_key", "") or "")
        if ck and ck in run_id_by_content_key:
            return run_id_by_content_key[ck], "content_key"
        return None

    by_run_id: dict[str, dict] = {
        rid: {
            "raw_abspath": path,
            "trial_events_joined": 0,
            "quality_entries_joined": 0,
        }
        for rid, path in raw_by_run_id.items()
    }
    joined_via_run_id = 0
    joined_via_content_key = 0
    unmatched_trial = 0
    unmatched_quality = 0
    unmatched_trial_run_ids: list[str] = []
    unmatched_quality_run_ids: list[str] = []
    unmatched_samples: list[dict] = []

    def _sample_unmatched(row: dict) -> None:
        """不可联结行采样入 unmatched.samples(与 unmatched 计数同一裁定路径;
        最多 5 行防输出爆炸,行内凭证由下游输出复扫把关)。"""
        if len(unmatched_samples) < 5:
            unmatched_samples.append(row)

    for ev in trial_events:
        hit = resolve(ev)
        if hit is None:
            unmatched_trial += 1
            rid = _present_rid(ev)
            if rid is not None:
                unmatched_trial_run_ids.append(rid)
            _sample_unmatched(ev)
            continue
        rid, via = hit
        by_run_id[rid]["trial_events_joined"] += 1
        if via == "run_id":
            joined_via_run_id += 1
        else:
            joined_via_content_key += 1

    for row in quality_rows:
        hit = resolve(row)
        if hit is None:
            unmatched_quality += 1
            rid = _present_rid(row)
            if rid is not None:
                unmatched_quality_run_ids.append(rid)
            _sample_unmatched(row)
            continue
        rid, via = hit
        by_run_id[rid]["quality_entries_joined"] += 1
        if via == "run_id":
            joined_via_run_id += 1
        else:
            joined_via_content_key += 1

    return {
        "by_run_id": by_run_id,
        "joined_via_run_id": joined_via_run_id,
        "joined_via_content_key": joined_via_content_key,
        "unmatched": {
            "trial_events": unmatched_trial,
            "quality_entries": unmatched_quality,
            "trial_run_ids": list(dict.fromkeys(unmatched_trial_run_ids)),
            "quality_run_ids": list(dict.fromkeys(unmatched_quality_run_ids)),
            "samples": unmatched_samples,
        },
    }


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
        dict: 反馈统计结果，含 fold_state、各源计数、quality 摘要、交叉引用、
        raw_join（run_id exact-key 联结与 unmatched 明细）。
    """
    # 1. 读三源
    harvest_rows, harvest_corrupted = _read_jsonl(harvest_ledger_path)
    trial_events, trial_corrupted = _read_jsonl(trial_journal_path)
    quality_rows, quality_corrupted = _read_jsonl(quality_ledger_path)

    # 2. 从 harvest-ledger 提取 committed 记录
    committed_records = [r for r in harvest_rows if r.get("event") == "committed"]

    # 3. run_id 联结(与 fold 成败无关,两条返回路径都带;不可联结行采样
    # 也在联结器内与计数同路径产出,防止两处裁定漂移)
    raw_join = join_raw_by_run_id(committed_records, trial_events, quality_rows)

    # 4. 折叠 trial 事件
    try:
        state = fold_reconcile(trial_events, committed_records=committed_records)
    except ResolverError as exc:
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
            "raw_join": raw_join,
        }

    # 5. quality 摘要：按 event 类型分组
    quality_by_event: dict[str, int] = {}
    for row in quality_rows:
        ev = row.get("event", "unknown")
        quality_by_event[ev] = quality_by_event.get(ev, 0) + 1

    # 6. 交叉引用：trial 事件与 quality 条目的 content_hash 重叠分析
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
        "raw_join": raw_join,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：读三源 → 产出反馈统计 → 输出前复扫 redaction → 打印 JSON。

    默认 Reject：输出含凭证形态即 exit 3（消息只点名位置与模式类，
    不回显命中值，stdout 不落任何输出）。``--redact``：替换 [REDACTED]
    后输出。
    """
    args = argv or sys.argv
    redact = False
    if len(args) == 5 and args[4] == "--redact":
        redact = True
    elif len(args) != 4:
        print(
            f"用法: python3 {__file__} <harvest-ledger> <trial-journal> "
            f"<quality-ledger> [--redact]",
            file=sys.stderr,
        )
        return 2

    result = harvest_feedback(args[1], args[2], args[3])
    output = json.dumps(result, ensure_ascii=False, indent=2)
    policy = RedactionPolicy.Redact if redact else RedactionPolicy.Reject
    try:
        scanned, _findings = scan_text(output, policy=policy)
    except RedactionRejectedError as exc:
        print(f"harvest_feedback: {exc}", file=sys.stderr)
        return 3
    print(scanned)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
