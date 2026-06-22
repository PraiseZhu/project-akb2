"""OpenAI 兼容端点路由 — 三族模型统一路由层。

支持模型家族:
    deepseek-v4-pro — 提炼（默认 Think Max；distill 可显式关闭）
    gpt-5.4         — 复核（全量，异族裁判）
    gemini-flash    — 兜底（正式版，非 -preview）

路由规则:
    - 模型名含 "-preview" 直接拒绝（宪法 P7）
    - key 从环境变量获取，缺失即抛 RouterConfigError（宪法 P7）
    - 预算具名常量: MAX_CALLS_PER_ARTICLE=5, TIMEOUT_S=120, MAX_RETRIES=2（宪法 P4）

端点/模型环境覆盖（走 OpenAI 兼容代理网关，缺省回退官方端点）:
    - DEEPSEEK_BASE_URL / DEEPSEEK_MODEL_ID
    - OPENAI_BASE_URL / OPENAI_MODEL_ID
    - GEMINI_BASE_URL / GEMINI_MODEL_ID
    - AI_GATEWAY_BASE_URL / OPENROUTER_BASE_URL / TAPTAP_BASE_URL

公开接口:
    route_llm(model_family, messages, response_schema=None, ...) -> LLMResponse
    LLMResponse = dataclass(content, model_used, calls_used)
    RouterConfigError — key 缺失时抛出
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# 具名预算常量（宪法 P4）
# ---------------------------------------------------------------------------

MAX_CALLS_PER_ARTICLE: int = 5
TIMEOUT_S: float = 120.0
MAX_RETRIES: int = 2
_DEFAULT_REASONING_EFFORT = "__default__"
DEEPSEEK_BETA_BASE_URL = "https://api.deepseek.com/beta"

# ---------------------------------------------------------------------------
# 模型家族 → 环境变量 / 端点映射
# ---------------------------------------------------------------------------

DEFAULT_GATEWAY_BASE_URL = "https://llm-proxy.tapsvc.com"
GATEWAY_BASE_ENV_CANDIDATES: tuple[str, ...] = (
    "AI_GATEWAY_BASE_URL",
    "OPENROUTER_BASE_URL",
    "TAPTAP_BASE_URL",
)
GATEWAY_KEY_ENV_CANDIDATES: tuple[str, ...] = (
    "AI_GATEWAY_API_KEY",
    "OPENROUTER_API_KEY",
    "TAPTAP_API_KEY",
)

# 每家含 env_key（API key 环境变量）+ base_url/model_id 的环境覆盖变量与官方默认。
# 默认走官方端点；若存在统一网关 base/key，则优先走 OpenAI 兼容代理网关。
_MODEL_CONFIG: dict[str, dict[str, str]] = {
    "deepseek-v4-pro": {
        "env_key": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "base_url_default": "https://api.deepseek.com/v1",
        "model_id_env": "DEEPSEEK_MODEL_ID",
        "model_id_default": "deepseek-v4-pro",
        "gateway_model_id_default": "deepseek/deepseek-v4-pro",
    },
    "gpt-5.4": {
        "env_key": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "base_url_default": "https://api.openai.com/v1",
        "model_id_env": "OPENAI_MODEL_ID",
        "model_id_default": "gpt-5.4",
        "gateway_model_id_default": "gpt-5.4",
    },
    "gemini-flash": {
        "env_key": "GEMINI_API_KEY",
        "base_url_env": "GEMINI_BASE_URL",
        "base_url_default": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_id_env": "GEMINI_MODEL_ID",
        "model_id_default": "gemini-2.5-flash",
        "gateway_model_id_default": "gemini-3.5-flash",
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


class LLMTimeoutError(RuntimeError):
    """LLM 调用超时。"""


class LLMProviderError(RuntimeError):
    """LLM provider/API 调用错误。"""


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _normalise_openai_base_url(base_url: str) -> str:
    """归一化 OpenAI-compatible base URL，兼容 env 里带或不带 /v1。"""
    cleaned = base_url.strip().rstrip("/")
    if cleaned.endswith("/v1/chat/completions"):
        return cleaned[: -len("/chat/completions")]
    if re.search(r"/v\d+(?:beta)?(?:/openai)?$", cleaned):
        return cleaned
    return f"{cleaned}/v1"


def _first_nonempty_env(env_names: tuple[str, ...] | list[str]) -> tuple[str, str] | None:
    """返回第一个非空环境变量名和值。"""
    for name in env_names:
        value = os.environ.get(name, "").strip()
        if value:
            return name, value
    return None


def _unique_env_names(env_names: list[str]) -> list[str]:
    """保持顺序去重。"""
    seen: set[str] = set()
    unique: list[str] = []
    for name in env_names:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def _validate_model_family(model_family: str) -> dict[str, Any]:
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
    cfg = _MODEL_CONFIG[model_family]

    family_base = os.environ.get(cfg["base_url_env"], "").strip()
    gateway_base = _first_nonempty_env(GATEWAY_BASE_ENV_CANDIDATES)
    gateway_key = _first_nonempty_env(GATEWAY_KEY_ENV_CANDIDATES)

    family_base_is_gateway = False
    if family_base:
        base_url = _normalise_openai_base_url(family_base)
        family_base_is_gateway = base_url != _normalise_openai_base_url(
            cfg["base_url_default"]
        )
        gateway_selected = family_base_is_gateway
    elif gateway_base:
        base_url = _normalise_openai_base_url(gateway_base[1])
        gateway_selected = True
    elif gateway_key:
        base_url = _normalise_openai_base_url(DEFAULT_GATEWAY_BASE_URL)
        gateway_selected = True
    else:
        base_url = _normalise_openai_base_url(cfg["base_url_default"])
        gateway_selected = False

    model_default = (
        cfg.get("gateway_model_id_default", cfg["model_id_default"])
        if gateway_selected
        else cfg["model_id_default"]
    )
    model_id = os.environ.get(cfg["model_id_env"], "").strip() or model_default

    key_env_candidates = [cfg["env_key"]]
    if gateway_selected or (family_base_is_gateway and gateway_key):
        key_env_candidates = [
            *GATEWAY_KEY_ENV_CANDIDATES,
            cfg["env_key"],
            # 兼容本机历史约定：DEEPSEEK_API_KEY 也可能是同一 Gateway key。
            "DEEPSEEK_API_KEY",
        ]

    return {
        "env_key": cfg["env_key"],
        "key_env_candidates": _unique_env_names(key_env_candidates),
        "base_url": base_url,
        "model_id": model_id,
    }


def _get_api_key(env_key: str | list[str] | tuple[str, ...]) -> str:
    """从环境变量获取 API key。

    Raises:
        RouterConfigError: 环境变量缺失或为空。
    """
    env_names = [env_key] if isinstance(env_key, str) else list(env_key)
    for name in env_names:
        key = os.environ.get(name, "").strip()
        if key:
            return key
    if len(env_names) == 1:
        missing = env_names[0]
    else:
        missing = "/".join(env_names)
    if not env_names or not missing:
        missing = "<unset>"
    raise RouterConfigError(
        f"API key 缺失: 环境变量 {missing} 未设置或为空。"
        f" 请设置后重试。"
    )


def _strip_deepseek_tool_schema(schema: Any) -> Any:
    """Return a DeepSeek strict-tool compatible subset of JSON Schema.

    DeepSeek beta strict function calling supports a smaller subset than
    OpenAI json_schema response_format. Keep structural fields, drop validation
    keywords such as minLength/minItems, and require every object property.
    Content quality remains enforced by local validators after parsing.
    """
    if isinstance(schema, list):
        return [_strip_deepseek_tool_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    allowed = {
        "type",
        "properties",
        "items",
        "required",
        "additionalProperties",
        "enum",
        "description",
    }
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in allowed:
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {
                prop: _strip_deepseek_tool_schema(prop_schema)
                for prop, prop_schema in value.items()
            }
        elif key == "items":
            cleaned[key] = _strip_deepseek_tool_schema(value)
        else:
            cleaned[key] = _strip_deepseek_tool_schema(value)

    if cleaned.get("type") == "object":
        properties = cleaned.get("properties", {})
        if isinstance(properties, dict):
            cleaned["required"] = list(properties)
        cleaned["additionalProperties"] = False
    return cleaned


def build_deepseek_beta_tool_payload(
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any],
    *,
    tool_name: str = "akb2_extract_segment",
    model_id: str | None = None,
) -> dict[str, Any]:
    """Build official DeepSeek beta strict tool-call payload."""
    schema = _strip_deepseek_tool_schema(response_schema)
    return {
        "model": model_id or os.environ.get("DEEPSEEK_MODEL_ID", "deepseek-chat"),
        "messages": messages,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": "Return AKB2 distill extraction data.",
                    "parameters": schema,
                    "strict": True,
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": tool_name}},
    }


def route_deepseek_beta_tool(
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any],
    *,
    timeout_s: float | None = None,
    max_retries: int | None = None,
    tool_name: str = "akb2_extract_segment",
) -> LLMResponse:
    """Call DeepSeek official beta strict function calling and parse arguments."""
    api_key = _get_api_key(
        [
            "DEEPSEEK_BETA_API_KEY",
            "AKB2_DEEPSEEK_BETA_API_KEY",
            "DEEPSEEK_API_KEY",
        ]
    )
    try:
        import openai
    except ImportError as exc:
        raise RouterConfigError(
            "openai 包未安装。请运行: pip install openai>=1.0"
        ) from exc

    actual_timeout = TIMEOUT_S if timeout_s is None else timeout_s
    actual_max_retries = MAX_RETRIES if max_retries is None else max_retries
    client = openai.OpenAI(
        api_key=api_key,
        base_url=os.environ.get("DEEPSEEK_BETA_BASE_URL", DEEPSEEK_BETA_BASE_URL),
        timeout=actual_timeout,
        max_retries=actual_max_retries,
    )
    payload = build_deepseek_beta_tool_payload(
        messages,
        response_schema,
        tool_name=tool_name,
    )
    try:
        response = client.chat.completions.create(**payload)
    except openai.APITimeoutError as exc:
        raise LLMTimeoutError(
            f"DeepSeek beta tool 调用超时 ({actual_timeout}s)"
        ) from exc
    except openai.APIError as exc:
        raise LLMProviderError(f"DeepSeek beta tool API 错误: {exc}") from exc

    message = response.choices[0].message
    tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        raise LLMProviderError("DeepSeek beta tool 未返回 tool_calls")
    function = getattr(tool_calls[0], "function", None)
    arguments = getattr(function, "arguments", "") if function is not None else ""
    try:
        content = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise LLMProviderError("DeepSeek beta tool arguments 不是合法 JSON") from exc
    return LLMResponse(
        content=content,
        model_used=payload["model"],
        calls_used=1,
    )


def route_deepseek_json_object(
    messages: list[dict[str, Any]],
    *,
    timeout_s: float | None = None,
    max_retries: int | None = None,
) -> LLMResponse:
    """Call DeepSeek through JSON Output mode and parse the JSON object.

    This follows the official DeepSeek JSON Output path (`json_object`) instead
    of OpenAI-style `json_schema`. Local validators still enforce AKB2's real
    schema and content-quality contract after parsing.
    """
    config = _validate_model_family("deepseek-v4-pro")
    api_key = _get_api_key(config["key_env_candidates"])
    try:
        import openai
    except ImportError as exc:
        raise RouterConfigError(
            "openai 包未安装。请运行: pip install openai>=1.0"
        ) from exc

    actual_timeout = TIMEOUT_S if timeout_s is None else timeout_s
    actual_max_retries = MAX_RETRIES if max_retries is None else max_retries
    client = openai.OpenAI(
        api_key=api_key,
        base_url=config["base_url"],
        timeout=actual_timeout,
        max_retries=actual_max_retries,
    )
    payload = {
        "model": config["model_id"],
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    try:
        response = client.chat.completions.create(**payload)
    except openai.APITimeoutError as exc:
        raise LLMTimeoutError(
            f"DeepSeek JSON mode 调用超时 ({actual_timeout}s)"
        ) from exc
    except openai.APIError as exc:
        raise LLMProviderError(f"DeepSeek JSON mode API 错误: {exc}") from exc

    raw = response.choices[0].message.content or ""
    try:
        content = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMProviderError("DeepSeek JSON mode 返回不是合法 JSON object") from exc
    if not isinstance(content, dict):
        raise LLMProviderError("DeepSeek JSON mode 返回不是 JSON object")
    return LLMResponse(
        content=content,
        model_used=config["model_id"],
        calls_used=1,
    )


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------


def route_llm(
    model_family: str,
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any] | None = None,
    *,
    timeout_s: float | None = None,
    max_retries: int | None = None,
    reasoning_effort: str | None = _DEFAULT_REASONING_EFFORT,
    strict_schema: bool = True,
) -> LLMResponse:
    """OpenAI 兼容端点路由入口。

    Args:
        model_family: 模型家族标识（deepseek-v4-pro / gpt-5.4 / gemini-flash）。
        messages: 消息列表，格式 [{"role": "...", "content": "..."}, ...]。
        response_schema: 可选的 JSON Schema 用于结构化输出（gpt-5.4 支持）。
        timeout_s: 本次调用超时秒数；None 时使用 TIMEOUT_S。
        max_retries: SDK 层重试次数；None 时显式使用 MAX_RETRIES。
        reasoning_effort: "__default__" 保持旧 DeepSeek max 行为；None 表示不发送。
        strict_schema: response_schema 是否以 strict json_schema 发送。

    Returns:
        LLMResponse: 含 content / model_used / calls_used。

    Raises:
        RouterConfigError: 配置错误（缺 key / 非法模型名）。
        RuntimeError: API 调用超时或返回非 200。
    """
    config = _validate_model_family(model_family)
    api_key = _get_api_key(config["key_env_candidates"])

    try:
        import openai
    except ImportError as exc:
        raise RouterConfigError(
            "openai 包未安装。请运行: pip install openai>=1.0"
        ) from exc

    actual_timeout = TIMEOUT_S if timeout_s is None else timeout_s
    actual_max_retries = MAX_RETRIES if max_retries is None else max_retries

    client = openai.OpenAI(
        api_key=api_key,
        base_url=config["base_url"],
        timeout=actual_timeout,
        max_retries=actual_max_retries,
    )

    kwargs: dict[str, Any] = {
        "model": config["model_id"],
        "messages": messages,
    }

    # Backward compatible default: legacy DeepSeek calls keep Think Max unless
    # distill/repair explicitly opts out with reasoning_effort=None.
    if reasoning_effort == _DEFAULT_REASONING_EFFORT:
        if model_family == "deepseek-v4-pro":
            kwargs["reasoning_effort"] = "max"
    elif reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort

    # 结构化输出（OpenAI 兼容端）
    if response_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": response_schema,
                "strict": strict_schema,
            },
        }

    try:
        response = client.chat.completions.create(**kwargs)
    except openai.APITimeoutError as exc:
        raise LLMTimeoutError(
            f"LLM 调用超时 ({actual_timeout}s): {model_family}"
        ) from exc
    except openai.APIError as exc:
        raise LLMProviderError(
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
    *,
    reasoning_effort: str | None = _DEFAULT_REASONING_EFFORT,
    strict_schema: bool = True,
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

    if reasoning_effort == _DEFAULT_REASONING_EFFORT:
        if model_family == "deepseek-v4-pro":
            payload["reasoning_effort"] = "max"
    elif reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort

    if response_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": response_schema,
                "strict": strict_schema,
            },
        }

    return payload
