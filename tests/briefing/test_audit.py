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


def test_enforce_llm_fail_verdict_fail():
    """standard=pass + llm_audit=fail → verdict=fail（LLM 判 fail 有阻断权）。

    取代旧 test_enforce_llm_fail_verdict_warn：把"LLM 判严重漂移只降级成 warn"
    锁成期望行为，等于让 fail 裁决照样发布，与 fail-closed 门控（constitution 原则 3）冲突。
    """
    from akb2.briefing.audit import audit_briefing

    def mock_fail(messages, model, **kwargs):
        return json.dumps({"drift_score": 0.9, "accuracy_issues": ["数字错误"], "llm_verdict": "fail"})

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_fail):
        result = audit_briefing(SYNTH_ZH_GOOD, SYNTH_EN)

    assert result["standard"] == "pass"
    assert result["llm_audit"] == "fail"
    assert result["verdict"] == "fail", "LLM 判 fail 必须阻断发布，不得降级为 warn"
    assert result["audit_degraded"] is False, "LLM 跑成了，不算降级"


def test_llm_skipped_verdict_warn_and_degraded():
    """llm_audit=skipped → verdict=warn + audit_degraded=True（不得当 pass 放行）。"""
    from akb2.briefing.audit import audit_briefing

    def raise_error(messages, model, **kwargs):
        raise ConnectionError("no network")

    with patch("akb2.briefing.synthesis._call_llm", side_effect=raise_error):
        result = audit_briefing(SYNTH_ZH_GOOD, SYNTH_EN)

    assert result["llm_audit"] == "skipped"
    assert result["verdict"] == "warn", "LLM 审计没跑成不得直接判 pass"
    assert result["audit_degraded"] is True


def test_audit_result_exposes_contract_keys():
    """audit 返回值必须含 pipeline 消费契约的 4 个键（verdict/standard/llm_audit/audit_degraded）。"""
    from akb2.briefing.audit import audit_briefing

    def mock_pass(messages, model, **kwargs):
        return json.dumps({"drift_score": 0.0, "accuracy_issues": [], "llm_verdict": "pass"})

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_pass):
        result = audit_briefing(SYNTH_ZH_GOOD, SYNTH_EN)

    for key in ("verdict", "standard", "llm_audit", "audit_degraded"):
        assert key in result, f"缺少契约键 {key}"
    assert result["verdict"] in ("pass", "warn", "fail")
    assert result["llm_audit"] in ("pass", "warn", "fail", "skipped")
    assert isinstance(result["audit_degraded"], bool)


def test_audit_log_write_failure_is_visible(tmp_path, monkeypatch, capsys):
    """审计日志写盘失败 → audit_log_failed=True + stderr 告警（不得静默丢裁决记录）。"""
    from akb2.briefing.audit import audit_briefing

    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")  # mkdir 必然失败
    monkeypatch.setenv("AKB2_LOG_DIR", str(blocker))

    def mock_pass(messages, model, **kwargs):
        return json.dumps({"drift_score": 0.0, "accuracy_issues": [], "llm_verdict": "pass"})

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_pass):
        result = audit_briefing(SYNTH_ZH_GOOD, SYNTH_EN)

    assert result.get("audit_log_failed") is True, "写盘失败必须回传标记"
    assert "failed to persist audit verdict" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# #13 确定性译后校验（不调 LLM）
# ---------------------------------------------------------------------------

def _fidelity_pair(en_text: str, zh_text: str):
    """构造只在 tweets[0].text 上做对照的 (synth_zh, synth_en)，其余字段均已合规。"""
    import copy

    synth_zh = copy.deepcopy(SYNTH_ZH_GOOD)
    synth_zh["tweets"][0]["text"] = zh_text
    synth_en = {
        "executive_summary": "AI agents showed growth with new memory tools.",
        "focus_discussions": [
            {"topic": "Agent Memory", "summary": "Tools for persistent memory.",
             "supporting_tweet_ids": ["1", "2"]},
        ],
        "tweets": [
            {"id": "1", "text": en_text, "score": 0.9},
            {"id": "2", "text": "Another memory tweet.", "score": 0.8},
        ],
    }
    return synth_zh, synth_en


def _kinds_for(en_text: str, zh_text: str) -> list[dict]:
    from akb2.briefing.audit import _check_standard_structured

    synth_zh, synth_en = _fidelity_pair(en_text, zh_text)
    return _check_standard_structured(synth_zh, synth_en)


