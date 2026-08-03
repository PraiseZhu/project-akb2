"""GitHub synthesis 的 **调用层** 测试 —— 打在 urlopen 上，不 mock `_call_deepseek`。

为什么必须打 urlopen：`tests/briefing/github/test_synthesis.py` 里 12 处
`monkeypatch.setattr(".._call_deepseek", ...)` 把整个函数换掉了，于是
`_call_deepseek` 内部的重试算术与错误分类**零覆盖**。两个真 bug 因此活到生产：

  1. `MAX_RETRIES=3` 配 `RETRY_BACKOFF=[5,15,45]` → 实际只等 `[5,15]`=20s，
     索引 2 的 45 永不执行（2026-08-02 日志 `elapsed=20.5s` 精确吻合），
     而读代码的人会以为最多等 65s。
  2. `except Exception` 一网打尽 → 401/403 这类不可重试错误照样烧满 3 次请求；
     预算耗尽型 429 也被当成瞬时限流退避重试（额度不会因为等待回来）。

覆盖分层（刻意如此）：
  - `_call_deepseek` = **单一模型族**的重试/分类循环 → SC-1/2/3/4 打这里
    （在 `synthesize()` 层看不到「urlopen 只调 1 次」，因为上面还有一层换族）
  - `synthesize()` = 主族失败后换族 → SC-6/7 打这里
"""

from __future__ import annotations

import io
import json
import time
from urllib.error import HTTPError, URLError

import pytest

from akb2.briefing.github import synthesis as syn
from akb2.common import http_retry

# ── 2026-08-01/08-02 生产实测的两种真实预算耗尽响应体（逐字，不是编的）──────────────
BUDGET_BODY_TOTAL = (
    "ExceededBudget: User=zhuzan over budget. "
    "Spend=18000.62608448676, Budget=18000.0"
)
BUDGET_BODY_UTC_DAILY = (
    "ExceededBudget: User=zhuzan exceeded UTC daily cap. "
    "Date=2026-08-01, Spend=2701.228476741798, DailyCap=2700.0"
)
# 瞬时限流：同样 429，但响应体不含预算字样 → 必须走退避重试，不能被误判成预算耗尽
RATE_LIMIT_BODY = '{"error": {"message": "Too many requests, slow down", "code": 429}}'

_SUMMARY = "这是一段足够具体的中文摘要，说明项目做什么、技术栈与解决的核心问题。"
_USECASE = "正向场景：需要该能力时可装。反向场景：不需要时别装。"


def _repos(n: int) -> list[dict]:
    return [
        {
            "repo_url": f"https://github.com/owner{i}/repo{i}",
            "stars": 100 + i,
            "language": "Python",
            "topic_tags": ["ai"],
            "mentioners": [f"@u{i}"],
            "description": f"Project {i} does something concrete and testable.",
            "readme_excerpt": f"Readme for project {i}.",
        }
        for i in range(n)
    ]


def _valid_items_payload(repos: list[dict]) -> bytes:
    items = [
        {"repo_url": r["repo_url"], "summary": _SUMMARY, "use_case": _USECASE}
        for r in repos
    ]
    return json.dumps(
        {"choices": [{"message": {"content": json.dumps(items, ensure_ascii=False)},
                      "finish_reason": "stop"}]}
    ).encode()


def _http_error(code: int, body: str, headers: dict | None = None) -> HTTPError:
    """每次都造新实例：HTTPError.read() 只能读一次，复用实例会让第 2 次读到空体。"""
    return HTTPError(
        syn.DEEPSEEK_API_URL, code, f"HTTP {code}", headers or {}, io.BytesIO(body.encode()),
    )


