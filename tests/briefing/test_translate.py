"""test_translate.py — translate.py 单元测试。"""
from __future__ import annotations

import json
import re
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
    "tweets": [
        {"idx": 0, "text_zh": "关于 agentmemory 的简短推文。", "zh_point": "记忆工具值得关注。"},
        {
            "idx": 1,
            "text_zh": "关于 AI 智能体和记忆系统核心洞察的长文推文译文。",
            "zh_point": "长文核心是记忆系统突破。",
            "core_excerpt_en": "This is the core insight about AI agents and memory systems.",
            "core_excerpt_zh": "这是关于 AI 智能体和记忆系统的核心洞察。",  # 模型直接翻译核心段
        },
    ],
}


_ITEM_IDX_RE = re.compile(r"TRANSLATE_ITEM_IDX: (\d+)")


def _requested_tweet_idx(messages) -> int | None:
    """从 prompt 里读出本次调用请求的推文 idx（None = 分析字段调用）。"""
    m = _ITEM_IDX_RE.search(messages[-1]["content"])
    return int(m.group(1)) if m else None


def _make_mock_call_llm(response: dict):
    """按条目级隔离后的调用结构派发预置响应。

    translate_briefing 现在发 1 次分析字段调用 + 每条展示推文各 1 次调用，
    因此 mock 必须按 prompt 里的 TRANSLATE_ITEM_IDX 返回对应条目；
    未预置的 idx 返回空对象（= 该条被拒收，走降级路径）。
    """
    def mock_call_llm(messages, model, **kwargs):
        idx = _requested_tweet_idx(messages)
        if idx is None:
            analysis = {k: v for k, v in response.items() if k != "tweets"}
            return json.dumps(analysis, ensure_ascii=False)
        item = next(
            (t for t in response.get("tweets", []) if t.get("idx") == idx), None
        )
        return json.dumps(item or {}, ensure_ascii=False)
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
# SC-5: translate 覆盖展示集合（被引用 ∪ 孤儿高分）
# ---------------------------------------------------------------------------

def test_key_quotes_translated_and_context_filled():
    """key_quotes 已从 pipeline 移除（SC-9）；translate 不再处理 key_quotes，字段原样保留。"""
    from akb2.briefing.translate import translate_briefing

    with patch("akb2.briefing.synthesis._call_llm", side_effect=_make_mock_call_llm(MOCK_TRANSLATE_RESPONSE)):
        result = translate_briefing(FIXTURE_SYNTH)

    # key_quotes 字段原样（不被翻译，不被删除）
    if "key_quotes" in result:
        kq = result["key_quotes"][0]
        # quote 不应被翻译（translate 不再处理）
        assert kq["quote"] == FIXTURE_SYNTH["key_quotes"][0]["quote"], "key_quotes.quote 应原样保留"


def test_translate_covers_referenced_and_orphan():
    """展示集合中的推文（被引用 + 孤儿高分）都应有 zh_point。"""
    import copy

    from akb2.briefing.translate import translate_briefing

    # synth 有 3 条推文：id=1 被主题引用，id=2 孤儿高分，id=3 孤儿低分（不在展示集合）
    synth = copy.deepcopy(FIXTURE_SYNTH)
    synth["tweets"].append({
        "id": "3", "author": "@low", "text": "Low score tweet.",
        "created_at": "2026-05-30T03:00:00Z", "score": 0.1, "url": "https://x.com/3",
    })

    mock_response = {
        **MOCK_TRANSLATE_RESPONSE,
        "tweets": [
            {"idx": 0, "text_zh": "简短推文。", "zh_point": "记忆工具值得关注。"},
            {"idx": 1, "text_zh": "长推译文。", "zh_point": "长文核心。",
             "core_excerpt_en": "This is the core insight about AI agents and memory systems.",
             "core_excerpt_zh": "这是核心洞察。"},
        ],
    }

    with patch("akb2.briefing.synthesis._call_llm", side_effect=_make_mock_call_llm(mock_response)):
        result = translate_briefing(synth)

    # 被引用的 id=1 (idx=0) 应有 zh_point
    t0 = result["tweets"][0]
    assert t0.get("zh_point"), "被主题引用的推文应有 zh_point"
    assert any("一" <= c <= "鿿" for c in t0["zh_point"]), "zh_point 应为中文"

    # 孤儿高分 id=2 (idx=1) 应有 zh_point
    t1 = result["tweets"][1]
    assert t1.get("zh_point"), "孤儿高分推文应有 zh_point"


def test_display_tweet_indices_basic():
    """_display_tweet_indices 返回被引用 + 孤儿高分的下标。"""
    from akb2.briefing.translate import _display_tweet_indices

    synth = {
        "focus_discussions": [
            {"supporting_tweet_ids": ["1", "2"]},
        ],
        "tweets": [
            {"id": "1", "score": 0.9},  # idx=0, 被引用
            {"id": "2", "score": 0.8},  # idx=1, 被引用
            {"id": "3", "score": 0.7},  # idx=2, 孤儿高分
            {"id": "4", "score": 0.1},  # idx=3, 孤儿低分（orphan_top_k=2 时不入选）
        ],
    }
    indices = _display_tweet_indices(synth, orphan_top_k=1)
    assert 0 in indices, "被引用 idx=0 应在展示集合"
    assert 1 in indices, "被引用 idx=1 应在展示集合"
    assert 2 in indices, "孤儿高分 idx=2 应在展示集合"
    assert 3 not in indices, "孤儿低分 idx=3 不在展示集合（top_k=1 只取最高1个孤儿）"


