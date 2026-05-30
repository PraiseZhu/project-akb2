"""test_audit.py — audit.py 单元测试。"""
from __future__ import annotations

import json
from unittest.mock import patch

SYNTH_EN = {
    "executive_summary": "AI agents showed strong growth with new memory tools.",
    "focus_discussions": [
        {"topic": "Agent Memory Infrastructure", "summary": "Tools for persistent memory.", "supporting_tweet_ids": ["1"]},
    ],
    "key_quotes": [{"author": "@x", "quote": "Quote here.", "tweet_id": "1", "url": "", "context": ""}],
    "tweets": [
        {"id": "1", "author": "@x", "text": "Short tweet.", "created_at": "", "score": 0.9, "url": ""},
    ],
}

SYNTH_ZH_GOOD = {
    "executive_summary": "今日 AI 智能体在记忆工具方面增长强劲。",
    "focus_discussions": [
        {"topic": "智能体长记忆基础设施", "summary": "多款工具涌现，提供持久记忆能力。", "supporting_tweet_ids": ["1"]},
    ],
    "key_quotes": [{"author": "@x", "quote": "Quote here.", "tweet_id": "1", "url": "", "context": "首次展示了跨会话记忆能力，有参考价值。"}],
    "tweets": [
        {"id": "1", "author": "@x", "text": "关于 agentmemory 的简短推文。", "created_at": "", "score": 0.9, "url": ""},
    ],
}

SYNTH_ZH_BAD = {
    "executive_summary": "AI agents showed strong growth with new memory tools.",  # 英文
    "focus_discussions": [
        {"topic": "AI 趋势观察 #1", "summary": "Multiple tools emerged.", "supporting_tweet_ids": ["1"]},  # 占位
    ],
    "key_quotes": [{"author": "@x", "quote": "Quote here.", "tweet_id": "1", "url": "", "context": ""}],  # 空 context
    "tweets": [
        {"id": "1", "author": "@x", "text": "This is still an English tweet with many words.", "created_at": "", "score": 0.9, "url": ""},
    ],
}


# ---------------------------------------------------------------------------
# SC-5: 代码标准检查
# ---------------------------------------------------------------------------

def test_audit_standard_pass():
    """好的中文简报通过代码标准检查。"""
    from akb2.briefing.audit import _check_standard

    issues = _check_standard(SYNTH_ZH_GOOD)
    assert issues == [], f"不期待有 issues，得到：{issues}"


def test_audit_standard_fail_english_summary():
    """executive_summary 无中文（含短英文）→ standard=fail。"""
    from akb2.briefing.audit import _check_standard

    issues = _check_standard(SYNTH_ZH_BAD)
    assert any("executive_summary" in i for i in issues), f"应检出英文 summary，issues={issues}"


def test_audit_standard_fail_short_english_summary():
    """短英文 summary（<5词）也应被检出（修 Codex F1 回归）。"""
    from akb2.briefing.audit import _check_standard

    synth = {
        **SYNTH_ZH_GOOD,
        "focus_discussions": [
            {"topic": "智能体长记忆", "summary": "Tools for persistent memory.", "supporting_tweet_ids": ["1"]},
        ],
    }
    issues = _check_standard(synth)
    assert any("summary" in i for i in issues), f"短英文 summary 应被检出，issues={issues}"


def test_audit_standard_fail_placeholder_topic():
    """topic 为占位符 AI 趋势观察 #N → standard=fail。"""
    from akb2.briefing.audit import _check_standard

    issues = _check_standard(SYNTH_ZH_BAD)
    assert any("占位符" in i for i in issues), f"应检出占位符 topic，issues={issues}"


def test_audit_standard_fail_empty_context():
    """key_quotes context 为空 → standard=fail。"""
    from akb2.briefing.audit import _check_standard

    issues = _check_standard(SYNTH_ZH_BAD)
    assert any("context" in i for i in issues), f"应检出空 context，issues={issues}"


# ---------------------------------------------------------------------------
# SC-5: LLM 漂移/准确审计
# ---------------------------------------------------------------------------

def test_audit_llm_drift_accuracy():
    """LLM 审计返回 accuracy_issues → 写入 verdict。"""
    from akb2.briefing.audit import audit_briefing

    mock_response = {
        "drift_score": 0.3,
        "accuracy_issues": ["GitHub stars 数字丢失"],
        "llm_verdict": "warn",
    }

    def mock_call_llm(messages, model, **kwargs):
        return json.dumps(mock_response)

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_call_llm):
        result = audit_briefing(SYNTH_ZH_GOOD, SYNTH_EN)

    assert result["drift_score"] == 0.3
    assert "GitHub stars 数字丢失" in result["accuracy_issues"]
    assert result["llm_audit"] == "warn"


# ---------------------------------------------------------------------------
# SC-7: LLM 不可用 → 仅代码检查，不崩
# ---------------------------------------------------------------------------

def test_audit_degrades_without_llm():
    """LLM 调用失败 → llm_audit=skipped，仍返回 standard 结果。"""
    from akb2.briefing.audit import audit_briefing

    def raise_error(messages, model, **kwargs):
        raise ConnectionError("no network")

    with patch("akb2.briefing.synthesis._call_llm", side_effect=raise_error):
        result = audit_briefing(SYNTH_ZH_GOOD, SYNTH_EN)

    assert result["llm_audit"] == "skipped"
    assert result["standard"] in ("pass", "fail")
    assert "verdict" in result


# ---------------------------------------------------------------------------
# Provisional enforcement matrix (Codex Finding 2)
# ---------------------------------------------------------------------------

def test_enforce_standard_fail_verdict_fail():
    """standard=fail → verdict=fail。"""
    from akb2.briefing.audit import audit_briefing

    def mock_pass(messages, model, **kwargs):
        return json.dumps({"drift_score": 0.1, "accuracy_issues": [], "llm_verdict": "pass"})

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_pass):
        result = audit_briefing(SYNTH_ZH_BAD, SYNTH_EN)

    assert result["standard"] == "fail"
    assert result["verdict"] == "fail"


def test_enforce_llm_fail_verdict_warn():
    """standard=pass + llm_audit=fail → verdict=warn。"""
    from akb2.briefing.audit import audit_briefing

    def mock_fail(messages, model, **kwargs):
        return json.dumps({"drift_score": 0.9, "accuracy_issues": ["数字错误"], "llm_verdict": "fail"})

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_fail):
        result = audit_briefing(SYNTH_ZH_GOOD, SYNTH_EN)

    assert result["standard"] == "pass"
    assert result["llm_audit"] == "fail"
    assert result["verdict"] == "warn"


def test_enforce_all_pass_verdict_pass():
    """standard=pass + llm_audit=pass → verdict=pass。"""
    from akb2.briefing.audit import audit_briefing

    def mock_pass(messages, model, **kwargs):
        return json.dumps({"drift_score": 0.05, "accuracy_issues": [], "llm_verdict": "pass"})

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_pass):
        result = audit_briefing(SYNTH_ZH_GOOD, SYNTH_EN)
    assert result["standard"] == "pass"
    assert result["llm_audit"] == "pass"
    assert result["verdict"] == "pass"
