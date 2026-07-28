"""采集韧性：开跑前 preflight 探针 + 三种成因归因 + keyword 模式 fail-closed。

背景（2026-07-28 残余风险）：
1. twitter-cli 的 ClientTransaction 修复是**工作区外的本地补丁**，
   `uv tool upgrade twitter-cli` 会把它冲掉。原有护栏是"连续 3 个账号命中同一
   全局签名才中止"，也就是坏了之后才发现，且要先赔 3 次超时。
2. `l0_collector`（keyword 回退模式）所有失败分支都 `used_fallback=True` +
   读 fixture —— 一旦有人启用 keyword 模式，抓取失败会**静默**变成 12 条
   fixture 假推文冒充真实抓取（2026-06/07 连续 12 天假日报的成因）。

本文件锁住的可观测行为：
- preflight：上游坏时从生产入口进只发生 1 次账号级调用就中止（不是 3、不是 47）
- preflight 反向：上游正常时不得增加任何调用（探针 = 第一个正式账号，结果复用）
- 报错必须区分三种成因（登录态失效 / 补丁缺失 / 补丁在位但上游又变）并给下一步
- keyword 模式：search 端点不可用时**不产出 fixture 数据**，且 CLI 非零退出
"""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from akb2.briefing.intake import account_collector as ac
from akb2.briefing.intake import l0_collector as l0

# 在 autouse fixture 打桩之前抓住真身，供直接测 _patch_state 逻辑的用例调用。
_REAL_PATCH_STATE = l0._patch_state

_FATAL_STDERR = (
    "WARNING twitter_cli.client: Failed to init ClientTransaction: "
    "'NoneType' object has no attribute 'group'"
)
_SEARCH_404_STDERR = "ERROR twitter_cli.client: GraphQL SearchTimeline returned HTTP 404"

_OK_PAYLOAD = json.dumps(
    {
        "ok": True,
        "data": [
            {
                "id": "111",
                "text": "hello ai",
                "author": {"screenName": "acct00"},
                "createdAtISO": "2026-07-28T08:00:00+00:00",
                "metrics": {"likes": 3},
            }
        ],
    }
)


@pytest.fixture(autouse=True)
def _deterministic_diagnosis(monkeypatch):
    """默认把两个辅助诊断探测钉死，避免测试结论依赖本机 twitter-cli 安装状态。

    需要断言成因文案的测试会自行覆盖这两个 patch。
    """
    monkeypatch.setattr(l0, "_login_state", lambda: ("authenticated", "stub"))
    monkeypatch.setattr(l0, "_patch_state", lambda: ("present", "stub"))


@pytest.fixture
def bin_exists(monkeypatch):
    monkeypatch.setattr(ac.Path, "exists", lambda self: True)


def _account_cmds(monkeypatch, responder):
    """替换 subprocess.run，记录每次账号级调用的 handle。"""
    cmds: list[list[str]] = []

    def _run(cmd, **kwargs):
        cmds.append(list(cmd))
        return responder(cmd)

    monkeypatch.setattr(subprocess, "run", _run)
    return cmds


def _account_calls(cmds: list[list[str]]) -> list[str]:
    """只统计账号级抓取调用（排除诊断用的 `twitter status`）。"""
    return [c[2] for c in cmds if len(c) > 2 and c[1] == "user-posts"]


# ---------------------------------------------------------------------------
# A. preflight 探针（生产入口 _fetch_tier_raw / collect_merged）
# ---------------------------------------------------------------------------

def test_preflight_aborts_after_one_account_when_upstream_broken(monkeypatch, bin_exists):
    """上游坏 → 只发生 1 次账号级调用就中止（原护栏要赔 3 次，原始事故赔 47 次）。"""
    monkeypatch.setattr(
        ac, "_load_fetch_tier", lambda tier=ac._DEFAULT_TIER: [f"acct{i:02d}" for i in range(47)]
    )
    cmds = _account_cmds(
        monkeypatch,
        lambda cmd: SimpleNamespace(returncode=1, stdout="", stderr=_FATAL_STDERR),
    )
    collector = ac.AccountCollector(pacing_sec=0.0)

    with pytest.raises(l0.UpstreamCollectorBroken) as exc:
        collector._fetch_tier_raw("en")

    assert _account_calls(cmds) == ["acct00"], (
        f"preflight 应在第 1 个账号后中止，实际调用了 {_account_calls(cmds)}"
    )
    assert ac.PREFLIGHT_PROBE_ACCOUNTS == 1
    assert "Failed to init ClientTransaction" in str(exc.value)