def test_param_scale_localization_is_warn_after_calibration():
    """405B/8B 被换算成「4050 亿/80 亿参数」→ number_localized(**warn**)，不再 fail。

    严重度下调的依据（2026-07-26 回放 60 篇已发布简报，见
    test_audit_fidelity_calibration.py 头部）：**9/60 篇（15%）**的真实译文就是这么写的
    （「4000 亿参数的 MoE 模型 Llama 4」「9750 亿参数（激活 410 亿）」），且换算后
    **数值等价**。判 fail 意味着约 15% 的天数直接不发布日报，而产出并无事实错误——
    这是可用性单点，不是质量收益。数值**不**等价的换算（405B → 40 亿）仍是 fail，
    见 test_param_scale_value_corruption_still_fails。
    """
    items = _kinds_for(
        "Llama 405B and the 8B variant were released today.",
        "今天发布了 4050 亿参数的 Llama 以及 80 亿参数版本。",
    )
    assert not [it for it in items if it["kind"] == "number_mismatch"], (
        f"等值本地化不得判 fail（15% 天数会因此停发），items={items}"
    )
    localized = [it for it in items if it["kind"] == "number_localized"]
    assert localized, f"应留 warn 级记录（可判读但不阻断），items={items}"
    assert all(it["severity"] == "warn" for it in localized)
    msg = " ".join(it["msg"] for it in localized)
    assert "405B" in msg and "8B" in msg, msg


def test_param_scale_value_corruption_still_fails():
    """数值不等价的换算（405B → 40 亿参数）是真实事实损坏，仍必须 fail。"""
    items = _kinds_for(
        "Llama 405B was released today.",
        "今天发布了 40 亿参数的 Llama。",
    )
    nums = [it for it in items if it["kind"] == "number_mismatch"]
    assert nums, f"数值损坏被放过了，items={items}"
    assert all(it["severity"] == "fail" for it in nums)


def test_param_scale_verbatim_passes():
    """405B/8B 逐字保留 → 不报数字问题。"""
    items = _kinds_for(
        "Llama 405B and the 8B variant were released today.",
        "今天发布了 Llama 405B 以及 8B 版本。",
    )
    assert not [it for it in items if it["kind"] == "number_mismatch"], items


def test_legit_unit_localization_not_flagged():
    """合法本地化（50k→5万、2M→200万、18,432→18432）不误报。"""
    items = _kinds_for(
        "The repo hit 50k stars, a 2M token context window, and 18,432 downloads.",
        "该仓库达到 5万 星标，上下文窗口 200万 token，下载 18432 次。",
    )
    assert not [it for it in items if it["kind"] == "number_mismatch"], items


def test_money_scale_localization_not_flagged():
    """货币语境的 $1B 允许本地化为 10亿美元（不是型号标识）。"""
    items = _kinds_for(
        "They raised $1B in funding this week.",
        "他们本周完成 10亿美元融资。",
    )
    assert not [it for it in items if it["kind"] == "number_mismatch"], items


def test_percent_loss_flagged():
    """百分比在译文里消失 → number_mismatch(fail)。"""
    items = _kinds_for(
        "The agent resolved 72% of real GitHub issues autonomously.",
        "该智能体自主解决了大部分真实 GitHub issue。",
    )
    assert [it for it in items if it["kind"] == "number_mismatch"], items


def test_version_number_must_be_verbatim():
    """版本号被改写 → number_mismatch(fail)。"""
    items = _kinds_for(
        "vLLM v0.22.0 ships continuous batching.",
        "vLLM 0.22 版本带来了连续批处理。",
    )
    assert [it for it in items if it["kind"] == "number_mismatch"], items


def test_url_must_be_verbatim_in_tweet_body_is_warn():
    """推文正文 URL 未逐字保留 → url_missing(**warn**)。

    严重度下调依据：推文正文的 URL 在渲染时被 `pipeline._strip_inline_links` 主动删除
    （用户决策：每条推文只留底部唯一 [原文] 链接），所以译文丢 URL 对已发布产出
    **没有任何影响**。用 fail 级去守一个下游必然丢弃的属性，只会白白停发日报。
    分析字段不经该剥离，仍 fail —— 见 test_url_missing_in_analysis_field_is_fail。
    """
    items = _kinds_for(
        "Full write-up at https://example.com/agents/deep-dive?ref=x for details.",
        "详细说明见 https://example.com/agents/deep-dive 。",
    )
    urls = [it for it in items if it["kind"] == "url_missing"]
    assert urls, items
    assert all(it["field"].startswith("tweets[") for it in urls), urls
    assert all(it["severity"] == "warn" for it in urls), urls


