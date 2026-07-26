"""test_audit_fidelity_calibration.py — 译后确定性校验的**严重度与豁免**回归锁。

背景（缺陷 S-2）：audit 的 4 个 fail 级确定性检查是日报可用性单点 —— 一次误报即
`standard=fail` → `verdict=fail` → `pipeline sys.exit(2)` → 当天日报不发布。

量化依据（回放 `/Volumes/AKB2/.../AI TRENDS/Social/` 全部 60 篇已发布简报，只读）：
  * 受检字段 27.8 个/篇（executive_summary + topic + summary + 展示推文）
    → 单字段 1% 误报即 1-(1-0.01)^27.8 ≈ 24.5% 的天数被判 fail。
  * 恒等回放（en := 已发布 ZH 文本自身）：1667 字段 0 误报 → 检查器无结构性假阳性。
  * 参数规模 B 逐字保留：9/60 篇（15%）实际把它本地化成「4000 亿参数」
    「9750 亿参数（激活 410 亿）」「2.8 万亿参数」，且**数值等价**（400B == 4000 亿）
    → 该类降 warn（kind=number_localized）。
  * 千分位逐字保留：11/11 token 全部逐字保留（4,000+ / 5,000 / 1,000 / 3,000 /
    4,100 / 10,000 / 2,000 / 50,000），观测误报 0 → 维持 fail 级。
  * 中文指标名曝光 28/60（准确率 13 / 召回率 1 走严格档；解决率 7 / 通过率 2 /
    成功率 8 走 rate|% 宽松档）。

这些豁免此前**一条断言都没有**：将来把「裸 1-2 位小整数跳过」「速率类接受 rate/%」
「$1B 货币语境豁免」「等值本地化只 warn」改坏，无人会知道。本文件把它们锁住。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from akb2.briefing.audit import _check_standard_structured, _check_translation_fidelity
from akb2.briefing.audit_fix import _FIXABLE_KINDS


def _synth(zh_text: str, en_text: str = "") -> tuple[dict, dict]:
    """构造只有 executive_summary 差异的 (zh, en) 对，便于单独观察某一层检查。"""
    import copy

    base = {
        "tweets": [
            {"id": "1", "text": "中文推文一。", "zh_point": "点题一", "score": 0.9},
            {"id": "2", "text": "中文推文二。", "zh_point": "点题二", "score": 0.8},
        ],
        "focus_discussions": [
            {
                "topic": "中文主题",
                "summary": "第一句。第二句。第三句。",
                "supporting_tweet_ids": ["1", "2"],
            }
        ],
    }
    # 深拷贝：zh / en 若共享嵌套 tweets 对象，改一侧会同时改另一侧，
    # 断言就会在"两侧完全相同"的假前提下恒真（空断言）。
    zh = {**copy.deepcopy(base), "executive_summary": zh_text}
    en = {**copy.deepcopy(base), "executive_summary": en_text}
    return zh, en


def _issues(zh_text: str, en_text: str, *, field="executive_summary") -> list[dict]:
    zh, en = _synth(zh_text, en_text)
    return [it for it in _check_translation_fidelity(zh, en) if it["field"] == field]


def _by_kind(issues: list[dict], kind: str) -> list[dict]:
    return [it for it in issues if it["kind"] == kind]


# ---------------------------------------------------------------------------
# 参数规模标识（_PARAM_SCALE_RE）：等值本地化 = warn，数值损坏 = fail
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("en", "zh"), [
    # 均取自 60 篇已发布简报的真实译法（2026-06-24/06-27/07-16/07-18/07-22）
    ("Meta open-sourced the 400B MoE model Llama 4 Maverick",
     "Meta 开源了 4000 亿参数的 MoE 模型 Llama 4 Maverick"),
    ("975B params with 41B active", "高达 9750 亿参数（激活 410 亿）"),
])
def test_value_equivalent_param_localization_is_warn_not_fail(en, zh):
    """实测 9/60 篇已发布简报这样译；数值等价 → 只能 warn，判 fail 等于 15% 天数停发。"""
    issues = _issues(zh, en)
    assert not _by_kind(issues, "number_mismatch"), (
        f"数值等价的型号本地化被判 fail → 当天日报不发布；issues={issues}"
    )
    assert _by_kind(issues, "number_localized"), (
        "等值本地化仍应留 warn 记录（可判读但不阻断），实际一条都没有"
    )
    assert all(it["severity"] == "warn" for it in _by_kind(issues, "number_localized"))


@pytest.mark.parametrize(("en", "zh"), [
    ("A 405B model was released", "发布了 40 亿参数的模型"),      # 4.05e11 → 4e9
    ("Reproducible on 8B models too", "在 700 亿模型上同样可复现"),  # 8e9 → 7e10
])
def test_value_inconsistent_param_scale_still_fails(en, zh):
    """数值不等价（405B → 40 亿）是真实事实损坏，必须仍判 fail。"""
    issues = _issues(zh, en)
    assert _by_kind(issues, "number_mismatch"), f"真实数值损坏被放过了: {issues}"
    assert all(it["severity"] == "fail" for it in _by_kind(issues, "number_mismatch"))


def test_t_suffix_is_not_identifier_layer_but_value_checked():
    """`2.8T` 不在标识符层（_PARAM_SCALE_RE 只认大写 B），走第 2 层数值等价。

    实测 2026-07-18「2.8 万亿参数」= 2.8e12，与 2.8T 等价 → 放行；
    若哪天把 T 也纳入标识符层，本断言会立刻失败，提醒同步评估误杀率。
    """
    assert _issues("月之暗面正式发布 2.8 万亿参数的开源大模型 Kimi",
                   "Kimi ships a 2.8T open model") == []
    assert _by_kind(_issues("发布了 28 亿参数的模型", "Kimi ships a 2.8T open model"),
                    "number_mismatch"), "数值不等价时第 2 层仍须 fail"


def test_verbatim_param_scale_passes():
    """逐字保留是首选行为，不得有任何 issue（40/60 篇实测如此）。"""
    assert _issues("在 8B 模型上同样可复现，405B 版本随后发布",
                   "Reproducible on 8B models too; 405B follows") == []


# ---------------------------------------------------------------------------
# 千分位（_THOUSANDS_RE）：实测 11/11 逐字保留 → 维持 fail 级
# ---------------------------------------------------------------------------

def test_thousands_verbatim_preserved_passes():
    """真实译法（2026-06-01/07-14/07-21 等 11 处）都逐字保留，不得报错。"""
    assert _issues("已获得 4,000+ 个 GitHub star，覆盖 50,000 美元额度",
                   "4,000+ GitHub stars, up to $50,000 in credits") == []


def test_thousands_dropped_is_fail():
    """千分位长数丢失仍 fail：观测误报 0/11，没有降级依据。"""
    issues = _issues("已获得很多 GitHub star", "18,432 GitHub stars")
    kinds = {it["kind"] for it in issues}
    assert "number_mismatch" in kinds, f"千分位丢失应 fail，实际 {issues}"


# ---------------------------------------------------------------------------
# 既有启发式豁免：此前无任何断言，改坏无人知
# ---------------------------------------------------------------------------

def test_bare_small_integers_are_exempt():
    """裸 1-2 位小整数（"2 things" → "两件事"）跳过：假阳性主要来源且非事实关键。"""
    assert _issues("3 things to know about 2 new agents",
                   "关于两个新智能体需要知道的三件事") == []


def test_money_context_b_token_is_exempt():
    """$1B / raised 2B 是数量而非型号，允许本地化为「10 亿美元」。"""
    assert _issues("OpenAI raised $2B at a new valuation",
                   "OpenAI 以新估值融资 20 亿美元") == []


@pytest.mark.parametrize("zh_metric", ["解决率", "通过率", "成功率"])
def test_rate_family_metrics_accept_rate_or_percent(zh_metric):
    """速率类中文名互为邻接措辞：原文只要有 rate/% 概念即不算捏造（28/59 实测依据）。"""
    issues = _issues(f"在 SWE-bench 上{zh_metric} 72%", "72% on SWE-bench verified")
    assert not _by_kind(issues, "metric_fabricated"), (
        f"{zh_metric} 在原文有 % 时不应判捏造: {issues}"
    )


@pytest.mark.parametrize("zh_metric", ["准确率", "召回率"])
def test_strict_metrics_still_flag_fabrication(zh_metric):
    """具名指标（准确率/召回率）只认对应词根：SWE-bench 报 resolve rate，译成准确率是事实错误。"""
    issues = _issues(f"在 SWE-bench 上达到 72% {zh_metric}", "72% on SWE-bench verified")
    assert _by_kind(issues, "metric_fabricated"), f"{zh_metric} 应判捏造: {issues}"


def test_strict_metric_with_evidence_root_passes():
    """原文确有 accuracy 词根时，准确率不算捏造。"""
    issues = _issues("92% accuracy on the eval", "评测准确率 92%")
    assert not _by_kind(issues, "metric_fabricated"), issues


# ---------------------------------------------------------------------------
# url_missing 的字段分级：推文正文的 URL 渲染时本就被剥离
# ---------------------------------------------------------------------------

def test_url_missing_in_tweet_body_is_warn_only():
    """推文正文 URL 在 pipeline._strip_inline_links 里必被删除 → fail 级守的是被丢弃的属性。"""
    zh, en = _synth("今日综述。", "Daily summary.")
    zh["tweets"][0]["text"] = "这条推文的中文译文，链接没了"
    en["tweets"][0]["text"] = "English tweet with https://example.com/paper link"
    issues = [
        it for it in _check_translation_fidelity(zh, en)
        if it["kind"] == "url_missing" and it["field"].startswith("tweets[")
    ]
    assert issues, "推文丢 URL 仍需留记录"
    assert all(it["severity"] == "warn" for it in issues), (
        f"推文正文 URL 渲染时就被剥离，判 fail 会白白停发日报: {issues}"
    )


def test_url_missing_in_analysis_field_still_fails():
    """分析字段不经 _strip_inline_links，URL 会逐字进产出 → 丢失仍 fail。"""
    issues = _by_kind(_issues("今日综述，链接丢了。",
                              "Daily summary, see https://example.com/paper"), "url_missing")
    assert issues and all(it["severity"] == "fail" for it in issues), issues


def test_strip_inline_links_really_removes_tweet_urls():
    """上一条降级的前提必须成立：渲染确实会剥掉推文正文 URL。"""
    from akb2.briefing.pipeline import _render_tweet

    rendered = "\n".join(_render_tweet({
        "author": "@sama", "score": 0.9,
        "text": "正文里有链接 https://example.com/paper 之后还有字",
        "url": "https://x.com/sama/status/1",
    }))
    assert "https://example.com/paper" not in rendered, (
        "前提不再成立：推文正文 URL 现在会进产出 → url_missing 应重新评估为 fail"
    )


def test_handle_missing_stays_fail():
    """@handle 实测 45/60 篇正文保留（203 token），且渲染不剥离 → 维持 fail。"""
    issues = _by_kind(_issues("某人发布了新模型。", "@karpathy shipped a new model."),
                      "handle_missing")
    assert issues and all(it["severity"] == "fail" for it in issues), issues


# ---------------------------------------------------------------------------
# 可用性契约：任何 fail 级 kind 都必须有修复通道
# ---------------------------------------------------------------------------

def test_every_fail_kind_from_fidelity_checks_has_a_fixer():
    """fail 级确定性检查若不在 _FIXABLE_KINDS 里，一次误报 = 当天日报直接不发布。

    27.8 个受检字段/篇（60 篇实测均值）→ 单字段 1% 误报即 24.5% 天数停发，
    所以"有修复通道"是硬契约，不是优化项。
    """
    zh, en = _synth("综述里没有链接、没有 @人、指标是准确率 72%、模型 40 亿参数。",
                    "Summary: 72% on SWE-bench by @karpathy, 405B model, "
                    "see https://example.com/x")
    fail_kinds = {
        it["kind"] for it in _check_translation_fidelity(zh, en)
        if it.get("severity") == "fail"
    }
    assert fail_kinds >= {"number_mismatch", "url_missing", "handle_missing",
                          "metric_fabricated"}, f"样例未覆盖全部 fail kind: {fail_kinds}"
    missing = fail_kinds - set(_FIXABLE_KINDS)
    assert not missing, (
        f"以下 fail 级 kind 没有修复通道，一次误报即当天停发: {sorted(missing)}"
    )


def test_standard_structured_marks_localization_as_warn_only():
    """整条 _check_standard_structured 口径下，等值本地化不得把 standard 推到 fail。"""
    zh, en = _synth("Meta 开源了 4000 亿参数的 MoE 模型 Llama 4 Maverick。",
                    "Meta open-sourced the 400B MoE model Llama 4 Maverick.")
    items = _check_standard_structured(zh, en)
    assert not [it for it in items if it.get("severity", "fail") == "fail"], (
        f"等值本地化触发了 fail 级问题: {items}"
    )


# ---------------------------------------------------------------------------
# 生产路径：修复循环真的会为 number_mismatch 发起定点重译
# ---------------------------------------------------------------------------

def test_audit_fix_loop_actually_retranslates_number_mismatch():
    """跑真实 run_audit_fix_loop，spy 住 _translate_snippet，断言它**被实际调用**。

    不是断言"传对参数时行为对"：修复分支从 kind 集合读取，若 number_mismatch 不在
    _FIXABLE_KINDS（修复前的状态），循环会直接 converged 且一次重译都不发起。
    """
    from akb2.briefing.audit_fix import run_audit_fix_loop

    zh, en = _synth("发布了 40 亿参数的模型。", "A 405B model was released.")
    calls: list[str] = []

    def fake_snippet(text_en, *a, **kw):
        calls.append(text_en)
        return "发布了 405B 的模型。"

    with (
        patch("akb2.briefing.translate._translate_snippet", side_effect=fake_snippet),
        patch("akb2.briefing.audit._run_llm_audit", side_effect=RuntimeError("no llm")),
    ):
        fixed, verdict = run_audit_fix_loop(zh, en, max_rounds=1)

    assert calls, "number_mismatch 未触发任何定点重译（修复循环没接上）"
    assert "A 405B model was released." in calls[0], f"重译源应是英文原文: {calls}"
    assert "405B" in fixed["executive_summary"], "重译结果未写回"
    assert verdict["fix_loop_reason"] == "converged", (
        f"重译后应收敛，实际 {verdict.get('fix_loop_reason')} / {verdict.get('standard_issues')}"
    )
