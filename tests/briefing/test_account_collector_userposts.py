"""采集端点切换 search → user-posts 的行为测试（2026-07-28）。

背景：X 轮换了 SearchTimeline 的 persisted queryId，`twitter search` 恒 404，
UserTweets（`twitter user-posts`）等端点正常。采集切到 user-posts 后，
原来由 search 语法承担的三类过滤（--exclude retweets/replies、--since/--until）
移到 raw 层 `_post_filter`。本文件锁住这些过滤行为 —— user-posts 没有服务端
过滤，raw 层漏一条 RT 就会进简报。
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from akb2.briefing.intake import account_collector as ac


def _tweet(tid: str, *, text: str = "hello", iso: str = "2026-07-28T08:00:00+00:00",
           is_rt: bool = False) -> dict:
    return {
        "id": tid,
        "text": text,
        "author": {"screenName": "acct"},
        "createdAtISO": iso,
        "isRetweet": is_rt,
        "metrics": {"likes": 1},
    }


def _run_with_payload(monkeypatch, raw: list[dict], collector: ac.AccountCollector,
                      since: str | None = None, until: str | None = None) -> tuple[list[dict], list[list[str]]]:
    cmds: list[list[str]] = []

    def _run(cmd, **kwargs):
        cmds.append(cmd)
        return SimpleNamespace(
            returncode=0, stderr="",
            stdout=json.dumps({"ok": True, "data": raw}),
        )

    monkeypatch.setattr(subprocess, "run", _run)
    return collector._fetch_one("acct", since, until), cmds


def test_uses_userposts_endpoint_not_search(monkeypatch):
    """生产命令必须是 user-posts —— search 端点已被 X 下线（恒 404）。"""
    collector = ac.AccountCollector(pacing_sec=0.0)
    _, cmds = _run_with_payload(monkeypatch, [_tweet("1")], collector)
    cmd = cmds[0]
    assert cmd[1] == "user-posts"
    assert cmd[2] == "acct"
    assert "search" not in cmd
    assert "--from" not in cmd


def test_retweets_filtered_in_raw_layer(monkeypatch):
    """user-posts 无 --exclude retweets，RT 必须在 raw 层被剔除。"""
    collector = ac.AccountCollector(pacing_sec=0.0)
    raw = [_tweet("1"), _tweet("2", is_rt=True), _tweet("3")]
    tweets, _ = _run_with_payload(monkeypatch, raw, collector)
    assert [t["id"] for t in tweets] == ["1", "3"]


def test_replies_filtered_in_raw_layer(monkeypatch):
    collector = ac.AccountCollector(pacing_sec=0.0)
    raw = [_tweet("1"), _tweet("2", text="@someone thanks!"), _tweet("3")]
    tweets, _ = _run_with_payload(monkeypatch, raw, collector)
    assert [t["id"] for t in tweets] == ["1", "3"]


def test_date_window_filtered_in_raw_layer(monkeypatch):
    """--since/--until 语义移到 raw 层：窗外与缺时间戳的条目都不得进入。"""
    collector = ac.AccountCollector(pacing_sec=0.0)
    raw = [
        _tweet("old", iso="2026-07-20T08:00:00+00:00"),
        _tweet("in", iso="2026-07-27T08:00:00+00:00"),
        _tweet("nodate", iso=""),
        _tweet("future", iso="2026-07-30T08:00:00+00:00"),
    ]
    tweets, _ = _run_with_payload(
        monkeypatch, raw, collector, since="2026-07-25", until="2026-07-28"
    )
    assert [t["id"] for t in tweets] == ["in"]


def test_cap_enforced_after_filtering(monkeypatch):
    """抓取放大 2 倍是为补偿过滤损耗，最终产出必须仍 ≤ per_account_cap。"""
    collector = ac.AccountCollector(pacing_sec=0.0, per_account_cap=3)
    raw = [_tweet(str(i)) for i in range(10)]
    tweets, cmds = _run_with_payload(monkeypatch, raw, collector)
    assert len(tweets) == 3
    # 抓取量 = cap*2（有界放大）
    n_idx = cmds[0].index("-n")
    assert cmds[0][n_idx + 1] == "6"
