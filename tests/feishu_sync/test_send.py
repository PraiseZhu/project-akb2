"""send：mock httpx，断言 endpoint / receive_id_type / msg_type；失败抛异常。"""

from __future__ import annotations

import pytest

from akb2.feishu_sync.feishu import auth, send


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _patch(monkeypatch, payload):
    monkeypatch.setattr(auth, "get_token", lambda: "tok")
    captured: dict = {}

    def fake_post(url, headers=None, params=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, params=params, json=json)
        return _Resp(payload)

    monkeypatch.setattr(send.httpx, "post", fake_post)
    return captured


def test_send_card_to_group(monkeypatch):
    cap = _patch(monkeypatch, {"code": 0})
    send.send_card("oc_1", {"x": 1})
    assert cap["url"].endswith("/im/v1/messages")
    assert cap["params"]["receive_id_type"] == "chat_id"
    assert cap["json"]["msg_type"] == "interactive"
    assert cap["json"]["receive_id"] == "oc_1"


def test_send_card_dm(monkeypatch):
    cap = _patch(monkeypatch, {"code": 0})
    send.send_card_dm("ou_1", {"x": 1})
    assert cap["params"]["receive_id_type"] == "open_id"
    assert cap["json"]["msg_type"] == "interactive"


def test_send_text_dm(monkeypatch):
    cap = _patch(monkeypatch, {"code": 0})
    send.send_text_dm("ou_1", "hi")
    assert cap["json"]["msg_type"] == "text"


def test_send_failure_raises(monkeypatch):
    _patch(monkeypatch, {"code": 99, "msg": "boom"})
    with pytest.raises(RuntimeError):
        send.send_card("oc_1", {"x": 1})
