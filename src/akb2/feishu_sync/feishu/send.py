"""飞书消息发送 — interactive 卡片 / 文本，群(chat_id) 或私信(open_id)。

移植自 Project Doki feishu/card.py::send_card + feishu/dm.py::send_card_dm/send_dm。
差异：requests→httpx；**发送失败抛 RuntimeError**（上层据此触发双通道告警 + exit 1），
不像 Doki 仅记日志后继续。
"""

from __future__ import annotations

import json

import httpx

from .. import config
from .auth import auth_headers


def _post_message(receive_id: str, receive_id_type: str, msg_type: str, content_obj) -> dict:
    """POST /im/v1/messages，失败抛 RuntimeError。"""
    body = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": json.dumps(content_obj, ensure_ascii=False),
    }
    resp = httpx.post(
        f"{config.FEISHU_API_BASE}/im/v1/messages",
        headers=auth_headers(),
        params={"receive_id_type": receive_id_type},
        json=body,
        timeout=config.HTTP_TIMEOUT,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(
            f"飞书发送失败 ({receive_id_type}/{msg_type}): "
            f"code={data.get('code')} msg={data.get('msg')}"
        )
    return data


def send_card(chat_id: str, card: dict) -> dict:
    """发送 interactive 卡片到群。"""
    return _post_message(chat_id, "chat_id", "interactive", card)


def send_card_dm(open_id: str, card: dict) -> dict:
    """发送 interactive 卡片私信。"""
    return _post_message(open_id, "open_id", "interactive", card)


def send_text_dm(open_id: str, text: str) -> dict:
    """发送纯文本私信（用于告警兜底）。"""
    return _post_message(open_id, "open_id", "text", {"text": text})
