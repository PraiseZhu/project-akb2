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
"""

from __future__ import annotations

import json
import os
import sys

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


def _call_json_with_chain(
    prompt: str,
    chain: list[str],
    *,
    timeout: int,
    label: str,
) -> tuple[dict | None, list[str]]:
    """按模型链顺序请求 JSON，返回 (解析结果|None, 重排后的链)。

    重排：某个模型成功后把它提到链首——条目级隔离后单次运行有 1+N 次调用，
    每次都从已知不可用的主力重试会白付 N 倍失败开销。
    """
    from akb2.briefing.synthesis import _call_llm

    for i, used_model in enumerate(chain):
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

    trans, chain = _call_json_with_chain(
        _build_analysis_prompt(synth), chain, timeout=timeout, label="analysis"
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
        return failed

    tweets = synth.get("tweets", [])
    tweet_timeout = min(timeout, _TWEET_TIMEOUT_CAP)
    tweet_items: list[dict] = []
    failed_fields: list[str] = []
    for idx in sorted(_display_tweet_indices(synth)):
        if idx >= len(tweets):
            continue
        payload, chain = _call_json_with_chain(
            _build_tweet_prompt(idx, tweets[idx]),
            chain,
            timeout=tweet_timeout,
            label=f"tweets[{idx}]",
        )
        item = _extract_tweet_item(payload, idx) if payload is not None else None
        if item is None:
            failed_fields.append(f"tweets[{idx}]")
            continue
        tweet_items.append(item)

    trans["tweets"] = tweet_items

    result = _apply_translation(synth, trans, model=model)
    if failed_fields:
        result["translate_degraded"] = True
        result["translate_failed_fields"] = (
            list(result.get("translate_failed_fields") or []) + failed_fields
        )
        sys.stderr.write(
            f"WARNING [translate]: {len(failed_fields)} item(s) left untranslated: "
            f"{failed_fields} (translate_degraded=True).\n"
        )
    return result


def _apply_translation(synth: dict, trans: dict, *, model: str | None = None) -> dict:
    """将翻译结果合并回 synth，逐字段回退，不抛异常；合并失败时打标记（失败可见）。"""
    import copy
    result = copy.deepcopy(synth)

    try:
        return _apply_translation_inner(result, trans, model=model)
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


def _apply_translation_inner(result: dict, trans: dict, *, model: str | None = None) -> dict:
    """实际合并逻辑，由 _apply_translation 包裹捕获。"""
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
                retranslated = _translate_snippet(topic_zh, model=model)
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
                    snippet_zh = _translate_snippet(core_en, model=model)
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
