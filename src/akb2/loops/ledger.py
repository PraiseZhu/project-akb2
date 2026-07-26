"""幂等账本追加。

公开函数:
    append_event(ledger_path, event: dict) -> bool

- event 必须符合 loop-ledger.schema.json（仅调用方保证，本模块不做 schema 校验）。
- 重复行（整行 SHA-256 相同）返回 False 不抛异常。
- 有限重试：MAX_LEDGER_RETRIES=3，无 while True（宪法 P1）。
- 写入是**真 append**（O_APPEND）且全程 fcntl.flock(LOCK_EX) 保护：去重扫描与
  追加在同一把锁内完成，多写者不丢行、不写重复行。

缺陷 #11c 修复记录（2026-07-26）：
    原实现是「读全文 → 写 tmp → os.replace」，并在 docstring 里假设"串行单工
    pipeline，不设锁"。Mac Mini 部署（第二台机器装同一套 plist、指向同一 vault）
    让该前提失效：两个写者各读到 N 行、各写 N+1 行，最后 rename 的覆盖前者 → 丢行
    （实测两进程各追加 150 条，只剩 226 条）。现改为 O_APPEND + flock，范式与
    promotion/promote.py、video/collector.py 一致。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 具名预算常量（宪法 P4）
# ---------------------------------------------------------------------------

MAX_LEDGER_RETRIES = 3
LEDGER_RETRY_DELAY_SEC = 0.1

# 损坏行告警最多列出的行号数（避免整份账本损坏时刷屏）
MAX_CORRUPT_LINES_REPORTED = 5


def _sha256_line(obj: dict) -> str:
    """计算 dict 的规范化 JSON（sort_keys）的 SHA-256 hex 摘要。"""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _scan_for_duplicate(fh, target_hash: str, ledger_path: str) -> bool:
    """在已打开（且已持锁）的账本句柄里扫描 target_hash 是否已存在。

    损坏行不静默跳过（宪法 P4 失败可见）：计数 + 行号打到 stderr。静默 continue
    会让幂等判定把"其实已处理"的内容当成未处理 → 全链重跑 / 重复入库。
    """
    fh.seek(0)
    found = False
    corrupt_lines: list[int] = []
    for lineno, raw_line in enumerate(fh, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            existing_obj = json.loads(line)
        except json.JSONDecodeError:
            corrupt_lines.append(lineno)
            continue
        if not found and _sha256_line(existing_obj) == target_hash:
            found = True

    if corrupt_lines:
        shown = ", ".join(str(n) for n in corrupt_lines[:MAX_CORRUPT_LINES_REPORTED])
        more = "" if len(corrupt_lines) <= MAX_CORRUPT_LINES_REPORTED else " ..."
        print(
            f"[ledger] 警告: {ledger_path} 有 {len(corrupt_lines)} 行 JSON 损坏"
            f"（行号 {shown}{more}）；这些行未参与幂等去重，可能导致内容被重复处理",
            file=sys.stderr,
        )
    return found


def _append_line_locked(path: Path, line: str, target_hash: str) -> bool:
    """持 flock 完成「去重扫描 + 真 append」。

    Returns:
        True 已写入新行；False 已存在同 hash 行（幂等跳过）。
    """
    # "a+" 创建文件但不截断；O_APPEND 保证写落在文件末尾（不受 seek 影响），
    # flock 让去重扫描与追加成为一个临界区，避免两写者同时判"不重复"。
    with open(path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            if _scan_for_duplicate(fh, target_hash, str(path)):
                return False
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return True


def append_event(ledger_path: str, event: dict) -> bool:
    """幂等追加一个事件行到 jsonl 账本。

    Args:
        ledger_path: jsonl 账本文件路径（不存在则创建）。
        event: 事件 dict，必须含 loop_id/node/event/content_hash/ts 等字段。

    Returns:
        True  —— 新行已写入。
        False —— 重复行（同 SHA-256）已跳过，不修改文件。

    Raises:
        OSError: 在 MAX_LEDGER_RETRIES 次重试后仍无法写入。
    """
    path = Path(ledger_path)
    target_hash = _sha256_line(event)
    line = json.dumps(event, sort_keys=True, ensure_ascii=False)

    # 确保目录存在
    path.parent.mkdir(parents=True, exist_ok=True)

    # 追加，限次重试（宪法 P1：无 while True）
    last_err: Exception | None = None
    for attempt in range(1, MAX_LEDGER_RETRIES + 1):
        try:
            return _append_line_locked(path, line, target_hash)
        except OSError as exc:
            last_err = exc
            if attempt < MAX_LEDGER_RETRIES:
                time.sleep(LEDGER_RETRY_DELAY_SEC)

    raise OSError(
        f"Failed to append to ledger {ledger_path} after {MAX_LEDGER_RETRIES} retries"
    ) from last_err
