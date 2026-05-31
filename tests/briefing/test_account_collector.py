"""test_account_collector.py — curator 逐账号抓取 + run_intake collector 切换测试。

数据源治理：用策展 AI 账号逐账号抓取替代泛关键词搜索，解决内容噪音。
所有 twitter-cli 调用均 mock；不触网、不真实 sleep。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from akb2.briefing.intake import account_collector as ac


def _fake_proc(returncode=0, payload=None, stdout=None):
    if stdout is None:
        stdout = json.dumps(payload if payload is not None else {"ok": True, "data": []})
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def _tw(id_, screen, text="hello AI world"):
    return {
        "id": id_,
        "text": text,
        "author": {"screenName": screen},
        "createdAtISO": "2026-05-31T01:00:00Z",
        "metrics": {"likes": 5, "retweets": 1, "replies": 0},
    }


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------

def test_fetch_tier_en_count():
    """curator 配置 fetch_tier.en 应为 19 个账号（含 OpenAI/karpathy）。"""
    handles = ac._load_fetch_tier("en")
    assert len(handles) == 19, f"en 层应 19 个，实际 {len(handles)}"
    assert "OpenAI" in handles
    assert "karpathy" in handles


def test_fetch_tier_zh_count():
    """fetch_tier.zh 应为 11 个（v1 不抓，仅校验配置可读）。"""
    assert len(ac._load_fetch_tier("zh")) == 11


def test_fetch_tier_reserve_loads_from_top_level():
    """P3c 集成回归：reserve_tier 是顶层 key（非 fetch_tier.reserve）。

    _load_fetch_tier('reserve') 必须能读到顶层 reserve_tier，否则补抓没数据可补
    → 安静日 en 不足时无法补充 → 主题数偏少。
    """
    handles = ac._load_fetch_tier("reserve")
    assert len(handles) > 0, "reserve 层应能读到账号（P3c 补抓依赖它）"
    assert len(handles) == 69, f"reserve 应 69 个，实际 {len(handles)}"


# ---------------------------------------------------------------------------
# 单账号命令形态
# ---------------------------------------------------------------------------

def test_fetch_one_command_shape():
    """逐账号命令含 --from / 两次 --exclude(retweets,replies) / -t latest / --since/--until。"""
    col = ac.AccountCollector(pacing_sec=0)
    with patch.object(
        ac.subprocess, "run",
        return_value=_fake_proc(payload={"ok": True, "data": [_tw("1", "karpathy")]}),
    ) as m:
        col._fetch_one("karpathy", "2026-05-28", "2026-06-01")

    cmd = m.call_args[0][0]
    assert cmd[:3] == [ac.TWITTER_BIN, "search", ""]
    assert "--from" in cmd and cmd[cmd.index("--from") + 1] == "karpathy"
    # --exclude 必须重复两次（之前栽过 "--exclude retweets replies" 把 replies 当位置参数的坑）
    assert cmd.count("--exclude") == 2
    assert "retweets" in cmd and "replies" in cmd
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "latest"
    assert cmd[cmd.index("--since") + 1] == "2026-05-28"
    assert cmd[cmd.index("--until") + 1] == "2026-06-01"


# ---------------------------------------------------------------------------
# 聚合 / 去重 / 失败跳过 / 全空降级
# ---------------------------------------------------------------------------

def test_aggregates_and_dedups_across_accounts():
    """多账号结果聚合 + 按 id 去重（同一条被两账号返回只留一份）。"""
    col = ac.AccountCollector(pacing_sec=0)
    per_handle = {
        "a": _fake_proc(payload={"ok": True, "data": [_tw("1", "a"), _tw("2", "a")]}),
        "b": _fake_proc(payload={"ok": True, "data": [_tw("2", "a"), _tw("3", "b")]}),  # id=2 dup
    }

    def fake_run(cmd, **kw):
        return per_handle[cmd[cmd.index("--from") + 1]]

    with patch.object(ac, "_load_fetch_tier", return_value=["a", "b"]), \
         patch.object(ac.subprocess, "run", side_effect=fake_run), \
         patch.object(ac.time, "sleep"), \
         patch("pathlib.Path.exists", return_value=True):
        tweets = col.collect()

    assert sorted(t["id"] for t in tweets) == ["1", "2", "3"]
    assert col.used_fallback is False


def test_single_account_failure_does_not_abort():
    """单账号失败(exit≠0)被跳过，其余账号正常聚合。"""
    col = ac.AccountCollector(pacing_sec=0)

    def fake_run(cmd, **kw):
        h = cmd[cmd.index("--from") + 1]
        if h == "bad":
            return _fake_proc(returncode=1, stdout="")
        return _fake_proc(payload={"ok": True, "data": [_tw("9", "good")]})

    with patch.object(ac, "_load_fetch_tier", return_value=["bad", "good"]), \
         patch.object(ac.subprocess, "run", side_effect=fake_run), \
         patch.object(ac.time, "sleep"), \
         patch("pathlib.Path.exists", return_value=True):
        tweets = col.collect()

    assert [t["id"] for t in tweets] == ["9"]
    assert col.used_fallback is False


def test_all_empty_falls_back_to_fixture():
    """全部账号 0 条 → 降级 fixture，标 used_fallback。"""
    col = ac.AccountCollector(pacing_sec=0)
    with patch.object(ac, "_load_fetch_tier", return_value=["a", "b"]), \
         patch.object(ac.subprocess, "run", return_value=_fake_proc(payload={"ok": True, "data": []})), \
         patch.object(ac.time, "sleep"), \
         patch("pathlib.Path.exists", return_value=True):
        tweets = col.collect()

    assert col.used_fallback is True
    assert isinstance(tweets, list) and len(tweets) > 0  # fixture 有内容


def test_pacing_called_between_accounts():
    """账号间 pacing：N 个账号调用 sleep N-1 次（限流防护）。"""
    col = ac.AccountCollector(pacing_sec=3, supplement_threshold=0)  # 关闭 reserve 补抓
    with patch.object(ac, "_load_fetch_tier", return_value=["a", "b", "c"]), \
         patch.object(ac.subprocess, "run",
                      return_value=_fake_proc(payload={"ok": True, "data": [_tw("1", "a")]})), \
         patch.object(ac.time, "sleep") as sl, \
         patch("pathlib.Path.exists", return_value=True):
        col.collect()
    assert sl.call_count == 2


def test_dry_run_uses_fixture():
    """--dry-run 直接走 fixture，不触网。"""
    col = ac.AccountCollector()
    with patch.object(ac.subprocess, "run") as m:
        tweets = col.collect(dry_run=True)
    m.assert_not_called()
    assert col.used_fallback is True
    assert isinstance(tweets, list)


# ---------------------------------------------------------------------------
# run_intake collector 切换（默认 account，env keyword 回退）
# ---------------------------------------------------------------------------

def _intake_fake_run_factory(captured):
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        out = cmd[cmd.index("--output") + 1]
        with open(out, "w") as f:
            json.dump({"tweets": []}, f)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return fake_run


def test_run_intake_defaults_to_account_collector(monkeypatch, tmp_path):
    from akb2.briefing import pipeline as pl
    captured: dict = {}
    monkeypatch.delenv("AKB2_INTAKE_COLLECTOR", raising=False)
    with patch.object(pl.subprocess, "run", side_effect=_intake_fake_run_factory(captured)):
        pl.run_intake(dry_run=False, output_path=tmp_path / "o.json")
    assert "akb2.briefing.intake.account_collector" in captured["cmd"]


def test_run_intake_keyword_override(monkeypatch, tmp_path):
    from akb2.briefing import pipeline as pl
    captured: dict = {}
    monkeypatch.setenv("AKB2_INTAKE_COLLECTOR", "keyword")
    with patch.object(pl.subprocess, "run", side_effect=_intake_fake_run_factory(captured)):
        pl.run_intake(dry_run=False, output_path=tmp_path / "o.json")
    assert "akb2.briefing.intake.l0_collector" in captured["cmd"]


# ---------------------------------------------------------------------------
# P3c: reserve_tier 补抓（en 层不足时自动顶替）
# ---------------------------------------------------------------------------

def test_reserve_supplement_when_en_below_threshold():
    """SC-P3c-1: en 层推文数 < threshold 时，从 reserve_tier 顺序补抓到 threshold。"""
    col = ac.AccountCollector(pacing_sec=0, supplement_threshold=5)
    en_handles = ["a", "b"]
    reserve_handles = ["r1", "r2", "r3"]

    call_count = {"n": 0}

    def fake_run(cmd, **kw):
        h = cmd[cmd.index("--from") + 1]
        call_count["n"] += 1
        if h in en_handles:
            # en 层只给 1 条
            return _fake_proc(payload={"ok": True, "data": [_tw(f"en-{h}", h)]})
        # reserve 层各给 2 条
        return _fake_proc(payload={"ok": True, "data": [_tw(f"r-{h}-1", h), _tw(f"r-{h}-2", h)]})

    with patch.object(ac, "_load_fetch_tier", side_effect=lambda tier: en_handles if tier == "en" else reserve_handles), \
         patch.object(ac.subprocess, "run", side_effect=fake_run), \
         patch.object(ac.time, "sleep"), \
         patch("pathlib.Path.exists", return_value=True):
        tweets = col.collect()

    assert len(tweets) >= 5, f"补抓后应达到阈值 5，实际 {len(tweets)}"
    assert col.used_fallback is False


def test_reserve_not_called_when_en_sufficient():
    """SC-P3c-2: en 层充足时不调用 reserve_tier（不额外消耗配额）。"""
    col = ac.AccountCollector(pacing_sec=0, supplement_threshold=3)
    en_handles = ["a", "b"]

    reserve_called: list[str] = []

    def fake_run(cmd, **kw):
        h = cmd[cmd.index("--from") + 1]
        if h == "reserve_should_not_be_called":
            reserve_called.append(h)
        # en 层给足 5 条
        return _fake_proc(payload={"ok": True, "data": [
            _tw(f"{h}-{i}", h) for i in range(3)
        ]})

    with patch.object(ac, "_load_fetch_tier", side_effect=lambda tier: en_handles if tier == "en" else ["reserve_should_not_be_called"]), \
         patch.object(ac.subprocess, "run", side_effect=fake_run), \
         patch.object(ac.time, "sleep"), \
         patch("pathlib.Path.exists", return_value=True):
        tweets = col.collect()

    assert not reserve_called, f"en 充足时 reserve 不应被调用，实际调了: {reserve_called}"
    assert len(tweets) >= 3


def test_reserve_has_budget_cap():
    """SC-P3c-2: reserve 补抓有账号上限（max_reserve_accounts），不触发 429。"""
    col = ac.AccountCollector(pacing_sec=0, supplement_threshold=100, max_reserve_accounts=2)
    en_handles = ["a"]
    reserve_handles = ["r1", "r2", "r3", "r4", "r5"]  # 5个，但只允许调 2 个

    reserve_calls: list[str] = []

    def fake_run(cmd, **kw):
        h = cmd[cmd.index("--from") + 1]
        if h in reserve_handles:
            reserve_calls.append(h)
        return _fake_proc(payload={"ok": True, "data": [_tw(f"{h}-1", h)]})

    with patch.object(ac, "_load_fetch_tier", side_effect=lambda tier: en_handles if tier == "en" else reserve_handles), \
         patch.object(ac.subprocess, "run", side_effect=fake_run), \
         patch.object(ac.time, "sleep"), \
         patch("pathlib.Path.exists", return_value=True):
        col.collect()

    assert len(reserve_calls) <= 2, f"reserve 调用数应 ≤ max_reserve_accounts=2，实际 {len(reserve_calls)}"
