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


# ---------------------------------------------------------------------------
# SC-6: 9499/429 退避重试 — retryable 退避 + 重试耗尽 fail-visible
# ---------------------------------------------------------------------------


def _patch_seq(monkeypatch, payloads):
    """httpx.post 按序返回 payloads；记录调用次数与 sleep 序列。"""
    monkeypatch.setattr(auth, "get_token", lambda: "tok")
    state = {"post": 0, "sleeps": []}

    def fake_post(url, headers=None, params=None, json=None, timeout=None):
        state["post"] += 1
        idx = min(state["post"] - 1, len(payloads) - 1)  # 耗尽测试时重复最后一项
        return _Resp(payloads[idx])

    monkeypatch.setattr(send.httpx, "post", fake_post)
    # full jitter 返回上界，使退避序列确定（base=1.0 → [1.0, 2.0, 4.0, ...]）
    monkeypatch.setattr(send.random, "uniform", lambda a, b: b)
    monkeypatch.setattr(send, "_sleep", lambda s: state["sleeps"].append(s))
    return state


def test_9499_retries_with_backoff_then_succeeds(monkeypatch):
    """SC-6: 前 2 次 9499、第 3 次 code=0 → 成功；调用 3 次，sleep 序列 [1.0, 2.0]。"""
    state = _patch_seq(monkeypatch, [
        {"code": 9499, "msg": "too many request"},
        {"code": 9499, "msg": "too many request"},
        {"code": 0},
    ])

    send.send_card("oc_1", {"x": 1})

    assert state["post"] == 3, f"应调用 3 次（2×9499 + 1×ok），实际 {state['post']}"
    assert state["sleeps"] == [1.0, 2.0], f"退避序列应为 [1.0, 2.0]，实际 {state['sleeps']}"


def test_9499_retry_exhausted_is_fail_visible(monkeypatch):
    """SC-6: 持续 9499 → 退避重试耗尽 → FeishuSendError(exhausted, 含 retry exhausted)。"""
    from akb2.feishu_sync.feishu.send import FeishuSendError

    state = _patch_seq(monkeypatch, [{"code": 9499, "msg": "too many request"}])

    with pytest.raises(FeishuSendError) as exc:
        send.send_card("oc_1", {"x": 1})

    assert exc.value.code == 9499
    assert exc.value.exhausted is True
    assert "retry exhausted" in str(exc.value)
    # 重试次数 = _MAX_RETRIES + 1（首次 + 5 次重试）
    assert state["post"] == send._MAX_RETRIES + 1


def test_hard_failure_raises_without_retry(monkeypatch):
    """SC-6: 硬失败（code=99）立即抛、不重试（仅 1 次调用、无 sleep）。"""
    from akb2.feishu_sync.feishu.send import FeishuSendError

    state = _patch_seq(monkeypatch, [{"code": 99, "msg": "boom"}])

    with pytest.raises(FeishuSendError) as exc:
        send.send_card("oc_1", {"x": 1})

    assert exc.value.retryable is False
    assert state["post"] == 1, "硬失败不应重试"
    assert state["sleeps"] == [], "硬失败不应退避"
