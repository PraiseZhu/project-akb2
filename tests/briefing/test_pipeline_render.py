"""test_pipeline_render.py — pipeline 渲染与集成测试。"""
from __future__ import annotations

from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# 2026-06-02 试跑发现的展示层质量过滤：剔重 / 最低分 / per-author 上限
# ---------------------------------------------------------------------------

def _scored(id_, author, score, novelty=1.0):
    return {"id": id_, "author": author, "text": f"t{id_}", "score": score,
            "dimensions": {"novelty": novelty}}


def test_display_filter_drops_novelty_zero_duplicates():
    """近重复（novelty==0）被硬剔除——修复 @lmsysorg 两条公告都入选的问题。"""
    from akb2.briefing.pipeline import _apply_display_filters
    tweets = [_scored("1", "@a", 0.72, novelty=1.0), _scored("2", "@a2", 0.27, novelty=0.0)]
    out = _apply_display_filters(tweets, min_score=0.0, per_author_cap=10)
    ids = [t["id"] for t in out]
    assert ids == ["1"], "novelty==0 的重复推文应被剔除"


def test_display_filter_min_score_threshold():
    """低于最低分的推文不入正文。"""
    from akb2.briefing.pipeline import _apply_display_filters
    tweets = [_scored("1", "@a", 0.50), _scored("2", "@b", 0.27), _scored("3", "@c", 0.34)]
    out = _apply_display_filters(tweets, min_score=0.35, per_author_cap=10)
    assert [t["id"] for t in out] == ["1"], "score<0.35 应被剔除"


def test_display_filter_per_author_cap():
    """单作者超过上限的推文被丢弃（防霸屏）——修复 @fankaishuoai 霸占整个主题。"""
    from akb2.briefing.pipeline import _apply_display_filters
    tweets = [_scored(str(i), "@fankaishuoai", 0.7 - i * 0.01) for i in range(4)]
    tweets.append(_scored("x", "@other", 0.6))
    out = _apply_display_filters(tweets, min_score=0.0, per_author_cap=2)
    authors = [t["author"] for t in out]
    assert authors.count("@fankaishuoai") == 2, "单作者最多保留 2 条"
    assert "@other" in authors


def test_collect_display_tweets_applies_filters():
    """_collect_display_tweets 端到端应用过滤（主题内剔重+限作者，孤儿区去低分）。"""
    from akb2.briefing.pipeline import _collect_display_tweets
    synth = {
        "tweets": [
            _scored("1", "@a", 0.72, novelty=1.0),
            _scored("2", "@a", 0.27, novelty=0.0),   # 重复+低分
            _scored("3", "@b", 0.60, novelty=1.0),   # 孤儿高分
            _scored("4", "@c", 0.20, novelty=1.0),   # 孤儿低分
        ],
        "focus_discussions": [
            {"topic": "T1", "supporting_tweet_ids": ["1", "2"]},
        ],
    }
    per_topic, orphans = _collect_display_tweets(synth)
    assert [t["id"] for t in per_topic[0]] == ["1"], "主题内重复+低分被剔除"
    orphan_ids = [t["id"] for t in orphans]
    assert "3" in orphan_ids and "4" not in orphan_ids, "孤儿区保留高分剔低分"


def test_briefing_uses_gate_whitelisted_heading():
    """简报首章标题用 gate HC-08 白名单内的「总览」（修复恒 needs_repair）。"""
    from akb2.briefing.pipeline import build_briefing_markdown
    md = build_briefing_markdown(SYNTH_ZH, "active", "2026-05-30")
    assert "## 总览" in md, "应使用白名单标题 ## 总览"
    assert "## 导读" not in md, "不应再用未在白名单的 ## 导读"


def test_strip_links_removes_orphan_link_emoji():
    """P0-2 SC-2：剥内联链接后，正文不残留孤立的 🔗 链接标记。"""
    from akb2.briefing.pipeline import _strip_inline_links

    # 🔗 + 空格 + URL → 链接和 🔗 都清，不留孤立 🔗
    assert _strip_inline_links("看这个 🔗 https://x.com/a/status/1") == "看这个"
    # 🔗 紧贴 URL
    assert _strip_inline_links("正文 🔗https://t.co/abc 结束") == "正文 结束"
    # 🔗 + pic.twitter
    assert _strip_inline_links("GPT-5 发布 🔗 pic.twitter.com/xy") == "GPT-5 发布"
    # 无链接的纯文字不受影响
    assert _strip_inline_links("纯文字无链接") == "纯文字无链接"
    # 任何情况下结果都不含 🔗
    for s in ["a 🔗 https://t.co/x", "🔗https://x.com/1", "b 🔗 pic.twitter.com/z"]:
        assert "🔗" not in _strip_inline_links(s)


