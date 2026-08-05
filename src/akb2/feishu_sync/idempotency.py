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
    },
    "card_progress": {
      "github:group": {
        "sent_indexes": [0],
        "total_cards": 2,
        "updated_at": "2026-08-02T10:00:07"
      }
    }
  }
}

设计约束：
1. 读旧格式不能崩，并会在下一次写入时平滑升级到新结构。
2. 状态损坏时 fail-closed，避免重复发群。
3. 发送前用 O_EXCL sidecar 原子预留；只有 owner 能释放预留。
4. 每张卡成功后即时记录 card_progress；整份成功后再升级为 sent。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PENDING_MISSING_EXPECTED = "missing_expected"
PENDING_SEND_FAILED_RETRYABLE = "send_failed_retryable"


class StateCorruptError(Exception):
    """幂等状态文件存在但损坏/不可读。"""


@dataclass(frozen=True)
class SendReservation:
    """一次发送预留；token 用于 owner-only 释放。"""

    state_path: Path
    path: Path
    token: str


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


def _normalize_card_progress(
    raw_progress: Any, state_path: Path, date: str
) -> dict[str, dict[str, Any]]:
    if raw_progress is None:
        return {}
    if not isinstance(raw_progress, dict):
        raise StateCorruptError(f"card_progress 非 dict: {state_path} ({date})")
    progress: dict[str, dict[str, Any]] = {}
    for key, value in raw_progress.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise StateCorruptError(f"card_progress 记录非法: {state_path} ({date})")
        sent_indexes = value.get("sent_indexes", [])
        total_cards = value.get("total_cards")
        if (
            not isinstance(sent_indexes, list)
            or any(not isinstance(index, int) or index < 0 for index in sent_indexes)
            or not isinstance(total_cards, int)
            or total_cards < 0
        ):
            raise StateCorruptError(f"card_progress 记录非法: {state_path} ({date})")
        progress[key] = dict(value)
    return progress


def _normalize_day(day: Any, state_path: Path, date: str) -> dict[str, Any]:
    if not isinstance(day, dict):
        raise StateCorruptError(f"日期节点非 dict: {state_path} ({date})")

    is_legacy = "sent" not in day and "pending" not in day and all(
        isinstance(key, str) and isinstance(value, str) for key, value in day.items()
    )
    if is_legacy:
        return {"sent": dict(day), "pending": {}}

    extra = {
        key: value
        for key, value in day.items()
        if key not in {"sent", "pending", "card_progress"}
    }
    extra["sent"] = _normalize_sent(day.get("sent"), state_path, date)
    extra["pending"] = _normalize_pending(day.get("pending"), state_path, date)
    extra["card_progress"] = _normalize_card_progress(
        day.get("card_progress"), state_path, date
    )
    return extra


def _load(state_path: Path) -> dict[str, dict[str, Any]]:
    raw = _load_raw(state_path)
    return {date: _normalize_day(day, state_path, date) for date, day in raw.items()}


