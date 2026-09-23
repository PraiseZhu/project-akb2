"""GitHub 早报 L1 限流治理 + skip 诊断留痕（2026-09-23 四次 skip 事故回归）。

事故：L1 与 intake-twitter 共用 X 账号，61 个账号全 429，每次 L1 白耗约 35 分钟；
decide() 的逐层失败原因从未落盘，L2 当时为何失败无从追溯。
"""

from __future__ import annotations

import json
import subprocess
from unittest import mock

from akb2.briefing.github import pipeline as gh_pipeline
from akb2.briefing.github.intake import twitter_collector as tc
from akb2.briefing.github.intake.fallback_judge import FallbackDecision
from akb2.briefing.github.intake.trending_html_collector import TrendingHTMLCollector
from akb2.briefing.github.intake.twitter_collector import TwitterCollector
from akb2.common import x_api_coordination as xc

_TRENDING_HTML = "<html><body>" + "".join(
    f'<article class="Box-row"><h2 class="h3"><a href="/o{i}/r{i}">o{i}/r{i}</a></h2></article>'
    for i in range(12)
) + "</body></html>" + " " * 1200


def _pool(tmp_path, n):
    path = tmp_path / "pool.yaml"
    path.write_text("pool:\n" + "".join(f'  - handle: "@h{i}"\n' for i in range(n)))
    return path


def _rate_limited_fetch(collector, calls, lease_seen=None):
    def fetch(handle):
        calls.append(handle)
        if lease_seen is not None:
            lease_seen.append(xc.active_priority_lease())
        collector._last_fetch_rate_limited = True
        return None
    return fetch


class TestL1RateLimitBreaker:
    def test_waits_one_window_then_aborts_and_hands_over(self, tmp_path):
        """连续 429：等一次窗口后仍 429 → 放弃 L1，剩余账号记失败，不再逐个白打。"""
        collector = TwitterCollector(twitter_bin="fake", db_path=tmp_path / "d.db")
        calls: list[str] = []
        with (
            mock.patch.object(collector, "_health_check", return_value=True),
            mock.patch.object(collector, "_fetch_user_posts",
                              side_effect=_rate_limited_fetch(collector, calls)),
            mock.patch("time.sleep") as sleep,
        ):
            result = collector.collect(_pool(tmp_path, 20))

        breaker = tc.RATE_LIMIT_CONSECUTIVE_BREAKER
        assert len(calls) == 2 * breaker, "等过一次窗口后应在第二轮断路时停手"
        window_waits = [c for c in sleep.call_args_list
                        if c.args == (tc.RATE_LIMIT_WINDOW_WAIT_SEC,)]
        assert len(window_waits) == tc.MAX_RATE_LIMIT_WAITS
        assert not result.ok
        assert len(result.failed_handles) == 20, "未打的账号也必须计入失败（fail_rate 不失真）"
        assert "rate_limited" in result.error

    def test_recovers_after_window_wait(self, tmp_path):
        """窗口等待后恢复 → 继续采集，L1 正常产出。"""
        collector = TwitterCollector(twitter_bin="fake", db_path=tmp_path / "d.db")
        tweets = [{"id": "1", "full_text": f"https://github.com/a/r{i}", "isRetweet": False}
                  for i in range(6)]
        state = {"n": 0}

        def fetch(handle):
            state["n"] += 1
            if state["n"] <= tc.RATE_LIMIT_CONSECUTIVE_BREAKER:
                collector._last_fetch_rate_limited = True
                return None
            return tweets

        with (
            mock.patch.object(collector, "_health_check", return_value=True),
            mock.patch.object(collector, "_fetch_user_posts", side_effect=fetch),
            mock.patch("time.sleep"),
        ):
            result = collector.collect(_pool(tmp_path, 10))

        assert state["n"] == 10
        assert result.ok, result.error
        assert len(result.failed_handles) == tc.RATE_LIMIT_CONSECUTIVE_BREAKER

    def test_no_window_wait_when_breaker_trips_on_last_handle(self, tmp_path):
        """断路恰好落在最后一个账号：没有账号可续打，不白等窗口。"""
        collector = TwitterCollector(twitter_bin="fake", db_path=tmp_path / "d.db")
        calls: list[str] = []
        with (
            mock.patch.object(collector, "_health_check", return_value=True),
            mock.patch.object(collector, "_fetch_user_posts",
                              side_effect=_rate_limited_fetch(collector, calls)),
            mock.patch("time.sleep") as sleep,
        ):
            collector.collect(_pool(tmp_path, tc.RATE_LIMIT_CONSECUTIVE_BREAKER))

        assert all(c.args != (tc.RATE_LIMIT_WINDOW_WAIT_SEC,) for c in sleep.call_args_list)

    def test_non_rate_limit_failures_do_not_trigger_wait(self, tmp_path):
        collector = TwitterCollector(twitter_bin="fake", db_path=tmp_path / "d.db")
        with (
            mock.patch.object(collector, "_health_check", return_value=True),
            mock.patch.object(collector, "_fetch_user_posts", return_value=None),
            mock.patch("time.sleep") as sleep,
        ):
            result = collector.collect(_pool(tmp_path, 8))

        assert len(result.failed_handles) == 8
        assert all(c.args != (tc.RATE_LIMIT_WINDOW_WAIT_SEC,) for c in sleep.call_args_list)

    def test_priority_lease_held_during_collect_and_released_after(self, tmp_path):
        collector = TwitterCollector(twitter_bin="fake", db_path=tmp_path / "d.db")
        calls: list[str] = []
        lease_seen: list[str | None] = []
        with (
            mock.patch.object(collector, "_health_check", return_value=True),
            mock.patch.object(collector, "_fetch_user_posts",
                              side_effect=_rate_limited_fetch(collector, calls, lease_seen)),
            mock.patch("time.sleep"),
        ):
            collector.collect(_pool(tmp_path, 4))

        assert lease_seen and all(h == tc.PRIORITY_LEASE_HOLDER for h in lease_seen)
        assert xc.active_priority_lease() is None, "采集结束必须释放租约"


