"""告警双通道（SC-5）：飞书通道挂了，本地通道仍触达；corrupt 文件 → exit 1。"""

from __future__ import annotations

from akb2.feishu_sync import agent, notify, paths
from akb2.feishu_sync.feishu import send as send_mod

from .conftest import write_ai_trends


def _redirect_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "repo_root", lambda: tmp_path)


def test_alert_dual_channel_when_feishu_down(monkeypatch, tmp_path, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise RuntimeError("feishu down")

    monkeypatch.setattr(send_mod, "send_text_dm", boom)
    called = {"local": False}
    monkeypatch.setattr(notify, "_local_notify", lambda msg: called.__setitem__("local", True))

    # 飞书通道抛错也不应让 alert_failure 崩
    notify.alert_failure("boom-msg")

    alert_log = tmp_path / "logs" / "feishu-sync-ALERT.log"
    assert alert_log.exists()
    assert "boom-msg" in alert_log.read_text(encoding="utf-8")
    assert called["local"] is True  # 飞书挂了，本地通道仍告警


def test_corrupt_file_returns_exit1(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    write_ai_trends(vault, "2026-06-03", raw="broken: no frontmatter here")
    alerts = []
    monkeypatch.setattr(agent.notify, "alert_failure", lambda msg: alerts.append(msg))

    rc = agent.run_sync(
        date="2026-06-03", vault_root=vault, target="dm", state_path=tmp_path / "s.json"
    )
    assert rc == 1
    assert alerts  # 解析失败触发告警


# ---------------------------------------------------------------------------
# SC-8: 告警通道不复用受限飞书 API
# ---------------------------------------------------------------------------


def test_alert_feishu_error_does_not_reuse_dm(monkeypatch, tmp_path, feishu_env):
    """SC-8: FeishuSendError(9499) → 写 ALERT log + 本地通知 + send_text_dm 未被调用。"""
    from akb2.feishu_sync.feishu.send import FeishuSendError

    _redirect_logs(monkeypatch, tmp_path)

    dm_called: list = []
    monkeypatch.setattr(send_mod, "send_text_dm", lambda *a, **k: dm_called.append(1))
    local_called: list = []
    monkeypatch.setattr(notify, "_local_notify", lambda msg: local_called.append(msg))

    notify.alert_failure(
        "rate-limited send",
        error=FeishuSendError(9499, "too many request", retryable=True, exhausted=True),
    )

    alert_log = tmp_path / "logs" / "feishu-sync-ALERT.log"
    assert alert_log.exists()
    assert "rate-limited send" in alert_log.read_text(encoding="utf-8")
    assert local_called, "本地通知应被调用"
    assert dm_called == [], "send_text_dm 不应被调用（不复用受限飞书 API）"


def test_alert_non_feishu_error_still_tries_dm(monkeypatch, tmp_path, feishu_env):
    """SC-8: 非飞书错误（如缺简报）仍 best-effort 发 DM，不抑制。"""
    _redirect_logs(monkeypatch, tmp_path)

    dm_called: list = []
    monkeypatch.setattr(send_mod, "send_text_dm", lambda *a, **k: dm_called.append(1))
    monkeypatch.setattr(notify, "_local_notify", lambda msg: None)

    notify.alert_failure("github 缺失（期望路径: /x/2026-06-30 GitHub Briefing.md）")

    assert dm_called, "非飞书错误应仍尝试飞书 DM"


def test_dry_run_smoke_no_feishu_send_or_alert_dm(monkeypatch, tmp_path, vault, feishu_env):
    """SC-8: dry-run 冒烟不触发任何飞书发送/告警 DM。"""
    _redirect_logs(monkeypatch, tmp_path)
    write_ai_trends(vault, "2026-06-03")

    sent: list = []
    monkeypatch.setattr(agent, "send_card_dm", lambda *a, **k: sent.append(1))
    monkeypatch.setattr(agent, "send_card", lambda *a, **k: sent.append(1))
    dm_called: list = []
    monkeypatch.setattr(send_mod, "send_text_dm", lambda *a, **k: dm_called.append(1))

    rc = agent.run_sync(
        date="2026-06-03", vault_root=vault, target="group",
        state_path=tmp_path / "s.json", dry_run=True,
    )
    assert rc == 0
    assert sent == [], "dry-run 不应发送"
    assert dm_called == [], "dry-run 不应发告警 DM"