def test_display_tweet_indices_nonexistent_id_skipped():
    """supporting_tweet_ids 里不存在的 id 被跳过（防编造）。"""
    from akb2.briefing.translate import _display_tweet_indices

    synth = {
        "focus_discussions": [{"supporting_tweet_ids": ["99", "1"]}],
        "tweets": [{"id": "1", "score": 0.9}],
    }
    indices = _display_tweet_indices(synth, orphan_top_k=0)
    assert 0 in indices, "真实 id=1(idx=0) 应在"
    assert len(indices) == 1, "id=99 不存在，应跳过"


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
    batch_mock = _make_mock_call_llm(response_no_core_zh)

    def mock_call_llm(messages, model, **kwargs):
        # 分析调用 + 逐条推文调用走 batch_mock；_translate_snippet 是 json_mode=False
        if kwargs.get("json_mode", True):
            return batch_mock(messages, model, **kwargs)
        snippet_calls.append("snippet")
        return "这是关于 AI 智能体和记忆系统的核心洞察。"  # snippet 翻译结果

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_call_llm):
        result = translate_briefing(synth)

    assert "snippet" in snippet_calls, "缺 core_excerpt_zh 时应触发 _translate_snippet 单独翻译"
    long_tw = result["tweets"][1]
    assert "core_excerpt" in long_tw
    assert "核心洞察" in long_tw["core_excerpt"], \
        f"core_excerpt 应包含核心段译文，实际: {long_tw['core_excerpt']!r}"


def test_snippet_translate_failure_no_english_core_excerpt():
    """_translate_snippet 失败时 core_excerpt 不应回退英文原段（Codex R3 F1 回归）。"""
    import copy

    from akb2.briefing.translate import translate_briefing

    synth = copy.deepcopy(FIXTURE_SYNTH)
    core_en = "This is the core insight about AI agents and memory systems."
    synth["tweets"][1]["text"] = "B" * 600 + " " + core_en

    response_no_core_zh = {
        **MOCK_TRANSLATE_RESPONSE,
        "tweets": [
            {"idx": 0, "text_zh": "短推文。"},
            {
                "idx": 1,
                "text_zh": "长文译文，包含有意义的中文内容，关于 AI 智能体的分析。",
                "core_excerpt_en": core_en,
                # 无 core_excerpt_zh
            },
        ],
    }

    batch_mock = _make_mock_call_llm(response_no_core_zh)

    def mock_call_llm(messages, model, **kwargs):
        if kwargs.get("json_mode", True):
            return batch_mock(messages, model, **kwargs)
        # _translate_snippet 模拟失败（全模型链）
        raise ConnectionError("snippet translate failed")

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_call_llm):
        result = translate_briefing(synth)

    # 补译全链失败必须回传降级标记（失败可见），不得静默吞掉
    assert result.get("translate_degraded") is True, "snippet 全链失败应标 translate_degraded"
    assert any(
        "core_excerpt" in f for f in result.get("translate_failed_fields", [])
    ), f"应记录失败字段，实际: {result.get('translate_failed_fields')}"

    long_tw = result["tweets"][1]
    core = long_tw.get("core_excerpt", "")
    # 核心段不应是英文原段
    assert core != core_en, "snippet 失败时不应回退英文原段"
    # 应是中文（取自 text_zh 首段）或空
    if core:
        assert any("一" <= c <= "鿿" for c in core), f"core_excerpt 应含中文，实际: {core!r}"


def test_audit_checks_core_excerpt_english():
    """audit._check_standard 应检出英文 core_excerpt（Codex R3 F1 回归）。"""
    from akb2.briefing.audit import _check_standard

    synth = {
        "executive_summary": "今日 AI 智能体增长强劲。",
        "focus_discussions": [{"topic": "智能体记忆", "summary": "多款工具涌现。", "supporting_tweet_ids": ["1"]}],
        "key_quotes": [{"author": "@x", "quote": "q", "tweet_id": "1", "url": "", "context": "有参考价值。"}],
        "tweets": [
            {
                "id": "1", "author": "@x",
                "text": "关于 AI 的中文推文。",
                "core_excerpt": "This is an English core excerpt that was not translated.",
                "created_at": "", "score": 0.9, "url": "",
            },
        ],
    }
    issues = _check_standard(synth)
    assert any("core_excerpt" in i for i in issues), f"应检出英文 core_excerpt，issues={issues}"


def test_malformed_idx_does_not_crash():
    """模型返回 idx 为列表等非法结构时，translate_briefing 不崩溃（Codex R4 F1 回归）。"""
    from akb2.briefing.translate import translate_briefing

    malformed_response = {
        "executive_summary": "今日 AI 趋势。",
        "focus_discussions": [
            {"idx": [0, 1], "topic": "MALFORMED", "summary": "bad"},  # idx 是 list，非 int
        ],
        "key_quotes": [{"idx": "zero", "context": "点题"}],  # idx 是字符串
        "tweets": [
            {"idx": None, "text_zh": "推文。"},  # idx 是 None
        ],
    }

    with patch("akb2.briefing.synthesis._call_llm",
               side_effect=lambda m, model, **kw: json.dumps(malformed_response, ensure_ascii=False)):
        result = translate_briefing(FIXTURE_SYNTH)

    # 不崩溃，至少返回合法 dict
    assert isinstance(result, dict)
    assert "tweets" in result


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


