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
    "key_quotes": [
        {"author": "@x", "quote": "Quote here.", "tweet_id": "1", "url": "https://x.com/1", "context": ""},
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
        {"topic": "智能体长记忆", "summary": "多款记忆工具涌现。", "supporting_tweet_ids": ["1"]},
    ],
    "key_quotes": [
        {"author": "@x", "quote": "Quote here.", "tweet_id": "1", "url": "https://x.com/1", "context": "首次公开了相关能力。"},
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
# 合并板块：关键引述已移除，关键推文含中文点题
# ---------------------------------------------------------------------------

def test_merged_section_no_key_quotes_block():
    """关键引述板块应已移除，只保留关键推文。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30")

    assert "## 关键引述" not in md, "关键引述板块应已合并移除"
    assert "## 关键推文" in md, "关键推文板块应保留"


def test_merged_tweet_has_zh_point():
    """每条关键推文应渲染中文点题（斜体）。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30")

    assert "*记忆工具值得关注。*" in md, "短推应有中文点题"
    assert "*长文核心是记忆系统突破。*" in md, "长推应有中文点题"


# ---------------------------------------------------------------------------
# SC-3: 长推折叠渲染
# ---------------------------------------------------------------------------

def test_long_tweet_rendered_with_callout():
    """长推（有 core_excerpt）渲染为折叠块。"""
    from akb2.briefing.pipeline import build_briefing_markdown

    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30")

    assert "核心洞察：AI 智能体记忆系统的新突破。" in md, "核心段应出现在正文"
    assert "[!quote]- 展开英文原文" in md, "长推应有折叠块"
    assert "core insight here." in md, "英文原文应在折叠块内"


# ---------------------------------------------------------------------------
# Codex F2 (SC-3): 多行英文原文完整折叠
# ---------------------------------------------------------------------------

def test_long_tweet_multiline_original_stays_in_callout():
    """多行英文原文在折叠块中每行都有 > 前缀。"""
    import copy
    from akb2.briefing.pipeline import build_briefing_markdown

    synth_multiline = copy.deepcopy(SYNTH_ZH)
    synth_multiline["tweets"][1]["text_original"] = "line1\nline2\nline3"
    synth_multiline["tweets"][1]["core_excerpt"] = "核心内容。"

    md = build_briefing_markdown(synth_multiline, "active", "2026-05-30")

    assert "> line1" in md, "第1行应有 > 前缀"
    assert "> line2" in md, "第2行应有 > 前缀"
    assert "> line3" in md, "第3行应有 > 前缀"
    # 确保不是裸文本（脱离折叠块）
    lines = md.splitlines()
    for line in ["line1", "line2", "line3"]:
        assert line not in lines, f"{line!r} 不应作为裸行出现（应带 > 前缀）"


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
