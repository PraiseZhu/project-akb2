"""test_pipeline_render_dedup.py — 缺陷 #5：同日跨小节重复渲染。

实证：2026-07-26 简报里 @sama / @AnthropicAI / @ylecun / @deepseek_ai 各出现 2 次
（落在不同 `###` 小节），文末却写「精选推文：12」而实渲染 16 条。
根因：`_apply_display_filters` 每次调用新建计数器，且只按 novelty==0 剔重，
函数间没有跨调用的"已渲染 id"集合 —— 这是渲染幂等问题，不是内容相似度问题。
"""
from __future__ import annotations

import re


def _t(tid: str, author: str = "@sama", score: float = 0.9) -> dict:
    return {
        "id": tid,
        "author": author,
        "text": f"tweet {tid}",
        "score": score,
        "dimensions": {"novelty": 1.0},
        "url": f"https://x.com/{tid}",
    }


def _multi_topic_synth() -> dict:
    return {
        "date": "2026-07-26",
        "executive_summary": "摘要。",
        "scoring_summary": {"total_tweets": 3, "scored_tweets": 3},
        "tweets": [_t("1"), _t("2", "@ylecun"), _t("3", "@deepseek_ai", 0.7)],
        "focus_discussions": [
            {"topic": "主题甲", "summary": "甲。", "supporting_tweet_ids": ["1", "2"]},
            {"topic": "主题乙", "summary": "乙。", "supporting_tweet_ids": ["1", "3"]},
            {"topic": "主题丙", "summary": "丙。", "supporting_tweet_ids": ["1"]},
        ],
    }


def test_collect_display_tweets_renders_each_tweet_once():
    """同一 tweet 被多个 topic 引用 → 只在第一个小节渲染一次（渲染幂等）。"""
    from akb2.briefing.pipeline import _collect_display_tweets

    per_topic, orphans = _collect_display_tweets(_multi_topic_synth())
    all_ids = [t["id"] for group in per_topic for t in group] + [t["id"] for t in orphans]

    assert len(all_ids) == len(set(all_ids)), f"存在跨小节重复：{all_ids}"
    assert [t["id"] for t in per_topic[0]] == ["1", "2"]
    assert [t["id"] for t in per_topic[1]] == ["3"], "id=1 已在主题甲渲染，主题乙不再重复"
    assert per_topic[2] == [], "主题丙的唯一支撑推文已渲染，本小节为空"


def test_briefing_markdown_has_no_duplicate_tweet_links():
    """整篇 markdown 里每条推文的 [原文] 链接只出现一次。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(_multi_topic_synth(), "active", "2026-07-26")
    links = re.findall(r"\[原文\]\((https://x\.com/\d+)\)", md)
    assert sorted(links) == sorted(set(links)), f"存在重复渲染的推文：{links}"
    assert len(links) == 3, f"3 条推文应各渲染 1 次，实际 {len(links)}"


def test_data_summary_count_matches_rendered_tweets():
    """文末「精选推文」计数 == 正文实际渲染条数（修复 12 vs 实渲染 16 的谎报）。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    synth = _multi_topic_synth()
    synth["scoring_summary"] = {"total_tweets": 3, "scored_tweets": 99}  # 上游谎报
    md = build_briefing_markdown(synth, "active", "2026-07-26")

    rendered = len(re.findall(r"\[原文\]\(https://x\.com/", md))
    m = re.search(r"- 精选推文：(\d+)", md)
    assert m, "数据摘要应含「精选推文」计数"
    assert int(m.group(1)) == rendered, f"计数 {m.group(1)} != 实渲染 {rendered}"


def test_temp_briefing_count_matches_rendered_tweets():
    """gate 临时文件同样不得谎报计数（gate 判的就是这份）。"""
    from akb2.briefing.pipeline import build_temp_briefing_markdown

    synth = _multi_topic_synth()
    synth["scoring_summary"] = {"total_tweets": 3, "scored_tweets": 99}
    md = build_temp_briefing_markdown(synth, "2026-07-26")

    rendered = len(re.findall(r"\[原文\]\(https://x\.com/", md))
    m = re.search(r"- 精选推文：(\d+)", md)
    assert m and int(m.group(1)) == rendered


def test_apply_display_filters_shared_rendered_ids():
    """_apply_display_filters 接受跨调用的已渲染 id 集合，第二次调用剔除已渲染项。"""
    from akb2.briefing.pipeline import _apply_display_filters

    rendered: set[str] = set()
    first = _apply_display_filters(
        [_t("1"), _t("2", "@b")], min_score=0.0, per_author_cap=10, rendered_ids=rendered
    )
    second = _apply_display_filters(
        [_t("1"), _t("3", "@c")], min_score=0.0, per_author_cap=10, rendered_ids=rendered
    )
    assert [t["id"] for t in first] == ["1", "2"]
    assert [t["id"] for t in second] == ["3"], "已渲染的 id=1 必须被剔除"
