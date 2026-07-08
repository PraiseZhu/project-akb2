"""test_f31_retake_timeout_retry.py — F31 traceability retake 超时有界重试。

第七轮 SW 实锤:grader 0 缺陷的文章因 retake 唯一一次 LLM 调用撞
LLMTimeoutError(deepseek 60s 抖动)而死于 floor。修:超时类 +1 有界重试
(TRACEABILITY_RETAKE_TIMEOUT_RETRY=1);非超时异常不重试(同 F20 语义)。
"""
from __future__ import annotations

from unittest import mock

from src.akb2.quality import distill
from src.akb2.quality.distill import TRACEABILITY_RETAKE_TIMEOUT_RETRY
from src.akb2.quality.llm_router import LLMResponse

_RAW = "## 锚点目录\n| §A |\n| §K |\n\n## §A\n\nfact."


def _data() -> dict:
    return {"claims": [
        {"text": "fact", "anchor": "§A", "provenance": "source_grounded",
         "evidence_gap": "缺少 x"},
        {"text": "interp", "anchor": "§B", "provenance": "akb_interpretation",
         "traces_to": ["§K"], "evidence_gap": "缺少 y"},
    ]}


class _TimeoutErr(Exception):
    pass


class LLMTimeoutError(Exception):
    pass


def test_budget_is_named_and_bounded() -> None:
    assert TRACEABILITY_RETAKE_TIMEOUT_RETRY == 1


def test_timeout_retries_once_then_succeeds() -> None:
    calls: list = []

    def flaky(model, messages, **k):
        calls.append(1)
        if len(calls) == 1:
            raise LLMTimeoutError("60s")
        return LLMResponse(content={"retake": []}, model_used="mock")

    with mock.patch("src.akb2.quality.llm_router.route_llm", flaky):
        r = distill.retake_traceability(_data(), _RAW)
    d = r["diagnostics"]
    assert len(calls) == 2
    assert d.get("llm_called") is True
    assert d.get("timeout_retried") == 1


def test_timeout_budget_exhausted_fail_closed() -> None:
    def always_timeout(model, messages, **k):
        raise LLMTimeoutError("60s")

    with mock.patch("src.akb2.quality.llm_router.route_llm", always_timeout):
        r = distill.retake_traceability(_data(), _RAW)
    assert r["changed"] is False
    assert "LLMTimeoutError" in r["diagnostics"]["reject_reason"]


def test_non_timeout_error_no_retry() -> None:
    calls: list = []

    def bad_request(model, messages, **k):
        calls.append(1)
        raise RuntimeError("400 invalid_request")

    with mock.patch("src.akb2.quality.llm_router.route_llm", bad_request):
        r = distill.retake_traceability(_data(), _RAW)
    assert len(calls) == 1, "非超时异常不得重试(同 F20)"
    assert r["changed"] is False
