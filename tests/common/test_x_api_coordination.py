"""X 优先租约原语：GitHub 早报采集期间独占共用 X 账号（2026-09-23 事故回归）。

从 tests/quality/test_x_account_rate_limit.py 迁来：主链路 intake-twitter 已删，
租约仍由 GitHub 早报与社媒早报的 X 采集共用。
"""

from __future__ import annotations

import pytest

from src.akb2.common import x_api_coordination as xc


@pytest.fixture(autouse=True)
def _isolate_state_root(monkeypatch, tmp_path):
    # 租约文件落在 <state_root>/locks/，不隔离就会改到生产早报正在用的那份
    monkeypatch.setenv("AKB2_STATE_ROOT", str(tmp_path / "state"))


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
