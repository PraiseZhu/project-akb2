# LLM Router 接口规格

版本: v1 | 日期: 2026-06-13 | 锁定: false

## 公开接口

### `route_llm`

```python
def route_llm(
    model_family: str,
    messages: list[dict],
    response_schema: dict | None = None,
) -> LLMResponse:
```

**参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model_family` | `str` | 是 | 模型家族: `deepseek-v4-pro` / `gpt-5.4` / `gemini-flash` |
| `messages` | `list[dict]` | 是 | OpenAI 兼容消息列表，每条含 `role` + `content` |
| `response_schema` | `dict | None` | 否 | JSON Schema 用于结构化输出（`gpt-5.4` 原生支持） |

**返回值**: `LLMResponse`

```python
@dataclass
class LLMResponse:
    content: str | dict   # 响应内容（字符串或已解析 JSON）
    model_used: str       # 实际使用的模型 ID
    calls_used: int       # 本次调用数（通常为 1）
```

**异常**

| 异常 | 触发条件 |
|------|---------|
| `RouterConfigError` | 模型名含 `-preview` / 不在支持列表 / API key 环境变量缺失 |
| `RuntimeError` | API 调用超时或返回非 200 |

### `build_outbound_payload`

```python
def build_outbound_payload(
    model_family: str,
    messages: list[dict],
    response_schema: dict | None = None,
) -> dict:
```

不发送请求，返回将要发送的 payload dict。用于测试断言（如验证 `reasoning_effort` 透传）。

## 模型阵容

| 家族 | 环境变量 | 端点 | 备注 |
|------|---------|------|------|
| `deepseek-v4-pro` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` | 提炼，`reasoning_effort="max"` |
| `gpt-5.4` | `OPENAI_API_KEY` | `https://api.openai.com/v1` | 复核（全量） |
| `gemini-flash` | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai` | 兜底 |

## 预算常量

| 常量 | 值 | 用途 |
|------|-----|------|
| `MAX_CALLS_PER_ARTICLE` | `5` | 单篇内容最大 LLM 调用次数 |
| `TIMEOUT_S` | `120.0` | 单次 API 调用超时（秒） |

## 路由规则

1. 模型名含 `-preview` 直接拒绝（宪法 P7）
2. API key 缺失时抛出 `RouterConfigError`，不静默
3. `deepseek-v4-pro` 请求体**必须**显式带 `reasoning_effort: "max"`
4. `response_schema` 非 None 时启用 `response_format: {type: json_schema}`

## 供下游引用

- step-4 `grader.py` 通过 `route_llm("gpt-5.4", ...)` 调用复核
- step-4 `repair.py` 通过 `route_llm` 调用修复 LLM
- step-6 `calibration.py` 通过 `route_llm` 调用全链路