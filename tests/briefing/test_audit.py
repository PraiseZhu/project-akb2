"""test_audit.py — audit.py 单元测试。"""
from __future__ import annotations

import json
from unittest.mock import patch

SYNTH_EN = {
    "executive_summary": "AI agents showed strong growth with new memory tools.",
    "focus_discussions": [
        {"topic": "Agent Memory Infrastructure", "summary": "Tools for persistent memory.", "supporting_tweet_ids": ["1"]},
    ],
    "tweets": [
        {"id": "1", "author": "@x", "text": "Short tweet.", "created_at": "", "score": 0.9, "url": ""},
    ],
}

SYNTH_ZH_GOOD = {
    "executive_summary": "今日 AI 智能体在记忆工具方面增长强劲。",
    "focus_discussions": [
        {
            "topic": "智能体长记忆基础设施",
            # ≥3 句（满足 SC-3）
            "summary": "多款工具涌现，为 AI 智能体提供跨会话的持久记忆能力。其中 agentmemory 首次实现了无限记忆接入，引发开发者广泛讨论。这一趋势表明智能体基础设施正快速成熟，未来将影响多智能体协作场景。",
            "supporting_tweet_ids": ["1"],
        },
    ],
    "tweets": [
        {"id": "1", "author": "@x", "text": "关于 agentmemory 的简短推文。", "zh_point": "记忆工具值得关注。", "created_at": "", "score": 0.9, "url": ""},
    ],
}

SYNTH_ZH_BAD = {
    "executive_summary": "AI agents showed strong growth with new memory tools.",  # 英文
    "focus_discussions": [
        {
            "topic": "AI 趋势观察 #1",                               # 占位
            "summary": "Multiple tools emerged.",                     # 英文且 < 3句
            "supporting_tweet_ids": ["1"],
        },
    ],
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
    """key_quotes context 检查已移除（SC-9）；此测试验证 SYNTH_ZH_BAD 因其他规则失败。"""
    from akb2.briefing.audit import _check_standard

    issues = _check_standard(SYNTH_ZH_BAD)
    # SYNTH_ZH_BAD 仍因英文 summary / 占位 topic 等失败
    assert len(issues) > 0, f"SYNTH_ZH_BAD 应有 issues，issues={issues}"
    # 不再检查 context（key_quotes 已从 pipeline 移除）
    assert not any("key_quotes" in i for i in issues), "key_quotes context 检查应已移除"


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


def test_llm_audit_unknown_verdict_not_pass():
    """llm_verdict 为非法枚举值时降级为 warn（不接受原始非法值）（Codex R5 F1 回归）。"""
    from akb2.briefing.audit import audit_briefing

    def mock_bad_verdict(messages, model, **kwargs):
        return json.dumps({"drift_score": 0.9, "accuracy_issues": ["数字错误"], "llm_verdict": "FAILED"})

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_bad_verdict):
        result = audit_briefing(SYNTH_ZH_GOOD, SYNTH_EN)

    # "FAILED" 不在白名单 → 降级为 warn（不透传原始值）
    assert result["llm_audit"] == "warn", f"未知值应降级为 warn，实际: {result['llm_audit']!r}"
    # warn 在 matrix 中为 pass（不阻断入库，但已记录）
    assert result["llm_audit"] != "FAILED", "不应透传非法 enum 值"


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


# ---------------------------------------------------------------------------
# SC-8: 新增检查 — summary 句数 + zh_point 中文
# ---------------------------------------------------------------------------

def test_audit_flags_short_summary():
    """summary 不足 3 句时 → standard=fail。"""
    from akb2.briefing.audit import _check_standard
    import copy

    synth = copy.deepcopy(SYNTH_ZH_GOOD)
    synth["focus_discussions"][0]["summary"] = "只有一句。"  # < 3 句

    issues = _check_standard(synth)
    assert any("句数不足" in i for i in issues), f"应检出 summary 句数不足，issues={issues}"


def test_audit_flags_missing_zh_point():
    """被主题引用的推文 zh_point 无中文 → standard 检出 issue。"""
    from akb2.briefing.audit import _check_standard
    import copy

    synth = copy.deepcopy(SYNTH_ZH_GOOD)
    synth["tweets"][0]["zh_point"] = "memory tool worth watching"  # 英文

    issues = _check_standard(synth)
    assert any("zh_point" in i for i in issues), f"应检出 zh_point 无中文，issues={issues}"


def test_audit_good_synth_passes_standard():
    """SYNTH_ZH_GOOD（≥3句 summary + 中文 zh_point）通过所有代码检查。"""
    from akb2.briefing.audit import _check_standard

    issues = _check_standard(SYNTH_ZH_GOOD)
    assert issues == [], f"SYNTH_ZH_GOOD 应全部通过，issues={issues}"


def test_audit_count_sentences():
    """_count_sentences 正确计算中文句数。"""
    from akb2.briefing.audit import _count_sentences

    assert _count_sentences("一句话。") == 1
    assert _count_sentences("第一句。第二句。第三句。") == 3
    assert _count_sentences("") == 0
    assert _count_sentences("一？二！三。") == 3


# ---------------------------------------------------------------------------
# F3 fix: 缺失 zh_point 也应报 issue
# ---------------------------------------------------------------------------

def test_audit_flags_missing_zh_point_absent():
    """被主题引用的推文缺失 zh_point（未设字段）时 → standard 检出 issue。"""
    from akb2.briefing.audit import _check_standard
    import copy

    synth = copy.deepcopy(SYNTH_ZH_GOOD)
    # 删掉 zh_point 字段
    synth["tweets"][0].pop("zh_point", None)

    issues = _check_standard(synth)
    assert any("zh_point" in i and "缺失" in i for i in issues), \
        f"应检出 zh_point 缺失，issues={issues}"


# ---------------------------------------------------------------------------
# Codex R4 findings: 空 summary + 缺引用推文
# ---------------------------------------------------------------------------

def test_audit_flags_empty_executive_summary():
    """executive_summary 为空 → standard 检出 issue。"""
    from akb2.briefing.audit import _check_standard
    import copy

    synth = copy.deepcopy(SYNTH_ZH_GOOD)
    synth["executive_summary"] = ""

    issues = _check_standard(synth)
    assert any("executive_summary" in i and "为空" in i for i in issues), \
        f"应检出空 executive_summary，issues={issues}"


def test_audit_flags_empty_focus_summary():
    """focus_discussions[i].summary 为空 → standard 检出 issue。"""
    from akb2.briefing.audit import _check_standard
    import copy

    synth = copy.deepcopy(SYNTH_ZH_GOOD)
    synth["focus_discussions"][0]["summary"] = ""

    issues = _check_standard(synth)
    assert any("summary" in i and "为空" in i for i in issues), \
        f"应检出空 summary，issues={issues}"


def test_audit_flags_nonexistent_supporting_tweet():
    """supporting_tweet_ids 引用不存在的 tweet_id → standard 检出 issue。"""
    from akb2.briefing.audit import _check_standard
    import copy

    synth = copy.deepcopy(SYNTH_ZH_GOOD)
    synth["focus_discussions"][0]["supporting_tweet_ids"] = ["99"]  # tweet 99 不存在

    issues = _check_standard(synth)
    assert any("supporting_tweet_ids" in i or "99" in i for i in issues), \
        f"应检出不存在的 tweet_id=99，issues={issues}"
