"""translate.py — 独立翻译阶段：将 synthesis payload 的分析字段与推文正文全部译为简体中文。

职责：
  - 翻译 executive_summary / focus_discussions[topic, summary] / key_quotes[quote, context]
  - 翻译 tweets[].text 正文（保留专业名词与 @博主id）
  - 长推（>500字）：先从英文原文抽核心段（子串校验防编造），再译中文；原文存 text_original
  - 每条引述补 context 中文点题
  - 任何字段/调用失败逐项回退（不抛异常，不崩流程）

设计：一次 LLM 调用处理全部翻译内容（batch），返回结构化 JSON。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

# 翻译阶段专用模型（默认中文母语模型；可由环境变量覆盖）
_TRANSLATE_MODEL = os.environ.get("AKB2_TRANSLATE_MODEL", "qwen/qwen3.6-plus")
_TRANSLATE_FALLBACK_MODEL = os.environ.get("AKB2_TRANSLATE_FALLBACK_MODEL", "z-ai/glm-5.1")

# 长推阈值（字符数，超过则抽核心段）
LONG_TWEET_THRESHOLD = 500

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


def _build_translate_prompt(synth: dict) -> str:
    """构建批量翻译 prompt，返回 JSON 字符串。"""
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
        "key_quotes": [
            {
                "idx": i,
                "quote": q.get("quote", ""),
                "context": q.get("context", ""),
            }
            for i, q in enumerate(synth.get("key_quotes", []))
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
            for i, t in enumerate(synth.get("tweets", []))
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
        '  "key_quotes": [{"idx": 0, "quote": "<英文原文（必须逐字保留，不翻译）>", "context": "<1句中文点题：说这条为什么值得引述>"}],\n'
        '  "tweets": [{"idx": 0, "text_zh": "<译文>", '
        '"core_excerpt_en": "<仅对 is_long=true 且无 core_excerpt_en 输入：从英文原文中逐字摘出最核心1段，≤400字，必须是原文子串>", '
        '"core_excerpt_zh": "<仅当输入中有 core_excerpt_en：将该英文核心段译为中文>"}]\n'
        "}\n"
        "```\n\n"
        "重要约束：\n"
        "- key_quotes 的 quote 字段：保留英文原文，不翻译\n"
        "- key_quotes 的 context 字段：用1句中文说明引述价值（如「首次公开了xxx数字，有参考意义」）\n"
        "- tweets 的 core_excerpt_en：必须是原文 text 的逐字子串，禁止改写或概括\n"
        "- is_long=false 的 tweet 无需输出 core_excerpt_en\n"
        "- 专业名词/@id/数字/URL 保持原样"
    )


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


def translate_briefing(
    synth: dict,
    *,
    model: str | None = None,
    timeout: int = 180,
) -> dict:
    """将 synth payload 翻译为中文，返回新 dict；失败时保留原值并 stderr 告警。

    新增字段（可选）：
      tweets[i].text       → 中文译文（原 text 移到 text_original）
      tweets[i].text_original → 英文原文备查
      tweets[i].core_excerpt  → 长推中文核心段
      key_quotes[i].context   → 中文引述点题
    """
    from akb2.briefing.synthesis import _call_llm

    used_model = model or _TRANSLATE_MODEL

    try:
        prompt = _build_translate_prompt(synth)
        messages = [
            {"role": "system", "content": _TRANSLATE_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        raw = _call_llm(messages, model=used_model, json_mode=True, timeout=timeout)
        trans = json.loads(raw)
    except Exception as e:
        # 主模型失败，尝试备用模型
        if used_model != _TRANSLATE_FALLBACK_MODEL:
            sys.stderr.write(
                f"WARNING [translate]: {used_model} failed ({e}), "
                f"retrying with {_TRANSLATE_FALLBACK_MODEL}\n"
            )
            try:
                messages = [
                    {"role": "system", "content": _TRANSLATE_SYSTEM},
                    {"role": "user", "content": _build_translate_prompt(synth)},
                ]
                raw = _call_llm(
                    messages, model=_TRANSLATE_FALLBACK_MODEL,
                    json_mode=True, timeout=timeout,
                )
                trans = json.loads(raw)
            except Exception as e2:
                sys.stderr.write(
                    f"WARNING [translate]: fallback {_TRANSLATE_FALLBACK_MODEL} also failed ({e2}). "
                    "Returning original synth without translation.\n"
                )
                return synth
        else:
            sys.stderr.write(
                f"WARNING [translate]: {used_model} failed ({e}). "
                "Returning original synth without translation.\n"
            )
            return synth

    return _apply_translation(synth, trans)


def _apply_translation(synth: dict, trans: dict) -> dict:
    """将翻译结果合并回 synth，逐字段回退，不抛异常。"""
    import copy
    result = copy.deepcopy(synth)

    # executive_summary
    zh_summary = (trans.get("executive_summary") or "").strip()
    if zh_summary:
        result["executive_summary"] = zh_summary

    # focus_discussions
    fd_trans = {item["idx"]: item for item in (trans.get("focus_discussions") or [])
                if isinstance(item, dict) and "idx" in item}
    fds = result.get("focus_discussions", [])
    for i, fd in enumerate(fds):
        t = fd_trans.get(i, {})
        if t.get("topic", "").strip():
            fd["topic"] = t["topic"].strip()
        if t.get("summary", "").strip():
            fd["summary"] = t["summary"].strip()

    # key_quotes — quote 保留英文，context 填中文点题
    kq_trans = {item["idx"]: item for item in (trans.get("key_quotes") or [])
                if isinstance(item, dict) and "idx" in item}
    kqs = result.get("key_quotes", [])
    for i, kq in enumerate(kqs):
        t = kq_trans.get(i, {})
        zh_context = (t.get("context") or "").strip()
        if zh_context:
            kq["context"] = zh_context

    # tweets
    tw_trans = {item["idx"]: item for item in (trans.get("tweets") or [])
                if isinstance(item, dict) and "idx" in item}
    tweets = result.get("tweets", [])
    for i, tw in enumerate(tweets):
        t = tw_trans.get(i, {})
        original_text = tw.get("text", "")

        # 译文
        text_zh = (t.get("text_zh") or "").strip()
        if text_zh:
            tw["text_original"] = original_text  # 备查英文原文
            tw["text"] = text_zh

        # 长推核心段（子串校验 + 直接翻译核心段）
        if len(original_text) > LONG_TWEET_THRESHOLD:
            core_en = (t.get("core_excerpt_en") or "").strip()
            core_zh_direct = (t.get("core_excerpt_zh") or "").strip()

            if core_en and _is_substring(core_en, original_text):
                # 模型直接返回了核心段译文 → 优先使用
                if core_zh_direct:
                    tw["core_excerpt"] = core_zh_direct
                else:
                    # 回退：截取译文前段（近似，但指定段不超过 400 字）
                    tw["core_excerpt"] = _first_segment(text_zh or original_text) or core_en
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


