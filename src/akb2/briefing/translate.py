"""translate.py — 独立翻译阶段：将 synthesis payload 的分析字段与展示推文全部译为简体中文。

职责：
  - 翻译 executive_summary / focus_discussions[topic, summary]
  - 翻译展示推文集合（被任何主题 supporting_tweet_ids 引用 ∪ 孤儿高分 top-K）
  - 长推（>500字）：先从英文原文抽核心段（子串校验防编造），再译中文；原文存 text_original
  - 每条展示推文生成中文点题 zh_point
  - 任何字段/调用失败逐项回退（不抛异常，不崩流程）

设计：一次 LLM 调用处理全部翻译内容（batch），返回结构化 JSON。
"""

from __future__ import annotations

import json
import os
import sys

# 翻译阶段模型链（实测：gemini 每次调用固定开销最低 ~24s，deepseek/glm ~42-47s，
# 质量三家持平；故主力 gemini，备用按不同厂商排 deepseek→glm，规避单厂商故障。
# 均可由环境变量覆盖。）
_TRANSLATE_MODEL = os.environ.get("AKB2_TRANSLATE_MODEL", "gemini-3.1-pro-preview")
_TRANSLATE_FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "AKB2_TRANSLATE_FALLBACK_MODELS",
        "deepseek/deepseek-v4-pro,z-ai/glm-5.1,anthropic/claude-sonnet-4-6",
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