def test_preflight_adds_no_extra_call_when_upstream_healthy(monkeypatch, bin_exists):
    """反向：上游正常时探针不得增加调用次数（探针 = 第一个正式账号，结果复用）。"""
    handles = ["a", "b", "c", "d", "e"]
    monkeypatch.setattr(ac, "_load_fetch_tier", lambda tier=ac._DEFAULT_TIER: handles)
    cmds = _account_cmds(
        monkeypatch,
        lambda cmd: SimpleNamespace(returncode=0, stdout=_OK_PAYLOAD, stderr=""),
    )
    collector = ac.AccountCollector(pacing_sec=0.0, supplement_threshold=0)

    tweets = collector._fetch_tier_raw("en")

    assert _account_calls(cmds) == handles, (
        f"每账号恰好 1 次调用；独立探针会多出一次，实际 {_account_calls(cmds)}"
    )
    # 探针账号的产出必须进聚合，不能被丢弃后重抓
    assert [t["id"] for t in tweets] == ["111"]
    assert all("-n" in c and c[c.index("-n") + 1] != "1" for c in cmds), (
        "不得存在 cap=1 的独立探针调用"
    )


def test_preflight_does_not_abort_on_stale_fatal_from_earlier_tier(monkeypatch, bin_exists):
    """探针判据必须只看自己这一次调用。

    生产入口 collect_merged 在同一个 collector 上先抓 en 再抓 zh。en 层中途抖动
    过一次全局签名（随后账号成功、连击已归零）会把粘滞的 `upstream_fatal_seen`
    留下来 —— 若探针拿它当判据，zh 层第一个账号明明成功也会被误杀。
    """
    monkeypatch.setattr(
        ac,
        "_load_fetch_tier",
        lambda tier: ["en0", "en1", "en2"] if tier in ("en", "reserve") else ["zh0", "zh1"],
    )

    def _responder(cmd):
        if cmd[2] == "en1":  # en 层中途抖一次全局签名
            return SimpleNamespace(returncode=1, stdout="", stderr=_FATAL_STDERR)
        return SimpleNamespace(returncode=0, stdout=_OK_PAYLOAD, stderr="")

    cmds = _account_cmds(monkeypatch, _responder)
    collector = ac.AccountCollector(pacing_sec=0.0, supplement_threshold=0)

    merged = collector.collect_merged(dry_run=False)

    assert collector.upstream_fatal_seen == "Failed to init ClientTransaction", (
        "前置条件：en 层确实留下了粘滞的全局签名"
    )
    assert "zh0" in _account_calls(cmds) and "zh1" in _account_calls(cmds), (
        f"zh 层不得被 en 层的陈旧签名误杀，实际调用 {_account_calls(cmds)}"
    )
    assert merged, "zh 探针成功即应正常产出"


def test_preflight_fires_on_production_merged_entry(monkeypatch, bin_exists):
    """生产入口是 collect_merged（pipeline 默认 --merge）：也必须 1 次调用即中止。"""
    monkeypatch.setattr(
        ac, "_load_fetch_tier", lambda tier=ac._DEFAULT_TIER: [f"acct{i:02d}" for i in range(47)]
    )
    cmds = _account_cmds(
        monkeypatch,
        lambda cmd: SimpleNamespace(returncode=1, stdout="", stderr=_FATAL_STDERR),
    )
    collector = ac.AccountCollector(pacing_sec=0.0)

    with pytest.raises(l0.UpstreamCollectorBroken):
        collector.collect_merged(dry_run=False)

    assert len(_account_calls(cmds)) == 1
    assert collector.used_fallback is False, "上游故障不得走 fixture 降级"


