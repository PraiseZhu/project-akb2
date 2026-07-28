"""无签名硬失败（超时 / 输出非 JSON）的 fail-fast（2026-07-28 对抗复核补漏）。

背景：签名护栏（`UPSTREAM_FATAL_SIGNATURES`）与 preflight 探针都只对「stderr 里出现
已登记字符串」的失败开火。但 twitter-cli 还有两条**不产生任何签名**的失败路径：
超时（每次赔满 `TWITTER_TIMEOUT_SEC=60s`）与 stdout 不可解析。
对抗复核实测：全账号超时时两道护栏都不开火，47 个账号跑满 = **2820s**，并把 IP 推进 429。

刻意的边界（都有反向测试钉住）：
- 429 限流**不算**硬失败 —— 它可重试，账号间 pacing 就是为它设计的，必须继续遍历
- 单次超时**不触发** preflight（探针预算是 1 个账号；一次网络抖动不该让当天无日报），
  只走连击（阈值 `UPSTREAM_HARD_FAILURE_FAILFAST_STREAK`）
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from akb2.briefing.intake import account_collector as ac
from akb2.briefing.intake import l0_collector as l0
from akb2.briefing.intake.l0_collector import (
    HARD_FAILURE_BAD_OUTPUT,
    HARD_FAILURE_TIMEOUT,
    UPSTREAM_HARD_FAILURE_FAILFAST_STREAK,
    UpstreamCollectorBroken,
)

_OK_PAYLOAD = (
    '{"ok": true, "data": [{"id": "1", "text": "hi", '
    '"author": {"screenName": "a"}, "createdAtISO": "2026-07-28T00:00:00Z"}]}'
)


@pytest.fixture(autouse=True)
def _deterministic_diagnosis(monkeypatch):
    """归因里的两个辅助探测钉死：本文件断言中止行为，不是本机安装状态。"""
    monkeypatch.setattr(l0, "_login_state", lambda: ("authenticated", "stub"))
    monkeypatch.setattr(l0, "_patch_state", lambda: ("present", "stub"))


@pytest.fixture
def many_handles(monkeypatch):
    handles = [f"acct{i:02d}" for i in range(47)]
    monkeypatch.setattr(ac, "_load_fetch_tier", lambda tier=ac._DEFAULT_TIER: handles)
    monkeypatch.setattr(ac.Path, "exists", lambda self: True)
    return handles


def _account_calls(calls: list[list[str]]) -> list[str]:
    """只数账号级抓取调用（排除诊断用的 `twitter status`）。"""
    return [c[2] for c in calls if len(c) > 2 and c[1] == "user-posts"]


def test_consecutive_timeouts_abort_instead_of_burning_all_accounts(
    many_handles, monkeypatch, capsys
):
    """核心：全账号超时时不再跑满 47 个。缺陷版跑满并赔 2820s。"""
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 60))

    monkeypatch.setattr(subprocess, "run", _run)
    collector = ac.AccountCollector(pacing_sec=0.0)

    with pytest.raises(UpstreamCollectorBroken) as exc:
        collector._fetch_tier_raw("en")

    tried = _account_calls(calls)
    assert len(tried) == UPSTREAM_HARD_FAILURE_FAILFAST_STREAK, (
        f"应在连续 {UPSTREAM_HARD_FAILURE_FAILFAST_STREAK} 次超时后中止，"
        f"实际调用了 {len(tried)} 个账号"
    )
    assert len(tried) < len(many_handles)
    assert HARD_FAILURE_TIMEOUT in str(exc.value)
    # 代价必须落到运维眼前（stderr），否则不知道为什么要立刻处理
    err = capsys.readouterr().err
    assert "中止本轮" in err
    assert "429" in err and "60s" in err


def test_consecutive_bad_output_aborts(many_handles, monkeypatch):
    """stdout 不可解析（工具坏了/被中间层改写）同样中止。"""
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="<html>not json</html>", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    collector = ac.AccountCollector(pacing_sec=0.0)

    with pytest.raises(UpstreamCollectorBroken) as exc:
        collector._fetch_tier_raw("en")

    assert len(_account_calls(calls)) == UPSTREAM_HARD_FAILURE_FAILFAST_STREAK
    assert HARD_FAILURE_BAD_OUTPUT in str(exc.value)


def test_rate_limit_still_sweeps_all_accounts(many_handles, monkeypatch):
    """反向保护：429 限流不得被当成硬失败中止整轮（它可重试，pacing 为它而设）。"""
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="WARNING twitter_cli.client: Rate limited (429), retrying in 5.2s",
        )

    monkeypatch.setattr(subprocess, "run", _run)
    collector = ac.AccountCollector(pacing_sec=0.0)

    assert collector._fetch_tier_raw("en") == []
    assert len(_account_calls(calls)) == len(many_handles), "限流应继续遍历全部账号"


def test_single_timeout_does_not_trip_preflight(many_handles, monkeypatch):
    """反向保护：单次超时不得触发 preflight（一次网络抖动 ≠ 当天无日报）。

    第 1 个账号超时、其余成功 → 必须跑完全部账号且有产出。
    """
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[2] == many_handles[0]:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 60))
        return SimpleNamespace(returncode=0, stdout=_OK_PAYLOAD, stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    collector = ac.AccountCollector(pacing_sec=0.0)

    result = collector._fetch_tier_raw("en")

    assert len(_account_calls(calls)) == len(many_handles)
    assert result, "其余账号有产出，不应因单次超时中止"


def test_success_resets_hard_failure_streak(many_handles, monkeypatch):
    """交替超时/成功不得累计到阈值（连击必须被成功打断）。"""
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        if int(cmd[2].replace("acct", "")) % 2 == 0:
            return SimpleNamespace(returncode=0, stdout=_OK_PAYLOAD, stderr="")
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 60))

    monkeypatch.setattr(subprocess, "run", _run)
    collector = ac.AccountCollector(pacing_sec=0.0)

    result = collector._fetch_tier_raw("en")  # 不应抛

    assert len(_account_calls(calls)) == len(many_handles)
    assert result


def test_hard_failure_never_falls_back_to_fixture(many_handles, monkeypatch):
    """硬失败中止路径不得产出 fixture 数据（12 天假日报的那条路）。"""

    def _run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 60))

    monkeypatch.setattr(subprocess, "run", _run)
    collector = ac.AccountCollector(pacing_sec=0.0)

    with pytest.raises(UpstreamCollectorBroken):
        collector.collect(dry_run=False)

    assert collector.used_fallback is False
