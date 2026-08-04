from __future__ import annotations

import ast
import datetime as dt
import inspect
import json
import sys
import textwrap
import types

import pytest

from src.akb2.common import budget_gate, http_retry
from src.akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file
from src.akb2.quality import llm_router
from src.akb2.quality.llm_router import LLMProviderError, route_llm

# Gateway 预算耗尽的两种真实响应体（2026-08-01/08-02 生产实测原文，逐字保留）。
# 形态 A 是 R1 审查复现的漏判源：它**不含** "total cap"/"total budget" 字样，
# llm_router 旧的本地解析器因此归出 scope=''，闸门永不短路。
FORM_A_TOTAL_BODY = (
    "ExceededBudget: User=zhuzan over budget. "
    "Spend=18000.62608448676, Budget=18000.0"
)
FORM_B_UTC_DAILY_BODY = (
    "ExceededBudget: User=zhuzan exceeded UTC daily cap. "
    "Date=2026-08-01, Spend=2701.228476741798, DailyCap=2700.0"
)


def _install_fake_openai(monkeypatch, create_fn):
    class _FakeCompletions:
        def create(self, **kwargs):
            return create_fn(**kwargs)

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=_FakeCompletions())

    class _FakeAPIError(RuntimeError):
        def __init__(self, message: str, *, status_code: int, body):
            super().__init__(message)
            self.message = message
            self.status_code = status_code
            self.body = body

    fake_openai = types.SimpleNamespace(
        OpenAI=_FakeOpenAI,
        APITimeoutError=TimeoutError,
        APIError=_FakeAPIError,
    )
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    return _FakeAPIError


@pytest.mark.usefixtures("_isolate_quality_state")
def test_budget_gate_short_circuits_all_consumers_without_real_http(
    monkeypatch, caplog
):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")
    monkeypatch.setenv("AKB2_BUDGET_GATE_NOW", "2026-08-01T21:30:00Z")
    budget_gate.record_budget_event(
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.BUDGET_EXCEEDED,
            budget_scope=budget_gate.UTC_DAILY_SCOPE,
            spend=2701.23,
            cap=2700.0,
            budget_date="2026-08-01",
            provider_code=budget_gate.PROVIDER_CODE_429,
            raw_message="ExceededBudget: exceeded UTC daily cap.",
        ),
        recorded_at=dt.datetime(2026, 8, 1, 21, 14, tzinfo=dt.UTC),
        consumer="seed",
    )

    create_calls: list[dict[str, object]] = []

    def _unexpected_create(**kwargs):
        create_calls.append(kwargs)
        raise AssertionError("budget gate should short-circuit before HTTP")

    _install_fake_openai(monkeypatch, _unexpected_create)

    for consumer in (
        "github.synthesize",
        "quality.admission",
        "quality.distill",
    ):
        with pytest.raises(LLMProviderError, match="blocked_by_shared_budget"):
            route_llm(
                "deepseek-v4-pro",
                [{"role": "user", "content": "test"}],
                budget_consumer=consumer,
            )

    assert create_calls == []
    assert "consumer=github.synthesize" in caplog.text
    assert "consumer=quality.admission" in caplog.text
    assert "consumer=quality.distill" in caplog.text


@pytest.mark.usefixtures("_isolate_quality_state")
def test_llm_router_records_structured_budget_diagnostics(monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")

    def _raise_429(**kwargs):
        error_body = {
            "error": {
                "message": (
                    "ExceededBudget: exceeded UTC daily cap. "
                    "Date=2026-08-01, Spend=2701.23, DailyCap=2700.0"
                )
            }
        }
        raise api_error(
            error_body["error"]["message"],
            status_code=429,
            body=error_body,
        )

    api_error = _install_fake_openai(monkeypatch, _raise_429)

    with pytest.raises(LLMProviderError, match="LLM API 错误"):
        route_llm(
            "deepseek-v4-pro",
            [{"role": "user", "content": "test"}],
            budget_consumer="quality.admission",
        )

    ledger_path = resolve_state_file("quality-ledger.jsonl", subdir=LEDGER_SUBDIR)
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])

    assert record["provider_code"] == "429"
    assert record["error_type"] == "budget_exceeded"
    assert record["budget_scope"] == "utc_daily"
    assert record["spend"] == 2701.23
    assert record["cap"] == 2700.0

    state = budget_gate.load_state_snapshot()
    assert state["last_error"]["error_type"] == "budget_exceeded"
    assert state["last_error"]["budget_scope"] == "utc_daily"


# ---------------------------------------------------------------------------
# SC-R1-01 —— 429 两形态由**单一**分类器一致映射为具名 scope
# ---------------------------------------------------------------------------