# ---------------------------------------------------------------------------
# B. 三种成因归因
# ---------------------------------------------------------------------------

def test_cause_login_expired(monkeypatch):
    monkeypatch.setattr(l0, "_login_state", lambda: ("unauthenticated", "authenticated=false"))
    monkeypatch.setattr(l0, "_patch_state", lambda: ("present", "/x/client.py"))

    text = l0.diagnose_upstream_fatal("Failed to init ClientTransaction")

    assert l0.CAUSE_LOGIN_EXPIRED in text
    assert "twitter login" in text
    assert l0.CAUSE_PATCH_MISSING not in text


def test_cause_patch_missing(monkeypatch):
    monkeypatch.setattr(l0, "_login_state", lambda: ("authenticated", "ok"))
    monkeypatch.setattr(l0, "_patch_state", lambda: ("absent", "/x/client.py"))

    text = l0.diagnose_upstream_fatal("Failed to init ClientTransaction")

    assert l0.CAUSE_PATCH_MISSING in text
    assert l0.PATCH_RUNBOOK in text
    assert "修复 1" in text, "补丁缺失必须指向 runbook 的重打补丁步骤"
    assert l0.CAUSE_LOGIN_EXPIRED not in text


def test_cause_patch_present_but_still_failing(monkeypatch):
    monkeypatch.setattr(l0, "_login_state", lambda: ("authenticated", "ok"))
    monkeypatch.setattr(l0, "_patch_state", lambda: ("present", "/x/client.py"))

    text = l0.diagnose_upstream_fatal("Failed to init ClientTransaction")

    assert l0.CAUSE_PATCH_PRESENT_UPSTREAM_CHANGED in text
    assert "验证方式" in text, "补丁在位仍失败必须指向 runbook 的验证三连命令"
    assert l0.PATCH_RUNBOOK in text


def test_patch_state_reads_real_marker(monkeypatch, tmp_path):
    """`_patch_state` 的判据是补丁注释块 marker，而不是"文件存在"。"""
    target = (
        tmp_path
        / ".local/share/uv/tools/twitter-cli/lib/python3.12/site-packages/twitter_cli"
    )
    target.mkdir(parents=True)
    client = target / "client.py"
    monkeypatch.setattr(l0.Path, "home", lambda: tmp_path)

    client.write_text("def _ensure_client_transaction(self): pass\n")
    assert _REAL_PATCH_STATE()[0] == "absent"

    client.write_text(f"# {l0.LOCAL_PATCH_MARKER}\nurl = 'https://x.com/home'\n")
    assert _REAL_PATCH_STATE()[0] == "present"


def test_preflight_error_message_carries_cause(monkeypatch, bin_exists):
    """成因判定必须真的挂在生产抛出的异常上（否则运维在日志里看不到）。"""
    monkeypatch.setattr(ac, "_load_fetch_tier", lambda tier=ac._DEFAULT_TIER: ["a", "b", "c"])
    monkeypatch.setattr(l0, "_login_state", lambda: ("authenticated", "ok"))
    monkeypatch.setattr(l0, "_patch_state", lambda: ("absent", "/x/client.py"))
    _account_cmds(
        monkeypatch,
        lambda cmd: SimpleNamespace(returncode=1, stdout="", stderr=_FATAL_STDERR),
    )
    collector = ac.AccountCollector(pacing_sec=0.0)

    with pytest.raises(l0.UpstreamCollectorBroken) as exc:
        collector._fetch_tier_raw("en")

    assert l0.CAUSE_PATCH_MISSING in str(exc.value)
    assert exc.value.diagnosis


# ---------------------------------------------------------------------------
# C. l0 keyword 模式 fail-closed
# ---------------------------------------------------------------------------

def _l0(monkeypatch, *, returncode=0, stdout="", stderr="") -> tuple[l0.L0Collector, list[list[str]]]:
    # 指向一个真实存在的可执行文件即可让 `Path(TWITTER_BIN).exists()` 为真；
    # 不能用 monkeypatch(Path.exists) —— 那会顺带让被测的输出文件断言也恒真。
    monkeypatch.setattr(l0, "TWITTER_BIN", sys.executable)
    cmds: list[list[str]] = []

    def _run(cmd, **kwargs):
        cmds.append(list(cmd))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", _run)
    collector = l0.L0Collector(config={"sources": {"l0": {"query": "AI", "max_results": 5}}})
    return collector, cmds