# ---------------------------------------------------------------------------
# P2b: 超时降低 + topic 残英重译
# ---------------------------------------------------------------------------

def test_translate_model_chain_and_timeout():
    """SC-P2b-1 + SC-3: 翻译主力=deepseek（非 preview，constitution 原则 7），
    备用链=[glm, anthropic/claude]（跨网关），默认 timeout=300（可经 AKB2_TRANSLATE_TIMEOUT 覆盖）。

    7×24 管线禁用 -preview 模型；默认主力取稳定 deepseek，备用按不同厂商排 glm→claude。
    """
    import inspect

    from akb2.briefing import translate as tr

    assert tr._TRANSLATE_MODEL == "deepseek/deepseek-v4-pro", \
        f"翻译主力应为 deepseek（非 preview），实际 {tr._TRANSLATE_MODEL}"
    assert "preview" not in tr._TRANSLATE_MODEL, "7x24 管线禁用 preview 模型"
    assert tr._TRANSLATE_FALLBACK_MODELS == [
        "z-ai/glm-5.1", "anthropic/claude-sonnet-4-6"
    ], f"备用链应为 [glm, anthropic/claude]，实际 {tr._TRANSLATE_FALLBACK_MODELS}"

    sig = inspect.signature(tr.translate_briefing)
    assert sig.parameters["timeout"].default == 300, \
        f"默认 timeout 应为 300s，实际 {sig.parameters['timeout'].default}s"


def test_translate_falls_through_chain_to_second_fallback():
    """主(deepseek)+备1(glm) 都失败 → 用备2(claude)；成功的模型被提到链首复用。

    条目级隔离后单次运行有 1+N 次调用，若每次都从已知不可用的主力重试，
    失败开销会被放大 N 倍；故断言：首次按链序尝试，后续调用直接打已成功模型。
    """
    from akb2.briefing.translate import translate_briefing

    synth = {
        "executive_summary": "Summary.",
        "focus_discussions": [{"topic": "T", "summary": "S.", "supporting_tweet_ids": ["1"]}],
        "tweets": [{"id": "1", "author": "@x", "text": "short tweet", "score": 0.9, "url": "u"}],
    }
    calls: list[str] = []
    payload = {
        "executive_summary": "今日摘要。",
        "focus_discussions": [{"idx": 0, "topic": "智能体话题", "summary": "中文总结。"}],
        "tweets": [{"idx": 0, "text_zh": "短推文中文。", "zh_point": "要点。"}],
    }
    mock = _make_mock_call_llm(payload)

    def fake(messages, model, **kw):
        calls.append(model)
        if model in ("deepseek/deepseek-v4-pro", "z-ai/glm-5.1"):
            raise TimeoutError("read timed out")
        return mock(messages, model, **kw)

    with patch("akb2.briefing.synthesis._call_llm", side_effect=fake):
        result = translate_briefing(synth)

    assert calls[:3] == [
        "deepseek/deepseek-v4-pro", "z-ai/glm-5.1", "anthropic/claude-sonnet-4-6"
    ], f"首次应按 [deepseek, glm, claude] 顺序尝试，实际: {calls}"
    assert calls[3:] == ["anthropic/claude-sonnet-4-6"], \
        f"后续条目调用应直接复用已成功模型，实际: {calls}"
    assert result["focus_discussions"][0]["topic"] == "智能体话题", "备2 成功后翻译应生效"
    assert result["tweets"][0]["text"] == "短推文中文。", "推文条目调用也应生效"


def test_fallback_gateway_follows_model_identity(monkeypatch):
    """备用模型即便被提到链首复用，仍必须走备用网关（按模型身份判，不按链内位置）。"""
    from akb2.briefing import translate as tr

    monkeypatch.setattr(tr, "_TRANSLATE_FALLBACK_BASE_URL", "https://backup.example/v1")
    seen: list[tuple[str, object]] = []
    mock = _make_mock_call_llm({
        "executive_summary": "今日有两条独立讨论。",
        "focus_discussions": [{"idx": 0, "topic": "对齐", "summary": "两种观点。"}],
        "tweets": [
            {"idx": 0, "text_zh": "译文0。", "zh_point": "点题0。"},
            {"idx": 1, "text_zh": "译文1。", "zh_point": "点题1。"},
        ],
    })

    def fake(messages, model, **kw):
        seen.append((model, kw.get("base_url")))
        if model == tr._TRANSLATE_MODEL:
            raise TimeoutError("primary down")
        return mock(messages, model, **kw)

    with patch("akb2.briefing.synthesis._call_llm", side_effect=fake):
        tr.translate_briefing(CONTAMINATION_SYNTH)

    fallback_calls = [(m, u) for m, u in seen if m == "z-ai/glm-5.1"]
    assert len(fallback_calls) >= 3, f"分析 + 2 条推文都应打到备用模型，实际 {seen}"
    assert all(u == "https://backup.example/v1" for _, u in fallback_calls), seen


