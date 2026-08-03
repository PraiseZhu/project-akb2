"""DeepSeek V4 Pro batch synthesis — 10 repos → summary + use_case via single LLM call."""

import json
import os
import sys
import time
import xml.sax.saxutils as saxutils
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import jsonschema

from akb2.common.http_retry import (
    ProviderErrorDiagnosis,
    backoff_delay,
    classify_provider_error,
    parse_retry_after,
)
from akb2.quality import llm_router

from .url_normalizer import normalize

PROMPT_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "docs"
PROMPT_FILE = "github-deepseek-prompt-v1.md"
ITEM_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "github_synthesis_item_v1.json"

# 全局走 AI Gateway（llm-proxy.tapsvc.com，OpenAI 兼容）。本机运行时设 DEEPSEEK_BASE_URL，
# 部署侧约定 AI_GATEWAY_BASE_URL；两者都指向同一 Gateway。绝不直连 api.deepseek.com。
_GATEWAY_BASE = (
    os.environ.get("AI_GATEWAY_BASE_URL")
    or os.environ.get("DEEPSEEK_BASE_URL")
    or "https://llm-proxy.tapsvc.com"
).rstrip("/")
DEEPSEEK_API_URL = f"{_GATEWAY_BASE}/v1/chat/completions"
MAX_INPUT_TOKENS = 12000

# ── 重试预算：两个常量必须从一个来源推导（宪法 P4「预算显式」）────────────────────
# 旧值 RETRY_BACKOFF=[5,15,45] + MAX_RETRIES=3 不自洽：3 次尝试只在 attempt 0/1 之后
# sleep，实际等待 [5,15]=20s，索引 2 的 45 **永不执行**。2026-08-02 日志里
# `elapsed=20.5s` 精确吻合，而读代码的人会以为最多等 65s —— 声明的预算和真实预算差
# 3 倍多，排查时按 65s 反推必然误判。现在 MAX_RETRIES 由退避表长度推导：改表即改次数,
# 不可能再漂。N 次尝试 = N-1 次 sleep，退避表恰好用满。
RETRY_BACKOFF = [5.0, 15.0, 45.0]
MAX_RETRIES = len(RETRY_BACKOFF) + 1
# 单次等待封顶取退避表的最大值（派生，不另写一个可能对不上的数字）
MAX_BACKOFF_S = max(RETRY_BACKOFF)
REQUEST_TIMEOUT_S = 120


def _gateway_model_id(family: str) -> str:
    """取该模型族在 Gateway 上的模型 id，顺带过 llm_router 的族校验。

    走 llm_router 而不是各写一份字面量：模型 id 只有一个权威来源，且
    `_validate_model_family` 会拒 `-preview` 与不支持的族（宪法 P7）。
    本模块的端点恒为 Gateway（见 _GATEWAY_BASE），故直接取 gateway 形态的 id，
    不用 llm_router 的 env 嗅探结果 —— 否则同一进程里模型 id 会随 env 漂移。
    """
    llm_router._validate_model_family(family)
    cfg = llm_router._MODEL_CONFIG[family]
    return cfg.get("gateway_model_id_default", cfg["model_id_default"])


# ── 模型族降级（宪法 P4「失败可见」+ P7 兜底族）─────────────────────────────────
# 修复前模型硬编码在请求体里，briefing 侧全链零降级余量：单点模型故障 = 当日简报
# 100% 缺失。而预算耗尽时退避完全无效（额度不会因为等待回来），**换族是唯一有效补救**。
# 主族 = deepseek（生产/提炼族，值与修复前的硬编码字面量一致，行为不变）；
# 备族 = gemini-flash（宪法 P7 点名的兜底族，正式版非 -preview）。
PRIMARY_SYNTHESIS_MODEL = _gateway_model_id("deepseek-v4-pro")
FALLBACK_SYNTHESIS_MODEL = _gateway_model_id("gemini-flash")

# COMMON.md 契约 1：Lane 3 的 backstop 第 2 轮补跑设置它，本模块是消费端 ——
# 读到 =1 就**直接从备族开始**（主族刚在第 1 轮打穿预算，再试一次纯属白烧）。
FAMILY_FALLBACK_ENV = "AKB2_LLM_FAMILY_FALLBACK"

