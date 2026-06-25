"""agent.run_sync 端到端（mock 发送）：发送→幂等 skip-dup、gate 拦截、缺失静默。"""

from __future__ import annotations

from akb2.feishu_sync import agent, idempotency, paths

from .conftest import write_ai_trends, write_github


def _redirect_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "repo_root", lambda: tmp_path)


def test_sends_then_skips_dup(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    write_ai_trends(vault, "2026-06-03")
    sent = []
    monkeypatch.setattr(agent, "send_card_dm", lambda oid, card: sent.append(card))
    sp = tmp_path / "state.json"

    rc1 = agent.run_sync(date="2026-06-03", vault_root=vault, target="dm", state_path=sp)
    assert rc1 == 0
    assert len(sent) == 1

    # 重跑 → skip-dup，不再发（SC-4）
    rc2 = agent.run_sync(date="2026-06-03", vault_root=vault, target="dm", state_path=sp)
    assert rc2 == 0
    assert len(sent) == 1


def test_gate_blocks_unapproved_ai_trends(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    write_ai_trends(vault, "2026-06-03", audit="fail")  # gate 不过
    sent = []
    monkeypatch.setattr(agent, "send_card_dm", lambda oid, card: sent.append(card))

    rc = agent.run_sync(
        date="2026-06-03", vault_root=vault, target="dm", state_path=tmp_path / "s.json"
    )
    assert rc == 0  # 静默（非失败）
    assert sent == []  # 草稿未发


def test_no_briefing_silent(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(agent, "send_card_dm", lambda oid, card: sent.append(card))
    rc = agent.run_sync(
        date="2026-06-03", vault_root=vault, target="dm", state_path=tmp_path / "s.json"
    )
    assert rc == 0  # SC-2 静默 exit 0
    assert sent == []


def test_send_failure_rolls_back_reservation(monkeypatch, tmp_path, vault, feishu_env):
    # 发送失败 → 回滚预留，重跑可重试（不会因预留漏发）（Codex P2 / SC-4）
    _redirect_logs(monkeypatch, tmp_path)
    write_github(vault, "2026-06-03")
    sp = tmp_path / "state.json"
    monkeypatch.setattr(agent.notify, "alert_failure", lambda msg: None)

    def boom(cid, card):
        raise RuntimeError("net down")

    monkeypatch.setattr(agent, "send_card", boom)
    rc1 = agent.run_sync(date="2026-06-03", vault_root=vault, target="group", state_path=sp)
    assert rc1 == 1
    # 预留已回滚：状态不含该条
    assert not idempotency.already_sent(sp, "2026-06-03", "github", "group")

    # 重跑：发送成功 → 重发（未被漏发）
    sent = []
    monkeypatch.setattr(agent, "send_card", lambda cid, card: sent.append(cid))
    rc2 = agent.run_sync(date="2026-06-03", vault_root=vault, target="group", state_path=sp)
    assert rc2 == 0
    assert sent


def test_corrupt_state_blocks_send(monkeypatch, tmp_path, vault, feishu_env):
    # 状态文件损坏 → 拒发（fail-closed）+ 告警 + exit 1（Codex P1 / SC-4）
    _redirect_logs(monkeypatch, tmp_path)
    write_github(vault, "2026-06-03")
    sp = tmp_path / "state.json"
    sp.write_text("{ corrupt", encoding="utf-8")
    sent = []
    monkeypatch.setattr(agent, "send_card", lambda cid, card: sent.append(cid))
    monkeypatch.setattr(agent, "send_card_dm", lambda oid, card: sent.append("DM!"))
    alerts = []
    monkeypatch.setattr(agent.notify, "alert_failure", lambda msg: alerts.append(msg))

    rc = agent.run_sync(date="2026-06-03", vault_root=vault, target="group", state_path=sp)
    assert rc == 1
    assert sent == []  # 损坏状态下绝不发送
    assert alerts


def test_group_target_uses_chat_id(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    write_github(vault, "2026-06-03")
    sent_group = []
    monkeypatch.setattr(agent, "send_card", lambda cid, card: sent_group.append(cid))
    monkeypatch.setattr(agent, "send_card_dm", lambda oid, card: sent_group.append("DM!"))

    rc = agent.run_sync(
        date="2026-06-03", vault_root=vault, target="group", state_path=tmp_path / "s.json"
    )
    assert rc == 0
    assert sent_group and all(c == "oc_group_dummy" for c in sent_group)  # 用 chat_id 不是 DM


def test_dry_run_does_not_send_or_write_state(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    write_ai_trends(vault, "2026-06-03")
    sent = []
    monkeypatch.setattr(agent, "send_card_dm", lambda oid, card: sent.append(card))
    sp = tmp_path / "state.json"

    rc = agent.run_sync(
        date="2026-06-03",
        vault_root=vault,
        target="dm",
        state_path=sp,
        dry_run=True,
    )

    assert rc == 0
    assert sent == []
    assert not sp.exists()
