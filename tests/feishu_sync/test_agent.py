"""agent.run_sync 端到端（stub 发送）。"""

from __future__ import annotations

from akb2.feishu_sync import agent, config, idempotency, paths

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

    rc2 = agent.run_sync(date="2026-06-03", vault_root=vault, target="dm", state_path=sp)
    assert rc2 == 0
    assert len(sent) == 1


def test_partial_ok_records_missing_then_reconcile_sends_only_github(
    monkeypatch, tmp_path, vault, feishu_env
):
    _redirect_logs(monkeypatch, tmp_path)
    write_ai_trends(vault, "2026-08-02")
    sp = tmp_path / "state.json"
    sent = {"ai_trends": 0, "github": 0}

    def fake_send(cid, card):
        title = card["header"]["title"]["content"]
        if "GitHub" in title:
            sent["github"] += 1
        else:
            sent["ai_trends"] += 1

    monkeypatch.setattr(agent, "send_card", fake_send)
    monkeypatch.setattr(agent.notify, "alert_failure", lambda msg: None)

    rc1 = agent.run_sync(
        date="2026-08-02",
        vault_root=vault,
        target="group",
        state_path=sp,
        expected=["ai_trends", "github"],
        strict=True,
        missing_policy=config.MISSING_POLICY_PARTIAL_OK_RECOVERABLE,
    )

    assert rc1 == 1
    assert sent == {"ai_trends": 1, "github": 0}
    pending = idempotency.pending_items(
        sp,
        date="2026-08-02",
        target="group",
        statuses=(idempotency.PENDING_MISSING_EXPECTED,),
    )
    assert len(pending) == 1
    assert pending[0]["kind"] == "github"

    write_github(vault, "2026-08-02")

    rc2 = agent.run_sync(
        date="2026-08-02",
        vault_root=vault,
        target="group",
        state_path=sp,
        reconcile_missing=True,
    )

    assert rc2 == 0
    assert sent == {"ai_trends": 1, "github": 1}
    day = idempotency.load_state(sp)["2026-08-02"]
    assert set(day["sent"]) == {"ai_trends:group", "github:group"}
    assert day["pending"] == {}


