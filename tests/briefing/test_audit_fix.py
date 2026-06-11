"""test_audit_fix.py — audit_fix.run_audit_fix_loop 单元测试。

所有测试通过 mock 隔离 LLM 调用，不发真实网络请求。
"""
from __future__ import annotations

import copy
from unittest.mock import patch

# ---------------------------------------------------------------------------
# 基础 fixture
# ---------------------------------------------------------------------------

_EN_TWEETS = [
    {"id": "1", "text": "AI agents are transforming software.", "score": 0.9, "author": "@a"},
    {"id": "2", "text": "Memory tools for LLM.", "score": 0.8, "author": "@b"},
]

SYNTH_EN = {
    "executive_summary": "AI agents showed strong growth.",
    "focus_discussions": [
        {
            "topic": "Agent Memory",
            "summary": "Tools for persistent memory emerged. They enable cross-session recall. This is a major shift.",
            "supporting_tweet_ids": ["1", "2"],
        }
    ],
    "tweets": _EN_TWEETS,
}

# 好的中文版本（audit 应 pass）
SYNTH_ZH_GOOD = {
    "executive_summary": "今日 AI 智能体在记忆工具方面增长强劲。",
    "focus_discussions": [
        {
            "topic": "智能体长记忆",
            "summary": "多款工具涌现，为 AI 智能体提供跨会话持久记忆。其中 agentmemory 首次实现无限接入。这一趋势表明基础设施正快速成熟。",
            "supporting_tweet_ids": ["1", "2"],
        }
    ],
    "tweets": [
        {"id": "1", "text": "AI 智能体正在改变软件开发。", "zh_point": "AI 改变软件。", "score": 0.9, "author": "@a"},
        {"id": "2", "text": "LLM 记忆工具。", "zh_point": "记忆工具涌现。", "score": 0.8, "author": "@b"},
    ],
}

# 含翻译缺陷的版本：executive_summary 英文未译，tweets[0].zh_point 缺失
SYNTH_ZH_WITH_UNTRANSLATED = {
    "executive_summary": "AI agents showed strong growth.",  # 英文 → untranslated
    "focus_discussions": [
        {
            "topic": "智能体长记忆",
            "summary": "多款工具涌现，为 AI 智能体提供跨会话持久记忆。其中 agentmemory 首次实现无限接入。这一趋势表明基础设施正快速成熟。",
            "supporting_tweet_ids": ["1", "2"],
        }
    ],
    "tweets": [
        {"id": "1", "text": "AI 智能体正在改变软件开发。", "score": 0.9, "author": "@a"},  # zh_point 缺失
        {"id": "2", "text": "LLM 记忆工具。", "zh_point": "记忆工具涌现。", "score": 0.8, "author": "@b"},
    ],
}

# 含结构性缺陷的版本（supporting_tweet_ids 不足）
SYNTH_ZH_STRUCTURAL = {
    "executive_summary": "今日 AI 智能体在记忆工具方面增长强劲。",
    "focus_discussions": [
        {
            "topic": "智能体长记忆",
            "summary": "多款工具涌现，为 AI 智能体提供跨会话持久记忆。其中 agentmemory 首次实现无限接入。这一趋势表明基础设施正快速成熟。",
            "supporting_tweet_ids": ["1"],  # 少于 2 → structural
        }
    ],
    "tweets": [
        {"id": "1", "text": "AI 智能体正在改变软件开发。", "zh_point": "AI 改变软件。", "score": 0.9, "author": "@a"},
        {"id": "2", "text": "LLM 记忆工具。", "zh_point": "记忆工具涌现。", "score": 0.8, "author": "@b"},
    ],
}


