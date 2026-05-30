"""test_pipeline_render.py — pipeline 渲染与集成测试。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SYNTH_EN = {
    "date": "2026-05-30",
    "executive_summary": "AI agents showed strong growth.",
    "focus_discussions": [
        {"topic": "Agent Memory", "summary": "Tools for persistent memory.", "supporting_tweet_ids": ["1"]},
    ],
    "tweets": [
        {"id": "1", "author": "@x", "text": "Short tweet.", "created_at": "2026-05-30T01:00:00Z", "score": 0.9, "url": "https://x.com/1"},
        {"id": "2", "author": "@y", "text": "A" * 600 + " core insight here.", "created_at": "2026-05-30T02:00:00Z", "score": 0.8, "url": "https://x.com/2"},
    ],
    "scoring_summary": {"total_tweets": 2, "scored_tweets": 2},
    "source": {"name": "deepseek_synthesis", "collector_version": "1.0.0"},
    "generated_at": "2026-05-30T06:00:00Z",
}

SYNTH_ZH = {
    **SYNTH_EN,
    "executive_summary": "今日 AI 智能体增长强劲。",
    "focus_discussions": [
        # tweet id=1 被引用；tweet id=2 是孤儿高分
        {"topic": "智能体长记忆", "summary": "多款记忆工具涌现。", "supporting_tweet_ids": ["1"]},
    ],
    "tweets": [
        {"id": "1", "author": "@x", "text": "简短推文。", "text_original": "Short tweet.", "zh_point": "记忆工具值得关注。", "created_at": "2026-05-30T01:00:00Z", "score": 0.9, "url": "https://x.com/1"},
        {"id": "2", "author": "@y", "text": "长文推文译文。", "text_original": "A" * 600 + " core insight here.", "core_excerpt": "核心洞察：AI 智能体记忆系统的新突破。", "zh_point": "长文核心是记忆系统突破。", "created_at": "2026-05-30T02:00:00Z", "score": 0.8, "url": "https://x.com/2"},
    ],
}


# ---------------------------------------------------------------------------
# SC-6: 日期不再偏移 +1
# ---------------------------------------------------------------------------

def test_date_no_offset():
    """build_briefing_markdown 和 build_temp_briefing_markdown 的文件名与 H1 = date，不再 +1。"""
    from akb2.briefing.pipeline import build_briefing_markdown, build_temp_briefing_markdown

    final_md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30")
    temp_md = build_temp_briefing_markdown(SYNTH_EN, "2026-05-30")

    assert "# 2026-05-30 AI 社交趋势日报" in final_md, "H1 标题应为抓取日 2026-05-30"
    assert "# 2026-05-31 AI 社交趋势日报" not in final_md, "不应有 +1 日期"
    assert "# 2026-05-30 AI 社交趋势日报" in temp_md
    assert "date: 2026-05-30" in final_md


# ---------------------------------------------------------------------------
# SC-1/SC-2: v2 结构 — 无独立关键推文板块，推文嵌入核心讨论主题 + 孤儿高分
# ---------------------------------------------------------------------------

def test_no_standalone_key_tweets_section():
    """v2：独立"关键推文"和"关键引述"板块均已移除。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30")

    assert "## 关键引述" not in md, "关键引述板块应已移除"
    assert "## 关键推文" not in md, "独立关键推文板块应已移除（推文嵌入主题）"


def test_temp_briefing_synced_to_v2_skeleton():
    """v2 Step5：temp（gate 内部产物）也同步骨架结构，移除旧"关键推文"/"关键发现"扁平板块。"""
    from akb2.briefing.pipeline import build_temp_briefing_markdown

    md = build_temp_briefing_markdown(SYNTH_EN, "2026-05-30")

    assert "## 关键推文" not in md, "temp 不应再有独立关键推文板块"
    assert "## 关键发现" not in md, "temp 不应再有独立关键发现板块"
    assert "## 核心讨论" in md, "temp 应包含核心讨论骨架"
    assert "Agent Memory" in md, "temp 核心讨论应含主题标题"


def test_exec_summary_and_data_summary_kept():
    """SC-2：执行摘要和数据摘要应保留。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30")

    assert "## 执行摘要" in md, "执行摘要应保留"
    assert "## 数据摘要" in md, "数据摘要应保留"
    assert "今日 AI 智能体增长强劲。" in md, "执行摘要内容应渲染"


def test_tweets_nested_under_topics():
    """SC-1：推文嵌入对应主题下（按 supporting_tweet_ids），而非独立板块。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30")

    # 主题标题应渲染
    assert "### 智能体长记忆" in md, "主题标题应渲染"
    # topic summary 应渲染
    assert "多款记忆工具涌现。" in md, "主题 summary 应渲染"
    # 被引用的 tweet id=1 应出现在核心讨论区域，且带中文点题
    assert "*记忆工具值得关注。*" in md, "被引用推文应有中文点题"