def test_fail_all_blocks_partial_send_but_keeps_pending(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    write_ai_trends(vault, "2026-08-02")
    sp = tmp_path / "state.json"
    sent = []
    monkeypatch.setattr(agent, "send_card", lambda cid, card: sent.append(card))
    monkeypatch.setattr(agent.notify, "alert_failure", lambda msg: None)

    rc = agent.run_sync(
        date="2026-08-02",
        vault_root=vault,
        target="group",
        state_path=sp,
        expected=["ai_trends", "github"],
        strict=True,
        missing_policy=config.MISSING_POLICY_FAIL_ALL,
    )

    assert rc == 1
    assert sent == []
    pending = idempotency.pending_items(
        sp,
        date="2026-08-02",
        target="group",
        statuses=(idempotency.PENDING_MISSING_EXPECTED,),
    )
    assert len(pending) == 1
    assert pending[0]["kind"] == "github"


def test_gate_blocks_unapproved_ai_trends(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    write_ai_trends(vault, "2026-06-03", audit="fail")
    sent = []
    monkeypatch.setattr(agent, "send_card_dm", lambda oid, card: sent.append(card))

    rc = agent.run_sync(
        date="2026-06-03", vault_root=vault, target="dm", state_path=tmp_path / "s.json"
    )
    assert rc == 0
    assert sent == []


def test_github_gate_failure_is_fail_visible(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    write_github(vault, "2026-06-03", with_json=False)
    sent = []
    alerts = []
    monkeypatch.setattr(agent, "send_card", lambda cid, card: sent.append(card))
    monkeypatch.setattr(agent.notify, "alert_failure", lambda msg: alerts.append(msg))

    rc = agent.run_sync(
        date="2026-06-03", vault_root=vault, target="group", state_path=tmp_path / "s.json"
    )

    assert rc == 1
    assert sent == []
    assert alerts and "skip-gate" in alerts[0]


def test_no_briefing_silent(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(agent, "send_card_dm", lambda oid, card: sent.append(card))
    rc = agent.run_sync(
        date="2026-06-03", vault_root=vault, target="dm", state_path=tmp_path / "s.json"
    )
    assert rc == 0
    assert sent == []


def test_send_failure_rolls_back_reservation_and_marks_retryable(monkeypatch, tmp_path, vault, feishu_env):
    from akb2.feishu_sync.feishu.send import FeishuSendError

    _redirect_logs(monkeypatch, tmp_path)
    write_github(vault, "2026-06-03")
    sp = tmp_path / "state.json"
    monkeypatch.setattr(agent.notify, "alert_failure", lambda msg: None)

    def boom(cid, card):
        raise FeishuSendError(
            9499,
            "rate-limit retry exhausted (too many request)",
            retryable=True,
            exhausted=True,
            retry_after=7.0,
        )

    monkeypatch.setattr(agent, "send_card", boom)
    rc1 = agent.run_sync(date="2026-06-03", vault_root=vault, target="group", state_path=sp)
    assert rc1 == 1
    assert not idempotency.already_sent(sp, "2026-06-03", "github", "group")
    retryable = idempotency.pending_items(
        sp,
        date="2026-06-03",
        statuses=(idempotency.PENDING_SEND_FAILED_RETRYABLE,),
    )
    assert retryable and retryable[0]["retry_after"] == 7.0

    sent = []
    monkeypatch.setattr(agent, "send_card", lambda cid, card: sent.append(cid))
    rc2 = agent.run_sync(date="2026-06-03", vault_root=vault, target="group", state_path=sp)
    assert rc2 == 0
    assert sent


def test_corrupt_state_blocks_send(monkeypatch, tmp_path, vault, feishu_env):
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
    assert sent == []
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
    assert sent_group and all(c == "oc_group_dummy" for c in sent_group)


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


def test_partial_expected_sources_fail_visible_dry_run(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    write_ai_trends(vault, "2026-06-03")
    sent: list = []
    monkeypatch.setattr(agent, "send_card_dm", lambda oid, card: sent.append(card))
    monkeypatch.setattr(agent, "send_card", lambda cid, card: sent.append(card))
    sp = tmp_path / "state.json"

    rc = agent.run_sync(
        date="2026-06-03",
        vault_root=vault,
        target="group",
        state_path=sp,
        dry_run=True,
        expected=["ai_trends", "github"],
        strict=True,
    )

    assert rc == 1
    assert sent == []
    assert not sp.exists()
    log_text = (tmp_path / "logs" / "feishu-sync.log").read_text(encoding="utf-8")
    assert "github 缺失" in log_text
    assert "GitHub Briefing.md" in log_text


def test_strict_both_present_dry_run_ok(monkeypatch, tmp_path, vault, feishu_env):
    _redirect_logs(monkeypatch, tmp_path)
    write_ai_trends(vault, "2026-06-03")
    write_github(vault, "2026-06-03")
    sent: list = []
    monkeypatch.setattr(agent, "send_card_dm", lambda oid, card: sent.append(card))
    monkeypatch.setattr(agent, "send_card", lambda cid, card: sent.append(card))
    sp = tmp_path / "state.json"

    rc = agent.run_sync(
        date="2026-06-03",
        vault_root=vault,
        target="group",
        state_path=sp,
        dry_run=True,
        expected=["ai_trends", "github"],
        strict=True,
    )

    assert rc == 0
    assert sent == []
    assert not sp.exists()
    log_text = (tmp_path / "logs" / "feishu-sync.log").read_text(encoding="utf-8")
    assert "ai_trends dry-run → group" in log_text
    assert "github dry-run → group" in log_text