# 本次 synthesize() 运行中已确认不可用的模型族。为什么需要记住：synthesize 的
# JSON 重试分支会**再调用一次**合成，若不记账，刚被判定「预算耗尽」的主族会被原样
# 再打一遍 —— 那正是本 lane 要消掉的「明知耗尽还硬试」，只是换了个位置复发。
# 生命周期 = 单次 synthesize()（入口清空），不跨调用累积；跨**进程**协调不在此处。
# 有这个闩后，单次 synthesize 的最坏请求数有界：
#   主族 MAX_RETRIES 次（仅第一次）+ 备族 MAX_RETRIES 次 × 2 个调用点。
_unavailable_families: set[str] = set()

# ── 输出预算（宪法 P4「预算显式」+ P1「有界自动化」）─────────────────────────────
# 2026-07-21/24/27 三次生产失败（logs/github-briefing-stdout.log:421/457/493）的真因
# 是这个预算不够：旧值 4000 是按「10 × 400 tokens 平均」定的，用平均值当上限，既没有
# worst-case 余量，也没给 deepseek-v4-pro 默认产出的 reasoning_content 留位置。撞顶后
# 半截 JSON 直送 json.loads，被报成 "Unterminated string ..."——预算问题伪装成语法问题。
#
# 重新推导（原则：schema 会**接受**的最长产出都不许被截断，不按平均值定上限）
#   单条字符上限 1000 = summary maxLength 500 + use_case maxLength 300
#                     + repo_url ~120 + JSON 键/引号/逗号/缩进 ~80
#     500/300 取自 schemas/github_briefing_v1.json —— 那是 vault 写入闸的硬上限，
#     闸门放行的长度就必须生成得出来，否则又是「产出合法却被截断」。
#   条数上限 10 —— pipeline `scored[:10]` + schema top_repos maxItems=10 双重硬卡；
#     与候选池无关（candidate_pool_size=61 是博主数，不是条目数），不会随池子增长。
#   信封 ~200 字符 = 数组括号 + 模型偶发的 ```json 围栏（围栏被 _extract_json 剥掉，
#     但生成时照样烧 output token）。
#   token/字符 1.1 —— 三次截断给的实测锚点是「每交付 1 字符烧 1.08~1.61 token」
#     （4000÷3707、4000÷3453、4000÷2490）；比值里偏高的部分是 reasoning 的**固定**
#     开销而非按字符线性增长，故把固定开销单列，字符换算取 1.1。
#   reasoning 余量 2600 —— repair.py:76 已实测 deepseek-v4-pro 即使 reasoning_effort=None
#     也照样产 reasoning_content，且它与 content 共用 max_tokens；2600 ≈ 上述锚点中
#     反推的最大固定开销（4000 − 2490×0.6）。
# → (10 × 1000 + 200) × 1.1 + 2600 = 13,820 → 取 14000（编译期常量，仍然有界）
#
# 注意：这是**上限**不是花费，实际只按生成量计费；真实产出约 390 字符/条（≈4000 tokens
# 总量，见 backstop 成功记录），新上限只在极端长产出时才起作用。
MAX_REPOS_PER_CALL = 10
OUTPUT_CHARS_PER_REPO = 1000
OUTPUT_CHARS_ENVELOPE = 200
OUTPUT_TOKENS_PER_CHAR = 1.1
REASONING_TOKENS_HEADROOM = 2600
MAX_OUTPUT_TOKENS = 14000


class SynthesisTruncatedError(RuntimeError):
    """LLM 输出撞 max_tokens 上限被截断 —— 预算问题，不是 JSON 格式问题。

    单列一个类型而不是复用 JSONDecodeError/ValueError，是为了让「预算撞顶」这条真因
    在日志里自证，并且**绕开** synthesize() 里的 JSON 重试分支：同一请求配同一上限
    重跑必然截在同一处，重试只是白烧一次调用（生产实测每次失败因此翻倍到 103-156s）。
    """


