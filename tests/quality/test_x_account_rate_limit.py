"""intake-twitter 限流治理：429 断路 + 冷却 + 给 GitHub 早报让路（2026-09-23 事故回归）。

事故：本链路日均约 1270 次 429 仍照打，把共用 X 账号的限流窗口持续占满，
GitHub 早报 L1 61 个账号全 429、四次补跑全部 0 候选。
"""

from __future__ import annotations

from pathlib import Path

from src.akb2.common import x_api_coordination as xc
from src.akb2.quality.intake import x_account_discovery as xd

_RATE_LIMITED = (
    [], None,
    "WARNING twitter_cli.client: Rate limited (429), retrying in 5.8s (attempt 1/3)",
)


def _source(n: int) -> dict:
    return {"slug": "x-test", "type": "x_account", "status": "active",
            "x_handles": [f"h{i}" for i in range(n)]}


def _tweet(i: int) -> dict:
    return {"id": str(i), "text": f"notes {i}", "author": {"screenName": "h", "name": "h"},
            "createdAtISO": "2026-08-20T08:00:00+00:00", "isRetweet": False,
            "urls": [f"https://example.com/p{i}"], "metrics": {"likes": 1}}


def test_consecutive_429_trips_breaker_and_starts_cooldown(tmp_path: Path):
    calls: list[str] = []

    def fetch(handle):
        calls.append(handle)
        return _RATE_LIMITED

    result = xd.discover_x_account_articles(
        _source(20), tmp_path / "ledger.jsonl", pacing_sec=0.0, fetch_one=fetch)

    assert len(calls) == xd.RATE_LIMIT_CONSECUTIVE_BREAKER, "断路后不得继续打剩余账号"
    assert "rate limited" in result.error
    assert xc.cooldown_remaining() > 0


def test_cooldown_skips_source_without_calling_x(tmp_path: Path):
    xc.start_cooldown(600, "test")
    calls: list[str] = []

    def fetch(handle):
        calls.append(handle)
        return ([], None, "")

    result = xd.discover_x_account_articles(
        _source(5), tmp_path / "ledger.jsonl", pacing_sec=0.0, fetch_one=fetch)

    assert calls == []
    assert "cooldown" in result.error


def test_yields_mid_source_when_priority_lease_appears(tmp_path: Path):
    """GitHub 早报中途拿到租约 → 本源在下一个账号前停手，已发现条目保留。"""
    calls: list[str] = []

    def fetch(handle):
        calls.append(handle)
        if len(calls) == 2:
            xc.acquire_priority_lease("github_briefing_l1", 600)
        return ([_tweet(len(calls))], None, "")

    result = xd.discover_x_account_articles(
        _source(6), tmp_path / "ledger.jsonl", pacing_sec=0.0, fetch_one=fetch)

    assert len(calls) == 2
    assert "yielded" in result.error
    assert len(result.articles) == 2


def test_non_consecutive_429_does_not_trip(tmp_path: Path):
    seq = [_RATE_LIMITED, _RATE_LIMITED, ([], None, ""), _RATE_LIMITED, _RATE_LIMITED,
           ([], None, "")]
    calls: list[int] = []

    def fetch(handle):
        calls.append(1)
        return seq[len(calls) - 1]

    result = xd.discover_x_account_articles(
        _source(6), tmp_path / "ledger.jsonl", pacing_sec=0.0, fetch_one=fetch)

    assert len(calls) == 6
    assert result.error is None
    assert xc.cooldown_remaining() == 0


def test_lease_expires_and_release_is_holder_scoped():
    xc.acquire_priority_lease("a", 60, now=1000.0)
    assert xc.active_priority_lease(now=1030.0) == "a"
    assert xc.active_priority_lease(now=1061.0) is None
    xc.release_priority_lease("b")
    assert xc.active_priority_lease(now=1030.0) == "a", "非持有方不得释放租约"
    xc.release_priority_lease("a")
    assert xc.active_priority_lease(now=1030.0) is None


def test_multiple_holders_coexist_and_renewal_extends():
    xc.acquire_priority_lease("a", 60, now=1000.0)
    xc.acquire_priority_lease("b", 600, now=1000.0)
    xc.release_priority_lease("a")
    assert xc.active_priority_lease(now=1030.0) == "b", "释放 a 不得清掉 b"
    xc.acquire_priority_lease("b", 600, now=1500.0)  # 续租
    assert xc.active_priority_lease(now=1700.0) == "b"
