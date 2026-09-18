"""paths + 幂等/待补发状态。"""

from __future__ import annotations

import json

import pytest

from akb2.feishu_sync import idempotency
from akb2.feishu_sync.paths import repo_root, resolve_state_path


def test_default_state_path_absolute():
    p = resolve_state_path(None)
    assert p.is_absolute()
    assert p == repo_root() / "logs" / "feishu-sync-state.json"


def test_cli_arg_state_path(tmp_path):
    target = tmp_path / "s.json"
    assert resolve_state_path(str(target)) == target.resolve()


def test_state_path_independent_of_cwd(monkeypatch, tmp_path):
    p1 = resolve_state_path(None)
    monkeypatch.chdir(tmp_path)
    p2 = resolve_state_path(None)
    assert p1 == p2


def test_idempotency_roundtrip_new_format(tmp_path):
    sp = tmp_path / "state.json"
    assert not idempotency.already_sent(sp, "2026-06-03", "ai_trends", "dm")
    idempotency.mark_sent(sp, "2026-06-03", "ai_trends", "dm")
    assert idempotency.already_sent(sp, "2026-06-03", "ai_trends", "dm")
    data = idempotency.load_state(sp)
    assert "sent" in data["2026-06-03"]
    assert "ai_trends:dm" in data["2026-06-03"]["sent"]


def test_old_format_state_loads_and_upgrades(tmp_path):
    sp = tmp_path / "state.json"
    sp.write_text(
        json.dumps({"2026-08-02": {"ai_trends:group": "2026-08-02T10:00:07"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert idempotency.already_sent(sp, "2026-08-02", "ai_trends", "group")
    assert not idempotency.already_sent(sp, "2026-08-02", "github", "group")

    idempotency.mark_missing_expected(
        sp,
        date="2026-08-02",
        kind="github",
        target="group",
        expected_path="/vault/2026-08-02 GitHub Briefing.md",
        reason="failed: synthesis_failed",
    )
    data = idempotency.load_state(sp)
    assert data["2026-08-02"]["sent"]["ai_trends:group"] == "2026-08-02T10:00:07"
    pending = data["2026-08-02"]["pending"]["github:group"]
    assert pending["status"] == idempotency.PENDING_MISSING_EXPECTED
    assert pending["expected_path"].endswith("2026-08-02 GitHub Briefing.md")


def test_missing_expected_record_shape(tmp_path):
    sp = tmp_path / "state.json"
    idempotency.mark_missing_expected(
        sp,
        date="2026-08-02",
        kind="github",
        target="group",
        expected_path="/vault/2026-08-02 GitHub Briefing.md",
        reason="failed: synthesis_failed",
    )
    pending = idempotency.pending_items(
        sp,
        date="2026-08-02",
        target="group",
        statuses=(idempotency.PENDING_MISSING_EXPECTED,),
    )
    assert len(pending) == 1
    item = pending[0]
    assert item["kind"] == "github"
    assert item["date"] == "2026-08-02"
    assert item["expected_path"].endswith("2026-08-02 GitHub Briefing.md")
    assert item["reason"] == "failed: synthesis_failed"


def test_mark_sent_clears_pending(tmp_path):
    sp = tmp_path / "state.json"
    idempotency.mark_missing_expected(
        sp,
        date="2026-08-02",
        kind="github",
        target="group",
        expected_path="/vault/2026-08-02 GitHub Briefing.md",
        reason="failed: synthesis_failed",
    )
    idempotency.mark_sent(sp, "2026-08-02", "github", "group")
    assert idempotency.pending_items(sp, date="2026-08-02") == []


def test_clear_pending_drops_missing_expected_without_marking_sent(tmp_path):
    """不补发的缺失项应能清掉 pending，且不得伪造成已发送。"""
    sp = tmp_path / "state.json"
    idempotency.mark_missing_expected(
        sp,
        date="2026-09-14",
        kind="github",
        target="group",
        expected_path="/vault/2026-09-14 GitHub Briefing.md",
        reason="owner: do not backfill",
    )
    idempotency.clear_pending(sp, "2026-09-14", "github", "group")
    assert idempotency.pending_items(sp, date="2026-09-14") == []
    assert not idempotency.already_sent(sp, "2026-09-14", "github", "group")


def test_retryable_send_pending_roundtrip(tmp_path):
    sp = tmp_path / "state.json"
    idempotency.mark_send_failed_retryable(
        sp,
        date="2026-08-02",
        kind="github",
        target="group",
        reason="rate-limit retry exhausted",
        retry_after=7.0,
    )
    pending = idempotency.pending_items(
        sp,
        date="2026-08-02",
        statuses=(idempotency.PENDING_SEND_FAILED_RETRYABLE,),
    )
    assert len(pending) == 1
    assert pending[0]["retry_after"] == 7.0


def test_missing_state_is_first_run(tmp_path):
    sp = tmp_path / "nope.json"
    assert not idempotency.already_sent(sp, "d", "k", "t")


def test_corrupt_state_fails_closed(tmp_path):
    sp = tmp_path / "state.json"
    sp.write_text("{ broken", encoding="utf-8")
    with pytest.raises(idempotency.StateCorruptError):
        idempotency.already_sent(sp, "d", "k", "t")


def test_non_dict_state_fails_closed(tmp_path):
    sp = tmp_path / "state.json"
    sp.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(idempotency.StateCorruptError):
        idempotency.already_sent(sp, "d", "k", "t")


def test_unmark_sent_removes_entry(tmp_path):
    sp = tmp_path / "state.json"
    idempotency.mark_sent(sp, "2026-06-03", "github", "group")
    assert idempotency.already_sent(sp, "2026-06-03", "github", "group")
    idempotency.unmark_sent(sp, "2026-06-03", "github", "group")
    assert not idempotency.already_sent(sp, "2026-06-03", "github", "group")


def test_mark_sent_atomic_no_tmp_left(tmp_path):
    sp = tmp_path / "state.json"
    idempotency.mark_sent(sp, "2026-06-03", "ai_trends", "dm")
    assert sp.exists()
    assert not (tmp_path / "state.json.tmp").exists()
    assert idempotency.already_sent(sp, "2026-06-03", "ai_trends", "dm")


def test_reservation_release_is_owner_only(tmp_path):
    sp = tmp_path / "state.json"
    reservation = idempotency.reserve_if_unsent(sp, "2026-06-03", "github", "group")
    assert reservation is not None
    impostor = idempotency.SendReservation(
        state_path=sp, path=reservation.path, token="not-the-owner"
    )

    assert idempotency.release_reservation(impostor) is False
    assert reservation.path.exists()
    assert idempotency.release_reservation(reservation) is True
    assert not reservation.path.exists()
