"""test_translate_budget.py — 翻译阶段总预算（缺陷 S-4）。

条目级隔离把 1 次调用变成 1+N 次：单条有 `_TWEET_TIMEOUT_CAP=120s`，但整段此前
**没有总时长/总调用数上限**（20 条 × 3 个 fallback 模型 × 120s ≈ 2 小时），违反
constitution 原则 1「循环必须有显式停止条件与预算」，且 launchd 侧
（com.akb2.briefing）既无 ExitTimeOut 也无 timeout 包裹。

生产路径绑定：所有测试都调真实入口 `translate_briefing()`，预算对象由它自己构造
（测试只覆盖模块常量），不手工 new `_TranslateBudget` —— 否则"预算类行为对"和
"生产真的用上了预算"是两回事。
"""
from __future__ import annotations

import json
from unittest.mock import patch

import akb2.briefing.translate as tr
from akb2.briefing.translate import translate_briefing


def _synth(n_tweets: int = 6) -> dict:
    tweets = [
        {"id": str(i), "text": f"English tweet number {i} about agents.", "score": 1.0 - i / 100}
        for i in range(n_tweets)
    ]
    return {
        "executive_summary": "Agents grew fast.",
        "tweets": tweets,
        "focus_discussions": [
            {
                "topic": "Agent memory",
                "summary": "Tools emerged.",
                "supporting_tweet_ids": [t["id"] for t in tweets],
            }
        ],
    }


def _analysis_reply() -> str:
    return json.dumps({
        "executive_summary": "智能体增长迅速。",
        "focus_discussions": [{"idx": 0, "topic": "智能体记忆", "summary": "工具涌现。"}],
    })


def _tweet_reply(idx: int) -> str:
    return json.dumps({"idx": idx, "text_zh": f"第 {idx} 条中文译文。", "zh_point": "点题"})


def _make_llm_stub(calls: list[str]):
    """按 prompt 里的 idx 标记返回对应条目的译文，并记录每次调用。"""
    def _stub(messages, **kwargs):
        prompt = messages[-1]["content"]
        calls.append(prompt[:60])
        if tr._ITEM_IDX_MARKER in prompt:
            idx = int(prompt.split(f"{tr._ITEM_IDX_MARKER}: ", 1)[1].split("\n", 1)[0])
            return _tweet_reply(idx)
        return _analysis_reply()
    return _stub


def test_call_budget_stops_further_llm_calls(monkeypatch):
    """总调用数上限触达后不得再发起任何模型调用，且未翻译条目必须被逐条点名。"""
    monkeypatch.setattr(tr, "_TRANSLATE_TOTAL_CALL_BUDGET", 3)
    calls: list[str] = []

    with patch("akb2.briefing.synthesis._call_llm", side_effect=_make_llm_stub(calls)):
        out = translate_briefing(_synth(6))

    assert len(calls) == 3, f"预算 3 次，实际发起 {len(calls)} 次调用"
    assert out.get("translate_budget_exhausted") == "call_budget", (
        f"预算耗尽必须可见地回传，实际 {out.get('translate_budget_exhausted')!r}"
    )
    assert out.get("translate_degraded") is True
    named = [f for f in out.get("translate_failed_fields") or [] if "budget_exhausted" in f]
    assert len(named) == 4, (
        f"6 条展示推文里 2 条译完、4 条应被点名未翻译，实际 {out.get('translate_failed_fields')}"
    )
    # 未翻译条目保持英文原文（不静默冒充译文）
    assert out["tweets"][5]["text"].startswith("English tweet number 5"), (
        "预算耗尽的条目应保持英文原文，且已在 translate_failed_fields 点名"
    )
    # 已翻译条目照常写入（预算是"停下"，不是"整批丢弃"）
    assert out["tweets"][0]["text"] == "第 0 条中文译文。"


def test_time_budget_blocks_before_any_call(monkeypatch):
    """总时长预算为 0（部署配置错误 / 上游已耗尽窗口）→ 一次调用都不发起，且失败可见。"""
    monkeypatch.setattr(tr, "_TRANSLATE_TOTAL_TIME_BUDGET_S", 0)
    calls: list[str] = []

    with patch("akb2.briefing.synthesis._call_llm", side_effect=_make_llm_stub(calls)):
        out = translate_briefing(_synth(3))

    assert calls == [], f"时长预算耗尽时不得发起调用，实际 {calls}"
    assert out.get("translate_budget_exhausted") == "time_budget"
    assert out.get("translate_failed") is True
    assert out.get("translate_failed_fields") == ["analysis"]