def test_translate_all_models_fail_returns_original():
    """三档全失败 → 返回原始 synth 不抛异常，且确实尝试了全部 3 个模型（1 主力 + 2 备用）。"""
    from akb2.briefing.translate import translate_briefing

    synth = {
        "executive_summary": "Summary.",
        "focus_discussions": [{"topic": "T", "summary": "S.", "supporting_tweet_ids": ["1"]}],
        "tweets": [{"id": "1", "author": "@x", "text": "short tweet", "score": 0.9, "url": "u"}],
    }
    calls: list[str] = []

    def always_fail(messages, model, **kw):
        calls.append(model)
        raise ConnectionError("down")

    with patch("akb2.briefing.synthesis._call_llm", side_effect=always_fail):
        result = translate_briefing(synth)

    assert len(calls) == 3, f"应尝试全部 3 个模型（1 主力 + 2 备用），实际 {calls}"
    assert result["executive_summary"] == "Summary.", "全失败应返回原始 synth"


# ---------------------------------------------------------------------------
# SC-3: 7×24 管线禁用 preview 模型 — 默认无 preview + 显式 preview 时 fail fast
# ---------------------------------------------------------------------------

def test_default_translate_model_has_no_preview():
    """SC-3: 默认翻译主力与备用链均不含 preview（constitution 原则 7）。"""
    from akb2.briefing import translate as tr

    assert "preview" not in tr._TRANSLATE_MODEL
    assert all("preview" not in m for m in tr._TRANSLATE_FALLBACK_MODELS)


def test_preview_translate_model_fails_fast(monkeypatch, capsys):
    """SC-3 验收3: AKB2_TRANSLATE_MODEL 含 preview → fail fast + stderr 含 forbidden。"""
    from akb2.briefing import pipeline

    monkeypatch.setenv("AKB2_TRANSLATE_MODEL", "gemini-3.1-pro-preview")
    with pytest.raises(SystemExit) as exc:
        pipeline._assert_no_preview_translate_models()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "preview model is forbidden for 7x24 pipeline" in err


def test_preview_fallback_model_fails_fast(monkeypatch, capsys):
    """SC-3: AKB2_TRANSLATE_FALLBACK_MODELS 含 preview → 同样 fail fast。"""
    from akb2.briefing import pipeline

    monkeypatch.setenv(
        "AKB2_TRANSLATE_FALLBACK_MODELS",
        "z-ai/glm-5.1,some-vendor/preview-model",
    )
    with pytest.raises(SystemExit) as exc:
        pipeline._assert_no_preview_translate_models()
    assert exc.value.code == 1
    assert "forbidden" in capsys.readouterr().err


def test_no_preview_env_passes(monkeypatch):
    """SC-3: 不设 preview 变量时不触发 fail fast（默认 deepseek 通过，不抛 SystemExit）。"""
    from akb2.briefing import pipeline

    monkeypatch.delenv("AKB2_TRANSLATE_MODEL", raising=False)
    monkeypatch.delenv("AKB2_TRANSLATE_FALLBACK_MODELS", raising=False)
    pipeline._assert_no_preview_translate_models()  # 不抛异常即通过


def test_translate_topic_retranslated_if_english():
    """_apply_translation_inner：topic 翻译结果含英文时，触发 _translate_snippet 补译。"""
    import copy
    from unittest.mock import patch as _patch

    from akb2.briefing import translate as tr
    from akb2.briefing.translate import _apply_translation

    synth = copy.deepcopy(FIXTURE_SYNTH)
    synth["focus_discussions"] = [
        {"topic": "原始英文topic", "summary": "第一句。第二句。第三句。", "supporting_tweet_ids": ["1"]},
    ]

    trans = {
        "executive_summary": "今日摘要。",
        "focus_discussions": [
            {"idx": 0, "topic": "Agent Memory Infrastructure", "summary": "智能体记忆基础设施正在快速发展。"},
        ],
        "tweets": [{"idx": 0, "text_zh": "短推文中文。", "zh_point": "要点。"}],
    }

    snippets_called: list[str] = []

    def fake_snippet(text_en: str, timeout: int = 60, **kwargs) -> str:
        snippets_called.append(text_en)
        return "智能体记忆基础设施"

    with _patch.object(tr, "_translate_snippet", side_effect=fake_snippet):
        result = _apply_translation(synth, trans)

    assert snippets_called, "残英 topic 应触发 _translate_snippet"
    fd = result["focus_discussions"][0]
    assert "智能体记忆基础设施" in fd["topic"], f"topic 应被补译，实际: {fd['topic']!r}"