def test_rendered_tweet_body_has_no_orphan_link_emoji():
    """渲染整条推文后正文区不含孤立 🔗（SC-2 端到端）。"""
    from akb2.briefing.pipeline import _render_tweet

    t = {
        "author": "@z",
        "text": "新模型发布 🔗 https://t.co/abc 细节见 🔗 pic.twitter.com/xy",
        "zh_point": "新模型发布。",
        "score": 0.8,
        "url": "https://x.com/555",
    }
    body = "\n".join(_render_tweet(t))
    body_before_link = body.split("[原文]")[0]
    assert "🔗" not in body_before_link, "正文不应残留孤立 🔗"
    assert "新模型发布" in body_before_link, "正文文字应保留"


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

    def mock_fix_loop(zh, en, **kwargs):
        verdict = {"verdict": "pass", "standard": "pass", "standard_issues": [],
                   "standard_issues_structured": [], "llm_audit": "skipped",
                   "drift_score": None, "accuracy_issues": [], "ts": "",
                   "fix_loop_reason": "converged"}
        return zh, verdict

    with (
        patch.object(pl, "run_intake", return_value={}),
        patch.object(pl, "run_scoring", return_value=SYNTH_EN),
        patch.object(pl, "run_synthesis", return_value=SYNTH_EN),
        patch.object(pl, "run_gate", return_value="active"),
        patch("akb2.briefing.pipeline.translate_briefing", side_effect=mock_translate),
        patch.object(pl, "run_audit_fix_loop", side_effect=mock_fix_loop),
    ):
        result = pl.main(["--date", "2026-05-30", "--vault-root", str(vault_root)])

    assert translate_called, "英文 synth 应进入 translate 阶段"
    assert result == 0


# ---------------------------------------------------------------------------
# Codex F2: enforcement matrix（SC-8）
# ---------------------------------------------------------------------------

