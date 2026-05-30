"""test_translate.py — translate.py 单元测试。"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

FIXTURE_SYNTH = {
    "date": "2026-05-30",
    "executive_summary": "Today AI agents showed strong growth with new memory tools.",
    "focus_discussions": [
        {
            "topic": "Agent Memory Infrastructure",
            "supporting_tweet_ids": ["1"],
            "summary": "Multiple tools emerged for giving AI agents persistent memory across sessions.",
        },
        {
            "topic": "Claude ecosystem growth",
            "supporting_tweet_ids": ["2"],
            "summary": "Claude integrations expanded with Vercel and Replit partnerships.",
        },
    ],
    "key_quotes": [
        {
            "author": "@dr_cintas",
            "quote": "You can now give AI agents infinite memory with agentmemory.",
            "tweet_id": "1",
            "url": "https://x.com/1",
            "context": "",
        }
    ],
    "tweets": [
        {
            "id": "1",
            "author": "@dr_cintas",
            "text": "Short tweet about agentmemory.",
            "created_at": "2026-05-30T01:00:00Z",
            "score": 0.9,
            "url": "https://x.com/1",
        },
        {
            "id": "2",
            "author": "@AndrewYNg",
            # Long tweet (>500 chars)
            "text": "A" * 600 + " This is the core insight about AI agents and memory systems. " + "B" * 100,
            "created_at": "2026-05-30T02:00:00Z",
            "score": 0.8,
            "url": "https://x.com/2",
        },
    ],
    "scoring_summary": {"total_tweets": 2, "scored_tweets": 2},
    "source": {"name": "deepseek_synthesis", "collector_version": "1.0.0"},
    "generated_at": "2026-05-30T06:00:00Z",
}

MOCK_TRANSLATE_RESPONSE = {
    "executive_summary": "今日 AI 智能体在新记忆工具方面表现出强劲增长。",
    "focus_discussions": [
        {"idx": 0, "topic": "智能体长记忆基础设施", "summary": "多款工具涌现，用于为 AI 智能体提供跨会话的持久记忆。"},
        {"idx": 1, "topic": "Claude 生态系统扩张", "summary": "Claude 与 Vercel、Replit 建立合作，集成持续扩展。"},
    ],
    "key_quotes": [
        {"idx": 0, "quote": "You can now give AI agents infinite memory with agentmemory.", "context": "首次公开了 agentmemory 的能力范围，对开发者有直接参考价值。"}
    ],
    "tweets": [
        {"idx": 0, "text_zh": "关于 agentmemory 的简短推文。"},
        {
            "idx": 1,
            "text_zh": "关于 AI 智能体和记忆系统核心洞察的长文推文译文。",
            "core_excerpt_en": "This is the core insight about AI agents and memory systems.",
            "core_excerpt_zh": "这是关于 AI 智能体和记忆系统的核心洞察。",  # 模型直接翻译核心段
        },
    ],
}


def _make_mock_call_llm(response: dict):
    def mock_call_llm(messages, model, **kwargs):
        return json.dumps(response, ensure_ascii=False)
    return mock_call_llm


# ---------------------------------------------------------------------------
# SC-1 + SC-2: 翻译后分析字段为中文，topic 非占位符
# ---------------------------------------------------------------------------

def test_translate_keeps_terms_and_ids():
    """分析字段译为中文；@id 与专业名词保留（由翻译 prompt 约束，此处验证结构）。"""
    from akb2.briefing.translate import translate_briefing

    with patch("akb2.briefing.synthesis._call_llm", side_effect=_make_mock_call_llm(MOCK_TRANSLATE_RESPONSE)):
        result = translate_briefing(FIXTURE_SYNTH)

    assert result["executive_summary"] == "今日 AI 智能体在新记忆工具方面表现出强劲增长。"
    assert result["focus_discussions"][0]["topic"] == "智能体长记忆基础设施"
    assert result["focus_discussions"][1]["topic"] == "Claude 生态系统扩张"
    assert "AI 趋势观察 #" not in result["focus_discussions"][0]["topic"]
    assert "AI 趋势观察 #" not in result["focus_discussions"][1]["topic"]


# ---------------------------------------------------------------------------
# SC-3 + SC-4: 长推核心段子串校验 + 折叠
# ---------------------------------------------------------------------------

def test_translate_long_tweet_excerpt_and_original():
    """长推(>500字)：core_excerpt 存在，text_original 保留英文原文。"""
    from akb2.briefing.translate import translate_briefing

    with patch("akb2.briefing.synthesis._call_llm", side_effect=_make_mock_call_llm(MOCK_TRANSLATE_RESPONSE)):
        result = translate_briefing(FIXTURE_SYNTH)

    long_tw = result["tweets"][1]
    assert "text_original" in long_tw, "长推应保留 text_original"
    assert "core_excerpt" in long_tw, "长推应有 core_excerpt"
    assert long_tw["text_original"] == FIXTURE_SYNTH["tweets"][1]["text"]


def test_core_excerpt_substring_guard():
    """core_excerpt_en 非子串时回退到首段。"""
    from akb2.briefing.translate import translate_briefing

    bad_response = {
        **MOCK_TRANSLATE_RESPONSE,
        "tweets": [
            {"idx": 0, "text_zh": "短推文译文。"},
            {
                "idx": 1,
                "text_zh": "长推文译文。",
                "core_excerpt_en": "This text does NOT appear in the original tweet at all!",
            },
        ],
    }

    with patch("akb2.briefing.synthesis._call_llm", side_effect=_make_mock_call_llm(bad_response)):
        result = translate_briefing(FIXTURE_SYNTH)

    long_tw = result["tweets"][1]
    assert "core_excerpt" in long_tw
    # 回退首段不应包含编造内容
    assert "does NOT appear" not in long_tw["core_excerpt"]


# ---------------------------------------------------------------------------
# SC-3: 关键引述 context 填中文点题，不截断 quote
# ---------------------------------------------------------------------------

def test_key_quotes_context_filled():
    """key_quotes context 由翻译阶段填入中文点题。"""
    from akb2.briefing.translate import translate_briefing

    with patch("akb2.briefing.synthesis._call_llm", side_effect=_make_mock_call_llm(MOCK_TRANSLATE_RESPONSE)):
        result = translate_briefing(FIXTURE_SYNTH)

    kq = result["key_quotes"][0]
    assert kq["context"] != ""
    assert any("一" <= c <= "鿿" for c in kq["context"]), "context 应包含中文"
    # quote 原文保留英文，不截断
    assert kq["quote"] == "You can now give AI agents infinite memory with agentmemory."
    assert "…" not in kq["quote"]


# ---------------------------------------------------------------------------
# SC-7: LLM 不可用时优雅降级，不崩
# ---------------------------------------------------------------------------

def test_translate_degrades_without_llm():
    """LLM 调用失败时返回原始 synth，不抛异常。"""
    from akb2.briefing.translate import translate_briefing

    def raise_error(messages, model, **kwargs):
        raise ConnectionError("no network")

    with patch("akb2.briefing.synthesis._call_llm", side_effect=raise_error):
        result = translate_briefing(FIXTURE_SYNTH)

    # 降级：返回原始 synth
    assert result["executive_summary"] == FIXTURE_SYNTH["executive_summary"]
    assert result["tweets"][0]["text"] == FIXTURE_SYNTH["tweets"][0]["text"]


# ---------------------------------------------------------------------------
# 短推文：不产出 core_excerpt，text_original
# ---------------------------------------------------------------------------

def test_missing_core_excerpt_zh_triggers_snippet_translate():
    """core_excerpt_en 通过子串校验但无 core_excerpt_zh 时，应单独翻译该核心段（Codex F2 回归）。"""
    import copy
    from akb2.briefing.translate import translate_briefing

    synth = copy.deepcopy(FIXTURE_SYNTH)
    core_en = "This is the core insight about AI agents and memory systems."
    synth["tweets"][1]["text"] = "B" * 600 + " " + core_en

    # 模型返回 core_excerpt_en 但不返回 core_excerpt_zh
    response_no_core_zh = {
        **MOCK_TRANSLATE_RESPONSE,
        "tweets": [
            {"idx": 0, "text_zh": "短推文。"},
            {
                "idx": 1,
                "text_zh": "B" * 30 + "... 长文译文开头噪声...",
                "core_excerpt_en": core_en,
                # 无 core_excerpt_zh
            },
        ],
    }

    snippet_calls = []

    def mock_call_llm(messages, model, **kwargs):
        # 第一次调用是批量翻译，后续调用是 _translate_snippet
        if len(snippet_calls) == 0:
            snippet_calls.append("batch")
            return json.dumps(response_no_core_zh, ensure_ascii=False)
        else:
            snippet_calls.append("snippet")
            return "这是关于 AI 智能体和记忆系统的核心洞察。"  # snippet 翻译结果

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_call_llm):
        result = translate_briefing(synth)

    assert "snippet" in snippet_calls, "缺 core_excerpt_zh 时应触发 _translate_snippet 单独翻译"
    long_tw = result["tweets"][1]
    assert "core_excerpt" in long_tw
    assert "核心洞察" in long_tw["core_excerpt"], \
        f"core_excerpt 应包含核心段译文，实际: {long_tw['core_excerpt']!r}"


def test_short_tweet_no_excerpt():
    """短推文(<500字)不应产出 core_excerpt 或 text_original（非 is_long）。"""
    from akb2.briefing.translate import translate_briefing

    with patch("akb2.briefing.synthesis._call_llm", side_effect=_make_mock_call_llm(MOCK_TRANSLATE_RESPONSE)):
        result = translate_briefing(FIXTURE_SYNTH)

    short_tw = result["tweets"][0]
    assert "core_excerpt" not in short_tw


# ---------------------------------------------------------------------------
# Codex F1: 长推核心段使用模型直接翻译的核心段译文（不取整条译文前段）
# ---------------------------------------------------------------------------

def test_core_excerpt_uses_selected_core_segment():
    """核心英文段在原文末尾时，core_excerpt 应使用模型直接翻译该段，而非整条译文前 400 字。"""
    import copy
    from akb2.briefing.translate import translate_briefing

    # 核心段在原文末尾（前 600 字是噪声，核心内容在后半部分）
    synth_with_tail_core = copy.deepcopy(FIXTURE_SYNTH)
    tail_core_en = "This is the core insight about AI agents and memory systems."
    synth_with_tail_core["tweets"][1]["text"] = "B" * 600 + " " + tail_core_en

    response_with_core_zh = {
        **MOCK_TRANSLATE_RESPONSE,
        "tweets": [
            {"idx": 0, "text_zh": "短推文。"},
            {
                "idx": 1,
                "text_zh": "B" * 30 + "...这是一条关于 AI 智能体的长文译文开头...",  # 前段无核心含义
                "core_excerpt_en": tail_core_en,
                "core_excerpt_zh": "这是关于 AI 智能体和记忆系统的核心洞察。",  # 模型直接翻译核心段
            },
        ],
    }

    with patch("akb2.briefing.synthesis._call_llm", side_effect=_make_mock_call_llm(response_with_core_zh)):
        result = translate_briefing(synth_with_tail_core)

    long_tw = result["tweets"][1]
    assert "core_excerpt" in long_tw
    # core_excerpt 应是模型直接翻译的核心段，不应包含开头的噪声内容
    assert "这是关于 AI 智能体和记忆系统的核心洞察" in long_tw["core_excerpt"], \
        f"core_excerpt 应包含核心段译文，实际: {long_tw['core_excerpt'][:100]!r}"
    # 不应把开头 BBB... 错误地作为核心段
    assert long_tw["core_excerpt"][:5] != "B" * 5, \
        "core_excerpt 不应以噪声开头（取错了整条译文前段）"
