"""发送幂等 — 记录 (date, kind, target) 已发，防重跑重复推送（Codex P1/P2 / SC-4）。

状态文件结构：{ "<date>": { "<kind>:<target>": "<iso_ts>" } }

两条 fail-closed 设计（针对"群消息不可撤回"）：
1. 文件存在但损坏/不可读 → 抛 StateCorruptError（拒发，由上层 exit 1 + 告警），
   绝不当空处理后重发。
2. **发送前预留**：send_node 先 mark_sent 再发送；发送失败则 unmark_sent 回滚。
   这样消除"已发成功但状态没写→重跑重复发"的窗口；残留风险只剩"预留后进程被杀→
   漏发"（方向安全：漏发可补/有告警，远好于重复刷群）。
写入用临时文件 + 原子 replace，避免写一半损坏。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


class StateCorruptError(Exception):
    """幂等状态文件存在但损坏/不可读。"""


def _load(state_path: Path) -> dict:
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


def _atomic_write(state_path: Path, data: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, state_path)  # 同目录原子替换


def _key(kind: str, target: str) -> str:
    return f"{kind}:{target}"


def already_sent(state_path: Path, date: str, kind: str, target: str) -> bool:
    """是否已发/已预留。状态文件损坏时抛 StateCorruptError（fail-closed）。"""
    return _key(kind, target) in _load(state_path).get(date, {})


def mark_sent(state_path: Path, date: str, kind: str, target: str) -> None:
    """记录（发送前预留）。原子写。"""
    data = _load(state_path)
    data.setdefault(date, {})[_key(kind, target)] = datetime.now().isoformat(timespec="seconds")
    _atomic_write(state_path, data)


def unmark_sent(state_path: Path, date: str, kind: str, target: str) -> None:
    """回滚预留（发送失败时调用），使重跑可重试。"""
    try:
        data = _load(state_path)
    except StateCorruptError:
        return  # 损坏交给 fail-closed，不在此处理
    day = data.get(date)
    if not day:
        return
    day.pop(_key(kind, target), None)
    if not day:
        data.pop(date, None)
    _atomic_write(state_path, data)