def test_translate_topic_with_product_name_not_retranslated():
    """P2b-2 误报回归：含产品名/版本号但已正确翻译的 topic 不应被误判残英而重译。

    'vLLM v0.22.0 发布与推理优化' 中文占比仅 ~28%（产品名占了大半），但它是正确翻译，
    旧的 0.4 比例阈值会误触发重译，浪费调用且可能改坏。改用绝对中文字数判断。
    """
    import copy
    from unittest.mock import patch as _patch

    from akb2.briefing import translate as tr
    from akb2.briefing.translate import _apply_translation

    synth = copy.deepcopy(FIXTURE_SYNTH)
    synth["focus_discussions"] = [
        {"topic": "init", "summary": "第一句。第二句。第三句。", "supporting_tweet_ids": ["1"]},
    ]
    trans = {
        "executive_summary": "今日摘要。",
        "focus_discussions": [{"idx": 0, "topic": "vLLM v0.22.0 发布与推理优化", "summary": "已是中文。"}],
        "tweets": [{"idx": 0, "text_zh": "短推文中文。", "zh_point": "要点。"}],
    }
    snippets_called: list[str] = []
    with _patch.object(tr, "_translate_snippet", side_effect=lambda t, **k: snippets_called.append(t) or ""):
        result = _apply_translation(synth, trans)

    assert not snippets_called, f"含产品名的正确中文 topic 不应触发重译，调了: {snippets_called}"
    assert result["focus_discussions"][0]["topic"] == "vLLM v0.22.0 发布与推理优化", "topic 应原样保留"


def test_translate_topic_chinese_no_retranslate():
    """topic 翻译结果已是中文时，不调 _translate_snippet（无多余 LLM 调用）。"""
    import copy
    from unittest.mock import patch as _patch

    from akb2.briefing import translate as tr
    from akb2.briefing.translate import _apply_translation

    synth = copy.deepcopy(FIXTURE_SYNTH)
    synth["focus_discussions"] = [
        {"topic": "初始topic", "summary": "第一句。第二句。第三句。", "supporting_tweet_ids": ["1"]},
    ]
    trans = {
        "executive_summary": "今日摘要。",
        "focus_discussions": [{"idx": 0, "topic": "智能体长记忆基础设施", "summary": "已是中文。"}],
        "tweets": [{"idx": 0, "text_zh": "短推文中文。", "zh_point": "要点。"}],
    }

    snippets_called: list[str] = []

    with _patch.object(tr, "_translate_snippet", side_effect=lambda t, **k: snippets_called.append(t) or ""):
        result = _apply_translation(synth, trans)

    assert not snippets_called, f"中文 topic 不应调 _translate_snippet，调了: {snippets_called}"
    assert result["focus_discussions"][0]["topic"] == "智能体长记忆基础设施"


# ---------------------------------------------------------------------------
# P3a: lang=zh 推文跳过 text 翻译，lang=en 正常翻译
# ---------------------------------------------------------------------------

def test_lang_zh_tweet_skips_text_translation():
    """SC-P3a-2: lang=zh 的推文不替换 text（中文原文保留），zh_point 仍可写入。"""
    import copy

    from akb2.briefing.translate import _apply_translation

    synth = copy.deepcopy(FIXTURE_SYNTH)
    synth["tweets"][0]["lang"] = "zh"
    synth["tweets"][0]["text"] = "这是中文原文，不应被翻译覆盖。"
    original_zh_text = synth["tweets"][0]["text"]

    trans = {
        "executive_summary": "今日摘要。",
        "focus_discussions": [{"idx": 0, "topic": "智能体", "summary": "已是中文。"}],
        "tweets": [
            # 模型返回了 zh 推文的 text_zh（但应被忽略）
            {"idx": 0, "text_zh": "错误覆盖", "zh_point": "中文推文要点。"},
            {"idx": 1, "text_zh": "英文推文的中文译文。", "zh_point": "英文推文要点。"},
        ],
    }

    result = _apply_translation(synth, trans)

    zh_tweet = result["tweets"][0]
    assert zh_tweet["text"] == original_zh_text, \
        f"lang=zh 的 text 不应被翻译覆盖，实际: {zh_tweet['text']!r}"
    assert "text_original" not in zh_tweet, "lang=zh 不应设置 text_original"
    assert zh_tweet.get("zh_point") == "中文推文要点。", "lang=zh 仍应写入 zh_point"


def test_lang_zh_display_tweet_does_not_call_llm():
    """lang=zh 展示推文不发翻译调用：中文原文已过 audit，zh_point 由确定性截取生成。

    2026-09-02 主跑 85 条里约一半是中文账号；条目级隔离仍对它们打 120s LLM，
    把 900s 总预算烧在不需要翻译的条目上。
    """
    from akb2.briefing.translate import translate_briefing

    synth = {
        "executive_summary": "Summary.",
        "focus_discussions": [
            {"topic": "T", "summary": "S.", "supporting_tweet_ids": ["zh", "en"]},
        ],
        "tweets": [
            {
                "id": "zh",
                "author": "@dotey",
                "lang": "zh",
                "text": "这是中文原文，不该再送去翻译。",
                "score": 0.9,
                "url": "u1",
            },
            {
                "id": "en",
                "author": "@sama",
                "lang": "en",
                "text": "English tweet that still needs translation.",
                "score": 0.8,
                "url": "u2",
            },
        ],
    }
    requested: list[int | None] = []
    mock = _make_mock_call_llm({
        "executive_summary": "今日摘要。",
        "focus_discussions": [{"idx": 0, "topic": "主题", "summary": "中文总结。"}],
        "tweets": [
            {"idx": 1, "text_zh": "仍需翻译的英文推文。", "zh_point": "英文看点。"},
        ],
    })

    def spy(messages, model, **kwargs):
        requested.append(_requested_tweet_idx(messages))
        return mock(messages, model, **kwargs)

    with patch("akb2.briefing.synthesis._call_llm", side_effect=spy):
        result = translate_briefing(synth)

    assert 0 not in requested, f"lang=zh 推文不该发 LLM，实际请求 idx={requested}"
    assert 1 in requested, f"英文推文仍应翻译，实际请求 idx={requested}"
    zh = result["tweets"][0]
    assert zh["text"] == "这是中文原文，不该再送去翻译。"
    assert "text_original" not in zh
    assert zh.get("zh_point"), "中文推文仍需中文点题，否则展示集 audit 会因缺 zh_point 拒发"


