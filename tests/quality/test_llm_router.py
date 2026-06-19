"""test_llm_router.py — 路由模块测试（全 fixture，不打真 API）。

覆盖:
    - 缺 key 显式抛 RouterConfigError
    - -preview 模型拒绝
    - 多模型路由
    - 预算具名常量
    - reasoning_effort 透传验证
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from src.akb2.quality.llm_router import (
    MAX_CALLS_PER_ARTICLE,
    TIMEOUT_S,
    RouterConfigError,
    build_outbound_payload,
    route_llm,
)


class TestRouterConfig:
    """路由配置验证。"""

    def test_rejects_preview_model(self):
        """模型名含 -preview 直接拒绝。"""
        with pytest.raises(RouterConfigError, match="预览模型"):
            route_llm("deepseek-v4-pro-preview", [{"role": "user", "content": "hi"}])

    def test_rejects_unknown_model(self):
        """不支持的模型名抛错。"""
        with pytest.raises(RouterConfigError, match="不支持的模型家族"):
            route_llm("unknown-model", [{"role": "user", "content": "hi"}])

    def test_rejects_empty_model(self):
        """空模型名抛错。"""
        with pytest.raises(RouterConfigError):
            route_llm("", [{"role": "user", "content": "hi"}])

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_key_raises(self):
        """环境变量缺失时抛 RouterConfigError。"""
        with pytest.raises(RouterConfigError, match="API key 缺失"):
            route_llm("gemini-flash", [{"role": "user", "content": "hi"}])

    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=True)
    def test_empty_key_raises(self):
        """环境变量为空字符串时抛 RouterConfigError。"""
        with pytest.raises(RouterConfigError, match="API key 缺失"):
            route_llm("gemini-flash", [{"role": "user", "content": "hi"}])

    @mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test-key"}, clear=True)
    def test_deepseek_key_present_no_error_on_config(self):
        """DeepSeek key 存在时配置验证通过（不实际发请求则不会触发 API 错误）。"""
        # 不实际发请求，只验证 key 解析不抛错
        from src.akb2.quality.llm_router import _get_api_key, _validate_model_family

        config = _validate_model_family("deepseek-v4-pro")
        assert config["model_id"] == "deepseek-v4-pro"
        assert config["base_url"] == "https://api.deepseek.com/v1"

        key = _get_api_key("DEEPSEEK_API_KEY")
        assert key == "sk-test-key"


class TestBudgetConstants:
    """预算具名常量验证。"""

    def test_max_calls_per_article_is_named_constant(self):
        """MAX_CALLS_PER_ARTICLE 是具名常量（宪法 P4）。"""
        assert isinstance(MAX_CALLS_PER_ARTICLE, int)
        assert MAX_CALLS_PER_ARTICLE > 0
        assert MAX_CALLS_PER_ARTICLE == 5

    def test_timeout_s_is_named_constant(self):
        """TIMEOUT_S 是具名常量（宪法 P4）。"""
        assert isinstance(TIMEOUT_S, (int, float))
        assert TIMEOUT_S > 0
        assert TIMEOUT_S == 120.0


class TestBuildOutboundPayload:
    """出站 payload 构建验证（fixture 断言，不打真 API）。"""

    def test_deepseek_payload_has_reasoning_effort(self):
        """DeepSeek 请求体必须显式带 reasoning_effort: max。"""
        payload = build_outbound_payload(
            "deepseek-v4-pro",
            [{"role": "user", "content": "test"}],
        )
        assert "reasoning_effort" in payload
        assert payload["reasoning_effort"] == "max"

    def test_gpt_payload_no_reasoning_effort(self):
        """GPT-5.4 请求体不应含 reasoning_effort。"""
        payload = build_outbound_payload(
            "gpt-5.4",
            [{"role": "user", "content": "test"}],
        )
        assert "reasoning_effort" not in payload

    def test_gemini_payload_no_reasoning_effort(self):
        """Gemini Flash 请求体不应含 reasoning_effort。"""
        payload = build_outbound_payload(
            "gemini-flash",
            [{"role": "user", "content": "test"}],
        )
        assert "reasoning_effort" not in payload

    def test_payload_has_response_format_when_schema_provided(self):
        """提供 response_schema 时 payload 含 response_format。"""
        schema = {"type": "object", "properties": {"pass": {"type": "boolean"}}}
        payload = build_outbound_payload(
            "gpt-5.4",
            [{"role": "user", "content": "test"}],
            response_schema=schema,
        )
        assert "response_format" in payload
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True

    def test_payload_no_response_format_without_schema(self):
        """不提供 response_schema 时 payload 不含 response_format。"""
        payload = build_outbound_payload(
            "gpt-5.4",
            [{"role": "user", "content": "test"}],
        )
        assert "response_format" not in payload

    def test_all_three_models_have_messages(self):
        """三种模型家族的 payload 都含 messages。"""
        for family in ["deepseek-v4-pro", "gpt-5.4", "gemini-flash"]:
            payload = build_outbound_payload(
                family,
                [{"role": "user", "content": "test"}],
            )
            assert "messages" in payload
            assert payload["messages"] == [{"role": "user", "content": "test"}]
            assert "model" in payload
