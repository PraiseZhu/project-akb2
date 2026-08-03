"""发送幂等 + 待补发状态。

状态文件兼容两种结构：

旧格式（生产已存在）：
{
  "2026-08-02": {
    "ai_trends:group": "2026-08-02T10:00:07"
  }
}

新格式：
{
  "2026-08-02": {
    "sent": {
      "ai_trends:group": "2026-08-02T10:00:07"
    },
    "pending": {
      "github:group": {
        "status": "missing_expected",
        "kind": "github",
        "date": "2026-08-02",
        "target": "group",
        "expected_path": "/vault/.../2026-08-02 GitHub Briefing.md",
        "reason": "failed: synthesis_failed: ...",
        "updated_at": "2026-08-02T10:00:07"
      }
    }
  }
}

设计约束：
1. 读旧格式不能崩，并会在下一次写入时平滑升级到新结构。
2. 状态损坏时 fail-closed，避免重复发群。
3. 发送前仍先预留 sent；发送失败则回滚 sent，并写出 retryable pending。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

PENDING_MISSING_EXPECTED = "missing_expected"
PENDING_SEND_FAILED_RETRYABLE = "send_failed_retryable"


class StateCorruptError(Exception):
    """幂等状态文件存在但损坏/不可读。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _key(kind: str, target: str) -> str:
    return f"{kind}:{target}"


def _load_raw(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise StateCorruptError(
            f"幂等状态文件损坏/不可读，拒绝继续以防重复发送: {state_path} ({exc})"
        ) from exc
    if not isinstance(data, dict):
        raise StateCorruptError(f"幂等状态文件格式非法（非 dict）: {state_path}")
    return data


def _normalize_sent(raw_sent: Any, state_path: Path, date: str) -> dict[str, str]:
    if raw_sent is None:
        return {}
    if not isinstance(raw_sent, dict):
        raise StateCorruptError(f"sent 非 dict: {state_path} ({date})")
    sent: dict[str, str] = {}
    for key, value in raw_sent.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise StateCorruptError(f"sent 记录非法: {state_path} ({date})")
        sent[key] = value
    return sent


def _normalize_pending(raw_pending: Any, state_path: Path, date: str) -> dict[str, dict[str, Any]]:
    if raw_pending is None:
        return {}
    if not isinstance(raw_pending, dict):
        raise StateCorruptError(f"pending 非 dict: {state_path} ({date})")
    pending: dict[str, dict[str, Any]] = {}
    for key, value in raw_pending.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise StateCorruptError(f"pending 记录非法: {state_path} ({date})")
        pending[key] = dict(value)
    return pending


def _normalize_day(day: Any, state_path: Path, date: str) -> dict[str, Any]:
    if not isinstance(day, dict):
        raise StateCorruptError(f"日期节点非 dict: {state_path} ({date})")

    is_legacy = "sent" not in day and "pending" not in day and all(
        isinstance(key, str) and isinstance(value, str) for key, value in day.items()
    )
    if is_legacy:
        return {"sent": dict(day), "pending": {}}

    extra = {key: value for key, value in day.items() if key not in {"sent", "pending"}}
    extra["sent"] = _normalize_sent(day.get("sent"), state_path, date)
    extra["pending"] = _normalize_pending(day.get("pending"), state_path, date)
    return extra


def _load(state_path: Path) -> dict[str, dict[str, Any]]:
    raw = _load_raw(state_path)
    return {date: _normalize_day(day, state_path, date) for date, day in raw.items()}


def _atomic_write(state_path: Path, data: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, state_path)


def _ensure_day(data: dict[str, dict[str, Any]], date: str) -> dict[str, Any]:
    day = data.setdefault(date, {"sent": {}, "pending": {}})
    day.setdefault("sent", {})
    day.setdefault("pending", {})
    return day


def _prune_day(data: dict[str, dict[str, Any]], date: str) -> None:
    day = data.get(date)
    if not day:
        return
    if not day.get("sent") and not day.get("pending"):
        data.pop(date, None)


def load_state(state_path: Path) -> dict[str, dict[str, Any]]:
    """读取并标准化状态；供测试/诊断使用。"""
    return _load(state_path)


def already_sent(state_path: Path, date: str, kind: str, target: str) -> bool:
    return _key(kind, target) in _load(state_path).get(date, {}).get("sent", {})


def mark_sent(state_path: Path, date: str, kind: str, target: str) -> None:
    data = _load(state_path)
    day = _ensure_day(data, date)
    key = _key(kind, target)
    day["sent"][key] = _now()
    day["pending"].pop(key, None)
    _prune_day(data, date)
    _atomic_write(state_path, data)


def unmark_sent(state_path: Path, date: str, kind: str, target: str) -> None:
    try:
        data = _load(state_path)
    except StateCorruptError:
        return
    day = data.get(date)
    if not day:
        return
    day.get("sent", {}).pop(_key(kind, target), None)
    _prune_day(data, date)
    _atomic_write(state_path, data)


def clear_pending(state_path: Path, date: str, kind: str, target: str) -> None:
    data = _load(state_path)
    day = data.get(date)
    if not day:
        return
    day.get("pending", {}).pop(_key(kind, target), None)
    _prune_day(data, date)
    _atomic_write(state_path, data)


def _mark_pending(
    state_path: Path,
    *,
    date: str,
    kind: str,
    target: str,
    payload: dict[str, Any],
) -> None:
    data = _load(state_path)
    day = _ensure_day(data, date)
    day["pending"][_key(kind, target)] = payload
    _atomic_write(state_path, data)


def mark_missing_expected(
    state_path: Path,
    *,
    date: str,
    kind: str,
    target: str,
    expected_path: str,
    reason: str,
) -> None:
    _mark_pending(
        state_path,
        date=date,
        kind=kind,
        target=target,
        payload={
            "status": PENDING_MISSING_EXPECTED,
            "kind": kind,
            "date": date,
            "target": target,
            "expected_path": expected_path,
            "reason": reason,
            "updated_at": _now(),
        },
    )


def mark_send_failed_retryable(
    state_path: Path,
    *,
    date: str,
    kind: str,
    target: str,
    reason: str,
    retry_after: float | None = None,
) -> None:
    payload: dict[str, Any] = {
        "status": PENDING_SEND_FAILED_RETRYABLE,
        "kind": kind,
        "date": date,
        "target": target,
        "reason": reason,
        "updated_at": _now(),
    }
    if retry_after is not None:
        payload["retry_after"] = retry_after
    _mark_pending(state_path, date=date, kind=kind, target=target, payload=payload)


def pending_items(
    state_path: Path,
    *,
    date: str,
    target: str | None = None,
    statuses: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    day = _load(state_path).get(date, {})
    pending = day.get("pending", {})
    allowed = set(statuses or [])
    items: list[dict[str, Any]] = []
    for key, payload in pending.items():
        status = payload.get("status")
        if allowed and status not in allowed:
            continue
        if target and payload.get("target") != target:
            continue
        item = dict(payload)
        item.setdefault("state_key", key)
        items.append(item)
    return items