class SynthesisAPIError(RuntimeError):
    """LLM **调用层**失败（HTTP / 网络），携带契约 2 诊断字段。

    与 SynthesisTruncatedError 分开：截断是「拿到了响应但产出被预算截短」，属产出层，
    换模型族救不了、也不该重试；本类是「响应没拿到」，是模型族级故障 ——
    上层据此决定是否降级到备用族（`family_fallback_worthwhile`）。
    """

    def __init__(
        self,
        message: str,
        *,
        diagnosis: ProviderErrorDiagnosis | None = None,
        model: str = "",
        exhausted: bool = False,
        total_wait_s: float = 0.0,
    ):
        super().__init__(message)
        self.diagnosis = diagnosis
        self.model = model
        self.exhausted = exhausted
        self.total_wait_s = total_wait_s

    @property
    def error_type(self) -> str:
        return self.diagnosis.error_type if self.diagnosis else "other"

    @property
    def family_fallback_worthwhile(self) -> bool:
        """换模型族还有意义吗。

        预算耗尽（共享账户额度打穿）与重试耗尽（该族持续限流/故障）都属「这一族现在
        不可用」，换族有效。非可重试硬错（401/403/400）是**配置/鉴权**问题，
        换族只会在另一族上原样再错一次 —— 不换，立即抛。
        """
        if self.exhausted:
            return True
        return bool(self.diagnosis and self.diagnosis.is_budget_exceeded)


def _sleep(seconds: float) -> None:
    """可被测试 monkeypatch 拦截的 sleep（沿用 send.py 的既有形态）。"""
    if seconds > 0:
        time.sleep(seconds)


def _load_prompt_template() -> str:
    """Load the v1 prompt template from docs/."""
    path = PROMPT_DIR / PROMPT_FILE
    if path.exists():
        content = path.read_text()
        # Extract the template from the ```text block
        marker = "```text"
        start = content.find(marker)
        if start >= 0:
            start = content.find("\n", start) + 1
            end = content.find("```", start)
            if end >= 0:
                return content[start:end].strip()
    # Minimal inline fallback
    return _FALLBACK_SYSTEM_PROMPT


_FALLBACK_SYSTEM_PROMPT = """<system>
你是 GitHub 项目情报编辑。任务：为以下 N 个 GitHub 仓库各产出两段中文文本：
  ① summary（50-500 字）— 客观陈述项目做什么、技术栈、解决的核心问题
  ② use_case（30-300 字）— 什么情况下该装，什么情况下别装；必须含 1-2 条正向场景 + 1 条反向场景

规则：
- 客观、紧凑、不堆形容词、不重复 README 原话
- summary 回答"是什么"，use_case 回答"什么时候用，什么时候别用"
- 输出 JSON 数组，每项 {"repo_url": "...", "summary": "...", "use_case": "..."}
- 严格按输入顺序输出，repo_url 必须完全匹配输入
</system>"""


def _build_prompt(repos: list[dict[str, Any]]) -> str:
    system = _load_prompt_template()
    repos_xml = "\n".join(_render_repo_xml(r) for r in repos)
    return (
        f"{system}\n\n"
        f"<input>\n{repos_xml}\n</input>\n\n"
        '<output_format>\n[{"repo_url": "...", "summary": "...", '
        '"use_case": "..."}, ...]\n</output_format>'
    )


def _grounding_text(repo: dict[str, Any]) -> str:
    """该 repo 可依据的原文（description + readme）。空串 = 无 grounding。"""
    return "\n".join(
        part for part in (
            (repo.get("description") or "").strip(),
            (repo.get("readme_excerpt") or "").strip(),
        ) if part
    )