def test_enforce_fail_no_social(tmp_path):
    """audit verdict=fail → Social 无文件，briefing 留 staging，sys.exit(2)。"""
    from akb2.briefing import pipeline as pl

    vault_root = tmp_path
    (vault_root / "Praise Space" / "AKB2.0" / "staging" / "candidate").mkdir(parents=True)
    (vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    def mock_fix_loop(zh, en, **kwargs):
        verdict = {"verdict": "fail", "standard": "fail",
                   "standard_issues": ["topic 是占位符"],
                   "standard_issues_structured": [{"kind": "structural", "field": "focus_discussions[0].topic", "idx": 0, "msg": "topic 是占位符"}],
                   "llm_audit": "skipped", "drift_score": None, "accuracy_issues": [], "ts": "",
                   "fix_loop_reason": "max_rounds_reached"}
        return zh, verdict

    with (
        patch.object(pl, "run_intake", return_value={}),
        patch.object(pl, "run_scoring", return_value=SYNTH_EN),
        patch.object(pl, "run_synthesis", return_value=SYNTH_EN),
        patch.object(pl, "run_gate", return_value="active"),
        patch("akb2.briefing.pipeline.translate_briefing", return_value=SYNTH_ZH),
        patch("akb2.briefing.pipeline.run_audit_fix_loop", side_effect=mock_fix_loop),
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

    def mock_fix_loop(zh, en, **kwargs):
        verdict = {"verdict": "warn", "standard": "pass",
                   "standard_issues": [], "standard_issues_structured": [],
                   "llm_audit": "fail", "drift_score": 0.7, "accuracy_issues": ["数字丢失"], "ts": "",
                   "fix_loop_reason": "converged"}
        return zh, verdict

    with (
        patch.object(pl, "run_intake", return_value={}),
        patch.object(pl, "run_scoring", return_value=SYNTH_EN),
        patch.object(pl, "run_synthesis", return_value=SYNTH_EN),
        patch.object(pl, "run_gate", return_value="active"),
        patch("akb2.briefing.pipeline.translate_briefing", return_value=SYNTH_ZH),
        patch("akb2.briefing.pipeline.run_audit_fix_loop", side_effect=mock_fix_loop),
    ):
        result = pl.main(["--date", "2026-05-30", "--vault-root", str(vault_root)])

    social_file = vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social" / "2026-05-30 Daily Briefing.md"
    assert social_file.exists(), "audit=warn 时 Social 应有文件"
    content = social_file.read_text()
    assert "audit_verdict: warn" in content, "frontmatter 应标 audit_verdict: warn"
    assert result == 0


# ---------------------------------------------------------------------------
# Step 3: fix_loop pipeline 接线测试（SC-1/SC-4/SC-2）
# ---------------------------------------------------------------------------



def test_pipeline_fix_loop_converges_writes_social(tmp_path):
    """SC-1：fix_loop 收敛 → Social 有文件、result==0、frontmatter pass。"""
    from akb2.briefing import pipeline as pl

    vault_root = tmp_path
    (vault_root / "Praise Space" / "AKB2.0" / "staging" / "candidate").mkdir(parents=True)
    (vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    def mock_fix_loop(zh, en, **kwargs):
        converged_verdict = {
            "verdict": "pass",
            "standard": "pass",
            "standard_issues": [],
            "standard_issues_structured": [],
            "llm_audit": "pass",
            "drift_score": 0.05,
            "accuracy_issues": [],
            "fix_loop_reason": "converged",
            "ts": "",
        }
        return zh, converged_verdict

    with (
        patch.object(pl, "run_intake", return_value={}),
        patch.object(pl, "run_scoring", return_value=SYNTH_EN),
        patch.object(pl, "run_synthesis", return_value=SYNTH_EN),
        patch.object(pl, "run_gate", return_value="active"),
        patch("akb2.briefing.pipeline.translate_briefing", return_value=SYNTH_ZH),
        patch.object(pl, "run_audit_fix_loop", side_effect=mock_fix_loop),
    ):
        result = pl.main(["--date", "2026-05-30", "--vault-root", str(vault_root)])

    social_file = vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social" / "2026-05-30 Daily Briefing.md"
    assert social_file.exists(), "收敛时 Social 应有文件"
    assert result == 0, f"收敛应返回 0，实际: {result}"
    content = social_file.read_text()
    assert "audit_verdict: pass" in content, "frontmatter 应标 audit_verdict: pass"
    assert "fix_loop: max_rounds_reached" not in content, "收敛时不应标 fix_loop"


def test_pipeline_fix_loop_max_rounds_stays_staging(tmp_path):
    """SC-4：max_rounds_reached + verdict=fail → Social 无、staging 有、SystemExit(2)、标 fix_loop。"""
    import pytest as pt

    from akb2.briefing import pipeline as pl

    vault_root = tmp_path
    (vault_root / "Praise Space" / "AKB2.0" / "staging" / "candidate").mkdir(parents=True)
    (vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    def mock_fix_loop_max(zh, en, **kwargs):
        max_verdict = {
            "verdict": "fail",
            "standard": "fail",
            "standard_issues": ["tweets[0].text 无中文"],
            "standard_issues_structured": [
                {"kind": "untranslated", "field": "text", "idx": 0, "msg": "tweets[0].text 无中文"}
            ],
            "llm_audit": "skipped",
            "drift_score": None,
            "accuracy_issues": [],
            "fix_loop_reason": "max_rounds_reached",
            "ts": "",
        }
        return zh, max_verdict

    with (
        patch.object(pl, "run_intake", return_value={}),
        patch.object(pl, "run_scoring", return_value=SYNTH_EN),
        patch.object(pl, "run_synthesis", return_value=SYNTH_EN),
        patch.object(pl, "run_gate", return_value="active"),
        patch("akb2.briefing.pipeline.translate_briefing", return_value=SYNTH_ZH),
        patch.object(pl, "run_audit_fix_loop", side_effect=mock_fix_loop_max),
    ):
        with pt.raises(SystemExit) as exc_info:
            pl.main(["--date", "2026-05-30", "--vault-root", str(vault_root)])

    social_file = vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social" / "2026-05-30 Daily Briefing.md"
    staging_file = vault_root / "Praise Space" / "AKB2.0" / "staging" / "candidate" / "2026-05-30 Daily Briefing.md"

    assert exc_info.value.code == 2, f"触上限应 exit(2)，实际: {exc_info.value.code}"
    assert not social_file.exists(), "触上限时 Social 不应有文件"
    assert staging_file.exists(), "触上限时 staging 应保留文件"
    content = staging_file.read_text()
    assert "fix_loop: max_rounds_reached" in content, "frontmatter 应标 fix_loop: max_rounds_reached"
    assert "audit_verdict: fail" in content, "frontmatter 应标 audit_verdict: fail"


def test_pipeline_no_upstream_rerun_during_fix(tmp_path):
    """SC-2：收敛场景断言 run_intake/run_scoring/run_synthesis 各被调恰 1 次。"""
    from akb2.briefing import pipeline as pl

    vault_root = tmp_path
    (vault_root / "Praise Space" / "AKB2.0" / "staging" / "candidate").mkdir(parents=True)
    (vault_root / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    def mock_fix_loop(zh, en, **kwargs):
        return zh, {
            "verdict": "pass", "standard": "pass", "standard_issues": [],
            "standard_issues_structured": [],
            "llm_audit": "pass", "drift_score": 0.05, "accuracy_issues": [],
            "fix_loop_reason": "converged", "ts": "",
        }

    with (
        patch.object(pl, "run_intake", return_value={}) as mock_intake,
        patch.object(pl, "run_scoring", return_value=SYNTH_EN) as mock_scoring,
        patch.object(pl, "run_synthesis", return_value=SYNTH_EN) as mock_synthesis,
        patch.object(pl, "run_gate", return_value="active"),
        patch("akb2.briefing.pipeline.translate_briefing", return_value=SYNTH_ZH),
        patch.object(pl, "run_audit_fix_loop", side_effect=mock_fix_loop),
    ):
        result = pl.main(["--date", "2026-05-30", "--vault-root", str(vault_root)])

    assert result == 0
    assert mock_intake.call_count == 1, f"run_intake 应只调 1 次，实际: {mock_intake.call_count}"
    assert mock_scoring.call_count == 1, f"run_scoring 应只调 1 次，实际: {mock_scoring.call_count}"
    assert mock_synthesis.call_count == 1, f"run_synthesis 应只调 1 次，实际: {mock_synthesis.call_count}"
