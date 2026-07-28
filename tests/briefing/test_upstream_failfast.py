"""上游 twitter-cli 全局故障的 fail-fast 与归因（2026-07-28 事故回归）。

事故实况：X 改版后 `x_client_transaction` 无法从 x.com 首页推导 ondemand bundle，
twitter-cli 每次调用都打 "Failed to init ClientTransaction" 并让 GraphQL 返回 404。
47 个账号逐个重试烧掉 793s、把 IP 推进 429，最终 0 条 → 降级 fixture → 被硬阻断，
连续两天无日报；而日志把成因归到 "先复核 curator-accounts.yaml 的 zh 账号"。

本文件锁住三件可观测行为：
1. 连续命中同一全局签名达阈值 → 立刻中止，不再遍历剩余账号（省时间、不触限流）
2. 上游故障导致的 0 产量 → 显式失败，**不降级 fixture**（不把工具坏了伪装成没素材）
3. 归因不得指向 curator 配置

分工提示（2026-07-28 追加 preflight 探针后）：**第 1 个账号就命中**的场景由探针
接管（回归在 `test_intake_preflight.py`）；本文件的连击护栏专管「跑到中途才坏」。
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from akb2.briefing.intake import account_collector as ac
from akb2.briefing.intake import l0_collector as l0
from akb2.briefing.intake.l0_collector import (
    UPSTREAM_FATAL_FAILFAST_STREAK,
    UpstreamCollectorBroken,
    match_upstream_fatal,
)

# 事故里 twitter-cli 逐字输出的 stderr
_FATAL_STDERR = (
    "WARNING twitter_cli.client: Failed to init ClientTransaction: "
    "'NoneType' object has no attribute 'group'"
)
_TRANSIENT_STDERR = "WARNING twitter_cli.client: Rate limited (429), retrying in 5.2s"


@pytest.fixture(autouse=True)
def _deterministic_diagnosis(monkeypatch):
    """成因归因里的两个辅助探测钉死：本文件断言的是中止行为，不是本机安装状态。"""
    monkeypatch.setattr(l0, "_login_state", lambda: ("authenticated", "stub"))
    monkeypatch.setattr(l0, "_patch_state", lambda: ("present", "stub"))


@pytest.fixture
def many_handles(monkeypatch):
    """47 个账号，与事故当天的账号规模一致。"""
    handles = [f"acct{i:02d}" for i in range(47)]
    monkeypatch.setattr(ac, "_load_fetch_tier", lambda tier=ac._DEFAULT_TIER: handles)
    monkeypatch.setattr(ac.Path, "exists", lambda self: True)
    return handles


def _handle_from_cmd(cmd: list[str]) -> str:
    # 2026-07-28 端点切换后 cmd = [bin, "user-posts", handle, "-n", N, "--json"]
    assert cmd[1] == "user-posts", f"采集端点应为 user-posts（search 已被 X 下线），实际: {cmd[1]}"
    return cmd[2]


def _stub_run(calls: list[str], *, stderr: str, returncode: int = 1, stdout: str = ""):
    def _run(cmd, **kwargs):
        calls.append(_handle_from_cmd(cmd))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    return _run


def test_match_upstream_fatal_only_matches_global_signature():
    assert match_upstream_fatal(_FATAL_STDERR) == "Failed to init ClientTransaction"
    # 单账号限流是可重试的瞬时问题，不能当全局故障
    assert match_upstream_fatal(_TRANSIENT_STDERR) is None
    assert match_upstream_fatal("") is None


def test_failfast_stops_after_streak_instead_of_sweeping_all_accounts(
    many_handles, monkeypatch, capsys
):
    """核心：不再遍历 47 个账号。缺陷版会跑满 47 个。

    第一个账号刻意给成功，把 preflight 探针放过去，专测「中途才坏」的连击护栏：
    探针之后再连续 STREAK 次命中同一签名就必须止损。
    """
    ok_payload = (
        '{"ok": true, "data": [{"id": "1", "text": "hi", '
        '"author": {"screenName": "a"}, "createdAtISO": "2026-07-28T00:00:00Z"}]}'
    )
    calls: list[str] = []

    def _run(cmd, **kwargs):
        handle = _handle_from_cmd(cmd)
        calls.append(handle)
        if handle == many_handles[0]:
            return SimpleNamespace(returncode=0, stdout=ok_payload, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr=_FATAL_STDERR)

    monkeypatch.setattr(subprocess, "run", _run)
    collector = ac.AccountCollector(pacing_sec=0.0)

    with pytest.raises(UpstreamCollectorBroken) as exc:
        collector._fetch_tier_raw("en")

    expected = ac.PREFLIGHT_PROBE_ACCOUNTS + UPSTREAM_FATAL_FAILFAST_STREAK
    assert len(calls) == expected, (
        f"探针放行后应在连续 {UPSTREAM_FATAL_FAILFAST_STREAK} 次命中时中止"
        f"（共 {expected} 次账号调用），实际调用了 {len(calls)} 个账号"
    )
    assert len(calls) < len(many_handles)
    assert "Failed to init ClientTransaction" in str(exc.value)
    # 报错必须点明「不是 curator 配置问题」，否则运维又被指错方向
    assert "curator 账号配置非成因" in str(exc.value)
    # 成因判定（登录态 / 补丁在位性 / 下一步）必须随异常一起送到运维眼前
    assert "成因判定" in str(exc.value)
    assert "下一步" in str(exc.value)


def test_transient_per_account_failure_does_not_trigger_failfast(
    many_handles, monkeypatch
):
    """反向保护：单账号限流/私密不得被误判为全局故障而中止整轮。"""
    calls: list[str] = []
    monkeypatch.setattr(subprocess, "run", _stub_run(calls, stderr=_TRANSIENT_STDERR))
    collector = ac.AccountCollector(pacing_sec=0.0)

    result = collector._fetch_tier_raw("en")

    assert result == []
    assert len(calls) == len(many_handles), "瞬时失败应继续遍历全部账号"
    assert collector.upstream_fatal_seen is None


def test_success_resets_streak(many_handles, monkeypatch):
    """交替失败/成功时不得累计到阈值（连击必须被成功打断）。"""
    ok_payload = (
        '{"ok": true, "data": [{"id": "1", "text": "hi", '
        '"author": {"screenName": "a"}, "createdAtISO": "2026-07-28T00:00:00Z"}]}'
    )
    calls: list[str] = []

    def _run(cmd, **kwargs):
        handle = _handle_from_cmd(cmd)
        calls.append(handle)
        idx = int(handle.replace("acct", ""))
        if idx % 2 == 0:  # 偶数账号成功
            return SimpleNamespace(returncode=0, stdout=ok_payload, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr=_FATAL_STDERR)

    monkeypatch.setattr(subprocess, "run", _run)
    collector = ac.AccountCollector(pacing_sec=0.0)

    result = collector._fetch_tier_raw("en")  # 不应抛

    assert len(calls) == len(many_handles)
    assert result, "偶数账号有产出，应正常返回"


def test_upstream_fatal_never_falls_back_to_fixture(many_handles, monkeypatch):
    """0 产量 + 已知上游故障 → 抛错，不得返回 fixture 数据。

    缺陷版返回 12 条 fixture 推文并置 used_fallback=True，正是 12 天假数据的路径。
    """
    monkeypatch.setattr(subprocess, "run", _stub_run([], stderr=_FATAL_STDERR))
    collector = ac.AccountCollector(pacing_sec=0.0)

    with pytest.raises(UpstreamCollectorBroken):
        collector.collect(dry_run=False)

    assert collector.used_fallback is False, "上游故障不得标记为 fixture 降级路径"


def test_zero_zh_attribution_does_not_blame_curator_config(monkeypatch, capsys):
    """归因分流：见过上游故障时，不得让运维去复核 curator-accounts.yaml。"""
    collector = ac.AccountCollector(pacing_sec=0.0)
    collector.upstream_fatal_seen = "Failed to init ClientTransaction"

    monkeypatch.setattr(
        ac.AccountCollector,
        "_fetch_tier_raw",
        lambda self, tier, *a, **kw: (
            [{"id": "1", "text": "x", "author": "@a", "created_at": "2026-07-28"}]
            if tier == "en"
            else []
        ),
    )

    collector.collect_merged(dry_run=False)
    err = capsys.readouterr().err

    assert "上游 twitter-cli 全局故障" in err
    # 不得再出现"去复核配置"的指引（事故里正是这句把运维引向错误方向）
    assert "先复核" not in err, "上游故障时不得让运维去复核 curator 配置"
    assert "**不是** curator-accounts.yaml 配置问题" in err


def test_zero_zh_without_upstream_fatal_still_points_at_config(monkeypatch, capsys):
    """反向：没见过上游故障时，保留原来的配置排查提示（别把有用信息删掉）。"""
    collector = ac.AccountCollector(pacing_sec=0.0)

    monkeypatch.setattr(
        ac.AccountCollector,
        "_fetch_tier_raw",
        lambda self, tier, *a, **kw: (
            [{"id": "1", "text": "x", "author": "@a", "created_at": "2026-07-28"}]
            if tier == "en"
            else []
        ),
    )

    collector.collect_merged(dry_run=False)
    err = capsys.readouterr().err

    assert "curator-accounts.yaml" in err
