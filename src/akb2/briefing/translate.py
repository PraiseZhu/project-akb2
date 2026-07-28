"""translate.py — 独立翻译阶段：将 synthesis payload 的分析字段与展示推文全部译为简体中文。

职责：
  - 翻译 executive_summary / focus_discussions[topic, summary]
  - 翻译展示推文集合（被任何主题 supporting_tweet_ids 引用 ∪ 孤儿高分 top-K）
  - 长推（>500字）：先从英文原文抽核心段（子串校验防编造），再译中文；原文存 text_original
  - 每条展示推文生成中文点题 zh_point
  - 任何字段/调用失败逐项回退（不抛异常，不崩流程），但失败状态必须回传（见下）

设计（2026-07-26 起）：**条目级隔离**。
  - 分析字段（executive_summary + focus_discussions）跨条目综述，天然需要全局视野 → 1 次调用
  - 每条展示推文单独一次调用，并做 idx 绑定校验（模型返回的 idx 与请求不符即拒收）
    → 切断"整批塞进同一次调用"的串味通道（实证：@AnthropicAI 的译文里被塞进同批次
      @paulfchristiano 推文的「消除对齐税」）

失败可见（constitution 原则 4）：返回的 dict 上会挂以下标记，供 pipeline 落盘/降级：
  translate_failed: bool         整体翻译失败（模型链全灭，正文仍是英文）
  translate_degraded: bool       部分失败（某些条目/补译未成功）
  translate_failed_fields: list  具体失败字段（如 ["tweets[3]", "core_excerpt[5]"]）
  translate_budget_exhausted: str  阶段总预算耗尽原因（call_budget | time_budget），
                                 未耗尽时该键不存在；见 _TranslateBudget
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

# 翻译阶段模型链。
# Constitution 原则 7：7×24 管线禁用 -preview 模型。故默认主力取稳定 deepseek
# （固定开销 ~42-47s，三家质量持平）；备用按不同厂商排 glm→claude，规避单厂商故障。
# 均可由环境变量覆盖；pipeline 入口会校验含 preview 即 fail fast。
_TRANSLATE_MODEL = os.environ.get("AKB2_TRANSLATE_MODEL", "deepseek/deepseek-v4-pro")
_TRANSLATE_FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "AKB2_TRANSLATE_FALLBACK_MODELS",
        "z-ai/glm-5.1,anthropic/claude-sonnet-4-6",
    ).split(",")
    if m.strip()
]
# 跨网关 fallback：当主力网关（llm-proxy.tapsvc.com）不可用时，可配置备用网关 URL。
# 通过 AKB2_TRANSLATE_FALLBACK_BASE_URL 环境变量设置为 OpenAI-compatible 的备用网关地址。
# 未配置时 fallback 模型仍走主网关（仅切换模型，不切换网关），防止单模型故障。
_TRANSLATE_FALLBACK_BASE_URL = os.environ.get(
    "AKB2_TRANSLATE_FALLBACK_BASE_URL", ""
).strip() or None

# 长推阈值（字符数，超过则抽核心段）
LONG_TWEET_THRESHOLD = 500

# 孤儿高分推文上限（被主题引用之外的高分推文最多保留这么多）
_ORPHAN_TOP_K = int(__import__("os").environ.get("AKB2_ORPHAN_TOP_K", "5"))

# ---------------------------------------------------------------------------
# 术语表（#14 术语锁定）
#
# 放在代码里而不是配置文件：这份表同时被翻译端（注入 prompt）和审计端
# （audit._check_glossary 校验译文是否遵守）读取，是两端共享的判据。做成配置文件
# 会引入"文件读不到/解析失败时怎么办"的降级分支，与 fail-closed 冲突；而术语表的
# 变更频率与代码同级（每次都要同步改校验用例），放代码里改一处即两端生效。
#
# 每条：term（英文源词） → {"zh": 唯一指定译法 | KEEP_EN(保留英文),
#                          "forbidden": 已实证的错译/分裂译法}
# 收录门槛：**同一管线内已实证出现译名分裂或错译**的词，不做预防性扩表。
# ---------------------------------------------------------------------------

KEEP_EN = "__KEEP_EN__"

TRANSLATION_GLOSSARY: dict[str, dict] = {
    # 实证：上下文腐烂(16 文件)/腐败(8)/衰减(2)/退化(3) 四种并存；"腐烂/腐败"是字面直译错误
    "Context Rot": {
        "zh": "上下文衰减",
        "forbidden": ["上下文腐烂", "上下文腐败", "上下文退化", "语境腐烂"],
    },
    # 实证：37 份译作"安德烈·卡帕斯"（对应 Karpas，错译）；人名按 prompt 规则保留原文
    "Andrej Karpathy": {
        "zh": KEEP_EN,
        "forbidden": ["安德烈·卡帕斯", "卡帕斯", "卡帕西"],
    },
    # 实证：译作"支架工程"（harness 误作 scaffolding）；AKB taxonomy 内该概念用英文
    "Harness Engineering": {
        "zh": KEEP_EN,
        "forbidden": ["支架工程", "线束工程", "脚手架工程"],
    },
    # 实证：氛围编程(39)/氛围编码(12)/保留原文 三种并存；取多数派为唯一指定
    "Vibe Coding": {
        "zh": "氛围编程",
        "forbidden": ["氛围编码", "振动编码", "感觉编程", "氛围写代码"],
    },
    # 实证：Copilot(114)/Co-pilot(3)；产品名不得改写
    "Copilot": {
        "zh": KEEP_EN,
        "forbidden": ["Co-pilot", "副驾驶", "协同驾驶"],
    },
    # benchmark 名不得翻译（另见 audit._check_metric_fabrication：SWE-bench 是 resolve rate）
    "SWE-bench": {
        "zh": KEEP_EN,
        "forbidden": ["软件工程基准测试", "SWE 基准"],
    },
}


def _render_glossary_rules() -> str:
    """把术语表渲染成 prompt 规则文本（翻译端与审计端同源）。"""
    lines: list[str] = []
    for term, spec in TRANSLATION_GLOSSARY.items():
        zh = spec.get("zh")
        target = (
            "保留英文原文，不得音译/意译"
            if zh == KEEP_EN
            else f"必须译作「{zh}」"
        )
        forbidden = spec.get("forbidden") or []
        tail = f"（禁用：{'、'.join(forbidden)}）" if forbidden else ""
        lines.append(f"   - {term} → {target}{tail}")
    return "\n".join(lines)


_TRANSLATE_SYSTEM = (
    "你是专业的 AI/ML 领域简体中文翻译编辑。"
    "将提供的英文 AI 趋势分析内容翻译为简体中文，要求：\n"
    "1. 专业名词保留英文（如 RAG、MCP、LangGraph、GPT-5、Claude、Transformer、API 等）\n"
    "2. 博主 @id 完全保留（如 @AndrewNg、@OpenAI）\n"
    "3. 数字、百分比、版本号保持原样。模型参数规模（405B / 8B / 72B 这类紧贴数字的大写 B）"
    "属于型号标识，必须逐字保留，禁止换算成「4050 亿参数」这类中文量级\n"
    "4. URL 完全保留（逐字，不得改写/截断/加空格）\n"
    "5. 禁止改写事实：不得新增原文没有的指标类型（原文只说 72% on SWE-bench，"
    "不得译成「72% 准确率」）、不得反转语义（Reproducible on 8B models too "
    "是「在 8B 上同样可复现」，不是「可扩展到 8B」）、"
    "不得删减限定词（production Rust 的 production 要保留）\n"
    "6. 译文准确、自然，避免机器翻译腔\n"
    "7. 术语表（唯一指定译法，不得使用其他说法）：\n"
    f"{_render_glossary_rules()}\n"
    "8. 严格按 JSON schema 输出，不添加额外字段"
)


def _display_tweet_indices(synth: dict, orphan_top_k: int = _ORPHAN_TOP_K) -> set[int]:
    """返回需要翻译的推文在 synth['tweets'] 列表中的下标集合。

    展示集合 = 被任何主题 supporting_tweet_ids 引用的推文
             ∪ 孤儿高分推文（未被引用，按 score 倒序取 top-k）
    """
    tweets = synth.get("tweets", [])
    id_to_idx = {str(t.get("id", "")): i for i, t in enumerate(tweets)}

    referenced_idx: set[int] = set()
    for fd in synth.get("focus_discussions", []):
        for tid in fd.get("supporting_tweet_ids", []):
            idx = id_to_idx.get(str(tid))
            if idx is not None:
                referenced_idx.add(idx)

    # 孤儿高分：未被引用的推文，按评分倒序取 top-k
    orphan_candidates = [
        (i, t.get("score", 0))
        for i, t in enumerate(tweets)
        if i not in referenced_idx
    ]
    orphan_candidates.sort(key=lambda x: x[1], reverse=True)
    orphan_idx = {i for i, _ in orphan_candidates[:orphan_top_k]}

    return referenced_idx | orphan_idx


# 单条推文 prompt 里的 idx 绑定标记（同时用于日志/测试定位请求的是哪一条）
_ITEM_IDX_MARKER = "TRANSLATE_ITEM_IDX"


def _build_analysis_prompt(synth: dict) -> str:
    """构建分析字段（executive_summary + focus_discussions）翻译 prompt。

    分析字段本身就是跨推文综述，全局视野是它的语义要求，故保留单次批量调用；
    条目级事实（推文正文/点题）改走 _build_tweet_prompt 逐条隔离。
    """
    payload = {
        "executive_summary": synth.get("executive_summary", ""),
        "focus_discussions": [
            {
                "idx": i,
                "topic": fd.get("topic", ""),
                "summary": fd.get("summary", ""),
            }
            for i, fd in enumerate(synth.get("focus_discussions", []))
        ],
    }

    return (
        "请将以下 AI 趋势简报的分析字段翻译为简体中文。\n\n"
        "输入 JSON：\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        "输出要求：返回 JSON，结构如下：\n"
        "```json\n"
        "{\n"
        '  "executive_summary": "<译文>",\n'
        '  "focus_discussions": [{"idx": 0, "topic": "<译文>", "summary": "<译文>"}]\n'
        "}\n"
        "```\n\n"
        "重要约束：\n"
        "- focus_discussions 的 idx 必须与输入 idx 一一对应，不可丢失或改变\n"
        "- 只翻译输入里出现的内容，禁止引入输入之外的概念、数字或论断\n"
        "- 专业名词/@id/数字/URL 保持原样"
    )


def _build_tweet_prompt(idx: int, tweet: dict) -> str:
    """构建**单条**推文的翻译 prompt（条目级隔离，切断跨条目串味）。"""
    text = tweet.get("text", "")
    payload = {
        "idx": idx,
        "text": text,
        "is_long": len(text) > LONG_TWEET_THRESHOLD,
        # 若已有通过校验的核心英文段，请求模型直接翻译该段
        **({"core_excerpt_en": tweet.get("core_excerpt", "")}
           if tweet.get("core_excerpt") else {}),
    }

    return (
        f"{_ITEM_IDX_MARKER}: {idx}\n"
        "请翻译下面这**一条**推文。它是独立条目：输入之外的任何推文、话题、概念都与它无关。\n\n"
        "输入 JSON：\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        "输出要求：返回 JSON，结构如下：\n"
        "```json\n"
        "{\n"
        f'  "idx": {idx},\n'
        '  "text_zh": "<译文>",\n'
        '  "zh_point": "<一句中文点题（≤30字）：点出这条推文的核心看点或为什么值得关注>",\n'
        '  "core_excerpt_en": "<仅对 is_long=true 且无 core_excerpt_en 输入：'
        '从英文原文中逐字摘出最核心1段，≤400字，必须是原文子串>",\n'
        '  "core_excerpt_zh": "<仅当输入中有 core_excerpt_en：将该英文核心段译为中文>"\n'
        "}\n"
        "```\n\n"
        "重要约束：\n"
        f"- idx 必须原样返回 {idx}，不可改变\n"
        "- 只依据本条 text 翻译与点题，禁止引入本条之外的概念、数字或论断\n"
        "- zh_point：1 句中文，点出核心看点，不要复述全文\n"
        "- core_excerpt_en：必须是本条 text 的逐字子串，禁止改写或概括\n"
        "- is_long=false 时无需输出 core_excerpt_en\n"
        "- 专业名词/@id/数字/URL 保持原样"
    )


def _han_count(text: str) -> int:
    """统计中文字符数。"""
    return sum(1 for c in text if "一" <= c <= "鿿") if text else 0


def _is_translated_topic(text: str, min_han: int = 2) -> bool:
    """判断 topic 是否已翻译（含足够中文）。

    用绝对中文字数而非比例：AI topic 术语密集（产品名/型号/英文缩写），
    '已正确翻译但产品名占大半' 的 topic（如 'vLLM v0.22.0 发布与推理优化'，中文占比仅 ~28%）
    若用比例阈值会被误判残英。只要含 ≥min_han 个中文字即视为已翻译；纯英文（0 中文）才补译。
    """
    return _han_count(text) >= min_han


def _is_substring(excerpt: str, original: str) -> bool:
    """检查 excerpt 是否为 original 的子串（允许首尾空白差异）。"""
    excerpt = excerpt.strip()
    original = original.strip()
    if not excerpt:
        return False
    return excerpt in original


def _first_segment(text: str, max_chars: int = 400) -> str:
    """回退：取正文第一段（按句界截断），最多 max_chars 字符。"""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    # 按句界截断
    for sep in ["\n\n", "\n", ". ", "。", "！", "？", "! ", "? "]:
        idx = text.find(sep, min(100, max_chars // 2))
        if 0 < idx <= max_chars:
            return text[:idx + len(sep)].strip()
    return text[:max_chars].strip()


_DEFAULT_TRANSLATE_TIMEOUT = int(os.environ.get("AKB2_TRANSLATE_TIMEOUT", "300"))
# 秒；plist/env 可覆盖（默认 300，给瞬时变慢留余量）


def _model_chain(model: str | None = None) -> list[str]:
    """构建模型链（主力 + 备用），去重保序。"""
    chain: list[str] = []
    for m in [model or _TRANSLATE_MODEL, *_TRANSLATE_FALLBACK_MODELS]:
        if m and m not in chain:
            chain.append(m)
    return chain


# ---------------------------------------------------------------------------
# 阶段总预算（缺陷 S-4，constitution 原则 1「循环必须有显式停止条件与预算」）
#
# 条目级隔离把 1 次调用变成 1+N 次，单条虽有 _TWEET_TIMEOUT_CAP=120s 上限，但整段
# 此前**没有任何总量约束**：20 条 × 3 个 fallback 模型 × 120s ≈ 2 小时，而 launchd
# 侧（com.akb2.briefing，07:30 触发）既无 ExitTimeOut 也无 timeout 包裹，超时行为
# 完全不可预期。故用具名常量表达两条硬上限，任一触达即**可见地**停止后续调用。
#
# 取值依据：展示推文上限 = 被引用推文 + 孤儿 top-K（_ORPHAN_TOP_K 默认 5），实测
# 每篇 18.2 条（60 篇已发布简报均值）→ 1 + 19 = 20 个逻辑条目；每条最多 len(chain)=3
# 次模型尝试 → 60 次尝试即"每条都把整条链打穿"的最坏情况，超过它说明链路整体故障，
# 继续打只是烧钱。时长 900s = 15 分钟，与「日报 07:30 触发、需在早高峰前落地」相容，
# 且远小于原先 ~2h 的最坏情况。
#
# 精度边界（如实记录）：时长预算在**发起调用前**检查，已在飞的单次调用不会被打断，
# 故实际上界是 900s + 单次上限 _TWEET_TIMEOUT_CAP(120s) = 1020s。要真正硬切需要在
# 传输层加 deadline，那是 synthesis._call_llm 的职责，不在本阶段范围内。
# ---------------------------------------------------------------------------

_TRANSLATE_TOTAL_CALL_BUDGET = int(
    os.environ.get("AKB2_TRANSLATE_TOTAL_CALLS", "60")
)
_TRANSLATE_TOTAL_TIME_BUDGET_S = int(
    os.environ.get("AKB2_TRANSLATE_TOTAL_SECONDS", "900")
)


class _TranslateBudget:
    """整段翻译的总预算（调用尝试数 + 墙钟时长）。任一耗尽即停止发起新调用。

    记录 `exhausted_reason` 而非静默返回：未翻译的条目必须在产出层可判读
    （constitution 原则 4），否则「预算耗尽没送去翻译」与「模型逐条失败」
    在产出里长得一模一样。
    """

    def __init__(self, max_calls: int | None = None, max_seconds: int | None = None) -> None:
        # 默认值在**构造时**读模块常量（不是函数定义时绑定默认参数）：否则测试/部署
        # 覆盖常量后新建的 budget 仍是旧上限，预算就成了不可调的死值。
        self.max_calls = _TRANSLATE_TOTAL_CALL_BUDGET if max_calls is None else max_calls
        self.max_seconds = (
            _TRANSLATE_TOTAL_TIME_BUDGET_S if max_seconds is None else max_seconds
        )
        self.started_at = time.monotonic()
        self.calls = 0
        self.exhausted_reason: str | None = None

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def take(self) -> bool:
        """领一次调用额度；返回 False 表示预算已耗尽（不得再发起调用）。"""
        if self.calls >= self.max_calls:
            self.exhausted_reason = "call_budget"
            return False
        if self.elapsed() >= self.max_seconds:
            self.exhausted_reason = "time_budget"
            return False
        self.calls += 1
        return True

    def describe(self) -> str:
        return (
            f"{self.exhausted_reason} (calls={self.calls}/{self.max_calls}, "
            f"elapsed={self.elapsed():.0f}s/{self.max_seconds}s)"
        )


def _call_json_with_chain(
    prompt: str,
    chain: list[str],
    *,
    timeout: int,
    label: str,
    budget: _TranslateBudget | None = None,
) -> tuple[dict | None, list[str]]:
    """按模型链顺序请求 JSON，返回 (解析结果|None, 重排后的链)。

    重排：某个模型成功后把它提到链首——条目级隔离后单次运行有 1+N 次调用，
    每次都从已知不可用的主力重试会白付 N 倍失败开销。

    budget 非 None 时，每次模型尝试都要先领额度；预算耗尽则不再尝试剩余模型
    （调用方据 `budget.exhausted_reason` 标记未翻译条目）。
    """
    from akb2.briefing.synthesis import _call_llm

    for i, used_model in enumerate(chain):
        if budget is not None and not budget.take():
            sys.stderr.write(
                f"WARNING [translate]: 翻译总预算耗尽（{budget.describe()}），"
                f"{label} 停止尝试剩余模型 {chain[i:]}\n"
            )
            return None, chain
        # 主力模型走默认网关，fallback 模型走跨网关备用 URL（防单网关故障）。
        # 按模型身份而非链内位置判定：链会因"成功者提到链首"重排，用位置会让
        # 备用模型在重排后被错误地打回主网关。
        fb_base_url = (
            _TRANSLATE_FALLBACK_BASE_URL
            if used_model in _TRANSLATE_FALLBACK_MODELS
            else None
        )
        try:
            raw = _call_llm(
                [
                    {"role": "system", "content": _TRANSLATE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                model=used_model,
                json_mode=True,
                timeout=timeout,
                base_url=fb_base_url,
            )
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
            reordered = [used_model, *[m for m in chain if m != used_model]]
            return parsed, reordered
        except Exception as e:
            remaining = chain[i + 1:]
            if remaining:
                sys.stderr.write(
                    f"WARNING [translate]: {used_model} failed on {label} ({e}), "
                    f"retrying with {remaining[0]}\n"
                )
            else:
                sys.stderr.write(
                    f"WARNING [translate]: all translate models failed on {label} "
                    f"(last {used_model}: {e}).\n"
                )
    return None, chain


def _extract_tweet_item(payload: dict, idx: int) -> dict | None:
    """从单条推文的模型响应里取出该条译文，并做 idx 绑定校验。

    接受两种形状：顶层字段，或被包进 {"tweets": [...]} 的单元素列表。
    idx 与请求不符（模型答错条目）→ 拒收，返回 None，由调用方标记失败。
    """
    item: dict | None = None
    wrapped = payload.get("tweets")
    if isinstance(wrapped, list):
        for cand in wrapped:
            if isinstance(cand, dict) and cand.get("idx") == idx:
                item = cand
                break
        if item is None:
            sys.stderr.write(
                f"WARNING [translate]: tweets[{idx}] response has no matching idx "
                f"(got {[c.get('idx') for c in wrapped if isinstance(c, dict)]}), rejected.\n"
            )
            return None
    elif "text_zh" in payload or "zh_point" in payload:
        returned_idx = payload.get("idx")
        if returned_idx is not None and returned_idx != idx:
            sys.stderr.write(
                f"WARNING [translate]: tweets[{idx}] response bound to idx="
                f"{returned_idx!r}, rejected (cross-item binding violation).\n"
            )
            return None
        item = payload
    if item is None:
        sys.stderr.write(
            f"WARNING [translate]: tweets[{idx}] response missing text_zh/zh_point, rejected.\n"
        )
        return None
    return {**item, "idx": idx}


# 单条推文调用的超时上限（秒）：单条内容远小于整批，300s 只会把串行总时长放大到不可接受
_TWEET_TIMEOUT_CAP = 120


# ---------------------------------------------------------------------------
# 译前/译后配对样本落盘（解锁 audit fail 级检查的误报率测量）
#
# 为什么采样点只能在这里：audit 的 4 个 fail 级确定性检查（number_mismatch /
# url_missing / handle_missing / metric_fabricated）都必须拿「英文原文 ↔ 中文译文」
# 配对才能判定，而 vault 里只有中文成品 —— 没有配对语料时误报率不是"还没测"，是
# **没有可测的对象**。translate 阶段是全链路唯一同时持有 synth_en 与 synth_zh 的
# 位置，故在此把配对落盘成可复算语料，评测由 `evals/audit-fidelity-fp/run.py` 离线跑。
#
# 硬约束：
#   - 落盘位置只能是机器本地状态根（`akb2.common.state_paths`），**绝不写 vault**
#     （宪法原则 5：禁止向 vault 写工程产物）；状态根本身已 fail-closed 拒绝同步路径。
#   - 采样必须有界（原则 1）：单次运行条数上限 + 目录字节上限 + 评测侧保留天数。
#   - 落盘失败必须可见（原则 4）：stderr 告警 + 在返回值上留标记，不静默 pass。
#   - 只存审计需要的字段（en / zh / field / date / captured_at），不存整份 payload。
# ---------------------------------------------------------------------------

# 开关：设为 0/false/no/off 关闭采样。**默认开启** —— 关掉等于让"fail 级检查误报率
# 不可测"这条风险永久留在原地，而采样对象是已经在内存里的同一份文本，增量成本只有
# 一次本地 JSONL 追加（无额外 LLM 调用、无网络）。
FIDELITY_SAMPLE_ENV = "AKB2_FIDELITY_SAMPLE"
FIDELITY_SAMPLE_SUBDIR = "fidelity-samples"
# 单次运行落盘条数上限。取值依据：受检字段 = 1 个 executive_summary + 每主题 2 个
# （topic/summary）+ 展示推文，展示集合上界 = 被引用推文 + 孤儿 top-K
# （_ORPHAN_TOP_K 默认 5）；实测 26.5 个受检配对/篇（60 篇已发布简报共 1587 个，复现
# 命令见 audit._check_numbers_detailed docstring），40 留余量。
# 超过即停止写入并点名（不静默截断）。
FIDELITY_SAMPLE_MAX_PAIRS_PER_RUN = 40
# 采样目录字节上限：达到即停止写入（可见告警）。按每配对 ~1.5KB、每天 ~30 条估算，
# 32MB ≈ 700+ 天用量，正常保留期内不会触达；触达说明有异常增长，停写而非静默膨胀。
FIDELITY_SAMPLE_MAX_DIR_BYTES = 32 * 1024 * 1024
# 保留天数：本模块**不自动删除**任何样本文件（自动删本机状态的风险高于收益），
# 过期样本由评测侧按本常量排除并在报告里显式计数（`pairs_expired_excluded`）；
# 磁盘上界由 FIDELITY_SAMPLE_MAX_DIR_BYTES 硬保证，不依赖删除。
FIDELITY_SAMPLE_RETENTION_DAYS = 30

_SAMPLE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _fidelity_sample_enabled() -> bool:
    raw = os.environ.get(FIDELITY_SAMPLE_ENV)
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def fidelity_sample_dir():
    """配对样本目录（机器本地状态根下，不建目录）。"""
    from akb2.common.state_paths import get_state_root

    return get_state_root() / FIDELITY_SAMPLE_SUBDIR


def _sample_dir_bytes(sample_dir) -> int:
    """采样目录已用字节（只数本模块写的 *.jsonl）。"""
    total = 0
    if not sample_dir.is_dir():
        return 0
    for path in sample_dir.glob("*.jsonl"):
        try:
            total += path.stat().st_size
        except OSError as exc:  # 不静默：漏算会让字节预算失效
            sys.stderr.write(
                f"WARNING [translate]: 采样目录用量统计跳过 {path}: {exc}\n"
            )
    return total


def dump_fidelity_pairs(synth_en: dict, synth_zh: dict, *, date: str | None = None) -> dict:
    """把 (en, zh) 配对样本追加落盘，返回状态 dict。

    配对集合直接来自 `audit._translation_pairs` —— 与 fail 级检查**同一个**取字段
    函数，否则语料与被测判据会各走一套口径，评测结论对不上生产。

    Returns:
        dict: `{"status": "written"|"empty"|"disabled"|"skipped"|"failed", ...}`
            written 带 `pairs` / `truncated` / `path`；skipped 带 `reason`；
            failed 带 `reason`。

    Note:
        采样是可观测性副作用，绝不能让它把日报主链路打挂，故整体包 except；
        但每条失败都写 stderr 并由返回值上浮（原则 4 失败可见），不是静默兜底。
    """
    if not _fidelity_sample_enabled():
        return {"status": "disabled"}
    try:
        from akb2.briefing.audit import _translation_pairs
        from akb2.common.state_paths import is_synced_path, state_file_path

        resolved = str(date or synth_zh.get("date") or synth_en.get("date") or "").strip()
        if not _SAMPLE_DATE_RE.match(resolved):
            resolved = time.strftime("%Y-%m-%d", time.gmtime())
        target = state_file_path(f"{resolved}.jsonl", subdir=FIDELITY_SAMPLE_SUBDIR)
        if is_synced_path(target):
            # get_state_root 已 fail-closed，这里是第二道：宁可不采样也绝不写进同步/vault
            raise RuntimeError(f"配对样本目标 {target} 落在跨机同步范围内，拒绝写入")

        pairs = [
            p for p in _translation_pairs(synth_zh, synth_en)
            # en == zh 的配对在构造上不可能触发 fail 级判据（数字/URL/@handle 必然
            # 逐字保留），落盘它们只会把"恒等回放同义反复"重新造进语料里
            if p.get("en") and p.get("zh") and p["en"] != p["zh"]
        ]
        truncated = max(0, len(pairs) - FIDELITY_SAMPLE_MAX_PAIRS_PER_RUN)
        kept = pairs[:FIDELITY_SAMPLE_MAX_PAIRS_PER_RUN]
        if not kept:
            return {"status": "empty", "pairs": 0, "path": str(target)}

        dir_bytes = _sample_dir_bytes(target.parent)
        if dir_bytes >= FIDELITY_SAMPLE_MAX_DIR_BYTES:
            sys.stderr.write(
                f"WARNING [translate]: 配对样本目录已用 {dir_bytes} 字节 ≥ 上限 "
                f"{FIDELITY_SAMPLE_MAX_DIR_BYTES}，本次不落盘（{len(kept)} 个配对丢失）；"
                f"请人工清理 {target.parent}\n"
            )
            return {
                "status": "skipped", "reason": "dir_budget",
                "dir_bytes": dir_bytes, "dropped": len(kept),
            }

        target.parent.mkdir(parents=True, exist_ok=True)
        captured_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with target.open("a", encoding="utf-8") as fh:
            for pair in kept:
                fh.write(json.dumps({
                    "date": resolved,
                    "field": pair["field"],
                    "en": pair["en"],
                    "zh": pair["zh"],
                    "captured_at": captured_at,
                }, ensure_ascii=False) + "\n")
        if truncated:
            sys.stderr.write(
                f"WARNING [translate]: 配对样本触达单次上限 "
                f"{FIDELITY_SAMPLE_MAX_PAIRS_PER_RUN}，{truncated} 个配对未落盘"
                f"（fidelity_sample_truncated）\n"
            )
        return {
            "status": "written", "pairs": len(kept),
            "truncated": truncated, "path": str(target),
        }
    except Exception as exc:
        sys.stderr.write(
            f"WARNING [translate]: 配对样本落盘失败（{type(exc).__name__}: {exc}）；"
            "本次运行不产生可测语料（fidelity_sample_failed）\n"
        )
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}


def translate_briefing(
    synth: dict,
    *,
    model: str | None = None,
    timeout: int = _DEFAULT_TRANSLATE_TIMEOUT,
) -> dict:
    """将 synth payload 翻译为中文，返回新 dict；失败时保留原值并 stderr 告警 + 打标记。

    调用结构（条目级隔离，见模块 docstring）：
      1 次分析字段调用 + 每条展示推文各 1 次调用；每次都走完整模型链。

    模型链：主力 + 备用链按序尝试，任一成功即用其结果并提到链首。

    新增字段（可选）：
      tweets[i].text       → 中文译文（原 text 移到 text_original）
      tweets[i].text_original → 英文原文备查
      tweets[i].core_excerpt  → 长推中文核心段

    失败标记（供 pipeline 落盘/降级）：
      translate_failed / translate_degraded / translate_failed_fields
    """
    chain = _model_chain(model)
    budget = _TranslateBudget()

    trans, chain = _call_json_with_chain(
        _build_analysis_prompt(synth), chain, timeout=timeout, label="analysis",
        budget=budget,
    )
    if trans is None:
        sys.stderr.write(
            "WARNING [translate]: analysis translation failed on all models; "
            "returning original synth WITHOUT translation (translate_failed=True).\n"
        )
        failed = dict(synth)
        failed["translate_failed"] = True
        failed["translate_degraded"] = True
        failed["translate_failed_fields"] = ["analysis"]
        if budget.exhausted_reason:
            failed["translate_budget_exhausted"] = budget.exhausted_reason
        return failed

    tweets = synth.get("tweets", [])
    tweet_timeout = min(timeout, _TWEET_TIMEOUT_CAP)
    tweet_items: list[dict] = []
    failed_fields: list[str] = []
    for idx in sorted(_display_tweet_indices(synth)):
        if idx >= len(tweets):
            continue
        if budget.exhausted_reason:
            # 预算已耗尽：剩余条目一律不再发起调用，但必须逐条点名（不静默返回英文）
            failed_fields.append(f"tweets[{idx}](budget_exhausted)")
            continue
        payload, chain = _call_json_with_chain(
            _build_tweet_prompt(idx, tweets[idx]),
            chain,
            timeout=tweet_timeout,
            label=f"tweets[{idx}]",
            budget=budget,
        )
        item = _extract_tweet_item(payload, idx) if payload is not None else None
        if item is None:
            suffix = "(budget_exhausted)" if budget.exhausted_reason else ""
            failed_fields.append(f"tweets[{idx}]{suffix}")
            continue
        tweet_items.append(item)

    trans["tweets"] = tweet_items

    result = _apply_translation(synth, trans, model=model, budget=budget)
    if budget.exhausted_reason:
        result["translate_degraded"] = True
        result["translate_budget_exhausted"] = budget.exhausted_reason
        sys.stderr.write(
            f"WARNING [translate]: 翻译总预算耗尽（{budget.describe()}）——"
            f"{len([f for f in failed_fields if 'budget_exhausted' in f])} 条未翻译"
            "（translate_budget_exhausted，产出保持英文并已点名）\n"
        )
    if failed_fields:
        result["translate_degraded"] = True
        result["translate_failed_fields"] = (
            list(result.get("translate_failed_fields") or []) + failed_fields
        )
        sys.stderr.write(
            f"WARNING [translate]: {len(failed_fields)} item(s) left untranslated: "
            f"{failed_fields} (translate_degraded=True).\n"
        )

    # 配对样本落盘（异常路径才打标记，正常路径不给产出层加噪声；标记语义与
    # translate_budget_exhausted 一致：只在"该有的东西没到位"时出现）
    sample = dump_fidelity_pairs(synth, result)
    if sample["status"] in {"failed", "skipped"}:
        result["fidelity_sample_failed"] = sample.get("reason") or sample["status"]
    elif sample.get("truncated"):
        result["fidelity_sample_truncated"] = sample["truncated"]
    return result


def _apply_translation(
    synth: dict,
    trans: dict,
    *,
    model: str | None = None,
    budget: _TranslateBudget | None = None,
) -> dict:
    """将翻译结果合并回 synth，逐字段回退，不抛异常；合并失败时打标记（失败可见）。"""
    import copy
    result = copy.deepcopy(synth)

    try:
        return _apply_translation_inner(result, trans, model=model, budget=budget)
    except Exception as e:
        sys.stderr.write(
            f"WARNING [translate]: _apply_translation failed ({e}), "
            "returning original synth (translate_failed=True).\n"
        )
        failed = dict(synth)
        failed["translate_failed"] = True
        failed["translate_degraded"] = True
        failed["translate_failed_fields"] = (
            list(synth.get("translate_failed_fields") or []) + ["_apply_translation"]
        )
        return failed


def _safe_idx_map(items: list | None) -> dict:
    """从 list 中构建 {idx: item} 映射，跳过 idx 不是 int 的 item（防模型返回非法结构崩溃）。"""
    result = {}
    for item in (items or []):
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        if not isinstance(idx, int):
            continue
        result[idx] = item
    return result


def _mark_degraded(result: dict, field: str) -> None:
    """记录一处降级/失败字段（失败可见：状态随返回值上浮到 pipeline）。"""
    result["translate_degraded"] = True
    fields = list(result.get("translate_failed_fields") or [])
    if field not in fields:
        fields.append(field)
    result["translate_failed_fields"] = fields


def _apply_translation_inner(
    result: dict,
    trans: dict,
    *,
    model: str | None = None,
    budget: _TranslateBudget | None = None,
) -> dict:
    """实际合并逻辑，由 _apply_translation 包裹捕获。

    budget 一路透传给补译（topic / core_excerpt）：补译也是真实 LLM 调用，
    不计入总预算就等于给预算开了后门（最多再来 N 次 × 完整模型链）。
    """
    # executive_summary
    zh_summary = (trans.get("executive_summary") or "").strip()
    if zh_summary:
        result["executive_summary"] = zh_summary

    # focus_discussions
    fd_trans = _safe_idx_map(trans.get("focus_discussions"))
    fds = result.get("focus_discussions", [])
    for i, fd in enumerate(fds):
        t = fd_trans.get(i, {})
        topic_zh = t.get("topic", "").strip()
        if topic_zh:
            if not _is_translated_topic(topic_zh):
                # SC-P2b-2: topic 几乎无中文（纯英文未翻译）→ 补译
                sys.stderr.write(
                    f"WARNING [translate]: focus_discussions[{i}].topic "
                    f"未翻译（纯英文）{topic_zh!r}，尝试补译\n"
                )
                retranslated = _translate_snippet(topic_zh, model=model, budget=budget)
                if retranslated:
                    fd["topic"] = retranslated
                else:
                    fd["topic"] = topic_zh
                    _mark_degraded(result, f"focus_discussions[{i}].topic")
            else:
                fd["topic"] = topic_zh
        if t.get("summary", "").strip():
            fd["summary"] = t["summary"].strip()

    # tweets（仅展示集合有 translation，其余 idx 的 tw_trans[i] 为 {}）
    tw_trans = _safe_idx_map(trans.get("tweets"))
    tweets = result.get("tweets", [])
    for i, tw in enumerate(tweets):
        t = tw_trans.get(i, {})
        original_text = tw.get("text", "")

        # SC-P3a-2: lang=zh 推文跳过 text 翻译（中文原文直接保留），lang=en 或无 lang 正常翻译
        is_zh_tweet = (tw.get("lang") or "").lower() == "zh"
        text_zh = (t.get("text_zh") or "").strip()
        if text_zh and not is_zh_tweet:
            tw["text_original"] = original_text  # 备查英文原文
            tw["text"] = text_zh

        # 中文点题（来自 translate 模型）
        gen_point = (t.get("zh_point") or "").strip()
        if gen_point:
            tw["zh_point"] = gen_point

        # 长推核心段（子串校验 + 直接翻译核心段）
        if len(original_text) > LONG_TWEET_THRESHOLD:
            core_en = (t.get("core_excerpt_en") or "").strip()
            core_zh_direct = (t.get("core_excerpt_zh") or "").strip()

            if core_en and _is_substring(core_en, original_text):
                # 模型直接返回了核心段译文 → 优先使用
                if core_zh_direct:
                    tw["core_excerpt"] = core_zh_direct
                else:
                    # 无直接译文：单独翻译 core_en
                    snippet_zh = _translate_snippet(core_en, model=model, budget=budget)
                    if snippet_zh:
                        tw["core_excerpt"] = snippet_zh
                    else:
                        # snippet 翻译失败 → 用中文译文第一段（不回退英文原段）
                        tw["core_excerpt"] = _first_segment(text_zh) if text_zh else ""
                        _mark_degraded(result, f"tweets[{i}].core_excerpt")
            else:
                if core_en:
                    sys.stderr.write(
                        f"WARNING [translate]: tweet[{i}] core_excerpt_en failed substring check, "
                        "falling back to first segment.\n"
                    )
                # 回退：取译文第一段（已是中文）
                first_seg_zh = _first_segment(text_zh) if text_zh else _first_segment(original_text)
                tw["core_excerpt"] = first_seg_zh or _first_segment(original_text)

    return result


def _translate_snippet(
    text_en: str,
    timeout: int = 60,
    *,
    model: str | None = None,
    budget: _TranslateBudget | None = None,
) -> str:
    """单独翻译一段英文（补译：残英 topic / core_excerpt_en 无对应 core_excerpt_zh）。

    与批量翻译共用 _TRANSLATE_SYSTEM（同一套数字/URL/术语表/禁改写事实约束），并走
    完整模型链——补译此前用的是更弱的 prompt 且只打主力模型，主力挂掉即静默返回空串。
    全链失败时返回空字符串并 stderr 告警，由调用方回退并打降级标记。
    """
    from akb2.briefing.synthesis import _call_llm

    if not text_en or not text_en.strip():
        return ""

    chain = _model_chain(model)
    for i, used_model in enumerate(chain):
        if budget is not None and not budget.take():
            sys.stderr.write(
                f"WARNING [translate]: 翻译总预算耗尽（{budget.describe()}），"
                "snippet 补译停止（返回空串，调用方降级）\n"
            )
            return ""
        fb_base_url = (
            _TRANSLATE_FALLBACK_BASE_URL
            if used_model in _TRANSLATE_FALLBACK_MODELS
            else None
        )
        try:
            messages = [
                {"role": "system", "content": _TRANSLATE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "把下面这段英文译为简体中文，只返回译文正文，不加解释、不加引号、"
                        "不要 JSON 包装。\n\n" + text_en.strip()
                    ),
                },
            ]
            return _call_llm(
                messages,
                model=used_model,
                json_mode=False,
                timeout=timeout,
                base_url=fb_base_url,
            ).strip()
        except Exception as e:
            remaining = chain[i + 1:]
            if remaining:
                sys.stderr.write(
                    f"WARNING [translate]: snippet translate via {used_model} failed ({e}), "
                    f"retrying with {remaining[0]}\n"
                )
            else:
                sys.stderr.write(
                    f"WARNING [translate]: snippet translate failed on all models "
                    f"(last {used_model}: {e}); returning empty (caller must degrade).\n"
                )
    return ""