def test_budget_counts_model_chain_attempts_not_items(monkeypatch):
    """预算按**模型尝试**计数：单条打穿整条 fallback 链也要计 3 次，否则 1+N 次调用
    在最坏情况下仍能放大到 chain 长度倍的时长。"""
    monkeypatch.setattr(tr, "_TRANSLATE_TOTAL_CALL_BUDGET", 2)
    calls: list[str] = []

    def failing_stub(messages, **kwargs):
        calls.append(kwargs.get("model", "?"))
        raise RuntimeError("gateway down")

    with patch("akb2.briefing.synthesis._call_llm", side_effect=failing_stub):
        out = translate_briefing(_synth(4))

    # 单个逻辑条目（analysis）就把 2 次额度用尽：链长 3，若按"条目"计数则 3 个模型
    # 都会被打一遍（3 次调用），预算也不会判耗尽。
    assert len(calls) == 2, f"应按模型尝试计数并在 2 次后停，实际 {len(calls)} 次: {calls}"
    assert calls == [tr._TRANSLATE_MODEL, tr._TRANSLATE_FALLBACK_MODELS[0]]
    assert out.get("translate_failed") is True
    assert out.get("translate_budget_exhausted") == "call_budget"


def test_snippet_retranslation_also_consumes_budget(monkeypatch):
    """补译（残英 topic / core_excerpt）也是真实 LLM 调用，必须计入总预算。

    不计入等于给预算开后门：最坏情况能再加 N 次 × 完整模型链。
    """
    monkeypatch.setattr(tr, "_TRANSLATE_TOTAL_CALL_BUDGET", 2)
    calls: list[str] = []

    def stub(messages, **kwargs):
        prompt = messages[-1]["content"]
        calls.append(prompt[:40])
        if tr._ITEM_IDX_MARKER in prompt:
            return _tweet_reply(0)
        # 分析字段返回**未翻译的英文 topic** → 触发 _translate_snippet 补译
        return json.dumps({
            "executive_summary": "中文摘要。",
            "focus_discussions": [{"idx": 0, "topic": "Agent Memory Infra",
                                   "summary": "工具涌现。"}],
        })

    with patch("akb2.briefing.synthesis._call_llm", side_effect=stub):
        out = translate_briefing(_synth(1))

    assert len(calls) == 2, f"分析 1 + 推文 1 = 2 次即用满预算，实际 {len(calls)}"
    assert out.get("translate_budget_exhausted") == "call_budget", (
        "补译被预算拦下时必须标记（否则补译成了预算之外的免费额度）"
    )
    # 补译未成功 → topic 保持英文并被点名降级
    assert out["focus_discussions"][0]["topic"] == "Agent Memory Infra"
    assert "focus_discussions[0].topic" in (out.get("translate_failed_fields") or [])


def test_budget_exhausted_drops_untranslated_display_tweets():
    """预算耗尽后未译展示推文必须从 payload 里拿掉，否则 audit 会因无中文拒发。

    2026-09-02：tweets[23].core_excerpt 英文残留 → standard=fail → 当天 Social 空。
    已译条目、主题 supporting_tweet_ids 必须跟着收敛。
    """
    from akb2.briefing.audit import _check_standard_structured
    from akb2.briefing.pipeline import _drop_untranslated_after_budget

    synth = {
        "executive_summary": "今日智能体记忆工具进展综述，覆盖长期记忆与对齐。",
        "focus_discussions": [
            {
                "topic": "智能体记忆",
                "summary": "多款记忆工具涌现。它们开始支撑跨会话召回。这是基础设施层面的变化。",
                "supporting_tweet_ids": ["0", "1", "5"],
            }
        ],
        "tweets": [
            {"id": "0", "text": "第 0 条中文译文。", "zh_point": "点题", "score": 1.0},
            {"id": "1", "text": "第 1 条中文译文。", "zh_point": "点题", "score": 0.99},
            {"id": "2", "text": "English tweet number 2 about agents.", "score": 0.98},
            {"id": "3", "text": "English tweet number 3 about agents.", "score": 0.97},
            {"id": "4", "text": "English tweet number 4 about agents.", "score": 0.96},
            {"id": "5", "text": "English tweet number 5 about agents.",
             "core_excerpt": "English tweet number 5 about agents.", "score": 0.95},
        ],
        "translate_degraded": True,
        "translate_budget_exhausted": "call_budget",
        "translate_failed_fields": [
            "tweets[2](budget_exhausted)",
            "tweets[3](budget_exhausted)",
            "tweets[4](budget_exhausted)",
            "tweets[5](budget_exhausted)",
        ],
    }

    pruned = _drop_untranslated_after_budget(synth)
    ids = [t["id"] for t in pruned["tweets"]]
    assert ids == ["0", "1"], f"未译展示推文应被剪掉，实际 {ids}"
    assert pruned["focus_discussions"][0]["supporting_tweet_ids"] == ["0", "1"]
    fail_issues = [
        it for it in _check_standard_structured(pruned)
        if it.get("severity", "fail") == "fail"
        and it["kind"] in {"untranslated", "zh_point_missing", "zh_point_untranslated"}
    ]
    assert fail_issues == [], f"剪完后不得再因未译拒发，issues={fail_issues}"


