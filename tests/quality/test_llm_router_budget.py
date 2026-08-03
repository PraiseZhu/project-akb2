from __future__ import annotations

import datetime as dt
import json
import sys
import types

import pytest

from src.akb2.common import budget_gate
from src.akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file
from src.akb2.quality.llm_router import LLMProviderError, route_llm


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