def _executable_source(obj) -> str:
    """对象的可执行代码（丢掉注释与 docstring）。

    静态「唯一 owner」断言必须只看代码：注释里出现 "total cap" 是**在解释**这条
    缺陷，不是在实现它；不剥注释的断言会把正确的修复也判红（本轮实测踩过一次）。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            body.pop(0)
    return ast.unparse(tree)


def _route_and_capture_budget_state(monkeypatch, body_text: str) -> dict[str, object]:
    """走完整 route_llm → record_budget_event 真实路径，返回落盘后的封锁槽。"""
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")

    def _raise_429(**kwargs):
        raise api_error(body_text, status_code=429, body={"error": {"message": body_text}})

    api_error = _install_fake_openai(monkeypatch, _raise_429)

    with pytest.raises(LLMProviderError):
        route_llm(
            "deepseek-v4-pro",
            [{"role": "user", "content": "test"}],
            budget_consumer="quality.distill",
        )
    return budget_gate.load_state_snapshot()


@pytest.mark.usefixtures("_isolate_quality_state")
def test_total_form_classified_via_single_shared_classifier(monkeypatch):
    """形态 A（total）经 llm_router 真实路径必须落成 scope='total' 并真的短路。

    绑定的可观测行为有三层，缺一层都能让这条缺陷复发：
      ① 落盘的 active_block.budget_scope == 'total'（旧代码是 ''）
      ② query_budget_gate 真的 allowed=False（旧代码 allowed=True
         reason='budget_cooldown_expired' —— 连归因都是误导的）
      ③ 全仓只有一处 429 分类实现：llm_router 委派给 http_retry，不再自带解析器
    """
    monkeypatch.setenv("AKB2_BUDGET_GATE_NOW", "2026-08-01T21:30:00Z")
    state = _route_and_capture_budget_state(monkeypatch, FORM_A_TOTAL_BODY)

    block = state["active_block"]
    assert block["error_type"] == budget_gate.BUDGET_EXCEEDED
    assert block["budget_scope"] == budget_gate.TOTAL_SCOPE, (
        "形态 A 必须归出 total scope；空 scope 会让 cooldown 算不出来"
    )
    assert block["cap"] == 18000.0
    assert block["spend"] == 18000.62608448676
    assert block["cooldown_until"], "total scope 必须有具名 cooldown 边界"

    decision = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 1, 21, 40, tzinfo=dt.UTC),
        consumer="github.synthesize",
    )
    assert decision.allowed is False
    assert decision.reason == "total_budget_exhausted"

    # 唯一 owner：llm_router 不得再自带 429 文本解析。
    # 比对的是**去掉注释与 docstring 的可执行代码**（ast.unparse 只留代码）——
    # 否则解释这条缺陷的注释本身会把断言打红。
    router_code = _executable_source(llm_router._diagnostics_from_provider_error)
    assert "classify_provider_error" in router_code
    for drifted_marker in ("total cap", "total budget", "re.compile", "DailyCap", "lower()"):
        assert drifted_marker not in router_code, (
            f"llm_router 又长出了本地 429 解析（命中 {drifted_marker!r}）——"
            "分类必须只有 http_retry 一处实现"
        )
    # 模块级也不许残留预算解析用的正则常量
    module_code = _executable_source(llm_router)
    assert "DailyCap" not in module_code
    assert "TotalCap" not in module_code


@pytest.mark.usefixtures("_isolate_quality_state")
def test_utc_daily_form_still_classified_via_shared_classifier(monkeypatch):
    """形态 B（utc_daily）在统一分类器下判定不回退（防修 A 打坏 B）。"""
    monkeypatch.setenv("AKB2_BUDGET_GATE_NOW", "2026-08-01T21:30:00Z")
    state = _route_and_capture_budget_state(monkeypatch, FORM_B_UTC_DAILY_BODY)

    block = state["active_block"]
    assert block["budget_scope"] == budget_gate.UTC_DAILY_SCOPE
    assert block["budget_date"] == "2026-08-01"
    assert block["cap"] == 2700.0

    decision = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 1, 23, 59, tzinfo=dt.UTC),
        consumer="github.synthesize",
    )
    assert decision.allowed is False
    assert decision.reason == "utc_daily_budget_exhausted"


@pytest.mark.usefixtures("_isolate_quality_state")
def test_provider_raw_text_is_redacted_before_ledger_and_exception(monkeypatch):
    """SC-R1-10 的 llm_router 侧：凭证样式与账户身份不得进账本 / 异常文本。"""
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")
    sentinel_body = (
        "ExceededBudget: User=zhuzan over budget. Spend=1.0, Budget=1.0 "
        "(auth used api_key=sk-probe-secret-2026 via Bearer sk-probe-secret-2026)"
    )

    def _raise_429(**kwargs):
        raise api_error(sentinel_body, status_code=429, body=sentinel_body)

    api_error = _install_fake_openai(monkeypatch, _raise_429)

    with pytest.raises(LLMProviderError) as excinfo:
        route_llm(
            "deepseek-v4-pro",
            [{"role": "user", "content": "test"}],
            budget_consumer="quality.distill",
        )

    assert "sk-probe-secret-2026" not in str(excinfo.value)
    assert "User=zhuzan" not in str(excinfo.value)

    ledger_text = resolve_state_file(
        "quality-ledger.jsonl", subdir=LEDGER_SUBDIR
    ).read_text(encoding="utf-8")
    assert "sk-probe-secret-2026" not in ledger_text
    assert "User=zhuzan" not in ledger_text
    # 分类证据必须保留（脱敏不能把诊断价值一起删掉）
    record = json.loads(ledger_text.splitlines()[-1])
    assert record["error_type"] == "budget_exceeded"
    assert record["budget_scope"] == "total"

    state_text = budget_gate._state_path().read_text(encoding="utf-8")
    assert "sk-probe-secret-2026" not in state_text
    assert "User=zhuzan" not in state_text


def test_redact_provider_text_is_idempotent_and_bounded():
    once = http_retry.redact_provider_text(
        "User=zhuzan token=abc123 Bearer sk-live-xyz " + "x" * 400
    )
    twice = http_retry.redact_provider_text(once)
    assert once == twice
    assert "zhuzan" not in once
    assert "sk-live-xyz" not in once
    assert once.endswith("…<truncated>")
    assert len(once) == http_retry.PROVIDER_TEXT_MAX_CHARS + len("…<truncated>")
