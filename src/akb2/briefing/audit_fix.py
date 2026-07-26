"""audit_fix.py — 译后审计定点修复收敛循环。

run_audit_fix_loop：在 audit_briefing 产出可定位的翻译类缺陷时，只重译被点名字段，
最多循环 max_rounds 轮，直到可修缺陷清零（收敛）或触达上限（fail-closed）。
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

from akb2.briefing.audit import audit_briefing
from akb2.briefing.synthesis import _call_llm

_MAX_FIX_ROUNDS: int = 3
try:
    _MAX_FIX_ROUNDS = max(1, int(os.environ["AKB2_AUDIT_FIX_MAX_ROUNDS"]))
except (KeyError, ValueError):
    _MAX_FIX_ROUNDS = 3

# 可程序化定点修复的 kind 集合；其余（empty/structural）走 fail-closed
#
# 2026-07-26 扩充（缺陷 S-2）：译后确定性校验新增的 4 个 **fail 级** kind 原先一个都
# 不在此集合里 —— 任何一次误报都直接 standard=fail → verdict=fail → pipeline
# sys.exit(2) → **当天日报不发布**，既没有修复循环也没有降级通道。
# 量化依据（回放 `AI TRENDS/Social/` 60 篇已发布简报）：平均 27.8 个受检字段/篇，
# 单字段 1% 误报即 24.5% 天数停发。故这 4 个 kind 全部纳入定点重译（constitution
# 原则 2「收敛优先于重跑」：先给一次定点重译机会，仍不过才 fail-closed）。
# 它们的修复动作与 untranslated 完全同构 —— 都是"从英文源重译该字段"，
# 见 _RETRANSLATE_KINDS。
_RETRANSLATE_KINDS: frozenset[str] = frozenset({
    "untranslated",
    "number_mismatch",
    "url_missing",
    "handle_missing",
    "metric_fabricated",
})

_FIXABLE_KINDS: frozenset[str] = _RETRANSLATE_KINDS | frozenset({
    "zh_point_missing",
    "zh_point_untranslated",
})


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _generate_zh_point(text_zh: str, model: str | None = None) -> str:
    """根据中文推文生成 ≤30 字的中文点题。失败返回空字符串。"""
    text_zh = (text_zh or "").strip()
    if not text_zh:
        return ""
    # 复用 translate 的模型选择（SC-3：7×24 管线禁用 -preview 模型）。
    from akb2.briefing.translate import _TRANSLATE_MODEL
    used_model = model or os.environ.get("AKB2_TRANSLATE_MODEL", _TRANSLATE_MODEL)
    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个简报编辑。"
                    "请用不超过 30 个中文字，为以下推文内容写一句点题（核心要点总结）。"
                    "只输出点题文字，不加引号、标点、解释。"
                ),
            },
            {"role": "user", "content": text_zh},
        ]
        result = _call_llm(messages, model=used_model, json_mode=False, timeout=60)
        return (result or "").strip()[:40]  # 宽松截断，避免模型轻微超长
    except Exception:
        return ""


def _apply_fixes(
    synth_zh: dict,
    synth_en: dict,
    fixable: list[dict],
    zh_point_model: str | None = None,
) -> tuple[dict, list[str], list[str]]:
    """对 fixable 缺陷逐字段定点写入，返回 (new_synth_zh, fix_succeeded, fix_empty)。

    - _RETRANSLATE_KINDS（untranslated / number_mismatch / url_missing /
      handle_missing / metric_fabricated）：调用 _translate_snippet(英文源) 定点重译该
      字段；推文英文源按 tweet id 匹配（非下标），避免 translate 过滤/重排推文时取到
      错误源（M2）。这几类缺陷的成因都是"该字段这一次译坏了"，修复动作同构。
    - zh_point_missing / zh_point_untranslated：调用 _generate_zh_point(中文text)。
      zh_point_model 仅作用于点题生成；_translate_snippet 内部读 AKB2_TRANSLATE_MODEL
      环境变量、不接收 model 参数，故翻译模型不经此函数传参（M1）。
    - tweet_text 直接赋值 + 补 text_original=英文（不走 _apply_translation_inner，避 R3 污染）
    """
    # 保持函数内 import（非惰性）：测试通过 patch("akb2.briefing.translate._translate_snippet")
    # mock 源模块；若提至模块级，patch 路径须改为 audit_fix._translate_snippet，
    # 现有 12 处测试均指向 translate 模块，故保留此处以避免测试 mock 路径失效。
    from akb2.briefing.translate import _translate_snippet  # noqa: PLC0415

    result = copy.deepcopy(synth_zh)
    zh_tweets = result.get("tweets", [])

    # 英文推文按 id 建索引（M2：不依赖 en/zh 下标对齐）
    en_tweets_by_id = {
        str(t.get("id", "")): t
        for t in synth_en.get("tweets", [])
        if t.get("id") is not None
    }
    en_tweets = synth_en.get("tweets", [])

    def _en_tweet_for(idx: int) -> dict:
        """按 zh tweet 的 id 取对应英文推文；id 缺失或无匹配时回退到同下标。"""
        if idx is None or idx >= len(zh_tweets):
            return {}
        tid = str(zh_tweets[idx].get("id", ""))
        if tid and tid in en_tweets_by_id:
            return en_tweets_by_id[tid]
        return en_tweets[idx] if idx < len(en_tweets) else {}

    fix_succeeded: list[str] = []
    fix_empty: list[str] = []

    for item in fixable:
        kind = item["kind"]
        field = item["field"]
        idx = item["idx"]

        if kind in _RETRANSLATE_KINDS:
            # --- executive_summary ---
            if field == "executive_summary":
                en_src = (synth_en.get("executive_summary") or "").strip()
                translated = _translate_snippet(en_src)
                if translated:
                    result["executive_summary"] = translated
                    fix_succeeded.append(field)
                else:
                    fix_empty.append(field)

            # --- focus_discussions[i].topic / .summary（结构相同，仅子字段名不同）---
            elif field.startswith("focus_discussions") and (
                subfield := next((s for s in ("topic", "summary") if f".{s}" in field), None)
            ):
                fds = result.get("focus_discussions", [])
                if idx is not None and idx < len(fds):
                    en_fd = (synth_en.get("focus_discussions") or [])
                    en_src = (en_fd[idx].get(subfield) or "").strip() if idx < len(en_fd) else ""
                    translated = _translate_snippet(en_src) if en_src else ""
                    if translated:
                        fds[idx][subfield] = translated
                        fix_succeeded.append(field)
                    else:
                        fix_empty.append(field)

            # --- tweets[i].text / .core_excerpt（结构相同；text 额外补 text_original）---
            elif field.startswith("tweets[") and (
                ".core_excerpt" in field
                or (".text" in field and ".text_original" not in field)
            ):
                tweet_attr = "core_excerpt" if ".core_excerpt" in field else "text"
                en_src = (_en_tweet_for(idx).get(tweet_attr) or "").strip()
                if idx is not None and idx < len(zh_tweets):
                    translated = _translate_snippet(en_src) if en_src else ""
                    if translated:
                        if tweet_attr == "text":
                            # 直接赋值 + 补 text_original（避 _apply_translation_inner R3 污染）
                            zh_tweets[idx]["text_original"] = en_src
                        zh_tweets[idx][tweet_attr] = translated
                        fix_succeeded.append(field)
                    else:
                        fix_empty.append(field)

            # --- 重译类字段未匹配任何已知分支 ---
            else:
                # 可见警告：新增 checker 字段若无对应 fixer 分支，第一次运行即暴露
                sys.stderr.write(
                    f"WARNING [audit_fix]: no fixer branch for {kind} field {field!r}"
                    " — update _apply_fixes to handle this field\n"
                )
                fix_empty.append(field)

        elif kind in ("zh_point_missing", "zh_point_untranslated"):
            # --- tweets[i].zh_point ---
            if idx is not None and idx < len(zh_tweets):
                text_zh = (zh_tweets[idx].get("text") or "").strip()
                generated = _generate_zh_point(text_zh, zh_point_model)
                if generated:
                    zh_tweets[idx]["zh_point"] = generated
                    fix_succeeded.append(field)
                else:
                    fix_empty.append(field)
            else:
                # idx 越界 → 记为未修复，保持可观测
                fix_empty.append(field)

    return result, fix_succeeded, fix_empty


def _log_loop_event(event: dict) -> None:
    """追加循环事件到日志文件，失败静默。"""
    log_dir = Path(os.environ.get("AKB2_LOG_DIR", "/tmp/akb2-logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "audit-fix.jsonl", "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------

def run_audit_fix_loop(
    synth_zh: dict,
    synth_en: dict,
    *,
    max_rounds: int = _MAX_FIX_ROUNDS,
    audit_model: str | None = None,
    translate_model: str | None = None,
) -> tuple[dict, dict]:
    """译后审计定点修复收敛循环。

    返回 (final_synth_zh, final_verdict)。
    final_verdict 在原有 audit_briefing 字段之上注入：
      fix_loop_reason: "converged" | "max_rounds_reached"
    """
    current_synth_zh = copy.deepcopy(synth_zh)

    # 轮 0：基线审计
    verdict = audit_briefing(current_synth_zh, synth_en, model=audit_model)
    structured = verdict.get("standard_issues_structured", [])
    fixable = [it for it in structured if it["kind"] in _FIXABLE_KINDS]

    # 若无可修缺陷（含 accuracy-only warn），直接收敛；一次 log 覆盖两种状态
    _log_loop_event({
        "round": 0,
        "reason": "no_fixable_remaining" if not fixable else "initial",
        "standard_issue_count": len(structured),
        "fixed_fields": [],
        "fix_succeeded": [],
        "fix_empty": [],
        "remaining_fixable": len(fixable),
    })
    if not fixable:
        verdict["fix_loop_reason"] = "converged"
        return current_synth_zh, verdict

    for round_num in range(1, max_rounds + 1):
        # 定点修复（translate_model 仅作用于 zh_point 生成，见 _apply_fixes docstring）
        new_synth_zh, fix_succeeded, fix_empty = _apply_fixes(
            current_synth_zh, synth_en, fixable, zh_point_model=translate_model
        )
        current_synth_zh = new_synth_zh

        # 重新审计
        verdict = audit_briefing(current_synth_zh, synth_en, model=audit_model)
        structured = verdict.get("standard_issues_structured", [])
        fixable = [it for it in structured if it["kind"] in _FIXABLE_KINDS]

        _log_loop_event({
            "round": round_num,
            "reason": "post_fix",
            "standard_issue_count": len(structured),
            "fixed_fields": [it["field"] for it in fixable],
            "fix_succeeded": fix_succeeded,
            "fix_empty": fix_empty,
            "remaining_fixable": len(fixable),
        })

        if not fixable:
            verdict["fix_loop_reason"] = "converged"
            _log_loop_event({
                "round": round_num,
                "reason": "converged",
                "standard_issue_count": len(structured),
                "fixed_fields": [],
                "fix_succeeded": fix_succeeded,
                "fix_empty": fix_empty,
                "remaining_fixable": 0,
            })
            return current_synth_zh, verdict

    # 触达上限，fail-closed
    verdict["fix_loop_reason"] = "max_rounds_reached"
    _log_loop_event({
        "round": max_rounds,
        "reason": "max_rounds_reached",
        "standard_issue_count": len(structured),
        "fixed_fields": [it["field"] for it in fixable],
        "fix_succeeded": fix_succeeded,
        "fix_empty": fix_empty,
        "remaining_fixable": len(fixable),
    })
    return current_synth_zh, verdict