def test_orphan_highscore_listed():
    """SC-4：孤儿高分推文出现在「其他高分推文」板块，而非主题下。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30")

    # tweet id=2 是孤儿（未被任何主题 supporting_tweet_ids 引用）
    assert "## 其他高分推文" in md, "孤儿高分推文应有单独板块"
    assert "*长文核心是记忆系统突破。*" in md, "孤儿高分推文应有中文点题"


def test_merged_tweet_has_zh_point():
    """每条展示推文（主题嵌套 + 孤儿）应有中文点题（斜体）。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30")

    assert "*记忆工具值得关注。*" in md, "被引用推文应有中文点题"
    assert "*长文核心是记忆系统突破。*" in md, "孤儿高分推文应有中文点题"


# ---------------------------------------------------------------------------
# SC-3 / P2a: 长推只展示中文核心段，不保留英文原文
# ---------------------------------------------------------------------------

def test_evidence_paths_reflect_actual_collector():
    """SC-P3b-1: 非 dry-run 时 evidence_paths 应含实际 collector 名，不含 dry_run。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30",
                                 collector_name="account_collector")
    assert "account_collector" in md, "evidence_paths 应含 account_collector"
    assert "dry_run" not in md, "非 dry-run 时不应含 dry_run"


def test_evidence_paths_dry_run_collector():
    """dry-run 模式传入 collector_name='dry_run' 时，frontmatter 保留 dry_run 标记。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30",
                                 collector_name="dry_run")
    assert "dry_run" in md, "dry-run 模式应含 dry_run"


