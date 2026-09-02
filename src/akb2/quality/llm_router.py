"""OpenAI 兼容端点路由 — 三族模型统一路由层。

支持模型家族:
    deepseek-v4-pro — 提炼（vendor=deepseek；默认 Think Max；distill 可显式关闭）
    gpt-5.4         — 旧复核回退（vendor=openai；AKB2_GRADER_FAMILY 覆盖）
    gpt-5.6-luna    — 现行复核（vendor=openai；骨折通路优先，同族通路重试不置 fallback_used）
    grok-4.6        — loop2 预筛（vendor=xai）
    gemini-flash    — 诊断兜底（vendor=google；正式版，非 -preview，不作 PASS）

路由规则:
    - 模型名含 "-preview" 直接拒绝（宪法 P7）
    - key 从环境变量获取，缺失即抛 RouterConfigError（宪法 P7）
    - 预算具名常量: MAX_CALLS_PER_ARTICLE=5, TIMEOUT_S=120, MAX_RETRIES=2（宪法 P4）
    - per-article 调用预算强制（sc-p3-max-calls-enforced）：route_llm(article_key=...)
      同一 article_key 的第 MAX_CALLS_PER_ARTICLE+1 次调用在发起 API 请求**之前**
      fail-closed（ArticleBudgetExceededError，RuntimeError 子类），并在 quality
      账本落 llm_article_budget_exceeded 事件行。计数真相源是账本（flock 原子
      预留），跨进程共享——repair 的 subprocess 直调与本进程调用同一计数。

端点/模型环境覆盖（走 OpenAI 兼容代理网关，缺省回退官方端点）:
    - DEEPSEEK_BASE_URL / DEEPSEEK_MODEL_ID
    - OPENAI_BASE_URL / OPENAI_MODEL_ID
    - LUNA_MODEL_ID / XAI_BASE_URL / XAI_MODEL_ID
    - GEMINI_BASE_URL / GEMINI_MODEL_ID
    - AI_GATEWAY_BASE_URL / OPENROUTER_BASE_URL / TAPTAP_BASE_URL

公开接口:
    route_llm(model_family, messages, response_schema=None, ..., article_key=None) -> LLMResponse
    LLMResponse = dataclass(content, model_used, calls_used, fallback_used)
    RouterConfigError — key 缺失时抛出
    ArticleBudgetExceededError — 同 article_key 第 MAX_CALLS_PER_ARTICLE+1 次调用抛出
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import logging
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
LUNA_GATEWAY_ROUTE_RETRY_MAX: int = 1
ROUTE_SWITCH_EVENT = "route_switch"
_DEFAULT_REASONING_EFFORT = "__default__"
DEEPSEEK_BETA_BASE_URL = "https://api.deepseek.com/beta"
QUALITY_LEDGER_FILE = "quality-ledger.jsonl"

# per-article 调用预算的账本事件名（sc-p3-max-calls-enforced）。
ARTICLE_BUDGET_EVENT = "llm_article_budget_exceeded"
# per-article 预留计数文件（与账本同目录；flock 原子判定真相源）。
ARTICLE_CALLS_FILE = "llm-article-calls.json"

logger = logging.getLogger(__name__)

try:  # pragma: no cover - runtime path
    from src.akb2.common.budget_gate import (
        BUDGET_EXCEEDED,
        OTHER_ERROR,
        PROVIDER_CODE_429,
        RATE_LIMIT,
        BudgetDiagnostics,
        UnscopedBudgetEventError,
        query_budget_gate,
        record_budget_event,
    )
    from src.akb2.common.http_retry import (
        ERROR_TYPE_BUDGET_EXCEEDED,
        ERROR_TYPE_RATE_LIMIT,
        classify_provider_error,
        redact_provider_text,
    )
    from src.akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file
    from src.akb2.loops.ledger import append_event
except ImportError:  # pragma: no cover - script runtime path
    from akb2.common.budget_gate import (
        BUDGET_EXCEEDED,
        OTHER_ERROR,
        PROVIDER_CODE_429,
        RATE_LIMIT,
        BudgetDiagnostics,
        UnscopedBudgetEventError,
        query_budget_gate,
        record_budget_event,
    )
    from akb2.common.http_retry import (
        ERROR_TYPE_BUDGET_EXCEEDED,
        ERROR_TYPE_RATE_LIMIT,
        classify_provider_error,
        redact_provider_text,
    )
    from akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file
    from akb2.loops.ledger import append_event

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
# vendor 供 validate_maker_checker_separation 比厂商（宪法 P7），不再比族名字符串。
_MODEL_CONFIG: dict[str, dict[str, Any]] = {
    "deepseek-v4-pro": {
        "env_key": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "base_url_default": "https://api.deepseek.com/v1",
        "model_id_env": "DEEPSEEK_MODEL_ID",
        "model_id_default": "deepseek-v4-pro",
        "gateway_model_id_default": "deepseek/deepseek-v4-pro",
        "vendor": "deepseek",
        "reasoning_effort": "max",
    },
    "gpt-5.4": {
        "env_key": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "base_url_default": "https://api.openai.com/v1",
        "model_id_env": "OPENAI_MODEL_ID",
        "model_id_default": "gpt-5.4",
        "gateway_model_id_default": "gpt-5.4",
        "vendor": "openai",
    },
    "gpt-5.6-luna": {
        "env_key": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "base_url_default": "https://api.openai.com/v1",
        "model_id_env": "LUNA_MODEL_ID",
        "model_id_default": "gpt-5.6-luna",
        "gateway_model_id_default": "codex/gpt-5.6-luna",
        "gateway_model_ids": ["codex/gpt-5.6-luna", "gpt-5.6-luna"],
        "vendor": "openai",
        "reasoning_effort": "medium",
    },
    # grok 网关前缀：x-ai/grok-4.6 与 x-ai-grok/grok-4.6 均实测通，/models 不标折扣档。
    # 2026-09-01 默认 x-ai/grok-4.6；阶段 5 上量前用小额对账单收口（计划 C6/2e）。
    "grok-4.6": {
        "env_key": "XAI_API_KEY",
        "base_url_env": "XAI_BASE_URL",
        "base_url_default": "https://api.x.ai/v1",
        "model_id_env": "XAI_MODEL_ID",
        "model_id_default": "grok-4.6",
        "gateway_model_id_default": "x-ai/grok-4.6",
        "vendor": "xai",
        "reasoning_effort": "low",
    },
    "gemini-flash": {
        "env_key": "GEMINI_API_KEY",
        "base_url_env": "GEMINI_BASE_URL",
        "base_url_default": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_id_env": "GEMINI_MODEL_ID",
        "model_id_default": "gemini-2.5-flash",
        "gateway_model_id_default": "gemini-3.5-flash",
        "vendor": "google",
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
    fallback_used: bool = False


class RouterConfigError(Exception):
    """路由配置异常：缺 key、非法模型名等。"""


class LLMTimeoutError(RuntimeError):
    """LLM 调用超时。"""


class LLMProviderError(RuntimeError):
    """LLM provider/API 调用错误。"""


class ArticleBudgetExceededError(RuntimeError):
    """同 article_key 的 LLM 调用超出 MAX_CALLS_PER_ARTICLE（sc-p3-max-calls-enforced）。

    继承 RuntimeError：grader / repair 既有的 ``except (RouterConfigError, RuntimeError)``
    分支无需改动即统一 fail-closed（与 admission.SecondAdmissionCallError 同范式）。
    """


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _extract_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            return status_code
    return None


def _provider_error_text(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    parts: list[str] = []
    if isinstance(body, dict):
        parts.append(json.dumps(body, ensure_ascii=False, sort_keys=True))
    elif body:
        parts.append(str(body))

    message = getattr(exc, "message", None)
    if message:
        parts.append(str(message))
    parts.append(str(exc))
    return " | ".join(part for part in parts if part).strip()


def _public_provider_detail(exc: Exception) -> str:
    """provider 异常 → 可公开的单行摘要：异常类型名 + 脱敏原文。

    唯一 owner（加固清单第 10 类形态①）：本模块所有把 provider 异常包成
    ``LLMProviderError`` / ``LLMTimeoutError`` 的点都经这里，任何一处都不再直接内插
    ``{exc}``、也不再 ``raise ... from exc``。理由是 SC-R2-11① 实测的两条打印路径：

    - ``str(err)`` —— 公开文本（旧代码这条已脱敏）；
    - ``traceback.format_exception(err)`` —— 会把 ``__cause__`` 全链打印出来。
      ``from exc`` 让**未脱敏的 provider 原始异常**留在链上（对抗席探针实测
      ``outer_secret=False`` 但 ``cause_secret=True`` / ``traceback_secret=True``；
      R1 也实测 provider 原文能带出 ``sk-*`` 字面量）。

    只覆盖其中一条不算脱敏，所以改用 ``from None``：``__cause__`` 清空、
    ``__suppress_context__`` 置真，两条路径同时干净。

    保留异常类型名有两个理由：它不敏感；而且是 **load-bearing** ——
    ``runner._INFRA_ERROR_MARKERS`` 用 ``"BadRequestError"`` 这类类名把上游 400 归成
    infra 走 F39 重审，原文被 200 字截断时类型名是唯一稳定的指纹。
    """
    return f"{type(exc).__name__}: {redact_provider_text(_provider_error_text(exc))}"


def _diagnostics_from_provider_error(exc: Exception) -> BudgetDiagnostics:
    """把 provider 异常归因成 BudgetDiagnostics —— 分类**只有一个实现**。

    此前这里另写了一份 429 文本解析，与 `http_retry.classify_provider_error` 漂移：
    真实的总预算形态是 `ExceededBudget: User=… over budget. Spend=…, Budget=…`
    （**不含** "total cap"/"total budget" 字样），本地解析器一条都不命中 → scope=''
    → cooldown 算不出 → 闸门永不短路。而 briefing 侧用共享分类器的同一响应体归出
    scope='total'。同一语义两份实现，其中一份已经错了 —— 现在统一委派给共享分类器
    （加固清单第 10 类形态①：判据该有唯一 owner）。

    本函数只做「共享诊断 → 落盘 dataclass」的形状转换：
    provider_code 在本模块是字符串（PROVIDER_CODE_429 语义是「这是 429 事件吗」），
    在 http_retry 是 int 状态码，故显式换形，不把 int 直接塞进字符串字段。
    """
    status_code = _extract_status_code(exc)
    text = _provider_error_text(exc)
    diagnosis = classify_provider_error(status_code, text)

    error_type = {
        ERROR_TYPE_BUDGET_EXCEEDED: BUDGET_EXCEEDED,
        ERROR_TYPE_RATE_LIMIT: RATE_LIMIT,
    }.get(diagnosis.error_type, OTHER_ERROR)

    return BudgetDiagnostics(
        error_type=error_type,
        budget_scope=diagnosis.budget_scope,
        spend=diagnosis.spend,
        cap=diagnosis.cap,
        budget_date=diagnosis.budget_date,
        provider_code=PROVIDER_CODE_429 if status_code == 429 else "",
        # 原文脱敏后才落盘/进异常（加固清单第 9 类）——
        # classify_provider_error 已在 raw_excerpt 上做过，这里直接用它。
        raw_message=diagnosis.raw_excerpt,
    )


def _quality_ledger_path() -> str:
    return str(resolve_state_file(QUALITY_LEDGER_FILE, subdir=LEDGER_SUBDIR))


def _article_calls_path() -> str:
    return str(resolve_state_file(ARTICLE_CALLS_FILE, subdir=LEDGER_SUBDIR))


def _normalise_article_key(article_key: str) -> str:
    """article_key → 计数文件/账本共用的稳定 id（SHA-256 前 16 hex）。

    归一化而不是裸存 key：调用方传的 key 可能是 raw 文件路径或 content_hash，
    含路径分隔符时直接当 JSON key 也能用，但落账本/日志时统一短 id 可避免
    把机器本地路径带进公开错误消息（脱敏口径同 _public_provider_detail）。
    """
    return hashlib.sha256(article_key.encode("utf-8")).hexdigest()[:16]


def reserve_article_call(
    article_key: str, *, limit: int = MAX_CALLS_PER_ARTICLE
) -> bool:
    """原子预留同 article_key 的一次 LLM 调用名额（sc-p3-max-calls-enforced）。

    计数真相源是状态目录下的 JSON 文件（flock 临界区内 read-modify-write），
    不用进程内 dict——repair live 模式经 subprocess 直调 route_llm，进程内
    计数会在每次 spawn 归零；文件计数让**函数入口与任何包装/子进程入口同源**。

    Returns:
        True —— 名额已预留（当前次数 +1 ≤ limit），可以发起调用。
        False —— 已达上限（不写计数文件），调用方必须 fail-closed。
    """
    calls_path = _article_calls_path()
    key = _normalise_article_key(article_key)
    os.makedirs(os.path.dirname(calls_path), exist_ok=True)
    lock_path = calls_path + ".lock"
    with open(lock_path, "a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            counts: dict[str, int] = {}
            if os.path.exists(calls_path):
                try:
                    with open(calls_path, encoding="utf-8") as fh:
                        loaded = json.load(fh)
                    if not isinstance(loaded, dict):
                        raise ValueError(
                            f"计数文件根节点不是 object: {type(loaded).__name__}"
                        )
                    bad_values = [
                        k
                        for k, v in loaded.items()
                        if not isinstance(v, int) or isinstance(v, bool)
                    ]
                    if bad_values:
                        raise ValueError(
                            f"计数文件含非整数字段: {bad_values}"
                        )
                    counts = {k: int(v) for k, v in loaded.items()}
                except (json.JSONDecodeError, OSError, ValueError) as exc:
                    # 计数文件损坏/形状不合法 = 无法证明还有名额 → fail-closed 拒绝放行
                    # （宪法 P3/P4：不降级、失败可见）。此前只捕获 JSONDecodeError/
                    # OSError，语法合法但形状错误（非 dict / 非 int 值，如 JSON
                    # null/list 或浮点值）会被 isinstance 过滤器悄悄当空/丢字段处理，
                    # 等价于清空已记满的计数、让打满预算的 article 重新获得名额——
                    # 与本函数文档承诺的 fail-closed 相反（实测复现：exhausted
                    # article 在计数文件被覆写成 `[]`/`null`/含浮点值 dict 后立即
                    # 重新拿到名额）。形状校验并入同一 fail-closed 分支堵死这条口子。
                    raise ArticleBudgetExceededError(
                        f"per-article 调用计数文件损坏，fail-closed 拒绝新调用:"
                        f" {calls_path} ({exc})"
                    ) from None
            current = counts.get(key, 0)
            if current >= limit:
                return False
            counts[key] = current + 1
            tmp = calls_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(counts, fh, ensure_ascii=False, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, calls_path)
            return True
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def _record_article_budget_exceeded(
    *, article_key: str, model_family: str, consumer: str, limit: int,
    loop_id: str = "loop1",
) -> None:
    """超限事件落 quality 账本（失败可见，宪法 P4）。

    与 _append_quality_budget_event 同范式：落账失败只告警不吞——本函数在抛
    ArticleBudgetExceededError **之前**调用，落账异常不能把 fail-closed 变成
    别的失败形态，所以这里捕获后用 logger.error 响亮暴露，再继续抛超限异常。
    """
    key = _normalise_article_key(article_key)
    try:
        append_event(
            _quality_ledger_path(),
            {
                "loop_id": loop_id or "loop1",
                "node": "llm_router",
                "event": ARTICLE_BUDGET_EVENT,
                "content_hash": key,
                "ts": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "budget": {
                    "token_count": 0,
                    "retry_count": 0,
                    "calls_limit": limit,
                },
                "details": {
                    "article_key_id": key,
                    "model_family": model_family,
                    "consumer": consumer,
                    "message": (
                        f"同 article 第 {limit + 1} 次调用超出 MAX_CALLS_PER_ARTICLE"
                        f"={limit}，fail-closed 拒绝（sc-p3-max-calls-enforced）"
                    ),
                },
            },
        )
    except Exception as exc:  # noqa: BLE001 - 落账失败不改变 fail-closed 语义
        logger.error(
            "failed to append %s ledger event for article %s: %s",
            ARTICLE_BUDGET_EVENT,
            key,
            exc,
        )


def _append_quality_budget_event(
    *,
    diagnostics: BudgetDiagnostics,
    model_family: str,
    consumer: str,
    loop_id: str = "loop1",
) -> None:
    if diagnostics.provider_code != PROVIDER_CODE_429:
        return
    try:
        append_event(
            _quality_ledger_path(),
            {
                "loop_id": loop_id or "loop1",
                "node": "llm_router",
                "ts": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "event": "llm_gateway_429",
                "model_family": model_family,
                "consumer": consumer,
                "provider_code": diagnostics.provider_code,
                "error_type": diagnostics.error_type,
                "budget_scope": diagnostics.budget_scope,
                "spend": diagnostics.spend,
                "cap": diagnostics.cap,
                "budget_date": diagnostics.budget_date,
                "message": diagnostics.raw_message,
            },
        )
    except Exception as extra:  # noqa: BLE001 - diagnostics must not hide provider error
        logger.warning("failed to append quality budget diagnostic: %s", extra)


def _append_route_switch_event(
    *,
    model_family: str,
    from_model: str,
    to_model: str,
    consumer: str,
    loop_id: str = "loop1",
) -> None:
    """luna 同族通路重试落账（route_switch，不是 fallback）。"""
    try:
        append_event(
            _quality_ledger_path(),
            {
                "loop_id": loop_id or "loop1",
                "node": "llm_router",
                "event": ROUTE_SWITCH_EVENT,
                "ts": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "details": {
                    "model_family": model_family,
                    "from_model": from_model,
                    "to_model": to_model,
                    "consumer": consumer,
                    "fallback_used": False,
                },
            },
        )
    except Exception as extra:  # noqa: BLE001 - 落账失败不改变通路重试语义
        logger.warning("failed to append %s ledger event: %s", ROUTE_SWITCH_EVENT, extra)


def _resolve_reasoning_effort(
    model_family: str, reasoning_effort: str | None
) -> str | None:
    """调用方默认 → 用族配置；显式 None 表示不发送。"""
    if reasoning_effort == _DEFAULT_REASONING_EFFORT:
        cfg = _MODEL_CONFIG.get(model_family) or {}
        family_effort = cfg.get("reasoning_effort")
        if family_effort:
            return str(family_effort)
        return None
    return reasoning_effort


def _route_model_candidates(model_family: str, config: dict[str, Any]) -> list[str]:
    raw = [str(item) for item in (config.get("model_id_candidates") or [config["model_id"]])]
    if not raw:
        raw = [str(config["model_id"])]
    retry_max = LUNA_GATEWAY_ROUTE_RETRY_MAX if model_family == "gpt-5.6-luna" else 0
    return raw[: 1 + retry_max]


# F20: enable_thinking 是 DeepSeek/Qwen 系参数,gpt-5.4(Azure via litellm)/gemini 不认。
# 无差别注入导致 gpt-5.4 端点 400 Unknown parameter(账本 169 条 distill_extract 7-03/7-04,
# content_hash 如 3c2bff1f7d020caa/2af524e463b565cf/460406a043bd70e7 等)。只对 deepseek 系
# 保留 enable_thinking,gpt-5.4/gemini 剥离(deepseek 行为一字不变)。
_THINKING_PARAM_MODEL_PREFIXES: tuple[str, ...] = ("deepseek",)


def _filter_extra_body_for_model(
    model_family: str, extra_body: dict[str, Any] | None
) -> dict[str, Any] | None:
    """F20: 按模型族过滤 extra_body——只保留端点支持的参数。

    enable_thinking 只对 deepseek 系注入(关思维链,DeepSeek 推理模型专属);
    gpt-5.4/gemini 端点不认此参数 → 剥离(否则 400 Unknown parameter)。
    deepseek 行为一字不变(原样保留 extra_body)。

    Args:
        model_family: 模型家族标识(deepseek-v4-pro / gpt-5.4 / gemini-flash)。
        extra_body: 调用方传入的额外字段(可能含 enable_thinking)。

    Returns:
        过滤后的 extra_body(gpt-5.4/gemini 剥离 enable_thinking;deepseek 原样)。
    """
    if not extra_body:
        return None  # None 或 {} 都不传(空 extra_body 无意义,不向端点发空 dict)
    if any(model_family.startswith(p) for p in _THINKING_PARAM_MODEL_PREFIXES):
        return extra_body  # deepseek 系:原样保留(行为不变)
    # 非 deepseek 系:剥离 enable_thinking(gpt-5.4/gemini 不认)
    filtered = {k: v for k, v in extra_body.items() if k != "enable_thinking"}
    return filtered or None


# F54: 上游 2026-07-23T03:24Z 起下掉 OpenAI 风格 json_schema。生产网关
# (https://llm-proxy.tapsvc.com/v1, 模型 deepseek/deepseek-v4-pro)实测 2026-07-30:
#   response_format={"type":"json_schema"} → 400 "This response_format type is
#   unavailable now" / {"type":"json_object"} → 通 / 不带 response_format → 通。
# 准入四判定(topic_relevance/how_tendency/novelty/content_lane)全部走
# route_llm(response_schema=...) → 全 400 → fail-closed 全拒 → 2026-07-24T17:40Z 起
# 零入库(账本实锤连续 6 天 finalize=0),且 abort 落账写成"准入未通过: topic_relevance,
# ..."把 API 故障伪装成内容质量拒绝(违宪法 P4 失败可见)。
#
# 降级策略选「deepseek 族静态降级」而非「捕获 400 后动态降级」,理由:
#   1. 上游是能力下架、不是瞬时抖动 —— 每次请求先撞一次 400 再降级是确定性浪费
#      (与 F20/F18 "400 不可重试立即 break" 同结论);
#   2. 静态降级是纯函数决策,build_outbound_payload 可直接断言"payload 不含
#      json_schema",动态降级只能靠 mock 异常路径间接复现,回归防线更弱;
#   3. json_object 是 DeepSeek 官方 JSON Output 正式能力(route_deepseek_json_object
#      已在用),不引入新的不确定性。
# 不复用 route_deepseek_json_object:那是 distill 的独立入口(AKB2_DISTILL_DEEPSEEK_
# JSON_MODE=1 专用),硬编 deepseek-v4-pro 族、不透传 timeout/max_tokens/extra_body/
# reasoning_effort、也不做 schema 校验。route_llm 要保住这些参数语义,借道过去会静默
# 丢参数(比重复几行 response_format 构造危险得多)。两者形状像、职责不同,不合并。
# gpt-5.4 / gemini 族路径一字不动(其 json_schema 仍可用)。
_JSON_SCHEMA_UNSUPPORTED_FAMILY_PREFIXES: tuple[str, ...] = ("deepseek",)

# 降级后 schema 不再由端点强制 → 写进 prompt + 本地 jsonschema 兜底。
# 文案含 "JSON" 字样:DeepSeek JSON Output 模式要求 prompt 出现 json 提示词。
SCHEMA_PROMPT_HEADER: str = (
    "【输出格式硬约束】只输出一个 JSON object，不要 Markdown 代码围栏，不要任何解释文字。"
    "该 JSON 必须严格符合下面的 JSON Schema（字段名/类型/枚举值/required 一致；"
    "additionalProperties 为 false 时不得输出多余字段）："
)


def _family_supports_json_schema(model_family: str) -> bool:
    """该模型族端点是否仍支持 OpenAI 风格 response_format=json_schema。

    False → 走 json_object + prompt 内嵌 schema + 本地 jsonschema 校验的降级路径。
    """
    return not any(
        model_family.startswith(prefix)
        for prefix in _JSON_SCHEMA_UNSUPPORTED_FAMILY_PREFIXES
    )


def build_schema_prompt_messages(
    messages: list[dict[str, Any]], response_schema: dict[str, Any]
) -> list[dict[str, Any]]:
    """json_object 降级路径：把 schema 约束追加进 prompt。

    返回新列表，不修改调用方传入的 messages（准入/提炼都复用同一 messages 构造，
    原地 append 会在重试时累积多份 schema 指令）。
    """
    instruction = (
        f"{SCHEMA_PROMPT_HEADER}\n"
        f"{json.dumps(response_schema, ensure_ascii=False, sort_keys=True)}"
    )
    return [*messages, {"role": "system", "content": instruction}]


def validate_response_against_schema(
    content: Any, response_schema: dict[str, Any]
) -> None:
    """json_object 降级路径的本地 jsonschema 校验（替代端点侧 strict schema）。

    Raises:
        LLMProviderError: content 不符合 schema。抛出后由调用方按原有失败路径处理
            —— distill 的有界重试循环重抽、admission 的 ``except RuntimeError``
            fail-closed —— fail-closed 语义不变，且失败原因显式可见（宪法 P4）。
    """
    import jsonschema

    try:
        jsonschema.validate(content, response_schema)
    except jsonschema.ValidationError as exc:
        raise LLMProviderError(
            f"json_object 降级路径本地 schema 校验失败: {exc.message}"
        ) from exc


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


def family_vendor(model_family: str) -> str:
    """返回模型家族的 vendor。缺字段 fail-closed（宪法 P7）。"""
    cfg = _MODEL_CONFIG.get(model_family)
    vendor = cfg.get("vendor") if isinstance(cfg, dict) else None
    if not isinstance(vendor, str) or not vendor.strip():
        raise RouterConfigError(
            f"模型家族缺少 vendor 字段: {model_family!r}（宪法 P7）"
        )
    return vendor.strip()


def validate_maker_checker_separation(maker_family: str, checker_family: str) -> None:
    """F10-D: 宪法护栏——maker 与 checker 必须不同厂商(无自评自批)。

    A/B 双臂对比通过 AKB2_DISTILL_FAMILY/AKB2_GRADER_FAMILY env 覆盖模型族。
    比的是 _MODEL_CONFIG.vendor，不是族名字符串：gpt-5.4 与 gpt-5.6-luna 同为
    openai，必须 fail-fast。7×24 禁 -preview 由 _validate_model_family 在调用时拒。
    """
    if not maker_family or not checker_family:
        raise RouterConfigError(
            f"maker/checker 模型族为空: maker={maker_family!r} checker={checker_family!r}"
        )
    maker_vendor = family_vendor(maker_family)
    checker_vendor = family_vendor(checker_family)
    if maker_vendor == checker_vendor:
        raise RouterConfigError(
            f"宪法护栏:maker({maker_family}/{maker_vendor})与 "
            f"checker({checker_family}/{checker_vendor})同厂商,"
            f"违 Maker-Checker 异族分离(P7)。"
        )


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

    env_model = os.environ.get(cfg["model_id_env"], "").strip()
    if env_model:
        candidates = [env_model]
    elif gateway_selected:
        listed = cfg.get("gateway_model_ids")
        if isinstance(listed, list) and listed:
            candidates = [str(item) for item in listed]
        else:
            candidates = [
                str(cfg.get("gateway_model_id_default", cfg["model_id_default"]))
            ]
    else:
        candidates = [str(cfg["model_id_default"])]
    model_id = candidates[0]

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
        "model_id_candidates": candidates,
        "vendor": cfg.get("vendor", ""),
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
            f"DeepSeek beta tool 调用超时 ({actual_timeout}s):"
            f" {_public_provider_detail(exc)}"
        ) from None
    except openai.APIError as exc:
        # SC-R2-11① 审计连带修复：旧代码把 `{exc}` **直接**内插进公开消息（比被点名
        # 的 route_llm 那处更露），且 `from exc` 又把原文留在链上。两条路径一起收口。
        raise LLMProviderError(
            f"DeepSeek beta tool API 错误: {_public_provider_detail(exc)}"
        ) from None

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
            f"DeepSeek JSON mode 调用超时 ({actual_timeout}s):"
            f" {_public_provider_detail(exc)}"
        ) from None
    except openai.APIError as exc:
        # SC-R2-11① 审计连带修复：同 route_deepseek_beta_tool，原文既进公开消息又挂链上。
        raise LLMProviderError(
            f"DeepSeek JSON mode API 错误: {_public_provider_detail(exc)}"
        ) from None

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
    max_tokens: int | None = None,
    extra_body: dict[str, Any] | None = None,
    budget_consumer: str = "quality.llm_router",
    article_key: str | None = None,
    loop_id: str = "loop1",
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
        max_tokens: completion token 上限；None 时不发送（不限）。提炼链路应留空
            以免截断 claims/how_steps；重写（repair）链路设上限防推理失控拖超时。
        extra_body: 透传到 OpenAI-compatible 端点的额外字段（如 DeepSeek 推理模型的
            ``{"enable_thinking": False}``，关闭思维链，让 token 预算落到 content 而非
            reasoning，避免 repair 重写被 reasoning 吃光 token / 拖超时）。None 时不发送。
        budget_consumer: 预算闸/诊断里的调用方标签，默认统一记为 quality.llm_router。
        article_key: per-article 调用预算的 article 身份（sc-p3-max-calls-enforced）。
            传入时同一 key 的第 MAX_CALLS_PER_ARTICLE+1 次调用在发起 API 请求之前
            fail-closed（ArticleBudgetExceededError）并落 quality 账本；计数跨进程
            共享。None（既有调用方现状）不参与计数——per-article 上限只对声明了
            article 身份的链路强制。

    Returns:
        LLMResponse: 含 content / model_used / calls_used。

    Raises:
        RouterConfigError: 配置错误（缺 key / 非法模型名）。
        ArticleBudgetExceededError: 同 article_key 调用超出 MAX_CALLS_PER_ARTICLE。
        RuntimeError: API 调用超时或返回非 200。
    """
    config = _validate_model_family(model_family)
    api_key = _get_api_key(config["key_env_candidates"])

    # sc-p3-max-calls-enforced：per-article 预算强制在 API 请求发起之前。
    # 放在 budget gate 之前——超限是确定性本地判定，不该再花一次闸查询。
    if article_key is not None:
        if not reserve_article_call(article_key, limit=MAX_CALLS_PER_ARTICLE):
            _record_article_budget_exceeded(
                article_key=article_key,
                model_family=model_family,
                consumer=budget_consumer,
                limit=MAX_CALLS_PER_ARTICLE,
                loop_id=loop_id,
            )
            raise ArticleBudgetExceededError(
                f"article {_normalise_article_key(article_key)} 的 LLM 调用已达"
                f" MAX_CALLS_PER_ARTICLE={MAX_CALLS_PER_ARTICLE}，"
                f"第 {MAX_CALLS_PER_ARTICLE + 1} 次调用 fail-closed 拒绝"
                f"（sc-p3-max-calls-enforced）；已落账 {ARTICLE_BUDGET_EVENT}"
            )

    try:
        import openai
    except ImportError as exc:
        raise RouterConfigError(
            "openai 包未安装。请运行: pip install openai>=1.0"
        ) from exc

    actual_timeout = TIMEOUT_S if timeout_s is None else timeout_s
    actual_max_retries = MAX_RETRIES if max_retries is None else max_retries

    gate_decision = query_budget_gate(consumer=budget_consumer)
    if not gate_decision.allowed:
        raise LLMProviderError(gate_decision.shell_message())

    client = openai.OpenAI(
        api_key=api_key,
        base_url=config["base_url"],
        timeout=actual_timeout,
        max_retries=actual_max_retries,
    )

    # F54: deepseek 族静态降级——json_schema 已被上游下掉(400),改 json_object +
    # prompt 内嵌 schema + 本地 jsonschema 校验。
    if response_schema is not None and not _family_supports_json_schema(model_family):
        degrade_to_json_object = True
        outbound_messages = build_schema_prompt_messages(messages, response_schema)
    else:
        degrade_to_json_object = False
        outbound_messages = messages

    kwargs: dict[str, Any] = {
        "model": config["model_id"],
        "messages": outbound_messages,
    }

    resolved_effort = _resolve_reasoning_effort(model_family, reasoning_effort)
    if resolved_effort is not None:
        kwargs["reasoning_effort"] = resolved_effort

    # completion token 上限（仅 repair 等需要硬上限的链路传入；提炼链路留空不截断）
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    # 透传额外字段（如关闭 DeepSeek 推理 enable_thinking=False）
    # F20: 按模型族过滤——enable_thinking 只对 deepseek 系注入,gpt-5.4/gemini 剥离
    # (否则 gpt-5.4 端点 400 Unknown parameter,账本 169 条 distill_extract 7-03/7-04)。
    if extra_body is not None:
        kwargs["extra_body"] = _filter_extra_body_for_model(model_family, extra_body)

    # 结构化输出（OpenAI 兼容端）
    if response_schema is not None:
        if degrade_to_json_object:
            # F54: schema 约束已进 prompt(outbound_messages),端点只保 JSON 语法
            kwargs["response_format"] = {"type": "json_object"}
        else:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": response_schema,
                    "strict": strict_schema,
                },
            }

    candidates = _route_model_candidates(model_family, config)
    response = None
    used_model_id = candidates[0]
    calls_used = 0
    last_error: Exception | None = None
    for attempt, model_id in enumerate(candidates):
        kwargs["model"] = model_id
        try:
            response = client.chat.completions.create(**kwargs)
            calls_used += 1
            used_model_id = model_id
            if attempt > 0:
                _append_route_switch_event(
                    model_family=model_family,
                    from_model=candidates[attempt - 1],
                    to_model=model_id,
                    consumer=budget_consumer,
                    loop_id=loop_id,
                )
            break
        except openai.APITimeoutError as exc:
            calls_used += 1
            last_error = LLMTimeoutError(
                f"LLM 调用超时 ({actual_timeout}s): {model_family}"
                f" — {_public_provider_detail(exc)}"
            )
            if attempt + 1 >= len(candidates):
                raise last_error from None
            continue
        except openai.APIError as exc:
            calls_used += 1
            diagnostics = _diagnostics_from_provider_error(exc)
            gate_write_note = ""
            if diagnostics.provider_code == PROVIDER_CODE_429:
                _append_quality_budget_event(
                    diagnostics=diagnostics,
                    model_family=model_family,
                    consumer=budget_consumer,
                    loop_id=loop_id,
                )
                try:
                    record_budget_event(diagnostics, consumer=budget_consumer)
                except UnscopedBudgetEventError as gate_exc:
                    # fail-loud 但不吞掉 provider 错误：分类器坏了要响亮暴露（ERROR 级 +
                    # 进抛出的异常文本），同时保留「这次调用因 provider 429 失败」这条
                    # 主因，否则运维只看到一个 ValueError 却不知道 provider 发生了什么。
                    logger.error("shared budget gate refused unscoped event: %s", gate_exc)
                    gate_write_note = f" | budget_gate_write_refused={gate_exc}"
            last_error = LLMProviderError(
                f"LLM API 错误: {model_family} — error_type={diagnostics.error_type}"
                f" budget_scope={diagnostics.budget_scope or 'n/a'}"
                f" provider_code={diagnostics.provider_code or 'n/a'}"
                f" detail={_public_provider_detail(exc)}{gate_write_note}"
            )
            if attempt + 1 >= len(candidates):
                raise last_error from None
            continue
    if response is None:
        raise last_error or LLMProviderError(
            f"LLM API 错误: {model_family} — 无可用通路"
        )

    raw = response.choices[0].message.content or ""

    # 尝试解析为 JSON
    content: str | dict = raw
    if response_schema is not None:
        try:
            content = json.loads(raw)
        except json.JSONDecodeError as exc:
            if degrade_to_json_object:
                # F54: 降级路径端点不再强制 schema,语法失败必须显式抛(宪法 P4),
                # 由 distill 有界重试 / admission fail-closed 接管;不静默回原文。
                raise LLMProviderError(
                    f"json_object 降级路径返回不是合法 JSON: {model_family}"
                ) from exc
            # 结构化输出模式下不强解析，返回原始字符串
            content = raw
        else:
            if degrade_to_json_object:
                validate_response_against_schema(content, response_schema)

    return LLMResponse(
        content=content,
        model_used=used_model_id,
        calls_used=calls_used,
        fallback_used=False,
    )