def test_translate_caps_http_timeout_to_remaining_budget():
    """单次 HTTP timeout 不得超过剩余总预算，避免一次 analysis 把整天额度烧光。"""
    from akb2.briefing import translate as tr

    synth = {
        "executive_summary": "Summary.",
        "focus_discussions": [
            {"topic": "T", "summary": "S.", "supporting_tweet_ids": ["1"]},
        ],
        "tweets": [
            {"id": "1", "author": "@x", "text": "short tweet", "score": 0.9, "url": "u"},
        ],
    }
    timeouts: list[int] = []

    def fake(messages, model, **kwargs):
        timeouts.append(int(kwargs["timeout"]))
        raise TimeoutError("read timed out")

    with patch.object(tr, "_TRANSLATE_TOTAL_TIME_BUDGET_S", 90), \
         patch.object(tr, "_DEFAULT_TRANSLATE_TIMEOUT", 300), \
         patch.object(tr, "_TWEET_TIMEOUT_CAP", 120), \
         patch("akb2.briefing.synthesis._call_llm", side_effect=fake):
        result = tr.translate_briefing(synth, timeout=300)

    assert timeouts, "至少应发起一次 analysis 调用"
    assert all(t <= 90 for t in timeouts), (
        f"单次 timeout 必须 ≤ 剩余 90s 预算，实际 {timeouts}"
    )
    assert result.get("translate_failed") is True


def test_analysis_http_timeout_capped_like_tweets():
    """analysis 不得用 300s 默认超时：DeepSeek 一挂就烧掉 1/3 的 900s 总预算。"""
    from akb2.briefing.translate import translate_briefing

    synth = {
        "executive_summary": "Summary.",
        "focus_discussions": [
            {"topic": "T", "summary": "S.", "supporting_tweet_ids": ["1"]},
        ],
        "tweets": [
            {"id": "1", "author": "@x", "text": "short tweet", "score": 0.9, "url": "u"},
        ],
    }
    analysis_timeouts: list[int] = []
    mock = _make_mock_call_llm({
        "executive_summary": "今日摘要。",
        "focus_discussions": [{"idx": 0, "topic": "主题", "summary": "中文总结。"}],
        "tweets": [{"idx": 0, "text_zh": "短推文。", "zh_point": "看点。"}],
    })

    def spy(messages, model, **kwargs):
        if _requested_tweet_idx(messages) is None:
            analysis_timeouts.append(int(kwargs["timeout"]))
        return mock(messages, model, **kwargs)

    with patch("akb2.briefing.synthesis._call_llm", side_effect=spy):
        translate_briefing(synth, timeout=300)

    assert analysis_timeouts == [120], (
        f"analysis HTTP timeout 应封顶 120s，实际 {analysis_timeouts}"
    )


def test_translate_disables_deepseek_thinking():
    """翻译调用必须关掉 DeepSeek thinking，否则单条 75s+ 会把 900s 预算烧光。"""
    from akb2.briefing.translate import translate_briefing

    extra_bodies: list[object] = []
    mock = _make_mock_call_llm({
        "executive_summary": "今日摘要。",
        "focus_discussions": [{"idx": 0, "topic": "主题", "summary": "中文总结。"}],
        "tweets": [{"idx": 0, "text_zh": "短推文。", "zh_point": "看点。"}],
    })

    def spy(messages, model, **kwargs):
        extra_bodies.append(kwargs.get("extra_body"))
        return mock(messages, model, **kwargs)

    with patch("akb2.briefing.synthesis._call_llm", side_effect=spy):
        translate_briefing({
            "executive_summary": "Summary.",
            "focus_discussions": [
                {"topic": "T", "summary": "S.", "supporting_tweet_ids": ["1"]},
            ],
            "tweets": [
                {"id": "1", "author": "@x", "text": "short tweet", "score": 0.9, "url": "u"},
            ],
        })

    assert extra_bodies, "应至少发起一次翻译调用"
    assert all(body == {"enable_thinking": False} for body in extra_bodies), (
        f"翻译必须传 enable_thinking=False，实际 {extra_bodies}"
    )


# ---------------------------------------------------------------------------
# #15 拆掉批量翻译的串味通道（条目级隔离）
# ---------------------------------------------------------------------------

