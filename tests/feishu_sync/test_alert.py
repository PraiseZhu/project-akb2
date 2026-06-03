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
