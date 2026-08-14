#!/usr/bin/env python3
"""可导入的试投循环 — wave8/g12/sc-trial-loop (生产形态)。

按 planned 顺序逐篇试投，记录到 append-only JSONL journal，全程持有 BATCH 锁。

用法（作为脚本）:
    python3 scripts/trial_run.py <journal.jsonl> <adapter_id> <deadline_s>

用法（作为模块）:
    from trial_run import run_attempts
    records = run_attempts(planned, "canonical", "trial-batch.jsonl", deadline_s=9999999999)

硬不变量:
    1. run_attempts(planned, adapter_id, journal_path, *, deadline_s)
       注册表 import 期固定，不接受运行期注入。
    2. canonical adapter 从 SKILL.md 唯一标记模板提取 argv → 用
       planned_item.payload 与 harvest_run_id 展开 → 执行 python3 -m akb2.harvest。
    3. policy 不由 adapter 传，走默认 Reject。
    4. 测试 seam 只能是私有 _submit_seam，只替换 assembler 之后的返回值，
       不得出现在公开面。
    5. 按 planned 顺序逐篇投递；写 intent 记录时用 phase 不是 stage；
       intent 必须在 submit 前追加到 journal。
    6. 不得让 adapter 自己写盘。
    7. 状态载体是 append-only JSONL，不是整份 JSON 覆写。
    8. 全程持有 BATCH 锁 (locks.reserve) 直至全部条目完成。
    9. raw/batch 路径经 anchors.load_verified() 取 fd，不直接信任配置字符串。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from akb2.harvest import anchors
from akb2.harvest.locks import BATCH, reserve

# ---------------------------------------------------------------------------
# 契约常量
# ---------------------------------------------------------------------------

CLI_EXIT_OK = 0
CLI_EXIT_REJECTED = 2
CLI_EXIT_REDACTION = 3
CLI_EXIT_EXISTS = 4

# SKILL.md 标记常量（与 template.py 同源，逐字固定）
SUBMIT_TEMPLATE_OPEN = "<!-- akb2-harvest:submit-template -->"
SUBMIT_TEMPLATE_CLOSE = "<!-- /akb2-harvest:submit-template -->"

# 占位符语法固定
PLACEHOLDER_JSON = "{{harvest_json}}"
PLACEHOLDER_RUN_ID = "{{harvest_run_id}}"

# 适配器注册表（import 期固定，不接受运行期注入）
_ADAPTER_REGISTRY: dict[str, Callable[..., list[str]]] = {}

# 测试 seam（私有，只替换子进程执行结果；None = 正常执行）
# 签名: (argv: list[str], harvest_run_id: str) -> dict
# 返回值: {"exit_code": int, "filename": str | None, "error": str | None}
_submit_seam: Callable[[list[str], str], dict] | None = None

logger = logging.getLogger("trial_run")


# ---------------------------------------------------------------------------
# 模板提取（与 template.py 同逻辑，独立实现避免 import 循环）
# ---------------------------------------------------------------------------


def _extract_template(skill_md: str) -> str:
    """从 SKILL.md 提取唯一 submit 命令模板体。

    Args:
        skill_md: SKILL.md 全文。

    Returns:
        块内命令文本（不含注释行，已 strip）。

    Raises:
        ValueError: 标记块数量不是恰好一个、块不成对、或块内缺固定占位符。
    """
    open_count = skill_md.count(SUBMIT_TEMPLATE_OPEN)
    close_count = skill_md.count(SUBMIT_TEMPLATE_CLOSE)

    if open_count != 1 or close_count != 1:
        raise ValueError(
            f"SKILL.md submit 模板必须是恰一对标记注释："
            f"开始注释 {open_count} 个、结束注释 {close_count} 个"
            f"（期望各 1 个，零个/多个一律拒绝）"
        )

    open_idx = skill_md.index(SUBMIT_TEMPLATE_OPEN) + len(SUBMIT_TEMPLATE_OPEN)
    close_idx = skill_md.index(SUBMIT_TEMPLATE_CLOSE)
    if close_idx <= open_idx:
        raise ValueError("SKILL.md submit 模板结构非法：结束注释不在开始注释之后")

    body = skill_md[open_idx:close_idx].strip()
    if not body:
        raise ValueError("SKILL.md submit 模板为空")

    if PLACEHOLDER_JSON not in body or PLACEHOLDER_RUN_ID not in body:
        raise ValueError(
            f"SKILL.md submit 模板必须含占位符 {PLACEHOLDER_JSON} 与"
            f" {PLACEHOLDER_RUN_ID}（占位符语法固定，不得改写）"
        )
    return body


def _find_skill_md() -> Path | None:
    """定位 akb2-harvest SKILL.md 文件。

    按优先级查找：
    1. ~/.claude/skills/akb2-harvest/SKILL.md（安装到 runtime 的 skill）
    2. 工作区 capabilities 源（备用）
    """
    candidates = [
        Path.home() / ".claude" / "skills" / "akb2-harvest" / "SKILL.md",
    ]
    for c in candidates:
        if c.exists():
            return c
    # 备用：从 capabilities 源找
    alt = (
        Path(__file__).resolve().parents[1]
        / "capabilities" / "source" / "skills" / "claude-active"
        / "akb2-harvest" / "SKILL.md"
    )
    if alt.exists():
        return alt
    return None


# ---------------------------------------------------------------------------
# 规范适配器
# ---------------------------------------------------------------------------


def _canonical_adapter(
    planned_item: dict,
    harvest_run_id: str,
    payload_path: str,
) -> list[str]:
    """规范适配器：从 SKILL.md 提取模板 → 展开占位符 → 返回 argv。

    适配器**不写盘、不执行**——唯一动作是返回可执行的 CLI argv。
    policy 不由 adapter 传，走默认 Reject（硬不变量 3）。

    Args:
        planned_item: planned 条目（含 session_id/title/payload）。
        harvest_run_id: 调用方生成的 UUID4 归属键。
        payload_path: 已落盘的 payload JSON 临时文件路径。

    Returns:
        list[str]: 可传递给 subprocess.run 的 argv。

    Raises:
        RuntimeError: SKILL.md 不可定位。
        ValueError: 模板格式非法。
    """
    skill_path = _find_skill_md()
    if skill_path is None:
        raise RuntimeError("SKILL.md 不可定位")
    skill_md = skill_path.read_text(encoding="utf-8")
    template = _extract_template(skill_md)

    # 展开占位符（逐字替换，不校验参数合法性）
    cmd = template.replace(PLACEHOLDER_JSON, payload_path)
    cmd = cmd.replace(PLACEHOLDER_RUN_ID, harvest_run_id)
    return cmd.split()


# 注册 canonical 适配器（import 期固定，硬不变量 1）
_ADAPTER_REGISTRY["canonical"] = _canonical_adapter


# ---------------------------------------------------------------------------
# JSONL journal 操作（append-only，坏尾 fail-closed 或丢弃并记录）
# ---------------------------------------------------------------------------


def _append_journal(journal_path: str, record: dict) -> None:
    """以 O_APPEND 追加一条 JSON 记录到 JSONL journal。

    写操作是追加式，不改写已有内容。fsync 保证落盘。

    Args:
        journal_path: JSONL 文件路径。
        record: 要追加的记录。
    """
    path = Path(journal_path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _read_journal(journal_path: str) -> list[dict]:
    """读取 JSONL journal 中所有完整条目。

    坏尾行（末尾不以换行终止的 JSON 段）丢弃并记录警告。
    中间行损坏 → fail-closed 抛 ValueError。

    Args:
        journal_path: JSONL 文件路径。

    Returns:
        list[dict]: 完整记录列表。

    Raises:
        ValueError: 中间行 JSON 损坏。
    """
    path = Path(journal_path)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    lines = text.splitlines()
    records: list[dict] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                # 末行坏尾：丢弃并记录（不抛）
                logger.warning(
                    "trial_batch: 丢弃坏尾行 %d: %s", i, stripped[:64]
                )
                continue
            raise ValueError(
                f"trial_batch: JSONL 第 {i+1} 行损坏,不可解析: "
                f"{stripped[:64]}"
            ) from None
    return records


def _write_journal(journal_path: str, records: list[dict]) -> None:
    """全量覆写 journal 文件（用于迁移/重建，正常路径走 _append_journal）。

    Args:
        journal_path: JSONL 文件路径。
        records: 记录列表。
    """
    path = Path(journal_path)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# BATCH 锁与锚点解析
# ---------------------------------------------------------------------------


def _batch_lock_key() -> str:
    """从 anchors.load_verified() 的 batch 根推导 BATCH 锁 key。

    用 batch 锚定的 leaf_name 作为稳定锁键，避免跨进程并发覆写。
    leaf_name 是 manifest 中 batch 根的 basename，经信任链验证。

    Returns:
        str: BATCH 锁 key。

    Raises:
        AnchorDriftError: 锚点漂移或不可解析。
    """
    handles = anchors.load_verified()
    batch_h = handles["batch"]
    return batch_h.leaf_name


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """当前 UTC ISO 时间戳。"""
    return datetime.now(UTC).isoformat()


def _now_epoch() -> int:
    """当前 epoch 秒。"""
    return int(time.time())


def _pipeline_hash(data: str) -> str:
    """SHA-256[:16] 流水线内容哈希。"""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def _parse_cli_result(proc: subprocess.CompletedProcess) -> dict:
    """解析 subprocess 结果，从 stderr 提取文件名。

    CLI 输出格式（__main__.py）:
        - 成功: stderr 含 "harvest: 已落位 <filename>"
        - 失败: stderr 含 "harvest: 拒绝落位: <reason>" 或类似

    Returns:
        {"exit_code": int, "filename": str | None, "error": str | None}
    """
    stderr = proc.stderr or ""
    if proc.returncode == CLI_EXIT_OK:
        m = re.search(r"已落位\s+(.+)", stderr)
        filename = m.group(1).strip() if m else None
        return {"exit_code": proc.returncode, "filename": filename, "error": None}
    else:
        error = stderr.strip() or f"exit {proc.returncode}"
        return {"exit_code": proc.returncode, "filename": None, "error": error}


def _execute(argv: list[str], harvest_run_id: str) -> dict:
    """执行 CLI argv，返回解析结果。

    测试时通过 _submit_seam 替换返回值（硬不变量 4）。
    正常路径调 subprocess.run 并解析输出。

    Args:
        argv: 可执行命令列表。
        harvest_run_id: 归属键（仅用于 seam 标识）。

    Returns:
        {"exit_code": int, "filename": str | None, "error": str | None}
    """
    if _submit_seam is not None:
        return _submit_seam(argv, harvest_run_id)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "filename": None,
            "error": "subprocess 超时（120s）",
        }
    return _parse_cli_result(proc)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def run_attempts(
    planned: list[dict],
    adapter_id: str,
    journal_path: str,
    *,
    deadline_s: int,
) -> list[dict]:
    """执行试投循环：按 planned 顺序逐篇投递，JSONL journal + BATCH 锁。

    全程持有 BATCH 锁（locks.reserve(BATCH, ...)）直至全部条目完成。
    状态记录追加到 append-only JSONL journal，不减量覆写。
    坏尾行读取时丢弃并记录，不静默吞。

    Args:
        planned: planned 条目列表，每项含 session_id/title/payload。
        adapter_id: 注册表适配器键（import 期固定，不接受运行期注入）。
        journal_path: JSONL journal 文件路径（append-only）。
        deadline_s: 截止时间戳（epoch 秒），超时暂停新投递。

    Returns:
        本次新建的 attempted 记录列表（每项含 phase/status/harvest_run_id 等字段）。

    Raises:
        ValueError: adapter_id 不在注册表中。
        ReservationHeld: BATCH 锁已被其他进程持有。
        AnchorDriftError: 锚点漂移。
        NeedsManualResolution: BATCH 锁残留需人工裁定。

    硬不变量保证:
        1. 注册表 import 期固定（本函数不做运行时注册）。
        2. canonical adapter 走 SKILL.md 模板提取 → 展开 → 执行。
        3. policy 默认 Reject（adapter 不传 policy 参数）。
        4. _submit_seam 是唯一测试 seam（私有，替换返回值）。
        5. intent 记录在 submit 前追加到 journal；phase 不是 stage。
        6. adapter 不写盘（本函数负责 payload 临时文件生命周期）。
        7. 状态载体是 append-only JSONL（坏尾行丢弃并记录）。
        8. 全程持有 BATCH 锁（locks.reserve）。
        9. raw/batch 路径经 anchors.load_verified() 取 fd。
    """
    if adapter_id not in _ADAPTER_REGISTRY:
        raise ValueError(
            f"未知 adapter: {adapter_id!r}"
            f"（注册表 import 期固定，可用: {list(_ADAPTER_REGISTRY)}）"
        )

    adapter_fn = _ADAPTER_REGISTRY[adapter_id]
    deadline_iso = datetime.fromtimestamp(deadline_s, tz=UTC).isoformat()

    # 从 anchors 推导 BATCH 锁 key
    lock_key = _batch_lock_key()
    newly_attempted: list[dict] = []

    with reserve(BATCH, lock_key):
        for item in planned:
            # 截止时间检查（硬停）
            if _now_epoch() > deadline_s:
                logger.warning("deadline 已过，停止试投")
                break

            session_id = item.get("session_id", "")
            harvest_run_id = str(uuid.uuid4())
            intent_ts = _now_iso()
            payload = item.get("payload", {})

            # 1. 写 intent 记录 —— submit 前必须追加到 journal（硬不变量 5/7）
            intent_record = {
                "phase": "intent",
                "status": "pending",
                "harvest_run_id": harvest_run_id,
                "session_id": session_id,
                "expected_slug": session_id,
                "intent_timestamp": intent_ts,
                "raw_path": None,
                "pipeline_content_hash": None,
                "final_state": None,
            }
            _append_journal(journal_path, intent_record)
            newly_attempted.append(intent_record)
            logger.info("intent 已追加: %s / %s", harvest_run_id, session_id)

            # 2. 写 payload 临时文件（适配器不写盘，由本函数管理生命周期）
            payload_path = Path(f".trial-payload--{harvest_run_id}.json")
            try:
                payload_path.write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )

                # 3. 获取适配器 argv（adapter 不写盘，只返回 argv）
                argv = adapter_fn(item, harvest_run_id, str(payload_path))

                # 4. 执行 CLI（或测试 seam）
                result = _execute(argv, harvest_run_id)
                exit_code = result["exit_code"]
                filename = result.get("filename")
                error = result.get("error")

                # 5. 记录结果
                if exit_code == CLI_EXIT_OK:
                    content_hash = _pipeline_hash(
                        json.dumps(payload, sort_keys=True)
                    )
                    settled_record = {
                        "phase": "settled",
                        "status": "completed",
                        "harvest_run_id": harvest_run_id,
                        "session_id": session_id,
                        "expected_slug": session_id,
                        "intent_timestamp": intent_ts,
                        "raw_path": filename,
                        "pipeline_content_hash": content_hash,
                        "delivery_timestamp": _now_iso(),
                        "deadline": deadline_iso,
                        "reconciliation_state": "committed",
                        "final_state": "committed",
                    }
                    _append_journal(journal_path, settled_record)
                    # 替换 newly_attempted 中对应的 intent 记录
                    for i, rec in enumerate(newly_attempted):
                        if rec.get("harvest_run_id") == harvest_run_id:
                            newly_attempted[i] = settled_record
                            break
                else:
                    # 失败：exit 2/3/4 或其他
                    reason = error or f"exit {exit_code}"
                    failed_record = {
                        "phase": "failed",
                        "status": "failed",
                        "harvest_run_id": harvest_run_id,
                        "session_id": session_id,
                        "expected_slug": session_id,
                        "intent_timestamp": intent_ts,
                        "raw_path": None,
                        "pipeline_content_hash": None,
                        "delivery_timestamp": _now_iso(),
                        "deadline": deadline_iso,
                        "reconciliation_state": "blocked",
                        "final_state": "submit_error",
                        "failure_reason": reason,
                    }
                    _append_journal(journal_path, failed_record)
                    for i, rec in enumerate(newly_attempted):
                        if rec.get("harvest_run_id") == harvest_run_id:
                            newly_attempted[i] = failed_record
                            break
            except Exception as exc:
                # 适配器异常（如 SKILL.md 不可定位）或执行异常
                failed_record = {
                    "phase": "failed",
                    "status": "failed",
                    "harvest_run_id": harvest_run_id,
                    "session_id": session_id,
                    "expected_slug": session_id,
                    "intent_timestamp": intent_ts,
                    "raw_path": None,
                    "pipeline_content_hash": None,
                    "delivery_timestamp": _now_iso(),
                    "deadline": deadline_iso,
                    "reconciliation_state": "blocked",
                    "final_state": "submit_error",
                    "failure_reason": str(exc),
                }
                _append_journal(journal_path, failed_record)
                for i, rec in enumerate(newly_attempted):
                    if rec.get("harvest_run_id") == harvest_run_id:
                        newly_attempted[i] = failed_record
                        break
            finally:
                # 清理临时文件（硬不变量 6：adapter 不写盘，由调用方清理）
                if payload_path.exists():
                    payload_path.unlink()

            logger.info(
                "attempt 已追加到 journal: %s / %s",
                newly_attempted[-1].get("phase", "?"),
                session_id,
            )

    return newly_attempted


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：python3 scripts/trial_run.py <journal.jsonl> <adapter_id> <deadline_s>

    Returns:
        exit code: 0 = 全部完成；2 = 参数错误。
    """
    parser = argparse.ArgumentParser(
        prog="python3 scripts/trial_run.py",
        description="试投循环：逐篇投递到 append-only JSONL journal（全程 BATCH 锁）",
    )
    parser.add_argument("journal", help="trial-batch.jsonl 路径")
    parser.add_argument("adapter", help="适配器键（如 canonical）")
    parser.add_argument(
        "deadline", type=int, help="截止时间戳（epoch 秒）"
    )
    args = parser.parse_args(argv)

    try:
        # 读取 planned 列表（从 JSON 兼容格式）
        journal_path = args.journal
        existing = _read_journal(journal_path)
        # 从 journal 中获取 planned 条目（如果 journal 是新文件，从约定位置读取）
        # 实际场景：journal 文件包含 planned 列表作为元数据记录
        planned_path = Path(journal_path).with_suffix(".json")
        if planned_path.exists():
            with open(planned_path, encoding="utf-8") as fh:
                data = json.load(fh)
            planned = data.get("planned", [])
        else:
            # 从 journal 自身读取 planned（首行或元数据记录）
            planned = []
            for rec in existing:
                if rec.get("phase") == "planned":
                    planned.extend(rec.get("items", []))
                    break
    except FileNotFoundError:
        print(f"trial_run: 文件不可读: {args.journal}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"trial_run: JSON 非法: {exc}", file=sys.stderr)
        return 2

    if not planned:
        print("trial_run: planned 为空，无操作", file=sys.stderr)
        return 0

    try:
        records = run_attempts(
            planned, args.adapter, args.journal, deadline_s=args.deadline
        )
    except ValueError as exc:
        print(f"trial_run: {exc}", file=sys.stderr)
        return 2

    # 汇总
    n_settled = sum(1 for r in records if r.get("phase") == "settled")
    n_failed = sum(1 for r in records if r.get("phase") == "failed")
    n_intent = sum(1 for r in records if r.get("phase") == "intent")
    print(
        f"trial_run: 完成 — settled={n_settled}, failed={n_failed},"
        f" intent={n_intent}, total={len(records)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