def test_long_tweet_shows_chinese_core_no_english_original():
    """长推只展示中文核心段；不再有英文原文折叠块（用户决策：简报不保留英文原文）。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30")

    assert "核心洞察：AI 智能体记忆系统的新突破。" in md, "中文核心段应出现在正文"
    assert "展开英文原文" not in md, "不应再有英文原文折叠块"
    assert "core insight here." not in md, "英文原文不应出现在简报中"
    assert "AAAA" not in md, "长推英文原文（A*600）不应出现"


# ---------------------------------------------------------------------------
# P2a: 每条推文只保留 1 个原文链接，正文内联短链被剥离
# ---------------------------------------------------------------------------

def test_tweet_body_strips_inline_links():
    """推文正文内联链接（t.co / pic.twitter）被剥离，正文区不含 http。"""
    from akb2.briefing.pipeline import _render_tweet

    t = {
        "author": "@x",
        "text": "重大发布：新模型上线 https://t.co/abc123 详见 pic.twitter.com/xyz",
        "zh_point": "新模型上线。",
        "score": 0.9,
        "url": "https://x.com/123",
    }
    body = "\n".join(_render_tweet(t))
    body_before_link = body.split("[原文]")[0]
    assert "重大发布" in body_before_link, "正文文字应保留"
    assert "http" not in body_before_link, "正文不应含任何 http 链接"
    assert "t.co" not in body_before_link, "t.co 内联链接应被剥离"
    assert "pic.twitter.com" not in body_before_link, "pic.twitter 应被剥离"


def test_tweet_has_single_original_link():
    """渲染后整条推文只有 1 个 markdown 链接（底部 [原文]）；正文裸链接清零。"""
    import re
    from akb2.briefing.pipeline import _render_tweet

    t = {
        "author": "@y",
        "text": "看看这个 https://t.co/aaa 和 https://t.co/bbb",
        "zh_point": "多链接推文。",
        "score": 0.7,
        "url": "https://x.com/999",
    }
    body = "\n".join(_render_tweet(t))
    md_links = re.findall(r"\]\(https?://", body)
    assert len(md_links) == 1, f"应只有 1 个 markdown 链接，实际 {len(md_links)}"
    assert re.findall(r"https?://t\.co", body) == [], "正文不应残留裸 t.co 链接"
    assert "[原文](https://x.com/999)" in body, "底部应保留唯一 [原文] 链接"


# ---------------------------------------------------------------------------
# Codex F1 (SC-8): 英文 synth 能通过 gate 进入 translate
# ---------------------------------------------------------------------------

def test_english_synth_reaches_translate(tmp_path):
    """英文 synthesis 经 gate（语言无关）后进入 translate 阶段（不被提前拒）。"""
    from akb2.briefing import pipeline as pl

    vault_root = tmp_path
    (vault_root / "Praise Space" / "AKB2.0" / "staging" / "candidate").mkdir(parents=True)
    (vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    translate_called = []

    def mock_translate(synth, **kwargs):
        translate_called.append(True)
        return SYNTH_ZH

    def mock_audit(zh, en, **kwargs):
        return {"verdict": "pass", "standard": "pass", "standard_issues": [],
                "llm_audit": "skipped", "drift_score": None, "accuracy_issues": [], "ts": ""}

    with (
        patch.object(pl, "run_intake", return_value={}),
        patch.object(pl, "run_scoring", return_value=SYNTH_EN),
        patch.object(pl, "run_synthesis", return_value=SYNTH_EN),
        patch.object(pl, "run_gate", return_value="active"),
        patch("akb2.briefing.pipeline.translate_briefing", side_effect=mock_translate),
        patch("akb2.briefing.pipeline.audit_briefing", side_effect=mock_audit),
    ):
        result = pl.main(["--date", "2026-05-30", "--vault-root", str(vault_root)])

    assert translate_called, "英文 synth 应进入 translate 阶段"
    assert result == 0


# ---------------------------------------------------------------------------
# Codex F2: enforcement matrix（SC-8）
# ---------------------------------------------------------------------------

def test_enforce_fail_no_social(tmp_path):
    """audit verdict=fail → Social 无文件，briefing 留 staging，sys.exit(2)。"""
    import pytest
    from akb2.briefing import pipeline as pl

    vault_root = tmp_path
    (vault_root / "Praise Space" / "AKB2.0" / "staging" / "candidate").mkdir(parents=True)
    (vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    def mock_audit(zh, en, **kwargs):
        return {"verdict": "fail", "standard": "fail",
                "standard_issues": ["topic 是占位符"],
                "llm_audit": "skipped", "drift_score": None, "accuracy_issues": [], "ts": ""}

    with (
        patch.object(pl, "run_intake", return_value={}),
        patch.object(pl, "run_scoring", return_value=SYNTH_EN),
        patch.object(pl, "run_synthesis", return_value=SYNTH_EN),
        patch.object(pl, "run_gate", return_value="active"),
        patch("akb2.briefing.pipeline.translate_briefing", return_value=SYNTH_ZH),
        patch("akb2.briefing.pipeline.audit_briefing", side_effect=mock_audit),
    ):
        with pytest.raises(SystemExit) as exc_info:
            pl.main(["--date", "2026-05-30", "--vault-root", str(vault_root)])

    social_file = vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social" / "2026-05-30 Daily Briefing.md"
    staging_file = vault_root / "Praise Space" / "AKB2.0" / "staging" / "candidate" / "2026-05-30 Daily Briefing.md"

    assert exc_info.value.code == 2, "audit=fail 应 sys.exit(2) 以便调度器区分"
    assert not social_file.exists(), "audit=fail 时 Social 不应有文件"
    assert staging_file.exists(), "audit=fail 时 staging 应保留文件"


def test_enforce_warn_writes_flagged(tmp_path):
    """audit verdict=warn → Social 有文件，frontmatter 标 audit_verdict: warn。"""
    from akb2.briefing import pipeline as pl

    vault_root = tmp_path
    (vault_root / "Praise Space" / "AKB2.0" / "staging" / "candidate").mkdir(parents=True)
    (vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    def mock_audit(zh, en, **kwargs):
        return {"verdict": "warn", "standard": "pass",
                "standard_issues": [],
                "llm_audit": "fail", "drift_score": 0.7, "accuracy_issues": ["数字丢失"], "ts": ""}

    with (
        patch.object(pl, "run_intake", return_value={}),
        patch.object(pl, "run_scoring", return_value=SYNTH_EN),
        patch.object(pl, "run_synthesis", return_value=SYNTH_EN),
        patch.object(pl, "run_gate", return_value="active"),
        patch("akb2.briefing.pipeline.translate_briefing", return_value=SYNTH_ZH),
        patch("akb2.briefing.pipeline.audit_briefing", side_effect=mock_audit),
    ):
        result = pl.main(["--date", "2026-05-30", "--vault-root", str(vault_root)])

    social_file = vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social" / "2026-05-30 Daily Briefing.md"
    assert social_file.exists(), "audit=warn 时 Social 应有文件"
    content = social_file.read_text()
    assert "audit_verdict: warn" in content, "frontmatter 应标 audit_verdict: warn"
    assert result == 0