CONTAMINATION_SYNTH = {
    "executive_summary": "Two separate threads today.",
    "focus_discussions": [
        {"topic": "Alignment", "summary": "Two takes.", "supporting_tweet_ids": ["1", "2"]},
    ],
    "tweets": [
        {"id": "1", "author": "@AnthropicAI", "score": 0.9, "url": "u1",
         "text": "New paper: steering large models toward helpful behavior with synthetic self-critique."},
        {"id": "2", "author": "@paulfchristiano", "score": 0.8, "url": "u2",
         "text": "Hot take: the 'alignment tax' is gone."},
    ],
}


def test_each_tweet_translated_in_isolated_call():
    """每条推文单独一次调用，且该次调用的输入里不含其它推文正文。

    这是串味通道被切断的可观测判据：实测事故是 @AnthropicAI 的译文里被塞进同批次
    @paulfchristiano 推文的「消除对齐税」——只要两条正文不再同时进入一次调用，
    这条污染路径就不存在了。
    """
    from akb2.briefing.translate import translate_briefing

    prompts: list[str] = []
    payload = {
        "executive_summary": "今日有两条独立讨论。",
        "focus_discussions": [{"idx": 0, "topic": "对齐", "summary": "两种观点。"}],
        "tweets": [
            {"idx": 0, "text_zh": "新论文：用合成自我批评引导大模型行为。", "zh_point": "论文要点。"},
            {"idx": 1, "text_zh": "观点：对齐税已经消失。", "zh_point": "对齐税消失。"},
        ],
    }
    mock = _make_mock_call_llm(payload)

    def spy(messages, model, **kwargs):
        prompts.append(messages[-1]["content"])
        return mock(messages, model, **kwargs)

    with patch("akb2.briefing.synthesis._call_llm", side_effect=spy):
        result = translate_briefing(CONTAMINATION_SYNTH)

    tweet_prompts = [p for p in prompts if "TRANSLATE_ITEM_IDX" in p]
    assert len(tweet_prompts) == 2, f"两条推文应各发一次调用，实际 {len(tweet_prompts)}"

    p0 = next(p for p in tweet_prompts if "TRANSLATE_ITEM_IDX: 0" in p)
    p1 = next(p for p in tweet_prompts if "TRANSLATE_ITEM_IDX: 1" in p)
    assert "alignment tax" not in p0, "翻译 tweet0 的调用不得看到 tweet1 的内容"
    assert "synthetic self-critique" not in p1, "翻译 tweet1 的调用不得看到 tweet0 的内容"

    assert result["tweets"][0]["text"] == "新论文：用合成自我批评引导大模型行为。"
    assert result["tweets"][1]["text"] == "观点：对齐税已经消失。"


def test_analysis_call_carries_no_tweet_bodies():
    """分析字段调用只带 executive_summary/focus_discussions，不再夹带全部推文正文。"""
    from akb2.briefing.translate import translate_briefing

    prompts: list[str] = []
    mock = _make_mock_call_llm({
        "executive_summary": "今日有两条独立讨论。",
        "focus_discussions": [{"idx": 0, "topic": "对齐", "summary": "两种观点。"}],
        "tweets": [
            {"idx": 0, "text_zh": "译文0。", "zh_point": "点题0。"},
            {"idx": 1, "text_zh": "译文1。", "zh_point": "点题1。"},
        ],
    })

    def spy(messages, model, **kwargs):
        prompts.append(messages[-1]["content"])
        return mock(messages, model, **kwargs)

    with patch("akb2.briefing.synthesis._call_llm", side_effect=spy):
        translate_briefing(CONTAMINATION_SYNTH)

    analysis_prompt = next(p for p in prompts if "TRANSLATE_ITEM_IDX" not in p)
    assert "alignment tax" not in analysis_prompt
    assert "synthetic self-critique" not in analysis_prompt


def test_tweet_response_with_wrong_idx_is_rejected():
    """模型把 A 条的译文绑到 B 条 idx 上 → 拒收该条，不落盘，并标记降级。"""
    from akb2.briefing.translate import translate_briefing

    def wrong_idx(messages, model, **kwargs):
        idx = _requested_tweet_idx(messages)
        if idx is None:
            return json.dumps({
                "executive_summary": "今日有两条独立讨论。",
                "focus_discussions": [{"idx": 0, "topic": "对齐", "summary": "两种观点。"}],
            }, ensure_ascii=False)
        # 永远回一个错的 idx（模拟条目错绑）
        return json.dumps(
            {"idx": idx + 99, "text_zh": "串味译文。", "zh_point": "串味点题。"},
            ensure_ascii=False,
        )

    with patch("akb2.briefing.synthesis._call_llm", side_effect=wrong_idx):
        result = translate_briefing(CONTAMINATION_SYNTH)

    for i, tw in enumerate(result["tweets"]):
        assert tw["text"] == CONTAMINATION_SYNTH["tweets"][i]["text"], \
            "idx 不匹配的译文必须被拒收，不得写入"
        assert "zh_point" not in tw, "被拒收的条目不得落 zh_point"
    assert result["translate_degraded"] is True
    assert result["translate_failed_fields"] == ["tweets[0]", "tweets[1]"], \
        result["translate_failed_fields"]


