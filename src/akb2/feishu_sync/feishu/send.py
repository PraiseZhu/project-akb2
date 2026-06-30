"""飞书消息发送 — interactive 卡片 / 文本，群(chat_id) 或私信(open_id)。

移植自 Project Doki feishu/card.py::send_card + feishu/dm.py::send_card_dm/send_dm。
差异：requests→httpx；**发送失败抛 FeishuSendError**（上层据此触发双通道告警 + exit 1），
不像 Doki 仅记日志后继续。

SC-6：9499/429/临时 5xx/网络瞬断标 retryable，有上限指数退避 + jitter，尊重 Retry-After，
进程内最小发送间隔；重试耗尽才抛（文案含 "rate-limit retry exhausted"），硬失败立即抛。
"""

from __future__ import annotations

import json
import os
import random
import time

import httpx

from .. import config
from .auth import auth_headers

# 退避参数（可经 env 覆盖；MIN_SEND_INTERVAL 运行时读取以便测试注入）
_MAX_RETRIES = int(os.environ.get("AKB2_FEISHU_MAX_RETRIES", "5"))
_BASE_BACKOFF = float(os.environ.get("AKB2_FEISHU_BASE_BACKOFF", "1.0"))
_MAX_BACKOFF = float(os.environ.get("AKB2_FEISHU_MAX_BACKOFF", "30.0"))

# 9499 = 飞书 "too many request"（限流）；429 = HTTP 限流
_RETRYABLE_API_CODES: set[int] = {9499, 429}
_RETRYABLE_HTTP_STATUS: set[int] = {429, 500, 502, 503, 504}

# 进程内最近一次发送的 monotonic 时间（最小发送间隔节流）
_last_send_monotonic: float = 0.0


class FeishuSendError(RuntimeError):
    """飞书发送失败 — 区分可重试 vs 硬失败，携带 code/retry_after/exhausted。

    retryable=True 且 exhausted=True 表示退避重试耗尽仍失败（文案含
    "rate-limit retry exhausted"）；retryable=False 表示硬失败（如鉴权失败/参数错），
    立即抛、不重试。
    """

    def __init__(
        self,
        code: int,
        msg: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
        exhausted: bool = False,
    ):
        super().__init__(f"飞书发送失败 (code={code} msg={msg})")
        self.code = code
        self.msg = msg
        self.retryable = retryable
        self.retry_after = retry_after
        self.exhausted = exhausted


def _sleep(seconds: float) -> None:
    """可被测试 monkeypatch 拦截的 sleep。"""
    if seconds > 0:
        time.sleep(seconds)


def _backoff_delay(attempt: int, retry_after: float | None) -> float:
    """指数退避 + full jitter；尊重 Retry-After（封顶 _MAX_BACKOFF）。"""
    if retry_after and retry_after > 0:
        return min(retry_after, _MAX_BACKOFF)
    base = min(_BASE_BACKOFF * (2 ** attempt), _MAX_BACKOFF)
    return random.uniform(0.0, base)


def _parse_retry_after(resp) -> float | None:
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    try:
        ra = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:  # noqa: BLE001 — headers 可能非 dict
        return None
    if not ra:
        return None
    try:
        return float(ra)
    except (TypeError, ValueError):
        return None


def _is_retryable_http(resp) -> bool:
    status = getattr(resp, "status_code", 200)
    return status in _RETRYABLE_HTTP_STATUS


def _post_message(receive_id: str, receive_id_type: str, msg_type: str, content_obj) -> dict:
    """POST /im/v1/messages，失败抛 FeishuSendError（含退避重试）。"""
    global _last_send_monotonic

    # 进程内最小发送间隔，避免高频请求触发限流（运行时读取，测试可置 0）
    min_interval = float(os.environ.get("AKB2_FEISHU_MIN_SEND_INTERVAL", "0.5"))
    gap = time.monotonic() - _last_send_monotonic
    if gap < min_interval:
        _sleep(min_interval - gap)

    body = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": json.dumps(content_obj, ensure_ascii=False),
    }
    url = f"{config.FEISHU_API_BASE}/im/v1/messages"

    last_error: FeishuSendError | None = None
    for attempt in range(_MAX_RETRIES + 1):
        # 网络瞬断（连接/超时/读错误）→ retryable
        try:
            resp = httpx.post(
                url,
                headers=auth_headers(),
                params={"receive_id_type": receive_id_type},
                json=body,
                timeout=config.HTTP_TIMEOUT,
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as e:
            last_error = FeishuSendError(-1, f"network: {e}", retryable=True)
            if attempt < _MAX_RETRIES:
                _sleep(_backoff_delay(attempt, None))
                continue
            raise FeishuSendError(
                -1, f"rate-limit retry exhausted (network): {e}",
                retryable=True, exhausted=True,
            ) from e

        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001 — 非 JSON 响应属硬失败
            raise FeishuSendError(-1, f"non-json response: {e}", retryable=False) from e

        if data.get("code") == 0:
            _last_send_monotonic = time.monotonic()
            return data

        code = data.get("code")
        msg = data.get("msg")
        retryable = code in _RETRYABLE_API_CODES or _is_retryable_http(resp)
        retry_after = _parse_retry_after(resp) if retryable else None

        if retryable and attempt < _MAX_RETRIES:
            last_error = FeishuSendError(
                code, msg, retryable=True, retry_after=retry_after,
            )
            _sleep(_backoff_delay(attempt, retry_after))
            continue

        # 重试耗尽（仍 9499/429）→ 文案含 rate-limit retry exhausted
        if code in _RETRYABLE_API_CODES:
            raise FeishuSendError(
                code, f"rate-limit retry exhausted ({msg})",
                retryable=True, exhausted=True,
            )
        # 硬失败（鉴权/参数等）→ 立即抛，不重试
        raise FeishuSendError(code, msg, retryable=False)

    # 兜底：循环走完仍未返回（理论上不会到）
    if last_error is not None:
        raise FeishuSendError(
            last_error.code, f"rate-limit retry exhausted: {last_error.msg}",
            retryable=True, exhausted=True,
        )
    raise FeishuSendError(-1, "rate-limit retry exhausted", retryable=True, exhausted=True)


def send_card(chat_id: str, card: dict) -> dict:
    """发送 interactive 卡片到群。"""
    return _post_message(chat_id, "chat_id", "interactive", card)


def send_card_dm(open_id: str, card: dict) -> dict:
    """发送 interactive 卡片私信。"""
    return _post_message(open_id, "open_id", "interactive", card)


def send_text_dm(open_id: str, text: str) -> dict:
    """发送纯文本私信（用于告警兜底）。"""
    return _post_message(open_id, "open_id", "text", {"text": text})
