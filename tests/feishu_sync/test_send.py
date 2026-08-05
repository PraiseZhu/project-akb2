"""send：mock httpx，断言 endpoint / retry / 节流。"""

from __future__ import annotations

import pytest

from akb2.feishu_sync.feishu import auth, send


class _Resp:
    def __init__(self, payload, *, status_code=200, headers=None):
        self._p = payload
        self.status_code = status_code
        self.headers = headers or {}

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


def _patch_seq(monkeypatch, payloads):
    monkeypatch.setattr(auth, "get_token", lambda: "tok")
    state = {"post": 0, "sleeps": []}

    def fake_post(url, headers=None, params=None, json=None, timeout=None):
        state["post"] += 1
        idx = min(state["post"] - 1, len(payloads) - 1)
        return payloads[idx]

    monkeypatch.setattr(send.httpx, "post", fake_post)
    monkeypatch.setattr(send.random, "uniform", lambda a, b: b)
    monkeypatch.setattr(send, "_sleep", lambda s: state["sleeps"].append(s))
    return state


def test_9499_retries_with_backoff_then_succeeds(monkeypatch):
    state = _patch_seq(monkeypatch, [
        _Resp({"code": 9499, "msg": "too many request"}),
        _Resp({"code": 9499, "msg": "too many request"}),
        _Resp({"code": 0}),
    ])

    send.send_card("oc_1", {"x": 1})

    assert state["post"] == 3
    assert state["sleeps"] == [1.0, 2.0]


def test_retry_after_header_is_respected(monkeypatch):
    state = _patch_seq(monkeypatch, [
        _Resp({"code": 9499, "msg": "too many request"}, headers={"Retry-After": "7"}),
        _Resp({"code": 0}),
    ])

    send.send_card("oc_1", {"x": 1})

    assert state["sleeps"][0] == 7.0


def test_9499_retry_exhausted_is_fail_visible(monkeypatch):
    from akb2.feishu_sync.feishu.send import FeishuSendError

    state = _patch_seq(monkeypatch, [_Resp({"code": 9499, "msg": "too many request"})])

    with pytest.raises(FeishuSendError) as exc:
        send.send_card("oc_1", {"x": 1})

    assert exc.value.code == 9499
    assert exc.value.exhausted is True
    assert "retry exhausted" in str(exc.value)
    assert state["post"] == send._MAX_RETRIES + 1


def test_hard_failure_raises_without_retry(monkeypatch):
    from akb2.feishu_sync.feishu.send import FeishuSendError

    state = _patch_seq(monkeypatch, [_Resp({"code": 99, "msg": "boom"})])

    with pytest.raises(FeishuSendError) as exc:
        send.send_card("oc_1", {"x": 1})

    assert exc.value.retryable is False
    assert state["post"] == 1
    assert state["sleeps"] == []


def test_min_send_interval_respected_without_real_sleep(monkeypatch):
    monkeypatch.setattr(auth, "get_token", lambda: "tok")
    monkeypatch.setenv("AKB2_FEISHU_MIN_SEND_INTERVAL", "0.3")

    state = {"now": 100.0, "posts": 0, "sleeps": []}

    def fake_monotonic():
        return state["now"]

    def fake_sleep(seconds):
        state["sleeps"].append(seconds)
        state["now"] += seconds

    def fake_post(url, headers=None, params=None, json=None, timeout=None):
        state["posts"] += 1
        return _Resp({"code": 0})

    monkeypatch.setattr(send.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(send, "_sleep", fake_sleep)
    monkeypatch.setattr(send.httpx, "post", fake_post)

    send.send_card("oc_1", {"x": 1})
    state["now"] += 0.1
    send.send_card("oc_1", {"x": 1})

    assert state["posts"] == 2
    assert state["sleeps"] == pytest.approx([0.2], abs=1e-9)
