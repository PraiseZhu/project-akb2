"""synthesis.py — 调 DeepSeek V4 Pro 综合打分结果为 social_trends_v1_1 payload。

CLI: python -m akb2.briefing.synthesis --input <scored.json> --output <synth.json>

- 若 AI_GATEWAY_API_KEY 可用：调真实 API（AI Gateway，默认 llm-proxy.tapsvc.com，OpenAI 兼容）
- 若无凭证：使用 deterministic fallback（从 scored.json 中前 5 条推文构造合规 payload）
- 输出必须通过 social_trends_v1_1 schema 校验
- 失败时最多重试 2 次（指数退避，间隔 1s / 2s）
- **模型可用性耗尽（主族 + 备族都挂）→ fail-closed 抛错，不写降级假简报**
- 中文化由独立 translate.py 阶段负责，synthesis 只产出干净结构化分析
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from akb2.common.http_retry import ProviderErrorDiagnosis, classify_provider_error


class SynthesisUnavailableError(RuntimeError):
    """LLM 合成不可用（主族 + 备族均失败）→ fail-closed，不产出任何 payload。

    修复前这条路径 `return _fallback_synthesis(...)`：一份 topic 恒为
    "AI Technology Trends"、summary 里明文写着 "This is a fallback summary generated
    when the synthesis model is unavailable" 的**降级假简报**会被照常写进 vault，
    读者无从分辨今天是真没内容还是模型挂了。同一仓库的 github 链遇到同样情形是
    `_write_failed` + exit 1、vault 零写入（github/pipeline.py:356-362）——
    两条链语义正好相反且都未登记豁免。现统一为 github 侧的 fail-closed（宪法 P3）。
    """


class SynthesisBudgetExceededError(SynthesisUnavailableError):
    """共享 Gateway 账户预算耗尽 —— 不退避、不换族、不重试（首击即抛）。

    继承 SynthesisUnavailableError 有两个实际作用，都是复用既有形状而不是新造：
      1. `_call_deepseek` 的 `except SynthesisUnavailableError: raise` 分支已经在那儿
         —— 预算耗尽自动获得「不换族」语义（账户级额度换族拿不回，见 github 侧
         SynthesisAPIError.family_fallback_worthwhile 的论证）。
      2. `synthesize()` 的质量重试循环同样对 SynthesisUnavailableError 立即上抛，
         不会再白烧 3 轮（宪法 P2 收敛优先于重跑）。
    """


def _reject_preview_model(model: str, source: str) -> str:
    """宪法 P7 / docs/constitution.md:95：7×24 管线 MUST NOT 使用 `-preview` 模型。

    rationale 点名的正是「预览版限流不受控」—— 而这条链此前默认值就是
    `gemini-3.1-pro-preview`，两个生产 env 文件与 launchd plist 都没有覆盖它，
    等于生产 7×24 一直明文违宪且 allowed-residuals.md 无豁免登记。
    错误文案与 pipeline.py:_assert_no_preview_translate_models 保持一致，便于统一 grep。
    """
    if "-preview" in model:
        raise SynthesisUnavailableError(
            f"preview model is forbidden for 7x24 pipeline: {source}={model}"
            "（宪法 P7 / docs/constitution.md:95，rationale：预览版限流不受控）"
        )
    return model


# 合成阶段专用模型（可由环境变量覆盖）。默认取正式版 deepseek flash ——
# 与 translate.py 同族（gateway id 仍是 deepseek/），但合成是日报主成本，走 flash+max。
# 这里的字面量在导入期就过 -preview 校验：默认值绝不可能再退回预览版。
_DEFAULT_SYNTHESIS_MODEL = _reject_preview_model(
    "deepseek/deepseek-v4-flash", "default"
)
_SYNTHESIS_MODEL = os.environ.get("AKB2_SYNTHESIS_MODEL", _DEFAULT_SYNTHESIS_MODEL)
_DEFAULT_SYNTHESIS_REASONING_EFFORT = "max"
_SYNTHESIS_REASONING_EFFORT_ENV = "AKB2_SYNTHESIS_REASONING_EFFORT"

# 备用模型族（宪法 P7 点名的兜底族，正式版）。主族不可用时降级再试一次，
# 两族都挂才 fail-closed —— 「降级用尽后才拒」而不是「一挂就拒」。
_FALLBACK_SYNTHESIS_MODEL = _reject_preview_model("gemini-3.5-flash", "fallback_default")

# COMMON.md 契约 1：读到 =1 直接从备族开始。适用场景是**单族级故障**（持续限流 /
# 5xx / 超时）；账户级预算耗尽换族无效（主备族同 Gateway 同 key 同 user），那条路径
# 由共享预算闸在 HTTP 之前短路，不靠这个开关。
_FAMILY_FALLBACK_ENV = "AKB2_LLM_FAMILY_FALLBACK"

# 共享预算闸里记「谁写下了这条封锁」。与 daily_briefing.sh preflight 的
# `social_briefing_main`（读者标签）刻意不同名：排查时要能一眼分出
# 「谁撞到 429 布的闸」和「谁被闸拦住」。
BUDGET_GATE_CONSUMER = "social.synthesize"

# 本次 synthesize() 运行中已确认不可用的模型族。synthesize 对**质量退化**重试 3 次
# （见 max_attempts），每次都重新调 _call_deepseek；不记账的话，刚判定不可用的主族
# 会被原样再打 3 遍 —— 正是本 lane 要消掉的「明知耗尽还硬试」换位复发。
# 生命周期 = 单次 synthesize()（入口清空）。用模块级而非参数传递，是因为
# `_call_deepseek(prompt, max_retries)` 的签名被 12+ 处既有测试 mock 钉死，
# 加参数会让那些 mock 全部 TypeError。
_unavailable_families: set[str] = set()


def _resolve_synthesis_model() -> str:
    """调用时解析主模型并硬校验 —— 不靠 env 自觉。

    env 覆盖在**调用点**校验而非导入期：导入期抛会让整个 test collection 炸掉，
    而调用点抛既拦住生产误配，又保留「显式设了预览版 → 调用即抛」的可测语义。
    """
    model = os.environ.get("AKB2_SYNTHESIS_MODEL") or _SYNTHESIS_MODEL
    return _reject_preview_model(model, "AKB2_SYNTHESIS_MODEL")


def _resolve_synthesis_reasoning_effort() -> str | None:
    """主族合成的 reasoning 档。空 / none / off 表示不传该字段。"""
    raw = os.environ.get(
        _SYNTHESIS_REASONING_EFFORT_ENV, _DEFAULT_SYNTHESIS_REASONING_EFFORT
    ).strip()
    if raw.lower() in {"", "none", "off"}:
        return None
    return raw

# 合成 prompt 文件路径：synthesis.py 在 <root>/src/akb2/briefing/ 下，docs/ 在 <root>/，
# 故 4 层 parent 到 <root>（worktree 与主仓库结构一致，均适用）。
# 历史 bug：曾用 5 层 → 多跳一级到不存在的目录 → 回退内联兜底 prompt（无短序号指令）
# → supporting_tweet_ids 全塌 top[:5]（P0 实跑根因）。
_PROMPT_PATH = (
    Path(__file__).parent.parent.parent.parent / "docs" / "deepseek-synthesis-prompt-v1.md"
)

# synthesis system message：约束分析质量，语言交由后续翻译阶段
_SYNTHESIS_SYSTEM = (
    "You are an expert AI/ML trend analyst. "
    "Analyze the provided tweets and return a structured JSON report following the schema exactly. "
    "Focus on genuine signal: specific tools, models, metrics, and developer sentiment. "
    "Avoid vague summaries. Every topic must be specific "
    "(e.g. 'Agent memory infrastructure' not 'AI Trends'). "
    "Do NOT fabricate events or numbers not present in the input tweets."
)


def _has_api_key() -> bool:
    """检测是否有可用 API 凭证。"""
    return bool(os.environ.get("AI_GATEWAY_API_KEY"))


def _call_llm(
    messages: list[dict],
    model: str,
    *,
    json_mode: bool = True,
    max_retries: int = 1,
    timeout: int = 300,
    base_url: str | None = None,
    reasoning_effort: str | None = None,
    extra_body: dict | None = None,
) -> str:
    """通用 LLM 调用（OpenAI-compatible），瞬时错误重试一次，返回 raw content string。

    读超时不重试同模型：再打只会再烧一整次 timeout，应由调用方换族。
    extra_body 合并进请求体（如 DeepSeek ``enable_thinking=False``）。
    """
    import httpx

    api_key = os.environ.get("AI_GATEWAY_API_KEY")
    url_base = (base_url or os.environ.get("AI_GATEWAY_BASE_URL", "https://llm-proxy.tapsvc.com")).rstrip("/")
    url = f"{url_base}/v1/chat/completions"

    payload: dict = {"model": model, "messages": messages, "temperature": 0.2}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if extra_body:
        payload.update(extra_body)

    for attempt in range(max_retries + 1):
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            _log_raw_response(model=model, raw=raw)
            return raw
        except SynthesisUnavailableError:
            raise
        except Exception as exc:
            # 预算耗尽首击即停：退避与换族都拿不回账户额度，继续打只是把确定失败的
            # 请求再发几遍（R1 实测本链对真实 UTC 日预算体发出 DeepSeek×3 + Gemini×3
            # 共 6 次请求才失败）。同时为后续入口布共享闸（SC-R1-06）。
            diagnosis = _classify_llm_exception(exc)
            if diagnosis is not None and diagnosis.is_budget_exceeded:
                _record_shared_budget_gate(diagnosis, model_id=model)
                raise SynthesisBudgetExceededError(
                    f"synthesis fail-fast: 共享 Gateway 预算耗尽，"
                    f"重试与换族均无效（{diagnosis.summary()} model={model}）——"
                    f"跳过剩余 {max_retries - attempt} 次重试；"
                    f"补救只有等额度重置到具名边界。"
                ) from exc
            # 读超时再打同一模型只会再烧一整次 timeout；翻译链应立刻换族。
            if isinstance(exc, httpx.TimeoutException):
                raise
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)

    raise RuntimeError(f"_call_llm({model}): exhausted retries")  # pragma: no cover


def _classify_llm_exception(exc: Exception) -> ProviderErrorDiagnosis | None:
    """把 httpx 异常归因成共享契约 2 结构；无响应可分类时返回 None。

    分类走 `http_retry.classify_provider_error` 这**唯一一处**实现 —— 本模块不自己
    认字符串（github 侧与 quality 侧各写一份的后果已经在 R1 审查里付过代价）。
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        return None
    try:
        body = response.text
    except Exception:  # noqa: BLE001 — 响应体读不到不影响状态码分类
        body = ""
    return classify_provider_error(status, body)