def test_single_tweet_failure_does_not_lose_other_tweets():
    """一条推文调用失败 → 只该条降级，其余译文照常落盘（部分失败必须可见且局部化）。"""
    from akb2.briefing.translate import translate_briefing

    payload = {
        "executive_summary": "今日有两条独立讨论。",
        "focus_discussions": [{"idx": 0, "topic": "对齐", "summary": "两种观点。"}],
        "tweets": [
            {"idx": 0, "text_zh": "新论文译文。", "zh_point": "论文要点。"},
            {"idx": 1, "text_zh": "对齐税译文。", "zh_point": "对齐税消失。"},
        ],
    }
    mock = _make_mock_call_llm(payload)

    def fail_idx1(messages, model, **kwargs):
        if _requested_tweet_idx(messages) == 1:
            raise ConnectionError("down")
        return mock(messages, model, **kwargs)

    with patch("akb2.briefing.synthesis._call_llm", side_effect=fail_idx1):
        result = translate_briefing(CONTAMINATION_SYNTH)

    assert result["tweets"][0]["text"] == "新论文译文。", "未失败的条目应正常翻译"
    assert result["tweets"][1]["text"] == CONTAMINATION_SYNTH["tweets"][1]["text"], \
        "失败条目保留英文原文"
    assert result["translate_degraded"] is True
    assert result["translate_failed_fields"] == ["tweets[1]"]


def test_all_models_fail_marks_translate_failed():
    """模型链全灭 → 返回值带 translate_failed/translate_degraded（不再是零标记的英文产出）。"""
    from akb2.briefing.translate import translate_briefing

    with patch("akb2.briefing.synthesis._call_llm", side_effect=ConnectionError("down")):
        result = translate_briefing(CONTAMINATION_SYNTH)

    assert result["translate_failed"] is True
    assert result["translate_degraded"] is True
    assert result["translate_failed_fields"] == ["analysis"]
    assert result["executive_summary"] == CONTAMINATION_SYNTH["executive_summary"]


def test_apply_translation_crash_marks_failed():
    """合并阶段异常 → 返回原 synth 但带 translate_failed 标记（不静默返回原文）。"""
    from akb2.briefing import translate as tr

    with patch.object(tr, "_apply_translation_inner", side_effect=RuntimeError("boom")):
        result = tr._apply_translation({"executive_summary": "EN"}, {"executive_summary": "中文"})

    assert result["translate_failed"] is True
    assert "_apply_translation" in result["translate_failed_fields"]


# ---------------------------------------------------------------------------
# 补译 prompt 统一：共用 _TRANSLATE_SYSTEM + 走 fallback 链
# ---------------------------------------------------------------------------

def test_snippet_uses_shared_system_prompt():
    """_translate_snippet 必须用与批量翻译同一套 system prompt（含 URL/术语表/禁改写约束）。"""
    from akb2.briefing.translate import _TRANSLATE_SYSTEM, _translate_snippet

    seen: list[str] = []

    def spy(messages, model, **kwargs):
        seen.append(messages[0]["content"])
        return "译文。"

    with patch("akb2.briefing.synthesis._call_llm", side_effect=spy):
        assert _translate_snippet("Some English core segment.") == "译文。"

    assert seen and seen[0] == _TRANSLATE_SYSTEM, "补译不得再用另一套更弱的 prompt"


def test_snippet_walks_fallback_chain():
    """_translate_snippet 主力失败时必须继续走备用链（此前只打主力，主力挂就返回空）。"""
    from akb2.briefing.translate import _translate_snippet

    calls: list[str] = []

    def fake(messages, model, **kwargs):
        calls.append(model)
        if model == "deepseek/deepseek-v4-pro":
            raise TimeoutError("read timed out")
        return "核心段译文。"

    with patch("akb2.briefing.synthesis._call_llm", side_effect=fake):
        out = _translate_snippet("Some English core segment.")

    assert out == "核心段译文。"
    assert calls == ["deepseek/deepseek-v4-pro", "z-ai/glm-5.1"], calls


def test_snippet_all_models_fail_warns(capsys):
    """补译全链失败 → 返回空串 + stderr 告警（不再是静默 except: return ""）。"""
    from akb2.briefing.translate import _translate_snippet

    with patch("akb2.briefing.synthesis._call_llm", side_effect=ConnectionError("down")):
        assert _translate_snippet("Some English core segment.") == ""

    assert "snippet translate failed on all models" in capsys.readouterr().err


def test_lang_en_tweet_translated_normally():
    """SC-P3a-2: lang=en（或无 lang）的推文正常翻译，text 被中文译文替换。"""
    import copy

    from akb2.briefing.translate import _apply_translation

    synth = copy.deepcopy(FIXTURE_SYNTH)
    synth["tweets"][0]["lang"] = "en"
    synth["tweets"][0]["text"] = "This is an English tweet."

    trans = {
        "executive_summary": "今日摘要。",
        "focus_discussions": [{"idx": 0, "topic": "智能体", "summary": "已是中文。"}],
        "tweets": [
            {"idx": 0, "text_zh": "这是英文推文的中文译文。", "zh_point": "英文推文要点。"},
            {"idx": 1, "text_zh": "另一条译文。", "zh_point": "要点。"},
        ],
    }

    result = _apply_translation(synth, trans)
    en_tweet = result["tweets"][0]
    assert en_tweet["text"] == "这是英文推文的中文译文。", "lang=en 推文应被翻译"
    assert en_tweet.get("text_original") == "This is an English tweet.", "应保留英文原文"