class _FakeResponse(io.BytesIO):
    """urlopen 的 context-manager 返回值替身。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """拦下退避 sleep 并收集实收参数（测试不真等，且能断言等了多久）。"""
    collected: list[float] = []
    monkeypatch.setattr(syn, "_sleep", lambda s: collected.append(s))
    return collected


@pytest.fixture
def no_family_fallback_env(monkeypatch):
    monkeypatch.delenv(syn.FAMILY_FALLBACK_ENV, raising=False)


@pytest.fixture(autouse=True)
def _clean_family_ledger():
    """族可用性闩是模块级的 —— 用例之间必须清干净，否则断言互相污染。"""
    syn._unavailable_families.clear()
    yield
    syn._unavailable_families.clear()


# ═══════════════════════════════════════════════════════════════════════════
# SC-1 / SC-4 — Retry-After 被尊重；重试次数与退避表自洽；耗尽文本自证总等待
# ═══════════════════════════════════════════════════════════════════════════

class TestRetryAfterAndBudgetArithmetic:
    def test_retry_after_header_is_respected_and_retries_exhaust(self, monkeypatch, sleeps):
        """SC-1：429 带 Retry-After: 7 → 第一次 sleep 恰为 7.0，尝试数 == MAX_RETRIES。

        旧实现只用 `str(e)` 拼错误信息，headers 与 body 全丢 → Retry-After 从未被读过，
        固定退避 5s/15s 在多进程并发下还会同步撞进同一个限流窗口。
        """
        calls: list[bytes] = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.data)
            raise _http_error(429, RATE_LIMIT_BODY, {"Retry-After": "7"})

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        with pytest.raises(syn.SynthesisAPIError) as exc:
            syn._call_deepseek("p", api_key="k")

        assert sleeps[0] == 7.0, f"未尊重 Retry-After，首次退避 {sleeps[0]}s"
        assert all(s == 7.0 for s in sleeps), f"Retry-After 只被读了一次: {sleeps}"
        assert len(calls) == syn.MAX_RETRIES, (
            f"尝试 {len(calls)} 次 != MAX_RETRIES={syn.MAX_RETRIES}"
        )
        assert len(sleeps) == syn.MAX_RETRIES - 1, "N 次尝试应有 N-1 次退避"
        assert exc.value.exhausted is True

    def test_max_retries_is_derived_from_backoff_table(self):
        """SC-4：两个常量必须从一个来源推导，否则声明的预算与真实预算会再次分叉。"""
        assert syn.MAX_RETRIES == len(syn.RETRY_BACKOFF) + 1, (
            f"MAX_RETRIES={syn.MAX_RETRIES} 与 RETRY_BACKOFF={syn.RETRY_BACKOFF} 不自洽："
            "退避表里会有永不执行的项（旧 bug：索引 2 的 45s 从未生效）"
        )
        assert syn.MAX_BACKOFF_S == max(syn.RETRY_BACKOFF)

    def test_exhaustion_message_reports_actual_total_wait(self, monkeypatch, sleeps):
        """SC-4：耗尽文案里的 total_wait 必须等于 sleep 实收参数之和（预算自证，P4）。

        旧文案是 `failed after {MAX_RETRIES} retries`，一个秒数都没有 —— 运维只能去读
        退避表反推，而退避表当时是错的（65s 声明 / 20s 实际）。
        """
        monkeypatch.setattr(
            syn, "urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(
                _http_error(429, RATE_LIMIT_BODY, {"Retry-After": "3"})
            ),
        )

        with pytest.raises(syn.SynthesisAPIError) as exc:
            syn._call_deepseek("p", api_key="k")

        expected = sum(sleeps)
        assert exc.value.total_wait_s == pytest.approx(expected)
        assert f"total_wait={expected:.1f}s" in str(exc.value), (
            f"耗尽文案没打出实收总等待 {expected:.1f}s: {exc.value}"
        )

    def test_jittered_backoff_without_retry_after_stays_within_table(self, monkeypatch, sleeps):
        """无 Retry-After 时退避取退避表上限 + jitter，且每次都 ≤ 表内对应上限。"""
        monkeypatch.setattr(
            syn, "urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(
                _http_error(503, "upstream down")
            ),
        )

        with pytest.raises(syn.SynthesisAPIError):
            syn._call_deepseek("p", api_key="k")

        assert len(sleeps) == syn.MAX_RETRIES - 1
        for i, delay in enumerate(sleeps):
            assert 0.0 <= delay <= syn.RETRY_BACKOFF[i], (
                f"第 {i} 次退避 {delay}s 超出表内上限 {syn.RETRY_BACKOFF[i]}s"
            )


# ═══════════════════════════════════════════════════════════════════════════
# SC-2 — 不可重试状态码立即抛，不消耗剩余尝试
# ═══════════════════════════════════════════════════════════════════════════

class TestNonRetryableStatusDoesNotBurnBudget:
    @pytest.mark.parametrize("code", [400, 401, 403, 404])
    def test_hard_failure_calls_once_and_never_sleeps(self, monkeypatch, sleeps, code):
        """SC-2：401/403/400/404 是配置或鉴权错，重试只是把同一个错再犯 N 次。"""
        calls: list[bytes] = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.data)
            raise _http_error(code, f'{{"error": "status {code}"}}')

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        with pytest.raises(syn.SynthesisAPIError) as exc:
            syn._call_deepseek("p", api_key="k")

        assert len(calls) == 1, f"不可重试的 {code} 烧掉了 {len(calls)} 次请求"
        assert sleeps == [], f"不可重试的 {code} 还退避了: {sleeps}"
        assert exc.value.exhausted is False
        assert exc.value.diagnosis is not None
        assert exc.value.diagnosis.retryable is False
        assert str(code) in str(exc.value)

    @pytest.mark.parametrize("code", sorted(http_retry.RETRYABLE_HTTP_STATUS))
    def test_retryable_status_set_matches_shared_policy(self, monkeypatch, sleeps, code):
        """可重试集合由共享模块单一定义；集合内每个码都真的走退避（不是写了不用）。"""
        calls: list[bytes] = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.data)
            raise _http_error(code, "transient")

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        with pytest.raises(syn.SynthesisAPIError):
            syn._call_deepseek("p", api_key="k")

        assert len(calls) == syn.MAX_RETRIES, f"{code} 应可重试，实际只试了 {len(calls)} 次"

    def test_network_error_is_retried_then_exhausts(self, monkeypatch, sleeps):
        """无状态码的纯网络异常（连接失败/超时）按瞬时故障退避重试。"""
        calls: list[int] = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise URLError("connection refused")

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        with pytest.raises(syn.SynthesisAPIError) as exc:
            syn._call_deepseek("p", api_key="k")

        assert len(calls) == syn.MAX_RETRIES
        assert exc.value.exhausted is True
        assert exc.value.diagnosis is not None
        assert exc.value.diagnosis.provider_code == 0


# ═══════════════════════════════════════════════════════════════════════════
# SC-3 — 预算耗尽双形态：fail-fast、零退避、契约 2 字段解析正确
# ═══════════════════════════════════════════════════════════════════════════

class TestBudgetExceededFailFast:
    @pytest.mark.parametrize(
        "body,expected_scope,expected_spend,expected_cap,expected_date",
        [
            (BUDGET_BODY_TOTAL, "total", 18000.62608448676, 18000.0, ""),
            (BUDGET_BODY_UTC_DAILY, "utc_daily", 2701.228476741798, 2700.0, "2026-08-01"),
        ],
        ids=["form_a_total_budget", "form_b_utc_daily_cap"],
    )
    def test_both_real_forms_fail_fast_with_contract2_fields(
        self, monkeypatch, sleeps, body, expected_scope,
        expected_spend, expected_cap, expected_date,
    ):
        """SC-3：两种真实形态都必须判成 budget_exceeded 并 5s 内 fail-fast。

        2026-08-02 主跑 3 次 + backstop 3 次共 **6 次同族尝试全 429 全预算耗尽**，
        每次都烧满退避 —— 预算耗尽不是「稍后会好」的瞬时故障，额度不会因为等待回来。
        只认一种形态就会漏判另一种，漏判即退回旧的硬重试行为。
        """
        calls: list[bytes] = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.data)
            raise _http_error(429, body, {"Retry-After": "60"})

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        started = time.monotonic()
        with pytest.raises(syn.SynthesisAPIError) as exc:
            syn._call_deepseek("p", api_key="k")
        elapsed = time.monotonic() - started

        assert len(calls) == 1, f"预算耗尽仍打了 {len(calls)} 次请求"
        assert sleeps == [], f"预算耗尽仍退避了 {sleeps}（重试无论多少次都无用）"
        assert elapsed < 5.0, f"fail-fast 用了 {elapsed:.2f}s"

        d = exc.value.diagnosis
        assert d is not None
        assert d.as_dict() == {
            "error_type": "budget_exceeded",
            "budget_scope": expected_scope,
            "spend": pytest.approx(expected_spend),
            "cap": pytest.approx(expected_cap),
            "budget_date": expected_date,
            "provider_code": 429,
        }
        # Retry-After: 60 存在也不许被采纳 —— 预算耗尽压根不该重试
        assert "60" not in str(sleeps)

    def test_rate_limit_429_is_not_mistaken_for_budget(self, monkeypatch, sleeps):
        """反向守护：不含预算字样的 429 是瞬时限流，必须照常退避重试。

        没有这条，「预算耗尽 fail-fast」很容易被写成「所有 429 都 fail-fast」，
        把真正该重试的限流也一并放弃。
        """
        calls: list[int] = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise _http_error(429, RATE_LIMIT_BODY)

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        with pytest.raises(syn.SynthesisAPIError) as exc:
            syn._call_deepseek("p", api_key="k")

        assert len(calls) == syn.MAX_RETRIES
        assert len(sleeps) == syn.MAX_RETRIES - 1
        assert exc.value.diagnosis.error_type == "rate_limit"
        assert exc.value.diagnosis.budget_scope == ""

    def test_budget_body_wrapped_in_json_envelope_still_detected(self):
        """生产响应体可能是裸文本也可能是 JSON 包装 —— 两者都要认出来。"""
        wrapped = json.dumps({"error": {"message": BUDGET_BODY_UTC_DAILY, "code": 429}})
        d = http_retry.classify_provider_error(429, wrapped)
        assert d.error_type == "budget_exceeded"
        assert d.budget_scope == "utc_daily"
        assert d.cap == 2700.0
        assert d.budget_date == "2026-08-01"


# ═══════════════════════════════════════════════════════════════════════════
# SC-5 — jitter 真的在抖
# ═══════════════════════════════════════════════════════════════════════════

class TestBackoffJitter:
    def test_backoff_delay_is_jittered_not_constant(self):
        """SC-5：同参数调 100 次，去重后必须 > 1 个值。

        固定退避序列在多进程并发（主跑 + backstop + 手动补跑）下会**同步**撞进
        同一个限流窗口 —— 越退避越同时。full jitter 是打散它的标准解法。
        """
        values = {
            http_retry.backoff_delay(0, None, schedule=syn.RETRY_BACKOFF,
                                     cap=syn.MAX_BACKOFF_S)
            for _ in range(100)
        }
        assert len(values) > 1, f"退避没有 jitter，100 次全是同一个值: {values}"
        assert all(0.0 <= v <= syn.RETRY_BACKOFF[0] for v in values)

    def test_retry_after_is_not_jittered(self):
        """provider 明说了等多久就等多久 —— 抖它等于抢跑（只做封顶）。"""
        values = {http_retry.backoff_delay(0, 7.0, cap=45.0) for _ in range(50)}
        assert values == {7.0}
        assert http_retry.backoff_delay(0, 999.0, cap=45.0) == 45.0

    def test_schedule_index_saturates_instead_of_raising(self):
        """attempt 超出退避表长度时取最后一项，不许 IndexError。"""
        delay = http_retry.backoff_delay(
            99, None, schedule=[1.0, 2.0, 3.0], cap=100.0,
        )
        assert 0.0 <= delay <= 3.0


# ═══════════════════════════════════════════════════════════════════════════
# SC-6 / SC-7 — 模型族降级（本 lane 最高价值项）
# ═══════════════════════════════════════════════════════════════════════════

class TestModelFamilyFallback:
    def test_primary_budget_exhausted_falls_back_to_second_family(
        self, monkeypatch, sleeps, no_family_fallback_env, capsys,
    ):
        """SC-6：主族恒 429、备族返回合法数组 → 简报照常产出，且降级可见。

        修复前 briefing 侧零降级余量：单点模型故障 = 当日简报 100% 缺失。
        而预算耗尽时退避完全无效，换族是**唯一**有效补救。
        """
        repos = _repos(10)
        seen_models: list[str] = []

        def fake_urlopen(req, timeout=None):
            model = json.loads(req.data)["model"]
            seen_models.append(model)
            if model == syn.PRIMARY_SYNTHESIS_MODEL:
                raise _http_error(429, BUDGET_BODY_UTC_DAILY)
            return _FakeResponse(_valid_items_payload(repos))

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        result = syn.synthesize(repos, api_key="k")

        assert len(result["top_repos"]) == 10
        assert result["synthesis_model"] == syn.FALLBACK_SYNTHESIS_MODEL, (
            "产出没记下实际使用的模型 → 降级变成静默降级（宪法 P4 禁止）"
        )
        assert seen_models == [syn.PRIMARY_SYNTHESIS_MODEL, syn.FALLBACK_SYNTHESIS_MODEL], (
            f"预算耗尽应只试主族 1 次就换族，实际请求序列: {seen_models}"
        )
        out = capsys.readouterr().out
        assert "SYNTHESIS_FALLBACK" in out, "降级没有 stdout 可见行"
        assert f"model={syn.FALLBACK_SYNTHESIS_MODEL}" in out
        assert "reason=budget_exceeded" in out

    def test_fallback_also_triggers_after_retry_exhaustion(
        self, monkeypatch, sleeps, no_family_fallback_env, capsys,
    ):
        """主族重试耗尽（持续限流）同样属「这一族现在不可用」→ 换族。"""
        repos = _repos(10)
        seen_models: list[str] = []

        def fake_urlopen(req, timeout=None):
            model = json.loads(req.data)["model"]
            seen_models.append(model)
            if model == syn.PRIMARY_SYNTHESIS_MODEL:
                raise _http_error(429, RATE_LIMIT_BODY)
            return _FakeResponse(_valid_items_payload(repos))

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        result = syn.synthesize(repos, api_key="k")

        assert result["synthesis_model"] == syn.FALLBACK_SYNTHESIS_MODEL
        assert seen_models.count(syn.PRIMARY_SYNTHESIS_MODEL) == syn.MAX_RETRIES
        assert seen_models[-1] == syn.FALLBACK_SYNTHESIS_MODEL
        assert "reason=rate_limit_exhausted" in capsys.readouterr().out

    def test_config_error_does_not_waste_a_second_family(
        self, monkeypatch, sleeps, no_family_fallback_env,
    ):
        """401 是鉴权/配置问题，换族只会在另一族上原样再错一次 → 不换。"""
        repos = _repos(10)
        seen_models: list[str] = []

        def fake_urlopen(req, timeout=None):
            seen_models.append(json.loads(req.data)["model"])
            raise _http_error(401, '{"error": "invalid api key"}')

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        with pytest.raises(syn.SynthesisAPIError, match="401"):
            syn.synthesize(repos, api_key="k")

        assert seen_models == [syn.PRIMARY_SYNTHESIS_MODEL], (
            f"配置错也去试了备族，白烧一族: {seen_models}"
        )

    def test_both_families_down_is_fail_closed_with_both_causes_visible(
        self, monkeypatch, sleeps, no_family_fallback_env,
    ):
        """两族都挂 → 抛错（fail-closed，P3），且两族真因都在文案里（P4）。"""
        repos = _repos(10)

        def fake_urlopen(req, timeout=None):
            model = json.loads(req.data)["model"]
            body = BUDGET_BODY_TOTAL if model == syn.PRIMARY_SYNTHESIS_MODEL else RATE_LIMIT_BODY
            raise _http_error(429, body)

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        with pytest.raises(syn.SynthesisAPIError) as exc:
            syn.synthesize(repos, api_key="k")

        msg = str(exc.value)
        assert "两个模型族均失败" in msg
        assert syn.PRIMARY_SYNTHESIS_MODEL in msg
        assert syn.FALLBACK_SYNTHESIS_MODEL in msg
        assert "budget_exceeded" in msg, "主族真因（预算耗尽）不可见"

    def test_env_flag_starts_from_fallback_family_with_zero_primary_calls(
        self, monkeypatch, sleeps, capsys,
    ):
        """SC-7 / 契约 1：AKB2_LLM_FAMILY_FALLBACK=1 → **首次**请求就打备族。

        供 Lane 3 的 backstop 第 2 轮补跑用：第 1 轮已确认主族打穿预算，
        第 2 轮再从主族试一次纯属白烧额度。
        """
        repos = _repos(10)
        seen_models: list[str] = []
        monkeypatch.setenv(syn.FAMILY_FALLBACK_ENV, "1")

        def fake_urlopen(req, timeout=None):
            seen_models.append(json.loads(req.data)["model"])
            return _FakeResponse(_valid_items_payload(repos))

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        result = syn.synthesize(repos, api_key="k")

        assert seen_models == [syn.FALLBACK_SYNTHESIS_MODEL], (
            f"契约 1 未生效，请求序列: {seen_models}"
        )
        assert syn.PRIMARY_SYNTHESIS_MODEL not in seen_models, "主族本应零请求"
        assert result["synthesis_model"] == syn.FALLBACK_SYNTHESIS_MODEL
        assert f"reason=env_{syn.FAMILY_FALLBACK_ENV}=1" in capsys.readouterr().out

    def test_json_retry_does_not_re_hit_a_family_proven_dead(
        self, monkeypatch, sleeps, no_family_fallback_env, capsys,
    ):
        """主族已判定预算耗尽后，JSON 重试分支**不得**再打主族一次。

        这是「明知耗尽还硬试」的换位复发：`synthesize()` 的 JSON 重试会二次调用合成，
        若不记账，刚死的主族会被原样再打一遍 —— 白烧一次请求，且在共享 Gateway 上
        把别的链路的额度一起拖下水。
        """
        repos = _repos(10)
        seen_models: list[str] = []
        fallback_bodies = [
            b'{"choices": [{"message": {"content": "\\u62b1\\u6b49\\uff0c\\u9700\\u8981"}}]}',
            _valid_items_payload(repos),
        ]

        def fake_urlopen(req, timeout=None):
            model = json.loads(req.data)["model"]
            seen_models.append(model)
            if model == syn.PRIMARY_SYNTHESIS_MODEL:
                raise _http_error(429, BUDGET_BODY_TOTAL)
            return _FakeResponse(fallback_bodies.pop(0))

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        result = syn.synthesize(repos, api_key="k")

        assert len(result["top_repos"]) == 10
        assert seen_models.count(syn.PRIMARY_SYNTHESIS_MODEL) == 1, (
            f"主族已判定预算耗尽却被重试分支再打了一次，请求序列: {seen_models}"
        )
        assert "reason=primary_already_known_unavailable" in capsys.readouterr().out

    def test_family_ledger_is_reset_between_synthesize_calls(
        self, monkeypatch, sleeps, no_family_fallback_env,
    ):
        """闩的生命周期是单次 synthesize —— 否则第二次调用会永久跳过主族。"""
        repos = _repos(10)
        syn._unavailable_families.add(syn.PRIMARY_SYNTHESIS_MODEL)
        seen_models: list[str] = []

        def fake_urlopen(req, timeout=None):
            seen_models.append(json.loads(req.data)["model"])
            return _FakeResponse(_valid_items_payload(repos))

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        result = syn.synthesize(repos, api_key="k")

        assert seen_models == [syn.PRIMARY_SYNTHESIS_MODEL], (
            f"上一次运行的族账本泄漏到本次: {seen_models}"
        )
        assert result["synthesis_model"] == syn.PRIMARY_SYNTHESIS_MODEL

    def test_happy_path_records_primary_model(
        self, monkeypatch, no_family_fallback_env,
    ):
        """未降级时 synthesis_model 必须是主族（否则 SC-6 的断言会恒真）。"""
        repos = _repos(10)

        monkeypatch.setattr(
            syn, "urlopen",
            lambda req, timeout=None: _FakeResponse(_valid_items_payload(repos)),
        )

        result = syn.synthesize(repos, api_key="k")
        assert result["synthesis_model"] == syn.PRIMARY_SYNTHESIS_MODEL

    def test_model_ids_come_from_llm_router_and_reject_preview(self):
        """模型 id 单一来源 = llm_router；`-preview` 在解析期就被拒（宪法 P7）。"""
        from akb2.quality.llm_router import RouterConfigError

        assert "-preview" not in syn.PRIMARY_SYNTHESIS_MODEL
        assert "-preview" not in syn.FALLBACK_SYNTHESIS_MODEL
        assert syn.PRIMARY_SYNTHESIS_MODEL != syn.FALLBACK_SYNTHESIS_MODEL, (
            "主备同族 = 没有降级余量"
        )
        with pytest.raises(RouterConfigError, match="预览模型"):
            syn._gateway_model_id("gemini-3.1-pro-preview")


# ═══════════════════════════════════════════════════════════════════════════
# 无 key / mock 路径 —— 保持 fail-closed，且被 urlopen 层测试覆盖到
# ═══════════════════════════════════════════════════════════════════════════

class TestKeyHandling:
    def test_missing_key_is_fail_closed_without_touching_network(self, monkeypatch):
        monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        calls: list[int] = []
        monkeypatch.setattr(
            syn, "urlopen", lambda req, timeout=None: calls.append(1),
        )

        with pytest.raises(RuntimeError, match="fail-closed"):
            syn._call_deepseek("p", api_key=None)
        assert calls == []

    def test_mock_opt_in_returns_marked_placeholder(self, monkeypatch):
        monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        resp = syn._call_deepseek(
            '<input>\n<repo url="https://github.com/o/r">\n</repo>\n</input>',
            api_key=None, allow_mock=True,
        )
        assert "[MOCK]" in resp["choices"][0]["message"]["content"]

    def test_request_carries_gateway_key_and_budgeted_max_tokens(self, monkeypatch):
        """请求体的模型/预算字段必须是具名常量，不是散落的字面量。"""
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data)
            captured["auth"] = req.get_header("Authorization")
            captured["timeout"] = timeout
            return _FakeResponse(b'{"choices": [{"message": {"content": "[]"}}]}')

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)
        syn._call_deepseek("p", api_key="secret-key")

        assert captured["body"]["model"] == syn.PRIMARY_SYNTHESIS_MODEL
        assert captured["body"]["max_tokens"] == syn.MAX_OUTPUT_TOKENS
        assert captured["auth"] == "Bearer secret-key"
        assert captured["timeout"] == syn.REQUEST_TIMEOUT_S

    def test_malformed_transport_json_is_retried_as_transient(self, monkeypatch, sleeps):
        """网关吐非 JSON（502 页面等）→ 按瞬时故障退避，不是静默返回 None。"""
        calls: list[int] = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            return _FakeResponse(b"<html>bad gateway</html>")

        monkeypatch.setattr(syn, "urlopen", fake_urlopen)

        with pytest.raises(syn.SynthesisAPIError):
            syn._call_deepseek("p", api_key="k")
        assert len(calls) == syn.MAX_RETRIES


# ═══════════════════════════════════════════════════════════════════════════
# 调用层辅助函数 —— 错误体读取与 sleep 的降级分支也要有覆盖
# ═══════════════════════════════════════════════════════════════════════════

class TestCallLayerHelpers:
    def test_error_body_read_failure_degrades_to_empty_not_crash(self):
        """读响应体自身失败（fp 已关）不许把归因流程带崩 —— 归因失败 ≠ 请求失败。"""

        class _Unreadable:
            def read(self):
                raise ValueError("fp closed")

        assert syn._read_error_body(_Unreadable()) == ""

    def test_error_body_accepts_str_payload(self):
        """有些 httpx/urllib 组合下 read() 已经是 str，不该被 decode 逻辑漏掉。"""

        class _StrBody:
            def read(self):
                return BUDGET_BODY_TOTAL

        assert syn._read_error_body(_StrBody()) == BUDGET_BODY_TOTAL
        assert syn._read_error_body(type("N", (), {"read": lambda self: None})()) == ""

    def test_sleep_wrapper_only_sleeps_for_positive_delay(self, monkeypatch):
        """full jitter 可能给出 0.0 —— 那时不该真调 time.sleep。"""
        slept: list[float] = []
        monkeypatch.setattr(syn.time, "sleep", lambda s: slept.append(s))

        syn._sleep(0.0)
        assert slept == []
        syn._sleep(0.01)
        assert slept == [0.01]