def test_budget_drop_does_not_promote_english_orphans():
    """剪掉未译展示推文后，剩余未译高分推文不得被提成新孤儿再打 fail。"""
    from akb2.briefing.audit import _check_standard_structured
    from akb2.briefing.pipeline import _drop_untranslated_after_budget

    synth = {
        "executive_summary": "今日智能体记忆工具进展综述，覆盖长期记忆与对齐。",
        "focus_discussions": [
            {
                "topic": "智能体记忆",
                "summary": "多款记忆工具涌现。它们开始支撑跨会话召回。这是基础设施层面的变化。",
                "supporting_tweet_ids": ["0", "1"],
            }
        ],
        "tweets": [
            {"id": "0", "text": "第 0 条中文译文。", "zh_point": "点题", "score": 1.0},
            {"id": "1", "text": "第 1 条中文译文。", "zh_point": "点题", "score": 0.99},
            {"id": "99", "text": "English leftover that was not in the original display set.",
             "score": 0.98},
        ],
        "translate_budget_exhausted": "time_budget",
        "translate_failed_fields": ["tweets[1](budget_exhausted)"],
    }
    # tweets[1] 其实已译；这里模拟「点名不准」+ 库里还有未展示英文推文
    pruned = _drop_untranslated_after_budget(synth)
    ids = [t["id"] for t in pruned["tweets"]]
    assert "99" not in ids, f"未译高分推文不得留在 payload 里被提成孤儿，实际 {ids}"
    fail_issues = [
        it for it in _check_standard_structured(pruned)
        if it.get("severity", "fail") == "fail"
        and it["kind"] in {"untranslated", "zh_point_missing", "zh_point_untranslated"}
    ]
    assert fail_issues == [], f"issues={fail_issues}"


def test_budget_drop_keeps_non_display_chinese_without_zh_point():
    """展示集之外的中文原文从未要求 zh_point，不得当未译剪掉。"""
    from akb2.briefing.pipeline import _drop_untranslated_after_budget

    tweets = [
        {"id": "0", "text": "第 0 条中文译文。", "zh_point": "点题", "score": 1.0},
        {"id": "1", "text": "English leftover after budget.", "score": 0.99},
    ]
    # 8 条未引用中文，超出 orphan top-K=5，最后一条不进展示集
    for i in range(2, 10):
        tweets.append({
            "id": str(i),
            "text": f"这是第 {i} 条中文原文推文。",
            "score": 0.5 - i / 100,
        })
    synth = {
        "executive_summary": "今日智能体记忆工具进展综述，覆盖长期记忆与对齐。",
        "focus_discussions": [
            {
                "topic": "智能体记忆",
                "summary": "多款记忆工具涌现。它们开始支撑跨会话召回。这是基础设施层面的变化。",
                "supporting_tweet_ids": ["0"],
            }
        ],
        "tweets": tweets,
        "translate_budget_exhausted": "time_budget",
        "translate_failed_fields": ["tweets[1](budget_exhausted)"],
    }
    pruned = _drop_untranslated_after_budget(synth)
    ids = [t["id"] for t in pruned["tweets"]]
    assert "1" not in ids
    assert "9" in ids, f"展示集外中文原文应保留，实际 {ids}"


def test_no_budget_marker_on_normal_run():
    """反向守卫：预算充裕的正常运行不得出现预算标记（避免恒真断言）。"""
    calls: list[str] = []
    with patch("akb2.briefing.synthesis._call_llm", side_effect=_make_llm_stub(calls)):
        out = translate_briefing(_synth(3))

    assert len(calls) == 4, f"分析 1 + 3 条推文 = 4 次调用，实际 {len(calls)}"
    assert "translate_budget_exhausted" not in out
    assert not out.get("translate_degraded")
    assert out["tweets"][2]["text"] == "第 2 条中文译文。"


def test_budget_constants_are_named_and_bounded():
    """预算必须由具名常量表达且有限（constitution 原则 1）。"""
    assert isinstance(tr._TRANSLATE_TOTAL_CALL_BUDGET, int)
    assert isinstance(tr._TRANSLATE_TOTAL_TIME_BUDGET_S, int)
    assert 0 < tr._TRANSLATE_TOTAL_CALL_BUDGET <= 200
    # 最坏时长必须显著低于"无预算"时的 20 条 × 3 模型 × 120s ≈ 7200s
    assert 0 < tr._TRANSLATE_TOTAL_TIME_BUDGET_S <= 1800