def test_keyword_endpoint_failure_produces_no_fixture_data(monkeypatch):
    """核心：search 端点失败时拒绝启动，绝不产出 fixture 数据。"""
    collector, _ = _l0(monkeypatch, returncode=1, stderr=_SEARCH_404_STDERR)

    with pytest.raises(l0.KeywordSearchEndpointUnavailable) as exc:
        collector.collect(dry_run=False)

    assert collector.used_fallback is False
    assert "404" in str(exc.value)
    assert "fixture" in str(exc.value), "报错必须说明为什么不降级 fixture"


def test_keyword_refusal_reports_upstream_fatal_signature(monkeypatch):
    """与 UPSTREAM_FATAL_SIGNATURES 打通：命中全局签名时报错要带签名 + 成因。"""
    collector, _ = _l0(monkeypatch, returncode=1, stderr=_FATAL_STDERR)

    with pytest.raises(l0.KeywordSearchEndpointUnavailable) as exc:
        collector.collect(dry_run=False)

    assert exc.value.signature == "Failed to init ClientTransaction"
    assert l0.CAUSE_PATCH_PRESENT_UPSTREAM_CHANGED in str(exc.value)


def test_keyword_zero_results_does_not_inject_fixture(monkeypatch):
    """0 条也不许降级 fixture —— "空结果"与"端点失效"无法区分，混淆即假数据。"""
    collector, _ = _l0(monkeypatch, stdout=json.dumps({"ok": True, "data": []}))

    with pytest.raises(l0.KeywordSearchEndpointUnavailable):
        collector.collect(dry_run=False)

    assert collector.used_fallback is False


def test_keyword_missing_binary_refuses_instead_of_fixture(monkeypatch, tmp_path):
    monkeypatch.setattr(l0, "TWITTER_BIN", str(tmp_path / "no-such-twitter"))
    collector = l0.L0Collector(config={"sources": {"l0": {"query": "AI", "max_results": 5}}})

    with pytest.raises(l0.KeywordSearchEndpointUnavailable):
        collector.collect(dry_run=False)

    assert collector.used_fallback is False


def test_keyword_healthy_path_uses_search_and_no_extra_probe(monkeypatch):
    """健康路径：仍走 search（keyword 跨账号搜索无等价端点），且只有 1 次调用。"""
    collector, cmds = _l0(monkeypatch, stdout=_OK_PAYLOAD)

    tweets = collector.collect(dry_run=False)

    assert [t["id"] for t in tweets] == ["111"]
    assert len(cmds) == 1, f"不得为可用性探测额外调用一次，实际 {cmds}"
    assert cmds[0][1] == "search"
    assert l0.KEYWORD_SEARCH_EXTRA_PROBE_CALLS == 0


def test_keyword_dry_run_still_reads_fixture(monkeypatch):
    """反向保护：显式 --dry-run 仍允许 fixture（它不冒充真实抓取）。"""
    collector = l0.L0Collector(config={"sources": {"l0": {}}})
    tweets = collector.collect(dry_run=True)

    assert collector.used_fallback is True
    assert len(tweets) > 0


def test_l0_cli_exits_nonzero_and_writes_no_output_on_refusal(monkeypatch, tmp_path):
    """CLI 契约：pipeline 只看退出码 + 输出文件；拒绝启动时不得留下 output。"""
    out = tmp_path / "l0-out.json"
    monkeypatch.setattr(
        l0.sys, "argv", ["l0_collector", "--output", str(out)]
    )
    monkeypatch.setattr(l0, "TWITTER_BIN", sys.executable)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr=_SEARCH_404_STDERR),
    )

    rc = l0.main()

    assert rc == 4, f"拒绝启动必须非零退出，实际 {rc}"
    assert not out.exists(), "拒绝启动不得写出 output（否则 pipeline 会当成真实抓取）"
