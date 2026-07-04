"""test_f20_enable_thinking.py — F20 enable_thinking 400 + 不可重试 400 break 修复测试。

工单 F20:修 enable_thinking 400——distill 链最后一个已知堵点。两个根因:
  1. enable_thinking 无差别注入:distill_extract 调 route_llm(DISTILL_MODEL_FAMILY, ...,
     extra_body={"enable_thinking": False})。当 DISTILL_MODEL_FAMILY=gpt-5.4(B 臂或 env
     覆盖)时,enable_thinking 流到 gpt-5.4(Azure via litellm)端点不认 → 400 Unknown
     parameter(账本 169 条 distill_extract 7-03/7-04)。
  2. 不可重试 400 烧满重试预算:extract_claims 的 except Exception 重试 3 次,400 参数
     错误重试结果必然相同,烧 3/3 是确定性浪费。

测试不打真 API:用 build_outbound_payload 断言 payload + mock route_llm_fn 断言重试行为。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "src/akb2/toolbox_scan"))

from src.akb2.quality.distill import (  # noqa: E402
    MAX_EXTRACT_RETRIES,
    _is_non_retryable_api_error,
    extract_claims,
)
from src.akb2.quality.llm_router import (  # noqa: E402
    LLMProviderError,
    _filter_extra_body_for_model,
    build_outbound_payload,
)

_MSGS = [{"role": "user", "content": "x"}]


# ---------------------------------------------------------------------------
# 根因 1:extra_body 按模型族过滤(enable_thinking 只对 deepseek 系)
# ---------------------------------------------------------------------------


class TestExtraBodyFilter:
    def test_gpt54_payload_strips_enable_thinking(self) -> None:
        """gpt-5.4 payload 不含 enable_thinking(否则 Azure 400 Unknown parameter)。"""
        payload = build_outbound_payload(
            "gpt-5.4", _MSGS, extra_body={"enable_thinking": False}
        )
        eb = payload.get("extra_body")
        assert eb is None or "enable_thinking" not in eb, (
            f"gpt-5.4 不该带 enable_thinking,实际 extra_body={eb}"
        )

    def test_deepseek_payload_keeps_enable_thinking_unchanged(self) -> None:
        """deepseek payload 保持 enable_thinking=False(行为一字不变)。"""
        payload = build_outbound_payload(
            "deepseek-v4-pro", _MSGS, extra_body={"enable_thinking": False}
        )
        assert payload.get("extra_body") == {"enable_thinking": False}, (
            "deepseek extra_body 必须原样保留(行为不变)"
        )

    def test_gemini_payload_strips_enable_thinking(self) -> None:
        """gemini payload 不含 enable_thinking(gemini 端点不认)。"""
        payload = build_outbound_payload(
            "gemini-flash", _MSGS, extra_body={"enable_thinking": False}
        )
        eb = payload.get("extra_body")
        assert eb is None or "enable_thinking" not in eb

    def test_filter_keeps_other_params_for_gpt54(self) -> None:
        """非 deepseek 系 extra_body 保留其他参数(只剥 enable_thinking)。"""
        result = _filter_extra_body_for_model(
            "gpt-5.4", {"enable_thinking": False, "other_param": "value"}
        )
        assert result == {"other_param": "value"}

    def test_filter_deepseek_extra_body_unchanged(self) -> None:
        """deepseek extra_body 原样保留(含 enable_thinking + 其他参数)。"""
        eb = {"enable_thinking": False, "other": "value"}
        assert _filter_extra_body_for_model("deepseek-v4-pro", eb) == eb

    def test_filter_none_passthrough(self) -> None:
        """extra_body=None 原样返回 None(所有模型族)。"""
        assert _filter_extra_body_for_model("gpt-5.4", None) is None
        assert _filter_extra_body_for_model("deepseek-v4-pro", None) is None

    def test_filter_empty_dict_returns_none(self) -> None:
        """extra_body={} 过滤后空 → None(不传空 dict)。"""
        assert _filter_extra_body_for_model("gpt-5.4", {}) is None


# ---------------------------------------------------------------------------
# 根因 2:不可重试 400 立即 break(不烧满重试预算)
# ---------------------------------------------------------------------------


class TestNonRetryable400Break:
    def test_is_non_retryable_400_enable_thinking(self) -> None:
        """enable_thinking 400 是不可重试(账本签名)。"""
        exc = LLMProviderError(
            "gpt-5.4 — Error code: 400 - litellm.BadRequestError: "
            "AzureException BadRequestError - Unknown parameter: 'enable_thinking'"
        )
        assert _is_non_retryable_api_error(exc)

    def test_is_non_retryable_badrequest(self) -> None:
        """BadRequest / invalid_request 是不可重试。"""
        assert _is_non_retryable_api_error(ValueError("BadRequest: invalid schema"))
        assert _is_non_retryable_api_error(RuntimeError("invalid_request param"))

    def test_not_non_retryable_timeout(self) -> None:
        """超时是可重试(非 400)。"""
        assert not _is_non_retryable_api_error(
            LLMProviderError("LLM 调用超时 (60s): gpt-5.4")
        )

    def test_not_non_retryable_5xx(self) -> None:
        """5xx 服务端错误是可重试(非 400)。"""
        assert not _is_non_retryable_api_error(
            LLMProviderError("LLM API 错误: gpt-5.4 — Error code: 503 - service unavailable")
        )

    def test_400_breaks_immediately_no_retry_budget_burn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """400 错误立即 break,只调 1 次(不是 MAX_EXTRACT_RETRIES=3 次)——不烧预算。"""
        calls: list[str] = []

        def _mock_400(model_family, messages, response_schema=None, **kwargs):
            calls.append(model_family)
            raise LLMProviderError(
                "gpt-5.4 — Error code: 400 - Unknown parameter: 'enable_thinking'"
            )

        result = extract_claims("raw content", route_llm_fn=_mock_400, content_lane="")
        assert not result.success
        assert "不可重试" in result.error, f"应标不可重试,实际: {result.error}"
        # 400 立即 break:只调 1 次(第一个 segment 400 就 break,不重试 attempt)
        assert len(calls) == 1, (
            f"400 应立即 break 只调 1 次,实际调 {len(calls)} 次(烧了重试预算)"
        )

    def test_retryable_error_still_retries_to_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非 400 错误(如超时)仍重试到 MAX_EXTRACT_RETRIES(不误伤可重试错误)。"""
        calls: list[int] = []

        def _mock_timeout(model_family, messages, response_schema=None, **kwargs):
            calls.append(1)
            raise LLMProviderError("LLM 调用超时 (60s): gpt-5.4")

        result = extract_claims("raw content", route_llm_fn=_mock_timeout, content_lane="")
        assert not result.success
        assert "不可重试" not in result.error, "超时不是不可重试,不该标"
        # 非 400 仍重试到 MAX_EXTRACT_RETRIES(每 attempt 调多个 segment,总调用 >= MAX)
        assert len(calls) >= MAX_EXTRACT_RETRIES, (
            f"超时应重试到 MAX_EXTRACT_RETRIES={MAX_EXTRACT_RETRIES},"
            f"实际调 {len(calls)} 次"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
