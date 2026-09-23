"""同一 X 账号上多条链路的限流协调（优先租约 + 429 冷却）。

背景（2026-09-23 GitHub 早报四次补跑全部 0 候选）：
    本机 GitHub 早报 L1 与 `com.akb2.intake-twitter` 共用同一个 X 账号跑
    `twitter user-posts`。intake-twitter 每 15 分钟一轮、实际首尾相接，日均约
    1420 次调用里约 1270 次 429，CLI 每次内部再重试 3 次，把限流窗口持续占满；
    09-23 起 GitHub 早报 61 个账号全部 429，L1 整层失效。

两个协调原语（都是机器本地状态文件，放 `<state_root>/locks/`，不进同步范围）：

- **优先租约**：GitHub 早报 L1 开跑时写租约（带过期时间），intake-twitter 看到有效
  租约即让路、不再打 X。租约有 TTL，持有方崩溃也不会永久卡死。
- **429 冷却**：intake-twitter 连续命中 429 时写冷却截止时间，冷却期内所有
  x_account 源直接跳过，不再对已占满的窗口继续加压。

文件读写失败一律按「无租约 / 无冷却」处理并打印告警（宪法 P4 失败可见）：
协调是减压手段，不能因为状态文件坏了就把采集卡死。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .state_paths import state_file_path

# ---------------------------------------------------------------------------
# 具名常量（宪法 P4：预算显式）
# ---------------------------------------------------------------------------

PRIORITY_LEASE_NAME = "x-api-priority-lease.json"
COOLDOWN_NAME = "x-api-cooldown.json"
LOCK_SUBDIR = "locks"

# twitter-cli 在 429 重试耗尽后的输出特征（stderr 警告 / stdout error.code）。
_RATE_LIMIT_MARKERS = ("rate_limited", "rate limited", "http 429", "rate limit exceeded")


def _path(name: str) -> Path:
    return state_file_path(name, subdir=LOCK_SUBDIR)


def _read(name: str) -> dict | None:
    path = _path(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        print(f"[x_api_coordination] 警告: 无法读取 {path}: {exc}；按无状态处理",
              file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def _write(name: str, payload: dict) -> None:
    path = _path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        print(f"[x_api_coordination] 警告: 无法写入 {path}: {exc}", file=sys.stderr)


def is_rate_limited(text: str | None) -> bool:
    """twitter-cli 输出是否为 429 限流。"""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def acquire_priority_lease(holder: str, ttl_sec: float, *, now: float | None = None) -> None:
    """写优先租约；已有租约会被覆盖（同机只有 GitHub 早报一个持有方）。"""
    ts = time.time() if now is None else now
    _write(PRIORITY_LEASE_NAME, {"holder": holder, "expires_at": ts + ttl_sec})


def release_priority_lease(holder: str) -> None:
    """只释放自己持有的租约。"""
    data = _read(PRIORITY_LEASE_NAME)
    if not data or data.get("holder") != holder:
        return
    try:
        _path(PRIORITY_LEASE_NAME).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"[x_api_coordination] 警告: 无法释放租约: {exc}", file=sys.stderr)


def active_priority_lease(*, now: float | None = None) -> str | None:
    """返回有效租约的持有方；无租约或已过期返回 None。"""
    data = _read(PRIORITY_LEASE_NAME)
    if not data:
        return None
    ts = time.time() if now is None else now
    try:
        expires_at = float(data.get("expires_at", 0))
    except (TypeError, ValueError):
        return None
    if expires_at <= ts:
        return None
    return str(data.get("holder") or "unknown")


def start_cooldown(seconds: float, reason: str, *, now: float | None = None) -> None:
    ts = time.time() if now is None else now
    _write(COOLDOWN_NAME, {"until": ts + seconds, "reason": reason})


def cooldown_remaining(*, now: float | None = None) -> float:
    """冷却剩余秒数；无冷却返回 0。"""
    data = _read(COOLDOWN_NAME)
    if not data:
        return 0.0
    ts = time.time() if now is None else now
    try:
        until = float(data.get("until", 0))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, until - ts)