def test_url_missing_in_analysis_field_is_fail():
    """executive_summary 的 URL 会逐字进产出（不被剥离）→ 丢失仍是 fail。"""
    import copy

    from akb2.briefing.audit import _check_standard_structured

    synth_zh, synth_en = _fidelity_pair("Another memory tweet.", "另一条记忆推文。")
    synth_zh = copy.deepcopy(synth_zh)
    synth_zh["executive_summary"] = "今日综述，链接丢了。"
    synth_en["executive_summary"] = "Summary, see https://example.com/agents/deep-dive"

    urls = [
        it for it in _check_standard_structured(synth_zh, synth_en)
        if it["kind"] == "url_missing"
    ]
    assert urls, "分析字段丢 URL 必须检出"
    assert all(it["severity"] == "fail" for it in urls), urls


def test_handle_must_be_verbatim():
    """原文 @handle 未逐字出现在译文 → handle_missing(fail)。"""
    items = _kinds_for(
        "Great thread by @paulfchristiano on alignment.",
        "关于对齐的精彩长文，作者是保罗·克里斯蒂亚诺。",
    )
    handles = [it for it in items if it["kind"] == "handle_missing"]
    assert handles, items
    assert handles[0]["severity"] == "fail"


def test_metric_type_fabrication_flagged():
    """原文只说 72% on SWE-bench，译文写成「72% 准确率」→ metric_fabricated(fail)。

    SWE-bench 报的是 resolve rate，「准确率」是译文凭空指定的指标类型（事实错误）。
    """
    items = _kinds_for(
        "Claude 4 Opus now achieves 72% on SWE-bench verified.",
        "Claude 4 Opus 在 SWE-bench verified 上达到 72% 准确率。",
    )
    fab = [it for it in items if it["kind"] == "metric_fabricated"]
    assert fab, items
    assert fab[0]["severity"] == "fail"


def test_metric_with_evidence_not_flagged():
    """原文自带 accuracy 词根时，译文用「准确率」不报。"""
    items = _kinds_for(
        "The model reached 91% accuracy on MATH-500.",
        "该模型在 MATH-500 上达到 91% 准确率。",
    )
    assert not [it for it in items if it["kind"] == "metric_fabricated"], items


def test_rate_family_metric_accepts_percent_evidence():
    """速率类中文名（成功率/通过率/解决率）在原文有速率/百分比概念时不报。

    实测 28/59 份日报含中文指标名；速率类名之间语义邻接，一律严格判会把正常日报打成 fail。
    「准确率」仍严格（见 test_metric_type_fabrication_flagged）。
    """
    items = _kinds_for(
        "Autonomous fix rate is now above 60% on real GitHub issues.",
        "在真实 GitHub issue 上的自主成功率已超过 60%。",
    )
    assert not [it for it in items if it["kind"] == "metric_fabricated"], items


def test_fidelity_fail_blocks_publication_end_to_end():
    """确定性 fail 级问题 → standard=fail → verdict=fail（即便 LLM 判 pass）。"""
    from akb2.briefing.audit import audit_briefing

    # 用**数值不等价**的损坏（405B → 40 亿）作样例：等值本地化已按实测降为 warn，
    # 拿它当 fail 样例会让本测试断言的其实是"误杀"而不是"阻断权"。
    synth_zh, synth_en = _fidelity_pair(
        "Llama 405B beats the 8B model on SWE-bench",
        "40 亿参数的 Llama 击败了 800 亿参数模型。",
    )

    def mock_pass(messages, model, **kwargs):
        return json.dumps({"drift_score": 0.0, "accuracy_issues": [], "llm_verdict": "pass"})

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_pass):
        result = audit_briefing(synth_zh, synth_en)

    assert result["standard"] == "fail"
    assert result["verdict"] == "fail", "译后确定性校验必须有阻断权"


def test_fidelity_checks_skipped_without_english():
    """未传 synth_en 时跳过跨语言校验（向后兼容单参调用），不误报。"""
    from akb2.briefing.audit import _check_standard

    issues = _check_standard(SYNTH_ZH_GOOD)
    assert issues == [], issues


# ---------------------------------------------------------------------------
# #14 术语表锁定
# ---------------------------------------------------------------------------

