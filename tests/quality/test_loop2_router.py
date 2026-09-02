"""loop2 阶段 2：模型路由族条目 / vendor 护栏 / luna 通路重试 / effort 通道。"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from src.akb2.quality.grader import (
    FALLBACK_GRADER_MODEL,
    GRADER_REASONING_EFFORT,
    PRIMARY_GRADER_MODEL,
)
from src.akb2.quality.llm_router import (
    _MODEL_CONFIG,
    LUNA_GATEWAY_ROUTE_RETRY_MAX,
    ROUTE_SWITCH_EVENT,
    RouterConfigError,
    build_outbound_payload,
    family_vendor,
    route_llm,
    validate_maker_checker_separation,
)


def _gateway_env(monkeypatch) -> None:
    for name in (
        "AI_GATEWAY_API_KEY",
        "OPENROUTER_API_KEY",
        "TAPTAP_API_KEY",
        "OPENAI_BASE_URL",
        "DEEPSEEK_BASE_URL",
        "GEMINI_BASE_URL",
        "XAI_BASE_URL",
        "AI_GATEWAY_BASE_URL",
        "OPENROUTER_BASE_URL",
        "TAPTAP_BASE_URL",
        "LUNA_MODEL_ID",
        "XAI_MODEL_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")


class _FakeAPIError(Exception):
    def __init__(self, message: str = "BUDGET_MODEL_REQUIRES_API_MODE", status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code
        self.body = {"error": {"message": message}}
        self.message = message


def _install_luna_retry_openai(monkeypatch) -> dict:
    captured: dict[str, object] = {"models": [], "create_calls": 0}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["create_calls"] = int(captured["create_calls"]) + 1
            captured["models"].append(kwargs["model"])
            if str(kwargs.get("model", "")).startswith("codex/"):
                raise _FakeAPIError()
            message = types.SimpleNamespace(content='{"ok": true}')
            choice = types.SimpleNamespace(message=message)
            return types.SimpleNamespace(choices=[choice])

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=_FakeCompletions())

    fake_openai = types.SimpleNamespace(
        OpenAI=_FakeOpenAI,
        APITimeoutError=TimeoutError,
        APIError=_FakeAPIError,
    )
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_openai)
    return captured


def test_every_family_has_vendor() -> None:
    required = {
        "deepseek-v4-pro": "deepseek",
        "gpt-5.4": "openai",
        "gpt-5.6-luna": "openai",
        "grok-4.6": "xai",
        "gemini-flash": "google",
    }
    assert set(_MODEL_CONFIG) == set(required)
    for family, vendor in required.items():
        assert _MODEL_CONFIG[family]["vendor"] == vendor
        assert family_vendor(family) == vendor


def test_same_vendor_rejected() -> None:
    with pytest.raises(RouterConfigError, match="同厂商"):
        validate_maker_checker_separation("gpt-5.4", "gpt-5.6-luna")


def test_cross_vendor_accepted() -> None:
    validate_maker_checker_separation("deepseek-v4-pro", "gpt-5.6-luna")
    validate_maker_checker_separation("deepseek-v4-pro", "gpt-5.4")
    validate_maker_checker_separation("grok-4.6", "gpt-5.6-luna")


def test_preview_model_rejected() -> None:
    with pytest.raises(RouterConfigError, match="预览模型"):
        route_llm("gpt-5.6-luna-preview", [{"role": "user", "content": "hi"}])


def test_grader_fallback_model_still_gemini() -> None:
    assert FALLBACK_GRADER_MODEL == "gemini-flash"
    assert PRIMARY_GRADER_MODEL == "gpt-5.6-luna"
    assert GRADER_REASONING_EFFORT == "medium"
    assert LUNA_GATEWAY_ROUTE_RETRY_MAX == 1


def test_route_payload_includes_reasoning_effort() -> None:
    luna = build_outbound_payload(
        "gpt-5.6-luna",
        [{"role": "user", "content": "test"}],
    )
    assert luna["reasoning_effort"] == "medium"
    assert luna["reasoning_effort"] == _MODEL_CONFIG["gpt-5.6-luna"]["reasoning_effort"]

    grok = build_outbound_payload(
        "grok-4.6",
        [{"role": "user", "content": "test"}],
    )
    assert grok["reasoning_effort"] == "low"
    assert grok["reasoning_effort"] == _MODEL_CONFIG["grok-4.6"]["reasoning_effort"]

    gpt = build_outbound_payload(
        "gpt-5.4",
        [{"role": "user", "content": "test"}],
    )
    assert "reasoning_effort" not in gpt


def test_luna_route_retry_does_not_set_fallback_used(monkeypatch) -> None:
    _gateway_env(monkeypatch)
    captured = _install_luna_retry_openai(monkeypatch)

    resp = route_llm("gpt-5.6-luna", [{"role": "user", "content": "grade"}])

    assert resp.fallback_used is False
    assert resp.model_used == "gpt-5.6-luna"
    assert captured["models"] == ["codex/gpt-5.6-luna", "gpt-5.6-luna"]
    assert int(captured["create_calls"]) == 2
    assert resp.calls_used == 2


def test_luna_route_switch_lands_in_quality_ledger(monkeypatch) -> None:
    _gateway_env(monkeypatch)
    _install_luna_retry_openai(monkeypatch)

    route_llm("gpt-5.6-luna", [{"role": "user", "content": "grade"}])

    from src.akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file

    ledger = resolve_state_file("quality-ledger.jsonl", subdir=LEDGER_SUBDIR)
    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    switches = [row for row in rows if row.get("event") == ROUTE_SWITCH_EVENT]
    assert switches, rows
    details = switches[-1]["details"]
    assert details["from_model"] == "codex/gpt-5.6-luna"
    assert details["to_model"] == "gpt-5.6-luna"
    assert details["fallback_used"] is False


def test_grader_verdict_fixture_unchanged() -> None:
    from src.akb2.quality.grader import _parse_grader_output

    fixture_dir = Path(__file__).parent / "fixtures"
    for name in ("grader_result_pass.json", "grader_result_fail.json"):
        payload = json.loads((fixture_dir / name).read_text(encoding="utf-8"))
        defects, parse_ok = _parse_grader_output(payload, "concept")
        # 旧 PASS/FAIL 形状仍不是现行 dimensions schema；判定器不得放宽误收。
        assert parse_ok is False
        assert defects == []
