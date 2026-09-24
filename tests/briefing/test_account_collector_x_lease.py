"""social 早报账号采集持有 X 优先租约（2026-09-24 全链 review 补漏）。

同一 X 账号上 social 账号采集与 intake-twitter 共用额度；采集期间持租约让 intake 让路，
且与 GitHub L1 的租约并存、各自释放。
"""

from __future__ import annotations

import sys

import pytest

from akb2.briefing.intake import account_collector as ac
from akb2.common import x_api_coordination as xc


@pytest.fixture
def collector(monkeypatch):
    monkeypatch.setattr(ac, "TWITTER_BIN", sys.executable)  # 任意存在的文件即可
    monkeypatch.setattr(ac, "_load_fetch_tier", lambda tier=ac._DEFAULT_TIER: ["a", "b", "c"])
    return ac.AccountCollector(pacing_sec=0.0)


def test_lease_held_while_fetching_and_released_after(collector, monkeypatch):
    seen: list[str | None] = []

    def fetch_one(handle, since, until):
        seen.append(xc.active_priority_lease())
        return [{"id": handle}]

    monkeypatch.setattr(collector, "_fetch_one", fetch_one)
    tweets = collector._fetch_tier_raw("en")

    assert len(tweets) == 3
    assert seen == [ac.PRIORITY_LEASE_HOLDER] * 3
    assert xc.active_priority_lease() is None


def test_lease_released_when_collector_aborts(collector, monkeypatch):
    def fetch_one(handle, since, until):
        raise RuntimeError("boom")

    monkeypatch.setattr(collector, "_fetch_one", fetch_one)
    with pytest.raises(RuntimeError):
        collector._fetch_tier_raw("en")
    assert xc.active_priority_lease() is None, "异常中止也必须释放租约，否则 intake 被白卡 TTL"


def test_social_release_does_not_drop_github_lease(collector, monkeypatch):
    """两条早报重叠（GitHub backstop 07:30 与 social 07:30）：social 释放不得清掉 GitHub 的租约。"""
    xc.acquire_priority_lease("github_briefing_l1", 600)
    monkeypatch.setattr(collector, "_fetch_one", lambda h, s, u: [{"id": h}])
    collector._fetch_tier_raw("en")
    assert xc.active_priority_lease() == "github_briefing_l1"
