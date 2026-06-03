"""日志 + 失败告警（双通道，Codex P2 / SC-5）。

告警双通道：
  ① 飞书私信（try/except 包裹，不掩盖原错）
  ② **不依赖飞书**的本地通知（macOS osascript）+ 独立 ALERT 日志
飞书本身不可达时，通道②仍能触达用户。
"""

from __future__ import annotations

import subprocess
from datetime import datetime

from . import paths


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_line(msg: str) -> None:
    """写一行到 logs/feishu-sync.log，并回显 stdout。"""
    line = f"{_ts()} [feishu-sync] {msg}"
    print(line)
    try:
        with open(paths.log_file(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _write_alert_log(msg: str) -> None:
    try:
        with open(paths.alert_log_file(), "a", encoding="utf-8") as f:
            f.write(f"{_ts()} [ALERT] {msg}\n")
    except OSError:
        pass


def _local_notify(msg: str) -> None:
    """macOS 本地通知 —— 不依赖飞书的独立告警通道。"""
    title = "简报飞书同步失败"
    body = msg.replace('"', "'")[:200]
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{body}" with title "{title}"'],
            check=False,
            timeout=10,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def alert_failure(msg: str) -> None:
    """失败告警：双通道。飞书私信失败不影响本地通道。"""
    _write_alert_log(msg)
    log_line(f"FAILED — {msg}")

    # 通道①：飞书私信（best-effort，失败不掩盖原错）
    try:
        from . import config
        from .feishu.send import send_text_dm

        send_text_dm(config.dm_open_id(), f"⚠️ 简报飞书同步失败\n{msg}")
    except Exception:  # noqa: BLE001 — 告警通道失败必须吞掉，否则掩盖原始错误
        pass

    # 通道②：本地通知（独立于飞书，飞书挂了也能触达）
    _local_notify(msg)