def _render_repo_xml(repo: dict[str, Any]) -> str:
    url = saxutils.escape(repo.get("repo_url", ""))
    stars = repo.get("stars", 0)
    language = saxutils.escape(repo.get("language") or "unknown")
    topics = saxutils.escape(", ".join(repo.get("topic_tags", [])))
    mentioned_by = saxutils.escape(", ".join(repo.get("mentioners", [])))
    # description 是 GitHub 侧最短最准的 grounding，必须进 prompt（缺陷 #7）
    description = saxutils.escape((repo.get("description") or "").strip())
    readme = saxutils.escape((repo.get("readme_excerpt") or "")[:500])
    return (
        f'<repo url="{url}">\n'
        f"  stars: {stars}\n"
        f"  language: {language}\n"
        f"  topics: [{topics}]\n"
        f"  mentioned_by: {mentioned_by}\n"
        f"  description: {description}\n"
        f"  readme_excerpt: |\n    {readme}\n"
        f"</repo>"
    )


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~1.3 chars per token for Chinese/English mix."""
    return max(1, len(text) // 2)


def _mock_response(prompt: str) -> dict:
    """显式 mock 产出（只能由 mock=True 触发）。文本自带 MOCK 标记，肉眼可辨。"""
    import re
    input_start = prompt.rfind("<input>")
    search_text = prompt[input_start:] if input_start >= 0 else prompt
    urls = re.findall(r'url="([^"]+)"', search_text)
    mock_data = [
        {"repo_url": url,
         "summary": (
             "[MOCK] 这是链路自检用的占位摘要，非真实 LLM 产出，"
             "不代表该项目的实际功能与技术栈。"
         ),
         "use_case": (
             "[MOCK] 正向场景：仅用于本地链路自检。"
             "反向场景：任何真实阅读场景都不该看到这段文字。"
         )}
        for url in urls
    ]
    return {"choices": [{"message": {"content": json.dumps(mock_data, ensure_ascii=False)}}]}


def _call_deepseek(prompt: str, api_key: str | None = None,
                   *, allow_mock: bool = False,
                   model: str | None = None) -> dict | None:
    """打一次合成请求（含同族退避重试）。失败抛 SynthesisAPIError，不返回 None。

    Args:
        model: 本次使用的模型 id；None → PRIMARY_SYNTHESIS_MODEL。
            换族由 `_call_with_family_fallback` 负责，本函数只忠实打一个族。
    """
    # AI Gateway key：部署侧约定 AI_GATEWAY_API_KEY，
    # 本机运行时设 DEEPSEEK_API_KEY（同一 Gateway key）。
    key = api_key or os.environ.get("AI_GATEWAY_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        # fail-closed（宪法 P3）：旧实现无 key 时返回 mock，而 mock 文本
        # "mock summary text "*5 = 90 字符恰好过 schema minLength:50 → 假简报可入
        # vault。mock 现在必须由调用方显式 opt-in（pipeline --mock），
        # 生产链路缺 key 直接硬失败。
        if allow_mock:
            return _mock_response(prompt)
        raise RuntimeError(
            "synthesis fail-closed: no AI_GATEWAY_API_KEY/DEEPSEEK_API_KEY set. "
            "缺 API key 时不产出任何内容（旧行为会静默产出 mock 假简报）。"
        )

    model_id = model or PRIMARY_SYNTHESIS_MODEL
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    body = json.dumps({
        "model": model_id,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": MAX_OUTPUT_TOKENS,
    }).encode()

    # 实收等待累计 —— 耗尽时打印它而不是打印退避表（自证真实预算，宪法 P4）
    total_wait = 0.0
    for attempt in range(MAX_RETRIES):
        try:
            req = Request(DEEPSEEK_API_URL, data=body, headers=headers)
            with urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            # HTTPError 本身就是 response：有 .code/.headers/.read()。旧实现只取
            # str(e)（= "HTTP Error 429: Too Many Requests"）就把状态码、Retry-After
            # 和错误体全丢了 —— 于是 401 和 429 走同一条路，预算耗尽也照样烧满重试。
            diagnosis = classify_provider_error(e.code, _read_error_body(e))
            if diagnosis.is_budget_exceeded:
                # 额度不会因为等待回来：退避 0 次，5s 内报出真因（L1-2）
                raise SynthesisAPIError(
                    f"synthesis fail-fast: 预算耗尽，重试无用 → 不退避不重试"
                    f"（{diagnosis.summary()} model={model_id}）。"
                    f"补救只有换模型族或等额度重置；本次已跳过全部 {MAX_RETRIES - 1} 次退避。",
                    diagnosis=diagnosis, model=model_id,
                ) from e
            if not diagnosis.retryable:
                # 401/403/400 这类配置/鉴权错：立即抛，**不消耗剩余尝试**
                raise SynthesisAPIError(
                    f"synthesis 硬失败（不可重试状态码 {e.code}）: {e} "
                    f"[{diagnosis.summary()} model={model_id}] — "
                    f"剩余 {MAX_RETRIES - attempt - 1} 次尝试未消耗。",
                    diagnosis=diagnosis, model=model_id,
                ) from e
            if attempt == MAX_RETRIES - 1:
                raise _exhausted_error(e, diagnosis, model_id, total_wait) from e
            total_wait += _wait_before_retry(attempt, parse_retry_after(e))
        except Exception as e:
            # 网络瞬断 / 超时 / 网关吐非 JSON —— 无状态码可分类，按瞬时故障退避
            diagnosis = classify_provider_error(None, str(e))
            if attempt == MAX_RETRIES - 1:
                raise _exhausted_error(e, diagnosis, model_id, total_wait) from e
            total_wait += _wait_before_retry(attempt, None)

    return None  # pragma: no cover — 循环内每条路径都 return 或 raise


def _read_error_body(err: HTTPError) -> str:
    """读 HTTPError 的响应体（只读一次，读失败按空处理）。"""
    try:
        raw = err.read()
    except Exception:  # noqa: BLE001 — fp 可能已关闭/不存在
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def _wait_before_retry(attempt: int, retry_after: float | None) -> float:
    """退避一次并返回实际等待秒数（尊重 Retry-After、指数上限表、full jitter）。"""
    delay = backoff_delay(
        attempt, retry_after, schedule=RETRY_BACKOFF, cap=MAX_BACKOFF_S,
    )
    _sleep(delay)
    return delay


def _exhausted_error(
    err: Exception, diagnosis: ProviderErrorDiagnosis, model_id: str, total_wait: float,
) -> SynthesisAPIError:
    """重试耗尽错误 —— 文本里打**实收总等待**，不是退避表的理论值。"""
    return SynthesisAPIError(
        f"synthesis API failed after {MAX_RETRIES} attempts "
        f"(total_wait={total_wait:.1f}s, model={model_id}): {err} "
        f"[{diagnosis.summary()}]",
        diagnosis=diagnosis, model=model_id, exhausted=True, total_wait_s=total_wait,
    )


def _call_with_family_fallback(
    prompt: str, api_key: str | None = None, *, allow_mock: bool = False,
) -> tuple[dict | None, str]:
    """主族失败后降级到备用族再试一次。返回 (响应, 实际使用的模型)。

    只在「这一族现在不可用」时换族（预算耗尽 / 重试耗尽），配置类硬错不换
    —— 见 SynthesisAPIError.family_fallback_worthwhile。
    降级**必须可见**（宪法 P4）：stdout 打一行 SYNTHESIS_FALLBACK，禁止静默降级。
    """
    # 契约 1：backstop 第 2 轮已知主族打穿预算 → 直接从备族开始，主族零请求
    if os.environ.get(FAMILY_FALLBACK_ENV) == "1":
        print(
            f"SYNTHESIS_FALLBACK model={FALLBACK_SYNTHESIS_MODEL} "
            f"reason=env_{FAMILY_FALLBACK_ENV}=1",
            flush=True,
        )
        return (
            _call_deepseek(prompt, api_key, allow_mock=allow_mock,
                           model=FALLBACK_SYNTHESIS_MODEL),
            FALLBACK_SYNTHESIS_MODEL,
        )

    # 本次运行已判定主族不可用（JSON 重试等二次调用会走到这里）→ 不再白烧一次
    if PRIMARY_SYNTHESIS_MODEL in _unavailable_families:
        print(
            f"SYNTHESIS_FALLBACK model={FALLBACK_SYNTHESIS_MODEL} "
            f"reason=primary_already_known_unavailable primary={PRIMARY_SYNTHESIS_MODEL}",
            flush=True,
        )
        return (
            _call_deepseek(prompt, api_key, allow_mock=allow_mock,
                           model=FALLBACK_SYNTHESIS_MODEL),
            FALLBACK_SYNTHESIS_MODEL,
        )

    try:
        response = _call_deepseek(prompt, api_key, allow_mock=allow_mock,
                                  model=PRIMARY_SYNTHESIS_MODEL)
        return response, PRIMARY_SYNTHESIS_MODEL
    except SynthesisAPIError as primary_err:
        if not primary_err.family_fallback_worthwhile:
            raise
        # 记账后再降级：本次运行剩下的调用点不许再碰这一族
        _unavailable_families.add(PRIMARY_SYNTHESIS_MODEL)
        print(
            f"SYNTHESIS_FALLBACK model={FALLBACK_SYNTHESIS_MODEL} "
            f"reason={primary_err.error_type}"
            f"{'_exhausted' if primary_err.exhausted else ''} "
            f"primary={primary_err.model}",
            flush=True,
        )
        sys.stderr.write(
            f"WARNING: 主模型族 {primary_err.model} 不可用（{primary_err}）→ "
            f"降级到 {FALLBACK_SYNTHESIS_MODEL}\n"
        )
        try:
            response = _call_deepseek(prompt, api_key, allow_mock=allow_mock,
                                      model=FALLBACK_SYNTHESIS_MODEL)
        except SynthesisAPIError as fallback_err:
            # 两族都挂：fail-closed（宪法 P3），且两族真因都要可见（P4）
            raise SynthesisAPIError(
                f"两个模型族均失败 → 不产出简报。"
                f"主族 {primary_err.model}: {primary_err} || "
                f"备族 {FALLBACK_SYNTHESIS_MODEL}: {fallback_err}",
                diagnosis=fallback_err.diagnosis, model=FALLBACK_SYNTHESIS_MODEL,
                exhausted=fallback_err.exhausted,
            ) from fallback_err
        return response, FALLBACK_SYNTHESIS_MODEL


def _content_or_raise(response: dict, *, repo_count: int) -> str:
    """取出 message.content；`finish_reason == "length"` 即预算撞顶，按真因抛错。

    OpenAI 兼容格式（AI Gateway / DeepSeek 都是）把停止原因放在
    `choices[0].finish_reason`，`"length"` 就是「被 max_tokens 截断」的官方信号。
    旧实现从不读它（本次修复前全仓 grep finish_reason 零命中），把半截 JSON 直送 json.loads，
    于是「预算不够」在日志里显示成 `Unterminated string starting at: line 50 ...`，
    归因完全错位 —— 违反宪法 P4「预算显式 / 失败可见」。

    `finish_reason` 缺失（None）时不判截断：部分网关不回该字段，不能因为字段缺失
    就把正常产出判成失败。
    """
    choice = response["choices"][0]
    content = choice["message"]["content"]
    if choice.get("finish_reason") != "length":
        return content

    usage = response.get("usage") or {}
    completion_tokens = usage.get("completion_tokens")
    consumed = f"，实际消耗 {completion_tokens} completion tokens" if completion_tokens else ""
    raise SynthesisTruncatedError(
        f"LLM 输出撞 max_tokens 上限被截断（finish_reason=length，"
        f"上限 MAX_OUTPUT_TOKENS={MAX_OUTPUT_TOKENS}{consumed}）——"
        f"这不是 JSON 格式问题，是输出预算不够；本次 {repo_count} 个 repo，"
        f"只拿到 {len(content or '')} 字符的半截产出。"
        f"处置：加大 MAX_OUTPUT_TOKENS 或减少单次 repo 数；不重试同一请求"
        f"（同上限重跑必然截在同一处）。"
    )


def _load_item_schema() -> dict:
    with open(ITEM_SCHEMA_PATH) as f:
        return json.load(f)


def _validate_items(summaries: Any) -> None:
    """逐项 jsonschema 校验（兑现 prompt 文档「少字段或多字段直接 abort」承诺）。"""
    if not isinstance(summaries, list):
        raise ValueError(f"LLM output is not a JSON array: {type(summaries).__name__}")
    validator = jsonschema.Draft7Validator(_load_item_schema())
    for i, item in enumerate(summaries):
        errors = [e.message for e in validator.iter_errors(item)]
        if errors:
            raise ValueError(f"LLM summary item #{i} failed schema: {errors}")


def _match_key(url: str) -> str:
    """对齐用的比较键 — 两侧同样归一化，把「模型顺手规范化了 URL」与「编造 repo」分开。

    严格全等匹配下，DeepSeek 只要对 URL 做任何无害规范化（补/去尾斜杠、改大小写、
    带 `.git`）就会 missing+extra 同时非空 → 整份简报 abort。归一后仍对不上的才是
    真编造，abort 语义不被削弱。
    """
    if not url:
        return ""
    return normalize(url) or url.strip().rstrip("/").lower()


def _align_by_repo_url(repos: list[dict[str, Any]],
                       summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 repo_url 对齐 summary；任何不匹配即 abort（比较前两侧同样归一化）。

    旧实现按位置 zip 且 repo_url 不匹配时 `pass`：模型重排数组即整份错位
    （A 仓库配 B 仓库的简介），而数量/schema/URL 全对，无从发现。
    docs/github-deepseek-prompt-v1.md:70 明文承诺 repo_url 不匹配 → abort。
    """
    by_url: dict[str, dict[str, Any]] = {}
    for item in summaries:
        key = _match_key(item["repo_url"])
        if key in by_url:
            raise ValueError(f"LLM 返回重复 repo_url，拒绝对齐: {item['repo_url']}")
        by_url[key] = item

    expected = [_match_key(r.get("repo_url", "")) for r in repos]
    missing = [u for u in expected if u not in by_url]
    extra = [u for u in by_url if u not in set(expected)]
    if missing or extra:
        raise ValueError(
            f"LLM 返回的 repo_url 与输入不匹配 → abort（防 LLM 编造 repo）；"
            f"missing={missing} unexpected={extra}"
        )

    return [
        {**repo,
         "summary": by_url[_match_key(repo.get("repo_url", ""))]["summary"],
         "use_case": by_url[_match_key(repo.get("repo_url", ""))]["use_case"]}
        for repo in repos
    ]