def _make_verdict(verdict_str: str, standard_issues_structured=None, accuracy_issues=None):
    structured = standard_issues_structured or []
    issues = [it["msg"] for it in structured]
    return {
        "standard": "fail" if issues else "pass",
        "standard_issues": issues,
        "standard_issues_structured": structured,
        "llm_audit": "pass",
        "drift_score": 0.0,
        "accuracy_issues": accuracy_issues or [],
        "verdict": verdict_str,
        "ts": "2026-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# SC-1: 收敛（一轮修复后 pass）
# ---------------------------------------------------------------------------

def test_fix_loop_converges_after_one_round():
    """SC-1: 轮0残英→修后pass，收敛返回，fix_loop_reason==converged。"""
    from akb2.briefing.audit_fix import run_audit_fix_loop

    untranslated_issue = {
        "kind": "untranslated", "field": "executive_summary", "idx": None,
        "msg": "executive_summary 无中文（未翻译）: ...",
    }
    round0_verdict = _make_verdict("fail", [untranslated_issue])
    round1_verdict = _make_verdict("pass", [])

    audit_seq = [round0_verdict, round1_verdict]
    call_count = {"n": 0}

    def mock_audit(sz, se, model=None):
        v = audit_seq[min(call_count["n"], len(audit_seq) - 1)]
        call_count["n"] += 1
        return copy.deepcopy(v)

    with patch("akb2.briefing.audit_fix.audit_briefing", side_effect=mock_audit), \
         patch("akb2.briefing.translate._translate_snippet", return_value="今日 AI 增长强劲。"):
        synth_zh_out, verdict = run_audit_fix_loop(
            copy.deepcopy(SYNTH_ZH_WITH_UNTRANSLATED), SYNTH_EN, max_rounds=3
        )

    assert verdict["verdict"] == "pass"
    assert verdict["fix_loop_reason"] == "converged"
    assert synth_zh_out["executive_summary"] == "今日 AI 增长强劲。"


# ---------------------------------------------------------------------------
# SC-2: 仅被点名字段变
# ---------------------------------------------------------------------------

def test_fix_loop_only_touches_named_field():
    """SC-2: 修复只改 executive_summary，不改其他字段。"""
    from akb2.briefing.audit_fix import run_audit_fix_loop

    untranslated_issue = {
        "kind": "untranslated", "field": "executive_summary", "idx": None,
        "msg": "executive_summary 无中文（未翻译）: ...",
    }
    round0_verdict = _make_verdict("fail", [untranslated_issue])
    round1_verdict = _make_verdict("pass", [])
    audit_seq = [round0_verdict, round1_verdict]
    call_count = {"n": 0}

    def mock_audit(sz, se, model=None):
        v = audit_seq[min(call_count["n"], len(audit_seq) - 1)]
        call_count["n"] += 1
        return copy.deepcopy(v)

    original_tweets = copy.deepcopy(SYNTH_ZH_GOOD["tweets"])
    synth_in = copy.deepcopy(SYNTH_ZH_GOOD)
    synth_in["executive_summary"] = "AI agents showed strong growth."  # 强制英文

    with patch("akb2.briefing.audit_fix.audit_briefing", side_effect=mock_audit), \
         patch("akb2.briefing.translate._translate_snippet", return_value="AI 增长强劲。"):
        synth_zh_out, _ = run_audit_fix_loop(synth_in, SYNTH_EN, max_rounds=3)

    # executive_summary 已修复
    assert synth_zh_out["executive_summary"] == "AI 增长强劲。"
    # tweets 未变
    assert synth_zh_out["tweets"] == original_tweets


# ---------------------------------------------------------------------------
# SC-3: max_rounds 内终止
# ---------------------------------------------------------------------------

def test_fix_loop_max_rounds_then_fail():
    """SC-3: 恒 fixable + 译空 → 跑满 max_rounds，返回非 pass，round==max_rounds。"""
    from akb2.briefing.audit_fix import run_audit_fix_loop

    untranslated_issue = {
        "kind": "untranslated", "field": "executive_summary", "idx": None,
        "msg": "executive_summary 无中文（未翻译）: ...",
    }
    persistent_verdict = _make_verdict("fail", [untranslated_issue])

    def mock_audit(sz, se, model=None):
        return copy.deepcopy(persistent_verdict)

    MAX = 3
    with patch("akb2.briefing.audit_fix.audit_briefing", side_effect=mock_audit), \
         patch("akb2.briefing.translate._translate_snippet", return_value=""):  # 译空
        _, verdict = run_audit_fix_loop(
            copy.deepcopy(SYNTH_ZH_WITH_UNTRANSLATED), SYNTH_EN, max_rounds=MAX
        )

    assert verdict["verdict"] != "pass"
    assert verdict["fix_loop_reason"] == "max_rounds_reached"


# ---------------------------------------------------------------------------
# FR-007: 仅 structural → 零修复动作、立即 fail-closed
# ---------------------------------------------------------------------------

def test_structural_issue_skips_loop():
    """FR-007: 仅 structural 缺陷 → 不进修复循环，直接返回 fail。"""
    from akb2.briefing.audit_fix import run_audit_fix_loop

    structural_issue = {
        "kind": "structural",
        "field": "focus_discussions[0].supporting_tweet_ids",
        "idx": 0,
        "msg": "focus_discussions[0].supporting_tweet_ids 少于 2 个（当前 1 个）",
    }
    structural_verdict = _make_verdict("fail", [structural_issue])

    audit_call_count = {"n": 0}

    def mock_audit(sz, se, model=None):
        audit_call_count["n"] += 1
        return copy.deepcopy(structural_verdict)

    with patch("akb2.briefing.audit_fix.audit_briefing", side_effect=mock_audit), \
         patch("akb2.briefing.translate._translate_snippet") as mock_translate:
        _, verdict = run_audit_fix_loop(
            copy.deepcopy(SYNTH_ZH_STRUCTURAL), SYNTH_EN, max_rounds=3
        )

    assert verdict["fix_loop_reason"] == "converged"  # 无 fixable → 立即"收敛"退出
    mock_translate.assert_not_called()
    assert audit_call_count["n"] == 1  # 只跑了轮0基线，不进循环


# ---------------------------------------------------------------------------
# R9: accuracy_issues only → 不修，立即返回 warn
# ---------------------------------------------------------------------------

def test_accuracy_issues_only_no_loop():
    """R9: standard 空 + accuracy 非空 → 不修，直接返回 warn，fix_loop_reason==converged。"""
    from akb2.briefing.audit_fix import run_audit_fix_loop

    warn_verdict = {
        "standard": "pass",
        "standard_issues": [],
        "standard_issues_structured": [],
        "llm_audit": "fail",
        "drift_score": 0.5,
        "accuracy_issues": ["某些内容可能存在准确性问题"],
        "verdict": "warn",
        "ts": "2026-01-01T00:00:00+00:00",
    }

    with patch("akb2.briefing.audit_fix.audit_briefing", return_value=copy.deepcopy(warn_verdict)), \
         patch("akb2.briefing.translate._translate_snippet") as mock_translate:
        _, verdict = run_audit_fix_loop(copy.deepcopy(SYNTH_ZH_GOOD), SYNTH_EN, max_rounds=3)

    assert verdict["verdict"] == "warn"
    assert verdict["fix_loop_reason"] == "converged"
    mock_translate.assert_not_called()


# ---------------------------------------------------------------------------
# Edge: 为空字段（empty kind）不修走 fail
# ---------------------------------------------------------------------------

def test_empty_field_not_fixable():
    """Edge: kind=empty 不在 _FIXABLE_KINDS，不进修复，fix_loop_reason==converged（无 fixable）。"""
    from akb2.briefing.audit_fix import run_audit_fix_loop

    empty_issue = {
        "kind": "empty", "field": "executive_summary", "idx": None,
        "msg": "executive_summary 为空（必填字段）",
    }
    empty_verdict = _make_verdict("fail", [empty_issue])

    with patch("akb2.briefing.audit_fix.audit_briefing", return_value=copy.deepcopy(empty_verdict)), \
         patch("akb2.briefing.translate._translate_snippet") as mock_translate:
        _, verdict = run_audit_fix_loop(
            {"executive_summary": "", "focus_discussions": [], "tweets": []}, SYNTH_EN, max_rounds=3
        )

    assert verdict["verdict"] == "fail"
    assert verdict["fix_loop_reason"] == "converged"
    mock_translate.assert_not_called()


# ---------------------------------------------------------------------------
# zh_point_missing → 填中文收敛
# ---------------------------------------------------------------------------

def test_zh_point_generation():
    """zh_point_missing → _generate_zh_point 填入，收敛。"""
    from akb2.briefing.audit_fix import run_audit_fix_loop

    zp_issue = {
        "kind": "zh_point_missing", "field": "tweets[0].zh_point", "idx": 0,
        "msg": "tweets[0].zh_point 缺失（展示推文必须有中文点题）",
    }
    round0_verdict = _make_verdict("fail", [zp_issue])
    round1_verdict = _make_verdict("pass", [])
    audit_seq = [round0_verdict, round1_verdict]
    call_count = {"n": 0}

    def mock_audit(sz, se, model=None):
        v = audit_seq[min(call_count["n"], len(audit_seq) - 1)]
        call_count["n"] += 1
        return copy.deepcopy(v)

    synth_in = copy.deepcopy(SYNTH_ZH_GOOD)
    del synth_in["tweets"][0]["zh_point"]

    with patch("akb2.briefing.audit_fix.audit_briefing", side_effect=mock_audit), \
         patch("akb2.briefing.audit_fix._generate_zh_point", return_value="AI 改变软件开发。") as mock_gen:
        synth_out, verdict = run_audit_fix_loop(synth_in, SYNTH_EN, max_rounds=3)

    assert verdict["fix_loop_reason"] == "converged"
    assert synth_out["tweets"][0].get("zh_point") == "AI 改变软件开发。"
    mock_gen.assert_called()


# ---------------------------------------------------------------------------
# R3: text 修后 text=中文 且 text_original=英文
# ---------------------------------------------------------------------------

def test_text_original_not_polluted():
    """R3: tweet_text 修复后 text=中文，text_original=英文原文（不污染）。"""
    from akb2.briefing.audit_fix import run_audit_fix_loop

    text_issue = {
        "kind": "untranslated", "field": "tweets[0].text", "idx": 0,
        "msg": "tweets[0].text 无中文（未翻译）: ...",
    }
    round0_verdict = _make_verdict("fail", [text_issue])
    round1_verdict = _make_verdict("pass", [])
    audit_seq = [round0_verdict, round1_verdict]
    call_count = {"n": 0}

    def mock_audit(sz, se, model=None):
        v = audit_seq[min(call_count["n"], len(audit_seq) - 1)]
        call_count["n"] += 1
        return copy.deepcopy(v)

    synth_in = copy.deepcopy(SYNTH_ZH_GOOD)
    synth_in["tweets"][0]["text"] = "AI agents are transforming software."  # 英文未译

    with patch("akb2.briefing.audit_fix.audit_briefing", side_effect=mock_audit), \
         patch("akb2.briefing.translate._translate_snippet", return_value="AI 智能体正在改变软件开发。"):
        synth_out, _ = run_audit_fix_loop(synth_in, SYNTH_EN, max_rounds=3)

    tw = synth_out["tweets"][0]
    assert tw["text"] == "AI 智能体正在改变软件开发。", f"text 应为中文，实际={tw['text']!r}"
    assert tw.get("text_original") == "AI agents are transforming software.", \
        f"text_original 应为英文原文，实际={tw.get('text_original')!r}"


# ---------------------------------------------------------------------------
# R4: rank= 问题归 structural，不误修 tweets[1]
# ---------------------------------------------------------------------------

def test_rank_issue_excluded_from_fixable():
    """R4: top3_uncovered(rank=1) 归 structural，不进修复集，不误修 tweets[1]。"""
    from akb2.briefing.audit_fix import run_audit_fix_loop

    rank_issue = {
        "kind": "structural",
        "field": "tweets[rank=1]",
        "idx": None,  # idx=None，不是数组下标
        "msg": "tweets[rank=1] id='1' score=0.900 未出现在任何主题的 supporting_tweet_ids",
    }
    rank_verdict = _make_verdict("fail", [rank_issue])

    original_tweets = copy.deepcopy(SYNTH_ZH_GOOD["tweets"])

    with patch("akb2.briefing.audit_fix.audit_briefing", return_value=copy.deepcopy(rank_verdict)), \
         patch("akb2.briefing.translate._translate_snippet") as mock_translate:
        synth_out, verdict = run_audit_fix_loop(
            copy.deepcopy(SYNTH_ZH_GOOD), SYNTH_EN, max_rounds=3
        )

    mock_translate.assert_not_called()
    assert synth_out["tweets"] == original_tweets


# ---------------------------------------------------------------------------
# SC-5: 译空 → 字段保留 + 日志 fix_empty + verdict!=pass + 缺陷仍在
# ---------------------------------------------------------------------------

def test_translate_empty_keeps_defect_logged():
    """SC-5: 译空 → 字段保留、fix_empty 记录、verdict!=pass、structured 仍含缺陷。"""
    import json
    import os
    import tempfile

    from akb2.briefing.audit_fix import run_audit_fix_loop

    untranslated_issue = {
        "kind": "untranslated", "field": "executive_summary", "idx": None,
        "msg": "executive_summary 无中文（未翻译）: ...",
    }
    persistent_verdict = _make_verdict("fail", [untranslated_issue])

    with tempfile.TemporaryDirectory() as tmpdir, \
         patch.dict(os.environ, {"AKB2_LOG_DIR": tmpdir}), \
         patch("akb2.briefing.audit_fix.audit_briefing", return_value=copy.deepcopy(persistent_verdict)), \
         patch("akb2.briefing.translate._translate_snippet", return_value=""):  # 译空
        synth_out, verdict = run_audit_fix_loop(
            copy.deepcopy(SYNTH_ZH_WITH_UNTRANSLATED), SYNTH_EN, max_rounds=3
        )

        # (a) 译空字段保留（未被覆写为空字符串）
        assert synth_out["executive_summary"] == SYNTH_ZH_WITH_UNTRANSLATED["executive_summary"]

        # (b) jsonl fix_empty 含该字段
        log_path = os.path.join(tmpdir, "audit-fix.jsonl")
        assert os.path.exists(log_path), "audit-fix.jsonl 应存在"
        events = [json.loads(line) for line in open(log_path)]
        fix_empty_fields = [f for e in events for f in e.get("fix_empty", [])]
        assert "executive_summary" in fix_empty_fields, \
            f"fix_empty 应含 executive_summary，实际={fix_empty_fields}"

    # (c) verdict != "pass"
    assert verdict["verdict"] != "pass", f"verdict 应非 pass，实际={verdict['verdict']!r}"

    # (d) standard_issues_structured 仍含该字段缺陷
    remaining_fields = [it["field"] for it in verdict.get("standard_issues_structured", [])]
    assert "executive_summary" in remaining_fields, \
        f"standard_issues_structured 应仍含 executive_summary，实际={remaining_fields}"


# ---------------------------------------------------------------------------
# M2: en/zh 推文下标错位时，仍按 id 取对英文源
# ---------------------------------------------------------------------------

def test_apply_fixes_matches_en_source_by_id_not_index():
    """M2: synth_en tweets 顺序与 synth_zh 不同时，text 修复按 id 取对英文源。"""
    from akb2.briefing.audit_fix import _apply_fixes

    # zh: tweet id=2 在下标 0，id=1 在下标 1
    synth_zh = {
        "executive_summary": "中文摘要内容。",
        "focus_discussions": [],
        "tweets": [
            {"id": "2", "text": "Memory tools for LLM.", "score": 0.8, "author": "@b"},  # 下标0，英文未译
            {"id": "1", "text": "AI 智能体正在改变软件开发。", "zh_point": "AI 改变软件。", "score": 0.9, "author": "@a"},
        ],
    }
    # en: tweet id=1 在下标 0，id=2 在下标 1（与 zh 下标相反）
    synth_en = {
        "executive_summary": "EN summary.",
        "focus_discussions": [],
        "tweets": [
            {"id": "1", "text": "AI agents are transforming software.", "score": 0.9, "author": "@a"},
            {"id": "2", "text": "Memory tools for LLM zh-source.", "score": 0.8, "author": "@b"},
        ],
    }
    # fixable: zh tweets[0]（id=2）的 text 未译
    fixable = [{"kind": "untranslated", "field": "tweets[0].text", "idx": 0,
                "msg": "tweets[0].text 无中文（未翻译）"}]

    captured = {}

    def fake_translate(text_en, *a, **k):
        captured["en_src"] = text_en
        return "【译】" + text_en

    with patch("akb2.briefing.translate._translate_snippet", side_effect=fake_translate):
        result, succeeded, _ = _apply_fixes(synth_zh, synth_en, fixable)

    # 必须取 id=2 的英文源（"Memory tools for LLM zh-source."），而非下标0的 id=1 英文
    assert captured["en_src"] == "Memory tools for LLM zh-source.", \
        f"应按 id=2 取英文源，实际取到={captured['en_src']!r}"
    assert result["tweets"][0]["text_original"] == "Memory tools for LLM zh-source."
    assert "tweets[0].text" in succeeded


def test_apply_fixes_falls_back_to_index_when_id_missing():
    """M2 回退：zh tweet 无 id 时回退到同下标英文源。"""
    from akb2.briefing.audit_fix import _apply_fixes

    synth_zh = {
        "executive_summary": "中文摘要。", "focus_discussions": [],
        "tweets": [{"text": "English untranslated.", "score": 0.9, "author": "@a"}],  # 无 id
    }
    synth_en = {
        "executive_summary": "EN.", "focus_discussions": [],
        "tweets": [{"id": "x", "text": "Index-0 english source.", "score": 0.9, "author": "@a"}],
    }
    fixable = [{"kind": "untranslated", "field": "tweets[0].text", "idx": 0,
                "msg": "tweets[0].text 无中文（未翻译）"}]

    captured = {}

    def fake_translate(text_en, *a, **k):
        captured["en_src"] = text_en
        return "【译】"

    with patch("akb2.briefing.translate._translate_snippet", side_effect=fake_translate):
        _apply_fixes(synth_zh, synth_en, fixable)

    assert captured["en_src"] == "Index-0 english source.", \
        f"id 缺失应回退同下标，实际={captured['en_src']!r}"