class TestFetchUserPostsPayload:
    def _run(self, stdout, stderr="", rc=0):
        collector = TwitterCollector(twitter_bin="fake")
        proc = subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)
        with mock.patch("subprocess.run", return_value=proc):
            return collector._fetch_user_posts("@x"), collector._last_fetch_rate_limited

    def test_exit0_with_ok_false_is_failure_not_empty(self):
        payload = json.dumps({"ok": False, "error": {"code": "rate_limited"}})
        tweets, limited = self._run(payload)
        assert tweets is None
        assert limited

    def test_exit1_429_stderr_marks_rate_limited(self):
        tweets, limited = self._run(
            "", stderr="WARNING twitter_cli.client: Rate limited (429), retrying", rc=1)
        assert tweets is None
        assert limited

    def test_ok_payload_returns_data(self):
        tweets, limited = self._run(json.dumps({"ok": True, "data": [{"id": "1"}]}))
        assert tweets == [{"id": "1"}]
        assert not limited


class TestTrendingRetry:
    def test_retries_once_then_succeeds(self):
        collector = TrendingHTMLCollector()
        with (
            mock.patch.object(collector, "_fetch_trending", side_effect=[None, _TRENDING_HTML]),
            mock.patch("time.sleep") as sleep,
        ):
            result = collector.collect()
        assert result.ok
        assert len(result.candidates) == 12
        sleep.assert_called_once()

    def test_all_attempts_fail_error_carries_cause(self):
        collector = TrendingHTMLCollector()
        proc = subprocess.CompletedProcess([], 28, stdout="", stderr="Operation timed out")
        with mock.patch("subprocess.run", return_value=proc), mock.patch("time.sleep"):
            result = collector.collect()
        assert not result.ok
        assert "rc=28" in result.error and "timed out" in result.error


def test_skip_diag_records_fallback_reason(tmp_path, monkeypatch):
    """0 候选 skip 时，逐层失败原因必须写进 briefing-skipped.json。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setattr(gh_pipeline, "_PROJECT_ROOT", tmp_path)
    reason = "All layers exhausted: L1=fail_rate=1.00, L2=HTTP request failed, L3=missing"
    l1 = mock.Mock(candidates=[], recency_stats={})
    with (
        mock.patch.object(gh_pipeline.TwitterCollector, "collect", return_value=l1),
        mock.patch.object(gh_pipeline, "decide",
                          return_value=FallbackDecision("skip", reason, [])),
    ):
        rc = gh_pipeline.run_pipeline(mock=False)

    assert rc == gh_pipeline.EXIT_NOT_DELIVERED
    skipped = json.loads((tmp_path / "briefing-skipped.json").read_text())
    assert skipped["reason"] == "no_candidates_after_intake"
    assert skipped["diag"]["fallback_reason"] == reason
