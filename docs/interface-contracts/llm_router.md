# LLM Router 接口规格

版本: v2 | 日期: 2026-08-15 | 锁定: false

## 公开接口

### `route_llm`

```python
def route_llm(
    model_family: str,
    messages: list[dict],
    response_schema: dict | None = None,
    *,
    article_key: str | None = None,
    ...
) -> LLMResponse:
```

**参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model_family` | `str` | 是 | 模型家族: `deepseek-v4-pro` / `gpt-5.4` / `gemini-flash` |
| `messages` | `list[dict]` | 是 | OpenAI 兼容消息列表，每条含 `role` + `content` |
| `response_schema` | `dict | None` | 否 | JSON Schema 用于结构化输出（`gpt-5.4` 原生支持） |
| `article_key` | `str | None` | 否 | per-article 调用预算的 article 身份（sc-p3-max-calls-enforced）。传入时同一 key 的第 `MAX_CALLS_PER_ARTICLE+1` 次调用在发起 API 请求之前 fail-closed 并落 quality 账本；`None`（既有调用方现状）不参与计数 |

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
| `ArticleBudgetExceededError` | 同 `article_key` 的调用超出 `MAX_CALLS_PER_ARTICLE`（`RuntimeError` 子类，既有 `except (RouterConfigError, RuntimeError)` 分支统一 fail-closed） |
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
| `MAX_CALLS_PER_ARTICLE` | `5` | 单篇内容最大 LLM 调用次数（`article_key` 传入时在 `route_llm` 内强制，见下） |
| `TIMEOUT_S` | `120.0` | 单次 API 调用超时（秒） |

## per-article 调用预算强制（sc-p3-max-calls-enforced）

- 计数入口在 `route_llm` 函数体内（API 请求发起之前），**函数直调与任何
  包装/子进程入口（如 `repair_worker`）走同一计数**——计数真相源是
  `AKB2_STATE_ROOT` 下 `ledgers/llm-article-calls.json`（flock 原子预留），
  跨进程共享，不在进程内存。
- 同一 `article_key` 的第 `MAX_CALLS_PER_ARTICLE+1` 次调用：
  1. 抛 `ArticleBudgetExceededError`（fail-closed，不打 API）；
  2. 向 quality 账本（`quality-ledger.jsonl`）落 `llm_article_budget_exceeded`
     事件行（含 `article_key_id` / `model_family` / `consumer` / `calls_limit`）。
- `article_key` 为 `None` 时不计数（既有调用方行为不变）；per-article 上限只对
  声明了 article 身份的链路强制。
- 计数文件损坏（非法 JSON）时同样拒绝放行（无法证明还有名额 → fail-closed）。
- `article_key` 落账/进错误消息前经 SHA-256 前 16 hex 归一化，不携带原始
  路径/内容（脱敏口径同 provider 错误摘要）。

## 路由规则

1. 模型名含 `-preview` 直接拒绝（宪法 P7）
2. API key 缺失时抛出 `RouterConfigError`，不静默
3. `deepseek-v4-pro` 请求体**必须**显式带 `reasoning_effort: "max"`
4. `response_schema` 非 None 时启用 `response_format: {type: json_schema}`
5. `article_key` 非 None 时先做 per-article 名额预留，超限 fail-closed 并落账

## 供下游引用

- step-4 `grader.py` 通过 `route_llm("gpt-5.4", ...)` 调用复核
- step-4 `repair.py` 通过 `route_llm` 调用修复 LLM
- step-6 `calibration.py` 通过 `route_llm` 调用全链路