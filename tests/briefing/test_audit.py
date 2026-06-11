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
            "supporting_tweet_ids": ["1", "2"],  # ≥2 个（满足 SC-1）
        },
    ],
    "tweets": [
        {"id": "1", "author": "@x", "text": "关于 agentmemory 的简短推文。", "zh_point": "记忆工具值得关注。", "created_at": "", "score": 0.9, "url": ""},
        {"id": "2", "author": "@y", "text": "长期记忆系统的新进展。", "zh_point": "持久记忆技术突破。", "created_at": "", "score": 0.8, "url": ""},
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
    import copy

    from akb2.briefing.audit import _check_standard

    synth = copy.deepcopy(SYNTH_ZH_GOOD)
    synth["focus_discussions"][0]["summary"] = "只有一句。"  # < 3 句

    issues = _check_standard(synth)
    assert any("句数不足" in i for i in issues), f"应检出 summary 句数不足，issues={issues}"


def test_audit_flags_missing_zh_point():
    """被主题引用的推文 zh_point 无中文 → standard 检出 issue。"""
    import copy

    from akb2.briefing.audit import _check_standard

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
    import copy

    from akb2.briefing.audit import _check_standard

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
    import copy

    from akb2.briefing.audit import _check_standard

    synth = copy.deepcopy(SYNTH_ZH_GOOD)
    synth["executive_summary"] = ""

    issues = _check_standard(synth)
    assert any("executive_summary" in i and "为空" in i for i in issues), \
        f"应检出空 executive_summary，issues={issues}"


def test_audit_flags_empty_focus_summary():
    """focus_discussions[i].summary 为空 → standard 检出 issue。"""
    import copy

    from akb2.briefing.audit import _check_standard

    synth = copy.deepcopy(SYNTH_ZH_GOOD)
    synth["focus_discussions"][0]["summary"] = ""

    issues = _check_standard(synth)
    assert any("summary" in i and "为空" in i for i in issues), \
        f"应检出空 summary，issues={issues}"


def test_audit_flags_nonexistent_supporting_tweet():
    """supporting_tweet_ids 引用不存在的 tweet_id → standard 检出 issue。"""
    import copy

    from akb2.briefing.audit import _check_standard

    synth = copy.deepcopy(SYNTH_ZH_GOOD)
    synth["focus_discussions"][0]["supporting_tweet_ids"] = ["1", "99"]  # tweet 99 不存在

    issues = _check_standard(synth)
    assert any("不存在" in i and "99" in i for i in issues), \
        f"应检出不存在的 tweet_id=99，issues={issues}"


# ---------------------------------------------------------------------------
# P1: _count_sentences 兼容逗号连缀长句（DeepSeek 实际写法）
# ---------------------------------------------------------------------------

def test_count_sentences_comma_clauses_pass():
    """逗号连缀的丰富长句（≥60字，≥3逗号段）应判为 ≥3 句，不误判句数不足。"""
    from akb2.briefing.audit import _count_sentences

    # DeepSeek 实际输出的典型写法：一句话包含多个逗号分句
    rich = (
        "Karpathy 提出 LLM 作为操作系统的新范式，"
        "每个智能体拥有记忆、规划和工具访问能力，"
        "这代表着从单模型调用向多智能体协作的根本性转变，"
        "对整个 AI 工程栈的架构设计产生深远影响。"
    )
    count = _count_sentences(rich)
    assert count >= 3, f"逗号连缀丰富长句应判 ≥3，实际 {count}，文本: {rich!r}"


def test_count_sentences_short_empty_fail():
    """空泛短句（<60字，标点切分 <3 句）仍判不足，不被逗号规则误放行。"""
    from akb2.briefing.audit import _count_sentences

    short = "AI 发展很快，值得关注。"  # 只有1个句号，字数少
    count = _count_sentences(short)
    assert count < 3, f"空泛短句应判 <3，实际 {count}"


def test_count_sentences_single_rich_comma_clause():
    """100字以上仅含逗号（无句号）的单句：应按逗号段数而非句末标点计。"""
    from akb2.briefing.audit import _count_sentences

    # 模拟"没有任何句号，但内容丰富有3个逗号分句，共100+字"
    long_no_period = (
        "Claude 4 Opus 在 SWE-bench 上达到 72% 准确率，"
        "多智能体循环让复杂软件工程任务在数分钟内自主完成，"
        "这标志着 AI 辅助编程进入生产级可靠性阶段并引发行业重新评估开发流程"
    )
    count = _count_sentences(long_no_period)
    assert count >= 3, f"长逗号连缀句应判 ≥3，实际 {count}"


def test_audit_standard_rich_comma_summary_passes():
    """_check_standard：逗号连缀丰富 summary 不报 '句数不足'。"""
    import copy

    from akb2.briefing.audit import _check_standard

    synth = copy.deepcopy(SYNTH_ZH_GOOD)
    synth["focus_discussions"][0]["summary"] = (
        "Karpathy 提出 LLM 作为操作系统的新范式，"
        "每个智能体拥有记忆、规划和工具访问能力，"
        "这代表从单模型向多智能体协作的根本转变，"
        "对整个 AI 工程栈的架构设计产生深远影响。"
    )
    issues = _check_standard(synth)
    sent_issues = [i for i in issues if "句数不足" in i]
    assert not sent_issues, f"逗号连缀丰富 summary 不应报句数不足，issues={issues}"


def test_audit_flags_top3_tweet_not_covered():
    """top-3 高分推文未被任何主题引用 → standard 检出 issue。"""
    from akb2.briefing.audit import _check_standard

    synth = {
        "executive_summary": "执行摘要测试内容，包含足够的中文文字信息。",
        "focus_discussions": [
            {
                "topic": "测试主题",
                "summary": "第一句总结。第二句补充。第三句展望。",
                "supporting_tweet_ids": ["3", "4"],  # id=1(top1)、id=2(top2) 未被引用
            },
        ],
        "tweets": [
            {"id": "1", "score": 0.95, "text": "最高分推文。", "zh_point": "最重要。", "author": "@a"},
            {"id": "2", "score": 0.90, "text": "次高分推文。", "zh_point": "次重要。", "author": "@b"},
            {"id": "3", "score": 0.85, "text": "第三推文。", "zh_point": "第三要点。", "author": "@c"},
            {"id": "4", "score": 0.80, "text": "第四推文。", "zh_point": "第四要点。", "author": "@d"},
        ],
    }
    issues = _check_standard(synth)
    top_issues = [i for i in issues if "top-3" in i or "rank=1" in i or "rank=2" in i]
    assert top_issues, f"应检出 top-3 推文未被引用，issues={issues}"


# ---------------------------------------------------------------------------
# Step 1 新测试
# ---------------------------------------------------------------------------

def test_standard_issues_backward_compat():
    """_check_standard 与 _check_standard_structured 输出 msg 完全一致。"""
    from akb2.briefing.audit import _check_standard, _check_standard_structured
    assert _check_standard(SYNTH_ZH_BAD) == [
        it["msg"] for it in _check_standard_structured(SYNTH_ZH_BAD)
    ]


def test_check_standard_structured_kinds():
    """占位符标 structural、无中文标 untranslated、zh_point 缺失标 zh_point_missing。"""
    from akb2.briefing.audit import _check_standard_structured

    items = _check_standard_structured(SYNTH_ZH_BAD)
    kinds = {it["kind"] for it in items}
    # 占位符 topic → structural
    assert "structural" in kinds, f"应有 structural，kinds={kinds}"
    # executive_summary 英文 → untranslated
    assert "untranslated" in kinds, f"应有 untranslated，kinds={kinds}"

    # zh_point_missing：展示推文缺 zh_point
    synth = {
        "executive_summary": "今日摘要包含中文内容满足要求。",
        "focus_discussions": [
            {"topic": "测试主题有中文", "summary": "第一句。第二句。第三句。",
             "supporting_tweet_ids": ["1", "2"]},
        ],
        "tweets": [
            {"id": "1", "score": 0.9, "text": "这是中文推文内容。", "author": "@a"},  # zh_point 缺失
            {"id": "2", "score": 0.8, "text": "另一条中文推文。", "author": "@b"},
        ],
    }
    items2 = _check_standard_structured(synth)
    kinds2 = {it["kind"] for it in items2}
    assert "zh_point_missing" in kinds2, f"应有 zh_point_missing，items2={items2}"


def test_audit_returns_structured_key():
    """audit_briefing 返回包含 standard_issues_structured 的 key。"""
    from unittest.mock import patch

    from akb2.briefing.audit import audit_briefing

    with patch("akb2.briefing.audit._run_llm_audit", return_value={"llm_verdict": "pass", "accuracy_issues": [], "drift_score": 0.0}):
        result = audit_briefing(SYNTH_ZH_BAD, SYNTH_EN)
    assert "standard_issues_structured" in result, "缺少 standard_issues_structured key"
    structured = result["standard_issues_structured"]
    assert isinstance(structured, list)
    assert all(isinstance(it, dict) and "kind" in it for it in structured)