def synthesize(repos: list[dict[str, Any]], api_key: str | None = None,
               *, mock: bool = False) -> dict[str, Any]:
    """Run batch synthesis for 10 repos. Returns {top_repos: [...], input_tokens: int}.

    Args:
        repos: 已 enrich 的 repo 列表，每项必须有非空 grounding
            （`description` 或 `readme_excerpt`）——空 grounding 直接 abort。
        api_key: gateway key；None 时回落 env，仍无 key 且 mock=False → RuntimeError。
        mock: 显式 opt-in 占位产出（pipeline --mock 自检用）。生产链路禁止。
    """
    ungrounded = [r.get("repo_url", "?") for r in repos if not _grounding_text(r)]
    if ungrounded:
        raise ValueError(
            f"empty grounding → abort（无原文可依据时 LLM 只能凭 repo 名编造）: {ungrounded}"
        )

    prompt = _build_prompt(repos)
    input_tokens = _estimate_tokens(prompt)

    if input_tokens > MAX_INPUT_TOKENS:
        raise ValueError(f"Prompt too large: {input_tokens} tokens > {MAX_INPUT_TOKENS} max")

    # 输出预算是按 MAX_REPOS_PER_CALL 条推导的（见常量区）。条数一旦超过推导前提，
    # 撞顶就从「可能」变成「必然」——在这里响亮地拒掉，而不是等模型截断后在 vault 侧
    # 表现为整份简报消失。pipeline 侧 `scored[:10]` 让这条在生产不可能触发，
    # 它防的是以后有人把 top-N 调大却忘了同步预算。
    if len(repos) > MAX_REPOS_PER_CALL:
        raise ValueError(
            f"单次 synthesize 最多 {MAX_REPOS_PER_CALL} 个 repo，收到 {len(repos)} 个 → 拒绝调用："
            f"输出预算 MAX_OUTPUT_TOKENS={MAX_OUTPUT_TOKENS} 是按 {MAX_REPOS_PER_CALL} 条推导的，"
            f"超出即必然撞顶被截断。要提高条数必须同步重算输出预算。"
        )

    # 每次 synthesize 从干净的族可用性账本开始（闩只在本次调用内有效）
    _unavailable_families.clear()

    response, model_used = _call_with_family_fallback(prompt, api_key, allow_mock=mock)
    if response is None:
        raise RuntimeError("DeepSeek API returned no response")

    # 先判截断再解析：撞顶的半截 JSON 必须报成预算问题，且不进下面的 JSON 重试分支。
    content = _content_or_raise(response, repo_count=len(repos))
    try:
        summaries = json.loads(_extract_json(content))
    except json.JSONDecodeError as e:
        # Retry once with error hint
        retry_prompt = prompt + "\n\n前次输出不是合法 JSON，重新输出严格 JSON 数组。"
        response, model_used = _call_with_family_fallback(
            retry_prompt, api_key, allow_mock=mock,
        )
        if response is None:
            raise RuntimeError("DeepSeek retry also failed") from e
        content = _content_or_raise(response, repo_count=len(repos))
        summaries = json.loads(_extract_json(content))

    _validate_items(summaries)

    if len(summaries) != len(repos):
        raise ValueError(f"Summary count mismatch: got {len(summaries)}, expected {len(repos)}")

    return {
        "top_repos": _align_by_repo_url(repos, summaries),
        "input_tokens": input_tokens,
        # 实际产出这份简报的模型 —— 降级后必须能从产出侧看出来（宪法 P4 禁静默降级）。
        # 写入 vault frontmatter 需要 pipeline.py + github_briefing_v1.json
        # （additionalProperties:false）配套改动，两者都不在本轮改动范围，故先在
        # synthesize 返回值上落字段，接线留给有权改那两个文件的一轮。
        "synthesis_model": model_used,
    }


def _extract_json(text: str) -> str:
    """Extract JSON array from text, handling markdown code fences."""
    text = text.strip()
    # Remove markdown code fences
    if text.startswith("```"):
        text = text[text.index("\n") + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # Remove optional "json" language tag
    if text.startswith("json\n"):
        text = text[5:]
    return text