def _atomic_write(state_path: Path, data: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=state_path.parent,
            prefix=f".{state_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            os.chmod(tmp_path, 0o600)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, state_path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@contextmanager
def _state_lock(state_path: Path) -> Iterator[None]:
    """跨进程串行化 state 的 read-modify-write，避免不同 key 相互覆盖。"""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name(f".{state_path.name}.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def _mutate_state(state_path: Path) -> Iterator[dict[str, dict[str, Any]]]:
    with _state_lock(state_path):
        data = _load(state_path)
        yield data
        _atomic_write(state_path, data)


def _ensure_day(data: dict[str, dict[str, Any]], date: str) -> dict[str, Any]:
    day = data.setdefault(date, {"sent": {}, "pending": {}, "card_progress": {}})
    day.setdefault("sent", {})
    day.setdefault("pending", {})
    day.setdefault("card_progress", {})
    return day


def _prune_day(data: dict[str, dict[str, Any]], date: str) -> None:
    day = data.get(date)
    if not day:
        return
    if not day.get("sent") and not day.get("pending") and not day.get("card_progress"):
        data.pop(date, None)


def _reservation_path(state_path: Path, date: str, kind: str, target: str) -> Path:
    identity = "\0".join((date, kind, target)).encode()
    digest = hashlib.sha256(identity).hexdigest()
    return state_path.parent / f".{state_path.name}.reservations" / f"{digest}.json"


def load_state(state_path: Path) -> dict[str, dict[str, Any]]:
    """读取并标准化状态；供测试/诊断使用。"""
    return _load(state_path)


def already_sent(state_path: Path, date: str, kind: str, target: str) -> bool:
    return _key(kind, target) in _load(state_path).get(date, {}).get("sent", {})


def reserve_if_unsent(
    state_path: Path, date: str, kind: str, target: str
) -> SendReservation | None:
    """CAS 预留一次发送；已发送或已有并发 owner 时返回 None。"""
    path = _reservation_path(state_path, date, kind, target)
    token = uuid.uuid4().hex
    payload = json.dumps(
        {"token": token, "date": date, "kind": kind, "target": target},
        ensure_ascii=False,
    ).encode()
    with _state_lock(state_path):
        data = _load(state_path)
        if _key(kind, target) in data.get(date, {}).get("sent", {}):
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return None
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return SendReservation(state_path=state_path, path=path, token=token)


def release_reservation(reservation: SendReservation) -> bool:
    """仅当磁盘 token 仍属于调用方时释放；否则 fail-closed 保留。"""
    with _state_lock(reservation.state_path):
        try:
            payload = json.loads(reservation.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(payload, dict) or payload.get("token") != reservation.token:
            return False
        try:
            reservation.path.unlink()
        except FileNotFoundError:
            return False
        return True


def mark_sent(state_path: Path, date: str, kind: str, target: str) -> None:
    with _mutate_state(state_path) as data:
        day = _ensure_day(data, date)
        key = _key(kind, target)
        day["sent"][key] = _now()
        day["pending"].pop(key, None)
        day["card_progress"].pop(key, None)
        _prune_day(data, date)


def sent_card_indexes(state_path: Path, date: str, kind: str, target: str) -> set[int]:
    progress = (
        _load(state_path)
        .get(date, {})
        .get("card_progress", {})
        .get(_key(kind, target), {})
    )
    return set(progress.get("sent_indexes", []))


def mark_card_sent(
    state_path: Path,
    date: str,
    kind: str,
    target: str,
    *,
    card_index: int,
    total_cards: int,
) -> None:
    """记录外部已接受的单卡；必须在 transport 成功返回后立即调用。"""
    with _mutate_state(state_path) as data:
        day = _ensure_day(data, date)
        key = _key(kind, target)
        progress = day["card_progress"].setdefault(
            key,
            {"sent_indexes": [], "total_cards": total_cards, "updated_at": _now()},
        )
        indexes = set(progress.get("sent_indexes", []))
        indexes.add(card_index)
        progress["sent_indexes"] = sorted(indexes)
        progress["total_cards"] = total_cards
        progress["updated_at"] = _now()


def unmark_sent(state_path: Path, date: str, kind: str, target: str) -> None:
    try:
        with _mutate_state(state_path) as data:
            day = data.get(date)
            if not day:
                return
            day.get("sent", {}).pop(_key(kind, target), None)
            _prune_day(data, date)
    except StateCorruptError:
        return


def clear_pending(state_path: Path, date: str, kind: str, target: str) -> None:
    with _mutate_state(state_path) as data:
        day = data.get(date)
        if not day:
            return
        day.get("pending", {}).pop(_key(kind, target), None)
        _prune_day(data, date)


def _mark_pending(
    state_path: Path,
    *,
    date: str,
    kind: str,
    target: str,
    payload: dict[str, Any],
) -> None:
    with _mutate_state(state_path) as data:
        day = _ensure_day(data, date)
        day["pending"][_key(kind, target)] = payload


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