_TRANSLATE_SYSTEM = (
    "你是专业的 AI/ML 领域简体中文翻译编辑。"
    "将提供的英文 AI 趋势分析内容翻译为简体中文，要求：\n"
    "1. 专业名词保留英文（如 RAG、MCP、LangGraph、GPT-5、Claude、Transformer、API 等）\n"
    "2. 博主 @id 完全保留（如 @AndrewNg、@OpenAI）\n"
    "3. 数字、百分比、版本号保持原样\n"
    "4. URL 完全保留\n"
    "5. 译文准确、自然，避免机器翻译腔\n"
    "6. 严格按 JSON schema 输出，不添加额外字段"
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


def _build_translate_prompt(synth: dict) -> str:
    """构建批量翻译 prompt，仅翻译展示集合（被引用 ∪ 孤儿高分）。"""
    display_indices = _display_tweet_indices(synth)
    tweets = synth.get("tweets", [])

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
        "tweets": [
            {
                "idx": i,
                "text": t.get("text", ""),
                "is_long": len(t.get("text", "")) > LONG_TWEET_THRESHOLD,
                # 若已有通过校验的核心英文段，请求模型直接翻译该段
                **({"core_excerpt_en": t.get("core_excerpt", "")}
                   if t.get("core_excerpt") else {}),
            }
            for i, t in enumerate(tweets)
            if i in display_indices
        ],
    }

    return (
        "请将以下 AI 趋势简报内容翻译为简体中文。\n\n"
        "输入 JSON：\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        "输出要求：返回 JSON，结构如下：\n"
        "```json\n"
        "{\n"
        '  "executive_summary": "<译文>",\n'
        '  "focus_discussions": [{"idx": 0, "topic": "<译文>", "summary": "<译文>"}],\n'
        '  "tweets": [{"idx": <原输入 idx 值，不可改变>, "text_zh": "<译文>", '
        '"zh_point": "<一句中文点题（≤30字）：点出这条推文的核心看点或为什么值得关注>", '
        '"core_excerpt_en": "<仅对 is_long=true 且无 core_excerpt_en 输入：从英文原文中逐字摘出最核心1段，≤400字，必须是原文子串>", '
        '"core_excerpt_zh": "<仅当输入中有 core_excerpt_en：将该英文核心段译为中文>"}]\n'
        "}\n"
        "```\n\n"
        "重要约束：\n"
        "- tweets 的 idx 必须与输入 idx 一一对应，不可丢失或改变\n"
        "- tweets 的 zh_point：每条都要，1句中文，点出核心看点，不要复述全文\n"
        "- tweets 的 core_excerpt_en：必须是原文 text 的逐字子串，禁止改写或概括\n"
        "- is_long=false 的 tweet 无需输出 core_excerpt_en\n"
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


_DEFAULT_TRANSLATE_TIMEOUT = int(os.environ.get("AKB2_TRANSLATE_TIMEOUT", "300"))   # 秒；plist/env 可覆盖（默认 300，给瞬时变慢留余量）


def translate_briefing(
    synth: dict,
    *,
    model: str | None = None,
    timeout: int = _DEFAULT_TRANSLATE_TIMEOUT,
) -> dict:
    """将 synth payload 翻译为中文，返回新 dict；失败时保留原值并 stderr 告警。

    模型链：主力 + 备用链按序尝试，任一成功即用其结果；全部失败则返回原 synth。

    新增字段（可选）：
      tweets[i].text       → 中文译文（原 text 移到 text_original）
      tweets[i].text_original → 英文原文备查
      tweets[i].core_excerpt  → 长推中文核心段
      key_quotes[i].context   → 中文引述点题
    """
    from akb2.briefing.synthesis import _call_llm

    # 构建模型链（主力 + 备用），去重保序
    chain: list[str] = []
    for m in [model or _TRANSLATE_MODEL, *_TRANSLATE_FALLBACK_MODELS]:
        if m and m not in chain:
            chain.append(m)

    prompt = _build_translate_prompt(synth)
    messages = [
        {"role": "system", "content": _TRANSLATE_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    trans = None
    for i, used_model in enumerate(chain):
        # 主力模型走默认网关，fallback 模型走跨网关备用 URL（防单网关故障）
        fb_base_url = _TRANSLATE_FALLBACK_BASE_URL if i > 0 else None
        try:
            raw = _call_llm(messages, model=used_model, json_mode=True, timeout=timeout,
                            base_url=fb_base_url)
            trans = json.loads(raw)
            break
        except Exception as e:
            remaining = chain[i + 1:]
            if remaining:
                sys.stderr.write(
                    f"WARNING [translate]: {used_model} failed ({e}), "
                    f"retrying with {remaining[0]}\n"
                )
            else:
                sys.stderr.write(
                    f"WARNING [translate]: all translate models failed "
                    f"(last {used_model}: {e}). Returning original synth without translation.\n"
                )

    if trans is None:
        return synth

    return _apply_translation(synth, trans)


def _apply_translation(synth: dict, trans: dict) -> dict:
    """将翻译结果合并回 synth，逐字段回退，不抛异常。"""
    import copy
    result = copy.deepcopy(synth)

    try:
        return _apply_translation_inner(result, trans)
    except Exception as e:
        sys.stderr.write(
            f"WARNING [translate]: _apply_translation failed ({e}), "
            "returning original synth.\n"
        )
        return synth


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


def _apply_translation_inner(result: dict, trans: dict) -> dict:
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
                    f"WARNING [translate]: focus_discussions[{i}].topic 未翻译（纯英文）{topic_zh!r}，尝试补译\n"
                )
                retranslated = _translate_snippet(topic_zh)
                fd["topic"] = retranslated if retranslated else topic_zh
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
                    snippet_zh = _translate_snippet(core_en)
                    if snippet_zh:
                        tw["core_excerpt"] = snippet_zh
                    else:
                        # snippet 翻译失败 → 用中文译文第一段（不回退英文原段）
                        tw["core_excerpt"] = _first_segment(text_zh) if text_zh else ""
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


def _translate_snippet(text_en: str, timeout: int = 60) -> str:
    """单独翻译一段英文（用于 core_excerpt_en 无对应 core_excerpt_zh 时）。
    失败时返回空字符串，由调用方回退到英文原段。
    """
    from akb2.briefing.synthesis import _call_llm

    if not text_en or not text_en.strip():
        return ""
    try:
        messages = [
            {"role": "system", "content": "将以下英文 AI 趋势内容翻译为简体中文，保留专业名词/@id/数字，只返回译文，不加解释。"},
            {"role": "user", "content": text_en.strip()},
        ]
        return _call_llm(
            messages,
            model=os.environ.get("AKB2_TRANSLATE_MODEL", "gemini-3.1-pro-preview"),
            json_mode=False,
            timeout=timeout,
        ).strip()
    except Exception:
        return ""


