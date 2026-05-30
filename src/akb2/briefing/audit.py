"""audit.py — 译后自检阶段：审标准（代码）+ 审漂移/准确（LLM）。

返回结构化 verdict：
  {
    "standard": "pass" | "fail",
    "standard_issues": [...],        # 代码检查发现的具体问题
    "llm_audit": "pass" | "warn" | "fail" | "skipped",
    "drift_score": 0.0-1.0,          # 0=无漂移, 1=严重漂移（LLM 不可用时为 null）
    "accuracy_issues": [...],         # LLM 找出的数字/工具名丢失或译错
    "verdict": "pass" | "warn" | "fail",
    "ts": "<ISO8601>",
  }

verdict 聚合规则（provisional enforcement matrix）：
  - standard=fail → verdict=fail（不入库）
  - standard=pass + llm_audit=fail → verdict=warn（入库但标记）
  - standard=pass + llm_audit=pass/warn/skipped → verdict=pass
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

# 审计专用模型（默认中文母语模型）
_AUDIT_MODEL = os.environ.get("AKB2_AUDIT_MODEL", "qwen/qwen3.6-plus")

_AUDIT_SYSTEM = (
    "你是严格的 AI 趋势简报质量审计员。\n"
    "对比英文原版和中文译版，检查：\n"
    "1. 数字是否准确（GitHub stars、百分比、版本号等）\n"
    "2. 工具名/模型名是否保留或正确处理（如 LangGraph → LangGraph，不应被翻译）\n"
    "3. 主要事实是否漂移（无中生有、意义反转、重要事实删减）\n"
    "输出 JSON，不添加额外说明。"
)


# ---------------------------------------------------------------------------
# 代码标准检查（不依赖 LLM）
# ---------------------------------------------------------------------------

def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text))


def _requires_chinese(text: str) -> bool:
    """分析字段（非空时）必须含中文，否则视为未翻译。"""
    if not text or len(text.strip()) == 0:
        return False  # 空字段不判
    return not _has_chinese(text)


def _check_standard(synth_zh: dict) -> list[str]:
    """代码规则检查，返回 issues 列表（空 = pass）。"""
    issues: list[str] = []

    # SC-1: 分析字段非空时必须含中文（不用词数粗判，防短英文漏检）
    summary = synth_zh.get("executive_summary", "")
    if _requires_chinese(summary):
        issues.append(f"executive_summary 无中文（未翻译）: {summary[:80]!r}")

    for i, fd in enumerate(synth_zh.get("focus_discussions", [])):
        topic = (fd.get("topic") or "").strip()
        sm = (fd.get("summary") or "").strip()
        if topic and not _has_chinese(topic):
            issues.append(f"focus_discussions[{i}].topic 无中文: {topic!r}")
        if _requires_chinese(sm):
            issues.append(f"focus_discussions[{i}].summary 无中文（未翻译）: {sm[:80]!r}")

    # SC-2: topic 非占位符
    for i, fd in enumerate(synth_zh.get("focus_discussions", [])):
        topic = (fd.get("topic") or "").strip()
        if re.match(r"^AI\s*趋势观察\s*#\d+$", topic):
            issues.append(f"focus_discussions[{i}].topic 仍是占位符: {topic!r}")

    # key_quotes context 应有中文
    for i, kq in enumerate(synth_zh.get("key_quotes", [])):
        ctx = (kq.get("context") or "").strip()
        if not ctx:
            issues.append(f"key_quotes[{i}].context 为空（应有中文点题）")
        elif not _has_chinese(ctx):
            issues.append(f"key_quotes[{i}].context 无中文: {ctx!r}")

    # tweets text 与 core_excerpt 非空时必须含中文
    for i, tw in enumerate(synth_zh.get("tweets", [])):
        text = (tw.get("text") or "").strip()
        if _requires_chinese(text):
            issues.append(f"tweets[{i}].text 无中文（未翻译）: {text[:60]!r}")
        core = (tw.get("core_excerpt") or "").strip()
        if _requires_chinese(core):
            issues.append(f"tweets[{i}].core_excerpt 无中文（未翻译）: {core[:60]!r}")

    return issues


# ---------------------------------------------------------------------------
# LLM 审计（漂移 + 准确性）
# ---------------------------------------------------------------------------

def _build_audit_prompt(synth_zh: dict, synth_en: dict) -> str:
    en_topics = [fd.get("topic", "") for fd in synth_en.get("focus_discussions", [])]
    zh_topics = [fd.get("topic", "") for fd in synth_zh.get("focus_discussions", [])]
    en_summary = synth_en.get("executive_summary", "")
    zh_summary = synth_zh.get("executive_summary", "")

    en_tweets_sample = [
        {"idx": i, "text": tw.get("text", "")[:300]}
        for i, tw in enumerate(synth_en.get("tweets", [])[:5])
    ]
    zh_tweets_sample = [
        {"idx": i, "text": tw.get("text", "")[:300]}
        for i, tw in enumerate(synth_zh.get("tweets", [])[:5])
    ]

    payload = {
        "en": {
            "executive_summary": en_summary,
            "topics": en_topics,
            "tweets_sample": en_tweets_sample,
        },
        "zh": {
            "executive_summary": zh_summary,
            "topics": zh_topics,
            "tweets_sample": zh_tweets_sample,
        },
    }

    return (
        "对比以下英文原版与中文译版，审查翻译质量。\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        "请输出 JSON：\n"
        "```json\n"
        "{\n"
        '  "drift_score": 0.0~1.0,  // 0=忠实无漂移, 1=严重漂移/意义反转\n'
        '  "accuracy_issues": [      // 数字/工具名丢失或译错的列表，无则空数组\n'
        '    "issue description"\n'
        "  ],\n"
        '  "llm_verdict": "pass" | "warn" | "fail"  // pass=高质量, warn=轻微问题, fail=严重漂移\n'
        "}\n"
        "```"
    )


def _run_llm_audit(synth_zh: dict, synth_en: dict, model: str) -> dict:
    from akb2.briefing.synthesis import _call_llm

    prompt = _build_audit_prompt(synth_zh, synth_en)
    messages = [
        {"role": "system", "content": _AUDIT_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    raw = _call_llm(messages, model=model, json_mode=True, timeout=120)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def audit_briefing(
    synth_zh: dict,
    synth_en: dict,
    *,
    model: str | None = None,
) -> dict:
    """执行译后自检，返回 verdict dict。LLM 不可用时仅运行代码检查。"""
    ts = datetime.now(timezone.utc).isoformat()
    used_model = model or _AUDIT_MODEL

    # 1. 代码标准检查
    standard_issues = _check_standard(synth_zh)
    standard = "fail" if standard_issues else "pass"

    # 2. LLM 漂移/准确审计
    llm_audit = "skipped"
    drift_score: float | None = None
    accuracy_issues: list[str] = []

    try:
        llm_result = _run_llm_audit(synth_zh, synth_en, model=used_model)
        drift_score = float(llm_result.get("drift_score", 0.0))
        accuracy_issues = llm_result.get("accuracy_issues") or []
        raw_verdict = llm_result.get("llm_verdict", "warn")
        # 白名单校验：未知值降级为 warn（fail-closed）
        _VALID_LLM_VERDICTS = {"pass", "warn", "fail"}
        if raw_verdict not in _VALID_LLM_VERDICTS:
            sys.stderr.write(
                f"WARNING [audit]: unexpected llm_verdict={raw_verdict!r}, "
                "treating as 'warn' (fail-closed).\n"
            )
            llm_audit = "warn"
        else:
            llm_audit = raw_verdict
    except Exception as e:
        sys.stderr.write(
            f"WARNING [audit]: LLM audit failed ({e}), skipping LLM checks.\n"
        )
        llm_audit = "skipped"

    # 3. 聚合 verdict（provisional enforcement matrix）
    if standard == "fail":
        verdict = "fail"
    elif llm_audit == "fail":
        verdict = "warn"
    else:
        verdict = "pass"

    result = {
        "standard": standard,
        "standard_issues": standard_issues,
        "llm_audit": llm_audit,
        "drift_score": drift_score,
        "accuracy_issues": accuracy_issues,
        "verdict": verdict,
        "ts": ts,
    }

    _log_audit(result)
    return result


def _log_audit(verdict: dict) -> None:
    """追加审计裁决到日志文件。"""
    import json
    from pathlib import Path

    log_dir = Path(os.environ.get("AKB2_LOG_DIR", "/tmp/akb2-logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "audit.jsonl", "a") as f:
            f.write(json.dumps(verdict, ensure_ascii=False) + "\n")
    except Exception:
        pass
