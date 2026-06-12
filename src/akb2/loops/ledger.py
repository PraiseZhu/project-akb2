"""幂等账本追加。

公开函数:
    append_event(ledger_path, event: dict) -> bool

- event 必须符合 loop-ledger.schema.json（仅调用方保证，本模块不做 schema 校验）。
- 重复行（整行 SHA-256 相同）返回 False 不抛异常。
- 有限重试：MAX_LEDGER_RETRIES=3，无 while True（宪法 P1）。
- 写入是原子化的：先写临时文件再 rename。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 具名预算常量（宪法 P4）
# ---------------------------------------------------------------------------

MAX_LEDGER_RETRIES = 3
LEDGER_RETRY_DELAY_SEC = 0.1


def _sha256_line(obj: dict) -> str:
    """计算 dict 的规范化 JSON（sort_keys）的 SHA-256 hex 摘要。"""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _append_line_atomic(path: Path, line: str) -> None:
    """原子追加一行到文件：写临时文件 → rename。

    并行写场景：os.replace 在 POSIX 上是原子的，但多个进程同时追加时
    最后 rename 的会覆盖前者的追加。当前场景为串行单工 pipeline，不设锁。
    """
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".ledger_tmp_"
    )
    try:
        # 先复制已有内容（如存在），再追加新行
        if path.exists():
            with open(path, "rb") as src:
                os.write(tmp_fd, src.read())
        os.write(tmp_fd, (line + "\n").encode("utf-8"))
        os.fsync(tmp_fd)
    finally:
        os.close(tmp_fd)

    os.replace(tmp_path, path)


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

    # 确保目录存在
    path.parent.mkdir(parents=True, exist_ok=True)

    # 检查重复：扫描已有行 hash
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            for existing_line in fh:
                existing_line = existing_line.strip()
                if not existing_line:
                    continue
                try:
                    existing_obj = json.loads(existing_line)
                except json.JSONDecodeError:
                    continue  # 跳过损坏行
                if _sha256_line(existing_obj) == target_hash:
                    return False  # 幂等：不追加重复行

    # 原子追加，限次重试（宪法 P1：无 while True）
    last_err: Exception | None = None
    for attempt in range(1, MAX_LEDGER_RETRIES + 1):
        try:
            line = json.dumps(event, sort_keys=True, ensure_ascii=False)
            _append_line_atomic(path, line)
            return True
        except OSError as exc:
            last_err = exc
            if attempt < MAX_LEDGER_RETRIES:
                time.sleep(LEDGER_RETRY_DELAY_SEC)

    raise OSError(
        f"Failed to append to ledger {ledger_path} after {MAX_LEDGER_RETRIES} retries"
    ) from last_err