def _record_shared_budget_gate(
    diagnosis: ProviderErrorDiagnosis, *, model_id: str
) -> None:
    """把预算耗尽写进跨进程共享闸（惰性 import，见 github 侧同名函数的论证）。"""
    try:
        from akb2.common.budget_gate import BudgetDiagnostics, record_budget_event

        record_budget_event(
            BudgetDiagnostics(
                error_type=diagnosis.error_type,
                budget_scope=diagnosis.budget_scope,
                spend=diagnosis.spend,
                cap=diagnosis.cap,
                budget_date=diagnosis.budget_date,
                provider_code=str(diagnosis.provider_code),
                raw_message=diagnosis.raw_excerpt,
            ),
            consumer=BUDGET_GATE_CONSUMER,
        )
    except Exception as exc:  # noqa: BLE001 — 布闸失败不得吞掉 provider 真因
        sys.stderr.write(
            f"WARNING: 共享预算闸写入失败（model={model_id}）: "
            f"{type(exc).__name__}: {exc}\n"
        )


def _log_raw_response(model: str, raw: str) -> None:
    """将原始响应元数据追加到日志（脱敏：仅记录 model + 长度，不含 key）。"""
    log_dir = Path(os.environ.get("AKB2_LOG_DIR", "/tmp/akb2-logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        entry = json.dumps(
            {"ts": datetime.now(UTC).isoformat(), "model": model, "raw_len": len(raw)},
            ensure_ascii=False,
        )
        with open(log_dir / "synthesis-raw.jsonl", "a") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def _call_deepseek(prompt: str, max_retries: int = 2) -> str:
    """调合成模型（带 system message），返回 raw response string。

    主族不可用（含预算耗尽、含重试耗尽）→ 降级到备用族再试一次；两族都挂才抛。
    修复前此处只有单一硬编码模型，单点模型故障 = 当日简报 100% 缺失，零降级余量；
    而预算耗尽时退避完全无效（额度不会因为等待回来），换族是唯一有效补救。

    降级在**函数内部**而不是包一层外部 wrapper：本函数已被 12+ 处测试整体 mock 掉，
    降级放外层等于让所有既有用例绕过它、同时又要求它们的 mock 认识新增的 model 参数。
    """
    messages = [
        {"role": "system", "content": _SYNTHESIS_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    # 契约 1：backstop 第 2 轮补跑时直接从备族开始，主族零请求
    if os.environ.get(_FAMILY_FALLBACK_ENV) == "1":
        print(
            f"SYNTHESIS_FALLBACK model={_FALLBACK_SYNTHESIS_MODEL} "
            f"reason=env_{_FAMILY_FALLBACK_ENV}=1",
            flush=True,
        )
        return _call_llm(
            messages, model=_FALLBACK_SYNTHESIS_MODEL,
            json_mode=True, max_retries=max_retries,
        )

    primary = _resolve_synthesis_model()
    primary_effort = _resolve_synthesis_reasoning_effort()

    # 本次运行已判定主族不可用（质量重试的第 2/3 轮会走到这里）→ 不再白烧一轮
    if primary in _unavailable_families:
        print(
            f"SYNTHESIS_FALLBACK model={_FALLBACK_SYNTHESIS_MODEL} "
            f"reason=primary_already_known_unavailable primary={primary}",
            flush=True,
        )
        return _call_llm(
            messages, model=_FALLBACK_SYNTHESIS_MODEL,
            json_mode=True, max_retries=max_retries,
        )

    try:
        return _call_llm(
            messages, model=primary, json_mode=True, max_retries=max_retries,
            reasoning_effort=primary_effort,
        )
    except SynthesisUnavailableError:
        raise  # 预览版误配是部署错误，换族掩盖不了
    except Exception as primary_err:
        _unavailable_families.add(primary)  # 记账后再降级
        print(
            f"SYNTHESIS_FALLBACK model={_FALLBACK_SYNTHESIS_MODEL} "
            f"reason={type(primary_err).__name__} primary={primary}",
            flush=True,
        )
        sys.stderr.write(
            f"WARNING: 主模型族 {primary} 不可用（{type(primary_err).__name__}: "
            f"{str(primary_err)[:200]}）→ 降级到 {_FALLBACK_SYNTHESIS_MODEL}\n"
        )
        try:
            return _call_llm(
                messages, model=_FALLBACK_SYNTHESIS_MODEL,
                json_mode=True, max_retries=max_retries,
            )
        except Exception as fallback_err:
            # 两族都挂 → fail-closed（宪法 P3），两族真因都要可见（P4）
            raise SynthesisUnavailableError(
                f"两个模型族均失败 → 不产出简报。"
                f"主族 {primary}: {type(primary_err).__name__}: "
                f"{str(primary_err)[:200]} || "
                f"备族 {_FALLBACK_SYNTHESIS_MODEL}: {type(fallback_err).__name__}: "
                f"{str(fallback_err)[:200]}"
            ) from fallback_err


def _validate_response(data: dict) -> None:
    """用 social_trends_v1_1 schema 校验，失败 raise ValidationError。"""
    from akb2.briefing.validator import validate_payload

    validate_payload(data)


def _validate_quality(merged: dict) -> None:
    """质量门控 — 校验合成产出可用，不通过则 raise 触发重试（防 model 退化输出直接放行到 audit）。

    注意架构：合成阶段语言无关（英文 OK），中文化由 translate 阶段负责、audit 阶段把关。
    因此这里**不**要求 topic 中文，只确保结构可用，让 translate 有东西可译：
    - 每个 focus_discussion 的 topic 必须非空且非空洞词
    - 每个 focus_discussion 的 summary 必须非空(deepseek/gemini 偶发只给 topic 丢 summary)
    - executive_summary 缺失不 raise(由 merge 的 _fallback_executive_summary 兜底)
    """
    fds = merged.get("focus_discussions", [])
    if len(fds) < 1:
        raise ValueError("quality: 无 focus_discussions")

    BANNED = {"ai trends", "ai 趋势", "trends", "核心讨论", "ai trend"}
    for i, fd in enumerate(fds):
        topic = (fd.get("topic") or "").strip()
        if not topic:
            raise ValueError(f"quality: focus_discussions[{i}].topic 为空")
        if topic.lower() in BANNED:
            raise ValueError(f"quality: topic 是空洞词 '{topic}'")
        if not (fd.get("summary") or "").strip():
            raise ValueError(f"quality: focus_discussions[{i}].summary 为空（合成退化）")


def _fallback_synthesis(scored_input: dict, date: str | None = None) -> dict:
    """无 API 凭证时的确定性 fallback：直接从 scored.json 构造合规 payload。"""
    tweets = scored_input.get("tweets", [])
    # 取 score 最高的前 5 条
    top = sorted(tweets, key=lambda t: t.get("score", 0), reverse=True)[:5]

    now = datetime.now(UTC)
    resolved_date = date or now.strftime("%Y-%m-%d")

    exec_summary = (
        f"{resolved_date} AI/ML 趋势快照，采集自公开社交时间线，共 {len(top)} 条高分推文。"
        " 本期数据为离线 fallback 产物，模型合成不可用时的确定性输出。"
        " 以下为基于评分排名的推文概览，供参考。"
    )
    payload: dict = {
        "date": resolved_date,
        "generated_at": now.isoformat(),
        "source": {"name": "synthesis_fallback", "collector_version": "1.0.0"},
        "executive_summary": exec_summary,
        "scoring_summary": {
            "total_tweets": len(tweets),
            "scored_tweets": len(top),
        },
        "tweets": [
            {
                "id": str(t["id"]),
                "author": t.get("author", ""),
                "text": t.get("text", ""),
                "created_at": t.get("created_at", ""),
                "score": t.get("score"),
                "dimensions": t.get("dimensions", {}),
                "url": t.get("url", ""),
                "lang": t.get("lang", ""),  # P2-1: 透传 lang，供 translate per-tweet 分流
            }
            for t in top
        ],
        "focus_discussions": [
            {
                "topic": "AI Technology Trends",
                "supporting_tweet_ids": [str(t["id"]) for t in top[:3]],
                "summary": (
                    f"Top {len(top)} tweets about AI trends, scored by relevance and authority."
                    + (
                        f" Highest scoring tweet by {top[0].get('author', 'unknown')} with score "
                        f"{top[0].get('score', 0):.3f}."
                        if top else " No tweets available for this period."
                    )
                    + (
                        " This is a fallback summary generated when the synthesis model "
                        "is unavailable."
                    )
                ),
            }
        ],
    }
    return payload


def _build_indexed_tweet_listing(top: list[dict]) -> tuple[str, dict[str, str]]:
    """推文列表用短序号 [1][2]... 展示，返回 (文本, {序号→真实id}) 映射。

    用短序号替代 19 位 tweet id，解决 DeepSeek 无法准确回显长数字 ID 的问题（P0 根因）。
    模型在 supporting_tweet_ids 里写 ["1","3"] 而非真实 ID，事后由 _map_index_refs_to_ids 还原。
    """
    lines = []
    idx_to_id: dict[str, str] = {}
    for i, t in enumerate(top, start=1):
        idx = str(i)
        idx_to_id[idx] = str(t["id"])
        lines.append(
            f"[{idx}] @{t.get('author', '?')} "
            f"(score={t.get('score', 0):.2f}): {t.get('text', '')}"
        )
    return "\n".join(lines), idx_to_id


def _map_index_refs_to_ids(
    raw_ids: list,
    idx_to_id: dict[str, str],
    valid_ids: set[str] | None = None,
) -> list[str]:
    """将模型返回的引用映射为真实 tweet id，同时兼容两种格式：

    - 短序号（"1"、"[1]"）→ 翻译为真实 id
    - 已是真实 tweet id（在 valid_ids 中）→ 原样保留

    模型（gemini）不稳定：有时遵守短序号指令，有时直接返回真实 19 位 id。只认短序号会把
    真实 id 全部丢弃 → 每个主题兜底 top[:5] → 多主题挂相同推文（P0 实跑回归）。
    既非短序号也非真实 id（编造）→ 丢弃。
    """
    import re as _re
    valid_ids = valid_ids or set()
    result = []
    for raw in raw_ids:
        s = str(raw).strip()
        s_idx = _re.sub(r"^\[(\d+)\]$", r"\1", s)  # 去方括号：[1] → 1
        if s_idx in idx_to_id:
            result.append(idx_to_id[s_idx])        # 短序号
        elif s in valid_ids:
            result.append(s)                        # 已是真实 id
    return result


def synthesize(scored_input: dict, date: str | None = None) -> dict:
    """核心逻辑：从打分结果生成 social_trends_v1_1 payload。

    若无 API key，直接走 fallback。
    若有 API key，调 DeepSeek；API 失败后 fallback。
    """
    if not _has_api_key():
        sys.stderr.write(
            "WARNING: AI_GATEWAY_API_KEY not set — synthesis using local fallback. "
            "Set SC-5-real = PENDING until real API key is configured.\n"
        )
        return _fallback_synthesis(scored_input, date)

    tweets = scored_input.get("tweets", [])
    top = sorted(tweets, key=lambda t: t.get("score", 0), reverse=True)

    # P0 fix: 用短序号 [1][2]... 而非 19 位 ID，解决 DeepSeek 无法准确回显长整数 ID 的问题
    tweets_text, idx_to_id = _build_indexed_tweet_listing(top)

    resolved_date = date or datetime.now(UTC).strftime("%Y-%m-%d")
    prompt_path = _PROMPT_PATH

    if prompt_path.exists():
        prompt_template = prompt_path.read_text()
        prompt = prompt_template.format(
            tweets=tweets_text,
            date=resolved_date,
            tweet_count=len(tweets),
        )
    else:
        prompt = (
            f"Analyze the following AI/ML tweets from {resolved_date} "
            f"and output a JSON object conforming to social_trends_v1_1 schema.\n\n"
            f"Tweets:\n{tweets_text}\n\n"
            "Return JSON with fields: date, generated_at, source, scoring_summary, "
            "tweets, focus_discussions."
        )

    # 每次 synthesize 从干净的族可用性账本开始（闩只在本次调用内有效）
    _unavailable_families.clear()

    last_err: Exception | None = None
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            raw = _call_deepseek(prompt)
            data = json.loads(raw)
            merged = _merge_with_input(data, scored_input, resolved_date, top, idx_to_id)
            _validate_response(merged)
            _validate_quality(merged)
            return merged
        except SynthesisUnavailableError:
            # 主族 + 备族都挂 / 预览版误配 —— 再试 max_attempts 次也是同样结果，
            # 立刻上抛（宪法 P2 收敛优先：无望的重试只是白烧额度与时间）
            raise
        except Exception as e:
            last_err = e
            if attempt == max_attempts - 1:
                break
            continue

    # fail-closed（宪法 P3）：有 key 但合成尝试全失败 → **抛错，不写降级假简报**。
    # 旧行为是 `return _fallback_synthesis(...)`，那份产出的 summary 明文含
    # "This is a fallback summary generated when the synthesis model is unavailable"
    # 且 topic 恒为 "AI Technology Trends"，会照常通过 schema 校验写进 vault ——
    # 读者看到的是一份「格式完整但没有任何真实信号」的简报，且没有任何失败信号。
    # 同仓 github 链遇到同样情形是 vault 零写入 + exit 1；现在两条链语义一致。
    err_msg = f"{type(last_err).__name__}: {str(last_err)[:200]}" if last_err else "unknown"
    raise SynthesisUnavailableError(
        f"synthesis fail-closed: 有 API key 但 {max_attempts} 次合成尝试全失败"
        f"（末次 {err_msg}）→ 不产出简报。"
        f"旧行为会在此处写入降级假简报（summary 明文标注 fallback、topic 恒为 "
        f"'AI Technology Trends'），与 github 链的 fail-closed 语义相反且未登记豁免。"
    )


def _merge_with_input(
    model_out: dict,
    scored_input: dict,
    resolved_date: str,
    top: list[dict],
    idx_to_id: dict[str, str] | None = None,
) -> dict:
    """Merge model output with input data — model 只贡献 focus_discussions/executive_summary,
    其余字段从 input 回填。

    防御性合并:DeepSeek 经常返回不合规字段(source 是字符串、tweets 缺 text/created_at 等),
    与其反复修 prompt,不如机械合并:input 数据是 ground truth,model 智慧只取分析字段。

    idx_to_id: 短序号→真实 ID 映射（P0 fix）。若提供，在 _patch_focus_discussions 前先将
    模型返回的序号还原为真实 ID，解决 DeepSeek 无法准确回显 19 位 ID 的根因。
    """
    all_tweets = scored_input.get("tweets", [])
    id_to_tweet = {str(t["id"]): t for t in top}

    # P0 fix: 把模型返回的引用还原为真实 id（短序号翻译 + 真实 id 保留，兼容模型不稳定）
    raw_fds = model_out.get("focus_discussions") or []
    if idx_to_id:
        valid_ids = {str(t["id"]) for t in top}
        mapped_fds = []
        for fd in raw_fds:
            if not isinstance(fd, dict):
                continue
            raw_ids = fd.get("supporting_tweet_ids") or []
            mapped = _map_index_refs_to_ids(raw_ids, idx_to_id, valid_ids)
            mapped_fds.append({**fd, "supporting_tweet_ids": mapped})
        raw_fds = mapped_fds

    # 先 patch focus_discussions，确定哪些 id 被引用
    patched_fds = _patch_focus_discussions(raw_fds, top)
    referenced_ids = {
        str(sid)
        for fd in patched_fds
        for sid in fd.get("supporting_tweet_ids", [])
    }

    # tweets 输出集 = top 10 ∪ 被引用的推文（确保 supporting_tweet_ids 总有对应 tweet 对象）
    top10_ids = {str(t["id"]) for t in top[:10]}
    extra_ids = referenced_ids - top10_ids
    top_for_display = list(top[:10]) + [
        id_to_tweet[eid] for eid in extra_ids if eid in id_to_tweet
    ]

    return {
        "date": model_out.get("date") or resolved_date,
        "generated_at": model_out.get("generated_at") or datetime.now(UTC).isoformat(),
        "source": {"name": "deepseek_synthesis", "collector_version": "1.0.0"},
        "scoring_summary": {
            "total_tweets": len(all_tweets),
            "scored_tweets": len(top_for_display),
        },
        "tweets": [
            {
                "id": str(t["id"]),
                "author": t.get("author", ""),
                "text": t.get("text", ""),
                "created_at": t.get("created_at", ""),
                "score": t.get("score"),
                "dimensions": t.get("dimensions", {}),
                "url": t.get("url", ""),
                "lang": t.get("lang", ""),  # P2-1: 透传 lang，供 translate per-tweet 分流
            }
            for t in top_for_display
        ],
        "focus_discussions": _ensure_top3_covered(patched_fds, top),
        # executive_summary 原样保留；缺失时用主题列表兜底（确保 audit 不因空值 fail）
        "executive_summary": (
            model_out.get("executive_summary", "").strip()
            or _fallback_executive_summary(patched_fds, resolved_date)
        ),
    }


def _ensure_top3_covered(fds: list, top: list[dict]) -> list:
    """确保 top-3 高分推文至少各出现在某个主题的 supporting_tweet_ids 里。

    若不满足，将未覆盖的 top-3 id 补入第一个主题（不引入新主题）。
    """
    import copy
    fds = copy.deepcopy(fds)
    if not fds:
        return fds
    covered: set[str] = {
        str(sid)
        for fd in fds
        for sid in fd.get("supporting_tweet_ids", [])
    }
    top3_ids = [str(t["id"]) for t in top[:3]]
    missing = [tid for tid in top3_ids if tid not in covered]
    if missing:
        # 补入第一个主题
        existing = list(fds[0].get("supporting_tweet_ids", []))
        for tid in missing:
            if tid not in existing:
                existing.append(tid)
        fds[0]["supporting_tweet_ids"] = existing
    return fds


def _fallback_executive_summary(fds: list, date: str) -> str:
    """生成确定性执行摘要兜底（模型漏 executive_summary 时使用）。"""
    topics = [fd.get("topic", "") for fd in fds[:3] if fd.get("topic")]
    if topics:
        return (
            f"{date} AI/ML 领域核心讨论主题：{' · '.join(topics)}。"
            "本期趋势信号聚焦于上述方向，以下为详细分析。"
        )
    return f"{date} AI/ML 领域社交趋势简报，基于公开时间线抓取与综合分析。"




def _patch_focus_discussions(discussions: list, top: list[dict]) -> list:
    """补齐 focus_discussions 缺失或编造的 supporting_tweet_ids。

    - 过滤掉模型编造的 id（不在 top 推文中）
    - 缺失或全部编造时，fallback 到 top[:5]（保守覆盖高分推文）
    - topic/summary 原样保留，由 translate 阶段中文化
    """
    valid_ids = {str(t["id"]) for t in top}
    top_ids = [str(t["id"]) for t in top[:5]] or ["unknown"]

    patched = []
    for d in discussions:
        if not isinstance(d, dict):
            continue
        raw_ids = d.get("supporting_tweet_ids")
        if isinstance(raw_ids, list) and raw_ids:
            # 过滤编造 id，保留真实存在的
            real_ids = [str(x) for x in raw_ids if str(x) in valid_ids]
        else:
            real_ids = []
        if not real_ids:
            real_ids = list(top_ids)
        elif len(real_ids) < 2:
            # 补足到 2 个：从 top 高分推文中取未包含的
            for tid in top_ids:
                if tid not in real_ids:
                    real_ids.append(tid)
                if len(real_ids) >= 2:
                    break
        patched.append({
            "topic": (d.get("topic") or "").strip(),
            "supporting_tweet_ids": real_ids,
            "summary": d.get("summary") or "",
        })
    return patched


def _cli(argv: list[str] | None = None) -> int:
    """CLI 入口：--input <scored.json> --output <synth.json>"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Synthesize scored tweets into social_trends_v1_1 payload."
    )
    parser.add_argument("--input", required=True, help="Path to scored JSON input")
    parser.add_argument("--output", required=True, help="Path to write synthesized JSON output")
    parser.add_argument("--date", default=None, help="Override date (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    with open(args.input) as f:
        scored = json.load(f)

    result = synthesize(scored, date=args.date)

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"OK: synthesis written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
