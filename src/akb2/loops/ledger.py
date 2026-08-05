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

SC-R2-11② 修复记录（2026-08-04）：
    账本行会带上 provider 错误原文（`llm_gateway_429` 事件的 `message` 字段）与失败
    上下文，属机器本地敏感状态；同链路的 `shared-budget-gate.json` / `.lock` 都是
    0600，而 `quality-ledger.jsonl` 走普通 `open(path, "a+")` → umask 022 下落成
    **0644**（组/其他可读）。修复点定在本模块这个唯一咽喉（`open_append_secure`）而
    **不在调用点 chmod**：`quality-ledger.jsonl` 至少有 4 个写者（quality/llm_router、
    quality/promote、quality/backfill_requeue、briefing 侧 synthesis_assembler），
    逐个调用点修会让权限退化成"谁先创建谁决定"的非确定态。`loops/signatures.py`
    写的 `failures.jsonl` 是同目录的姊妹状态文件，同样经本咽喉打开。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import IO

# ---------------------------------------------------------------------------
# 具名预算常量（宪法 P4）
# ---------------------------------------------------------------------------

MAX_LEDGER_RETRIES = 3
LEDGER_RETRY_DELAY_SEC = 0.1

# 损坏行告警最多列出的行号数（避免整份账本损坏时刷屏）
MAX_CORRUPT_LINES_REPORTED = 5

# append-only 状态文件的落盘权限（与 common/budget_gate.STATE_FILE_MODE 同口径）。
# 0600 = 仅属主可读写；umask 只会去位不会加位，故 umask 022/077 下结果都是 0600。
STATE_FILE_MODE = 0o600


def _tighten_mode(fh: IO[str], path: Path) -> None:
    """已存在的账本若比 0600 宽（历史上以默认 umask 创建的 0644），就地收紧。

    只在实测比 0600 宽时才 chmod —— 用 `fchmod`/`fstat` 作用于已打开的 fd 而非路径，
    避免 TOCTOU（路径在检查与改权限之间被换掉）。

    chmod 失败不让账本写入整体 fail：账本是别的账户建的时会 EPERM，那时丢账
    （→ 全量重跑 / 重复入库）比权限宽一档更危险。但必须响亮告警（宪法 P4 失败可见），
    不静默继续。
    """
    current = stat.S_IMODE(os.fstat(fh.fileno()).st_mode)
    if current & ~STATE_FILE_MODE == 0:
        return
    try:
        os.fchmod(fh.fileno(), STATE_FILE_MODE)
    except OSError as exc:
        print(
            f"[ledger] 警告: 无法把 {path} 权限从 {oct(current)} 收紧到"
            f" {oct(STATE_FILE_MODE)}: {exc}；账本内容对同机其他账户仍可读",
            file=sys.stderr,
        )


def open_append_secure(path: Path) -> IO[str]:
    """打开（必要时以 0600 创建）append-only 状态文件——权限判据的唯一 owner。

    `os.open(O_CREAT, STATE_FILE_MODE)` 让**首次创建即 0600**（普通 `open()` 走默认
    umask → 0644）；已存在且更宽的文件由 `_tighten_mode` 收紧，因此权限不取决
    于"哪个写者先创建"。

    Args:
        path: 状态文件路径（父目录须已存在）。

    Returns:
        以 "a+" 包装的文本句柄：O_APPEND 保证写落在末尾（不受 seek 影响），可读用于
        幂等去重扫描。调用方负责关闭与加锁。
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, STATE_FILE_MODE)
    try:
        handle: IO[str] = os.fdopen(fd, "a+", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    try:
        _tighten_mode(handle, path)
    except BaseException:
        handle.close()
        raise
    return handle


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
    # open_append_secure 创建文件但不截断（0600，SC-R2-11②）；O_APPEND 保证写落在
    # 文件末尾（不受 seek 影响），flock 让去重扫描与追加成为一个临界区，避免两写者
    # 同时判"不重复"。
    with open_append_secure(path) as fh:
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