def test_glossary_forbidden_variant_is_warn():
    """禁用译法（上下文腐烂）→ glossary_violation(warn)，verdict 降 warn 但不阻断。"""
    from akb2.briefing.audit import audit_briefing

    synth_zh, synth_en = _fidelity_pair(
        "Context Rot is the real bottleneck for long sessions.",
        "上下文腐烂是长会话的真正瓶颈。",
    )

    def mock_pass(messages, model, **kwargs):
        return json.dumps({"drift_score": 0.0, "accuracy_issues": [], "llm_verdict": "pass"})

    with patch("akb2.briefing.synthesis._call_llm", side_effect=mock_pass):
        result = audit_briefing(synth_zh, synth_en)

    kinds = [it for it in result["standard_issues_structured"]
             if it["kind"] == "glossary_violation"]
    assert kinds, result["standard_issues"]
    assert all(it["severity"] == "warn" for it in kinds)
    assert result["standard"] == "pass", "术语分裂不阻断入库"
    assert result["verdict"] == "warn", "但必须把 verdict 压到 warn"
    assert any("上下文腐烂" in m for m in result["standard_warn_issues"])


def test_glossary_canonical_translation_passes():
    """使用唯一指定译法（上下文衰减）→ 不报。"""
    items = _kinds_for(
        "Context Rot is the real bottleneck for long sessions.",
        "上下文衰减是长会话的真正瓶颈。",
    )
    assert not [it for it in items if it["kind"] == "glossary_violation"], items


def test_glossary_keep_en_person_name():
    """人名指定保留英文；译作「安德烈·卡帕斯」→ glossary_violation。"""
    items = _kinds_for(
        "Andrej Karpathy on LLM operating systems.",
        "安德烈·卡帕斯谈 LLM 操作系统。",
    )
    viol = [it for it in items if it["kind"] == "glossary_violation"]
    assert viol, items
    assert "Andrej Karpathy" in viol[0]["msg"]


def test_glossary_product_name_not_rewritten():
    """产品名 Copilot 被改写成 Co-pilot → glossary_violation。"""
    items = _kinds_for(
        "Copilot now supports custom agents.",
        "Co-pilot 现在支持自定义智能体。",
    )
    assert [it for it in items if it["kind"] == "glossary_violation"], items


def test_glossary_is_shared_with_translate_prompt():
    """术语表被注入翻译 prompt（翻译端与审计端同源，不是两份表）。"""
    from akb2.briefing.translate import _TRANSLATE_SYSTEM, TRANSLATION_GLOSSARY

    for term in TRANSLATION_GLOSSARY:
        assert term in _TRANSLATE_SYSTEM, f"术语 {term} 未注入翻译 prompt"
    assert "上下文衰减" in _TRANSLATE_SYSTEM
    assert "上下文腐烂" in _TRANSLATE_SYSTEM, "禁用译法也要写进 prompt"


# ---------------------------------------------------------------------------
# #13 语言纯度（假阳性是主要风险，用实测样本锁死）
# ---------------------------------------------------------------------------

def test_language_purity_allows_tech_stack_and_original_titles():
    """技术栈堆叠 / 保留的项目原名 / 版本号密集短语都是合法的，不得报错。

    实测：粗暴的「≥6 个英文长词」启发式在 71 条命中里 70 条是假阳性（98.6%）。
    """
    from akb2.briefing.audit import _check_language_purity

    legit = [
        "Remix / React / TypeScript / Prisma / SQLite / Tailwind 全栈组合，一天上线",
        "项目原名保留：Machine Learning for Algorithmic Trading，第 2 版已更新",
        "vLLM v0.22.0 发布与推理优化，吞吐提升 30%",
        "NVFP4 评估：MMLU Pro 86.3，GPQA Diamond 85.5",
        "5 个努力级别（Light、Medium、High、Extra High、Ultra）",
    ]
    for text in legit:
        assert _check_language_purity(text, prose=False) == [], text


def test_language_purity_flags_english_prose_leak():
    """整句英文散文混在中文里（虚词成串 + 英文占比高）→ language_mixed(warn)。"""
    from akb2.briefing.audit import _check_language_purity

    leaked = (
        "今日焦点是智能体记忆。The alignment tax is gone and the top models "
        "now outperform base models on reasoning benchmarks."
    )
    problems = _check_language_purity(leaked, prose=True)
    assert problems, "英文散文泄漏应被检出"
    assert any("虚词" in p for p in problems), problems


def test_language_mixed_is_warn_not_fail():
    """language_mixed 是 warn 级：不阻断入库，但把 verdict 压到 warn。"""
    items = _kinds_for(
        "The alignment tax is gone and the top models now outperform base models.",
        "对齐税消失了。The alignment tax is gone and the top models now outperform.",
    )
    mixed = [it for it in items if it["kind"] == "language_mixed"]
    assert mixed, items
    assert all(it["severity"] == "warn" for it in mixed)


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
