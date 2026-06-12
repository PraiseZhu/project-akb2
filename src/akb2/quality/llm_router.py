"""OpenAI 兼容端点路由 — 三族模型统一路由层。

支持模型家族:
    deepseek-v4-pro — 提炼（reasoning_effort="max", Think Max 档）
    gpt-5.4         — 复核（全量，异族裁判）
    gemini-flash    — 兜底（正式版，非 -preview）

路由规则:
    - 模型名含 "-preview" 直接拒绝（宪法 P7）
    - key 从环境变量获取，缺失即抛 RouterConfigError（宪法 P7）
    - 预算具名常量: MAX_CALLS_PER_ARTICLE=5, TIMEOUT_S=120（宪法 P4）

公开接口:
    route_llm(model_family, messages, response_schema=None) -> LLMResponse
    LLMResponse = dataclass(content, model_used, calls_used)
    RouterConfigError — key 缺失时抛出
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# 具名预算常量（宪法 P4）
# ---------------------------------------------------------------------------

MAX_CALLS_PER_ARTICLE: int = 5
TIMEOUT_S: float = 120.0

# ---------------------------------------------------------------------------
# 模型家族 → 环境变量 / 端点映射
# ---------------------------------------------------------------------------

_MODEL_CONFIG: dict[str, dict[str, str]] = {
    "deepseek-v4-pro": {
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "model_id": "deepseek-v4-pro",
    },
    "gpt-5.4": {
        "env_key": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "model_id": "gpt-5.4",
    },
    "gemini-flash": {
        "env_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_id": "gemini-2.5-flash",
    },
}

# ---------------------------------------------------------------------------
# 公开类型
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """路由调用返回结构。

    content: str | dict — 响应内容（字符串或已解析的 JSON dict）
    model_used: str — 实际使用的模型 ID
    calls_used: int — 本次调用数（通常为 1，重试会在内部发生）
    """

    content: str | dict
    model_used: str
    calls_used: int = 1


class RouterConfigError(Exception):
    """路由配置异常：缺 key、非法模型名等。"""


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _validate_model_family(model_family: str) -> dict[str, str]:
    """验证模型家族名并返回对应配置。

    Raises:
        RouterConfigError: 模型名含 "-preview" 或不在支持列表中。
    """
    if "-preview" in model_family:
        raise RouterConfigError(
            f"预览模型禁止进入 7x24 管线: {model_family}（宪法 P7）"
        )
    if model_family not in _MODEL_CONFIG:
        raise RouterConfigError(
            f"不支持的模型家族: {model_family}。"
            f" 支持: {', '.join(sorted(_MODEL_CONFIG))}"
        )
    return _MODEL_CONFIG[model_family]


def _get_api_key(env_key: str) -> str:
    """从环境变量获取 API key。

    Raises:
        RouterConfigError: 环境变量缺失或为空。
    """
    key = os.environ.get(env_key, "").strip()
    if not key:
        raise RouterConfigError(
            f"API key 缺失: 环境变量 {env_key} 未设置或为空。"
            f" 请设置后重试。"
        )
    return key


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------


def route_llm(
    model_family: str,
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any] | None = None,
) -> LLMResponse:
    """OpenAI 兼容端点路由入口。

    Args:
        model_family: 模型家族标识（deepseek-v4-pro / gpt-5.4 / gemini-flash）。
        messages: 消息列表，格式 [{"role": "...", "content": "..."}, ...]。
        response_schema: 可选的 JSON Schema 用于结构化输出（gpt-5.4 支持）。

    Returns:
        LLMResponse: 含 content / model_used / calls_used。

    Raises:
        RouterConfigError: 配置错误（缺 key / 非法模型名）。
        RuntimeError: API 调用超时或返回非 200。
    """
    config = _validate_model_family(model_family)
    api_key = _get_api_key(config["env_key"])

    try:
        import openai
    except ImportError as exc:
        raise RouterConfigError(
            "openai 包未安装。请运行: pip install openai>=1.0"
        ) from exc

    client = openai.OpenAI(
        api_key=api_key,
        base_url=config["base_url"],
        timeout=TIMEOUT_S,
    )

    kwargs: dict[str, Any] = {
        "model": config["model_id"],
        "messages": messages,
    }

    # DeepSeek 提炼必须显式传 reasoning_effort="max"
    if model_family == "deepseek-v4-pro":
        kwargs["reasoning_effort"] = "max"

    # 结构化输出（OpenAI 兼容端）
    if response_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": response_schema,
                "strict": True,
            },
        }

    try:
        response = client.chat.completions.create(**kwargs)
    except openai.APITimeoutError as exc:
        raise RuntimeError(
            f"LLM 调用超时 ({TIMEOUT_S}s): {model_family}"
        ) from exc
    except openai.APIError as exc:
        raise RuntimeError(
            f"LLM API 错误: {model_family} — {exc}"
        ) from exc

    raw = response.choices[0].message.content or ""

    # 尝试解析为 JSON
    content: str | dict = raw
    if response_schema is not None:
        try:
            content = json.loads(raw)
        except json.JSONDecodeError:
            # 结构化输出模式下不强解析，返回原始字符串
            content = raw

    return LLMResponse(
        content=content,
        model_used=config["model_id"],
        calls_used=1,
    )


def build_outbound_payload(
    model_family: str,
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建出站请求体（供测试断言 reasoning_effort 透传）。

    不发送请求，只返回将要发送的 payload dict。
    用于 fixture 测试中验证 reasoning_effort 字段存在。
    """
    config = _validate_model_family(model_family)
    payload: dict[str, Any] = {
        "model": config["model_id"],
        "messages": messages,
    }

    if model_family == "deepseek-v4-pro":
        payload["reasoning_effort"] = "max"

    if response_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": response_schema,
                "strict": True,
            },
        }

    return payload
