"""日志 + 失败告警（双通道，Codex P2 / SC-5 / SC-8）。

告警双通道：
  ① 飞书私信（try/except 包裹，不掩盖原错）
  ② **不依赖飞书**的本地通知（macOS osascript）+ 独立 ALERT 日志
飞书本身不可达时，通道②仍能触达用户。

SC-8：失败来自飞书限流/认证/不可达时，**不再复用受限飞书 API 发 DM**（会再次 9499、
放大噪声）——只写本地 ALERT + macOS 通知。非飞书错误仍 best-effort 发 DM（复用 SC-6 退避）。
"""

from __future__ import annotations

import subprocess
from datetime import datetime

from . import paths

# 飞书侧错误特征（限流/认证/不可达）— 命中即抑制 DM，不复用受限飞书 API
_FEISHU_ERROR_KEYWORDS: tuple[str, ...] = (
    "9499", "too many request", "rate-limit", "retry exhausted",
    "限流", "飞书发送失败", "unauthorized", "Unauthorized",
)
_FEISHU_ERROR_CODES: set[int] = {9499, 429}


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


def _local_notify(msg: str, title: str = "简报飞书同步失败") -> None:
    """macOS 本地通知 —— 不依赖飞书的独立告警通道。"""
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


def _is_feishu_error(error: Exception | None, msg: str = "") -> bool:
    """判定失败是否来自飞书本身（限流/认证/不可达）— 命中则抑制 DM。"""
    if error is not None:
        code = getattr(error, "code", None)
        if code in _FEISHU_ERROR_CODES:
            return True
        if getattr(error, "retryable", False) or getattr(error, "exhausted", False):
            return True
        if any(k in str(error) for k in _FEISHU_ERROR_KEYWORDS):
            return True
    if msg:
        return any(k in msg for k in _FEISHU_ERROR_KEYWORDS)
    return False


def alert_title_for(msg: str) -> str:
    """按真因分级标题：上游 skip/阻断 ≠ 飞书发送失败。"""
    if "飞书发送失败" in msg:
        return "简报飞书同步失败"
    lowered = msg.lower()
    if (
        "skipped:" in lowered
        or "blocked:" in lowered
        or "真因: skipped" in msg
        or "skip-gate" in lowered
        or "缺失" in msg
    ):
        return "简报上游未产出"
    return "简报飞书同步失败"


def alert_failure(msg: str, *, error: Exception | None = None, title: str | None = None) -> None:
    """失败告警：双通道，飞书本身不可达时不复用受限飞书 API。

    Args:
        msg: 失败摘要（面向用户/日志）。
        error: 可选的结构化错误对象（FeishuSendError 等）；提供时用于精确判定
            是否飞书侧错误。未提供时从 msg 文本判定。
        title: 可选覆盖标题；缺省按 msg 真因分级。
    """
    resolved_title = title or alert_title_for(msg)
    _write_alert_log(msg)
    log_line(f"FAILED — {msg}")

    # SC-8：飞书限流/认证/不可达 → 不复用受限飞书 API 发 DM（只本地告警）
    if _is_feishu_error(error, msg):
        _write_alert_log(f"alert DM suppressed (feishu error): {msg}")
        _local_notify(msg, title=resolved_title)
        return

    # 通道①：飞书私信（best-effort，复用 SC-6 退避；失败不掩盖原错）
    try:
        from . import config
        from .feishu.send import send_text_dm

        send_text_dm(config.dm_open_id(), f"⚠️ {resolved_title}\n{msg}")
    except Exception as exc:  # noqa: BLE001 — 告警通道失败必须吞掉；飞书侧失败则抑制
        if _is_feishu_error(exc):
            _write_alert_log(f"alert DM suppressed (feishu error): {exc}")
        # 飞书通道挂了不影响本地通道

    # 通道②：本地通知（独立于飞书，飞书挂了也能触达）
    _local_notify(msg, title=resolved_title)
