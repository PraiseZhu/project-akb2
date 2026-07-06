"""requeue.py — F26 有界 re-enqueue CLI(手动触发,白名单,fail-visible)。

背景:runner 把 finalize/abort 当终态,已判死的 raw 永不重审。当管线能力
修复后(如 F18-F25),需要一个**宪法合规**的通道让指定文章重新过审:
    - 手动触发(不注册任何自动化触发器,宪法:新增触发器须人工确认)
    - 有界(单次最多 REQUEUE_MAX_PER_RUN 篇;显式文件名白名单,不支持 glob)
    - fail-visible(逐篇打印结局;任一失败退出码非 0)
    - 幂等(账本 append_event 按行 SHA 去重;同文重复 requeue 且未重审时跳过)

语义(与 runner._is_processed 配对):向账本追加 `requeue` 事件;终态之后
出现 requeue → 该 raw 重新进 daemon 扫描队列;重审产生的新终态再次生效。
**不改任何判定标准**——重审仍走全套 fail-closed 闸门。

用法:
    python3 -m akb2.quality.requeue \\
        --raw-file addy-osmani--de4f29655cc7--raw--2026-07-05.md \\
        --reason "F25 准入口径校准后重审" \\
        [--vault-root "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0"]

vault-root 默认取 AKB2_VAULT_ROOT 环境变量;二者皆缺 → fail-closed 退出
(路径契约:不猜路径,不静默指向错误盘)。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# 有界(宪法 P1):单次调用最多 requeue 的篇数,超出直接拒绝整个调用
REQUEUE_MAX_PER_RUN: int = 10

_TERMINAL_EVENTS = {"finalize", "abort"}


def _compute_raw_hash(raw_path: str) -> str:
    """与 runner._compute_raw_hash 同口径:sha256(路径字符串)[:16]。"""
    return hashlib.sha256(raw_path.encode("utf-8")).hexdigest()[:16]


def _ledger_state(content_hash: str, ledger_path: Path) -> str:
    """返回该 hash 在账本中的状态:'terminal' / 'pending_requeue' / 'absent' / 'in_flight'。

    - terminal: 最近一次相关事件是 finalize/abort(可 requeue)
    - pending_requeue: 终态后已有 requeue(重复 requeue 无意义,跳过)
    - absent: 账本中无此 hash(从未处理——本来就在队列里,不需要 requeue)
    - in_flight: 有事件但无终态(正在处理/中断,不允许 requeue 添乱)
    """
    if not ledger_path.exists():
        return "absent"
    seen = False
    processed = False
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("content_hash") != content_hash:
                continue
            seen = True
            if entry.get("event") in _TERMINAL_EVENTS:
                processed = True
            elif entry.get("event") == "requeue":
                processed = False
    if not seen:
        return "absent"
    return "terminal" if processed else "pending_requeue_or_in_flight"


def run_requeue(
    raw_files: list[str],
    reason: str,
    vault_root: str,
) -> int:
    """执行有界 requeue。返回退出码(0=全部成功;1=存在失败,fail-visible)。"""
    if len(raw_files) > REQUEUE_MAX_PER_RUN:
        print(
            f"ERROR: 单次最多 requeue {REQUEUE_MAX_PER_RUN} 篇"
            f"(收到 {len(raw_files)}),拒绝整个调用(有界,宪法 P1)",
            file=sys.stderr,
        )
        return 1

    raw_dir = Path(vault_root) / "raw"
    ledger_path = Path(vault_root) / "staging" / "quality-ledger.jsonl"
    if not raw_dir.is_dir() or not ledger_path.exists():
        print(
            f"ERROR: raw 目录或账本不存在(raw={raw_dir}, ledger={ledger_path})"
            "——fail-closed 退出",
            file=sys.stderr,
        )
        return 1

    from src.akb2.loops.ledger import append_event

    failures = 0
    for name in raw_files:
        base = Path(name).name  # 只认文件名,防路径穿越
        if "--raw--" not in base:
            print(f"[SKIP-FAIL] {base}: 非 --raw-- 命名,拒绝")
            failures += 1
            continue
        raw_path = raw_dir / base
        if not raw_path.is_file():
            print(f"[SKIP-FAIL] {base}: 不在 raw 目录,拒绝")
            failures += 1
            continue
        content_hash = _compute_raw_hash(str(raw_path))
        state = _ledger_state(content_hash, ledger_path)
        if state == "absent":
            print(f"[SKIP-OK] {base}: 账本无终态记录,本就在待处理队列,无需 requeue")
            continue
        if state != "terminal":
            print(f"[SKIP-OK] {base}: 已有未消费的 requeue 或处理中,不重复追加")
            continue
        event = {
            "loop_id": "loop1",
            "node": "requeue",
            "event": "requeue",
            "content_hash": content_hash,
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "details": {
                "raw_filename": base,
                "reason": reason,
                "by": "manual-cli",
            },
        }
        written = append_event(str(ledger_path), event)
        print(
            f"[REQUEUED] {base} hash={content_hash}"
            + ("" if written else " (幂等:同事件已存在)")
        )

    if failures:
        print(f"完成但有 {failures} 篇失败(见上),退出码 1", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AKB2 有界 re-enqueue(手动,白名单)")
    parser.add_argument(
        "--raw-file", action="append", required=True, dest="raw_files",
        help="要重审的 raw 文件名(可多次;只认文件名不认路径/不支持 glob)",
    )
    parser.add_argument("--reason", required=True, help="requeue 理由(入账本,审计用)")
    parser.add_argument(
        "--vault-root", default=os.environ.get("AKB2_VAULT_ROOT", ""),
        help="AKB2 vault 根(默认 env AKB2_VAULT_ROOT;缺失 fail-closed)",
    )
    args = parser.parse_args(argv)

    if not args.vault_root:
        print(
            "ERROR: 未提供 --vault-root 且 AKB2_VAULT_ROOT 未设置——"
            "fail-closed 退出(路径契约:不猜 vault 路径)",
            file=sys.stderr,
        )
        return 1

    return run_requeue(args.raw_files, args.reason, args.vault_root)


if __name__ == "__main__":
    sys.exit(main())
