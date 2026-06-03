"""paths + 幂等：绝对 state-path 防 cwd 漂移；按 (date,kind,target) 去重（SC-4）。"""

from __future__ import annotations

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
    assert p1 == p2  # cwd 变化不影响（Codex P1）


def test_idempotency_roundtrip(tmp_path):
    sp = tmp_path / "state.json"
    assert not idempotency.already_sent(sp, "2026-06-03", "ai_trends", "dm")
    idempotency.mark_sent(sp, "2026-06-03", "ai_trends", "dm")
    assert idempotency.already_sent(sp, "2026-06-03", "ai_trends", "dm")
    # target / kind 维度独立
    assert not idempotency.already_sent(sp, "2026-06-03", "ai_trends", "group")
    assert not idempotency.already_sent(sp, "2026-06-03", "github", "dm")


def test_missing_state_is_first_run(tmp_path):
    sp = tmp_path / "nope.json"
    assert not idempotency.already_sent(sp, "d", "k", "t")  # 不存在=首跑，不抛


def test_corrupt_state_fails_closed(tmp_path):
    # 损坏状态 → 抛 StateCorruptError（拒发以防重复，Codex P1）
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
    # 原子写：不留 .tmp 残file
    assert not (tmp_path / "state.json.tmp").exists()
    # 写出的内容可被再次正常加载（不损坏）
    assert idempotency.already_sent(sp, "2026-06-03", "ai_trends", "dm")