def build_outbound_payload(
    model_family: str,
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any] | None = None,
    *,
    reasoning_effort: str | None = _DEFAULT_REASONING_EFFORT,
    strict_schema: bool = True,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建出站请求体（供测试断言 reasoning_effort / extra_body 透传）。

    不发送请求，只返回将要发送的 payload dict。
    用于 fixture 测试中验证 reasoning_effort / extra_body 字段存在 + 按模型族过滤。
    F20: extra_body 经 _filter_extra_body_for_model 过滤后透传(测试可断言 gpt-5.4 不含
    enable_thinking / deepseek 保持现状)。
    F54: deepseek 族 response_schema 请求降级为 json_object + schema 进 prompt
    (测试可断言 payload 不含 json_schema、messages 携 schema 约束)。
    """
    config = _validate_model_family(model_family)
    if response_schema is not None and not _family_supports_json_schema(model_family):
        degrade_to_json_object = True
        outbound_messages = build_schema_prompt_messages(messages, response_schema)
    else:
        degrade_to_json_object = False
        outbound_messages = messages

    payload: dict[str, Any] = {
        "model": config["model_id"],
        "messages": outbound_messages,
    }

    resolved_effort = _resolve_reasoning_effort(model_family, reasoning_effort)
    if resolved_effort is not None:
        payload["reasoning_effort"] = resolved_effort

    if extra_body is not None:
        payload["extra_body"] = _filter_extra_body_for_model(model_family, extra_body)

    if response_schema is not None:
        if degrade_to_json_object:
            payload["response_format"] = {"type": "json_object"}
        else:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": response_schema,
                    "strict": strict_schema,
                },
            }

    return payload
