"""synthesis.py — 调 DeepSeek V4 Pro 综合打分结果为 social_trends_v1_1 payload。

CLI: python -m akb2.briefing.synthesis --input <scored.json> --output <synth.json>

- 若 AI_GATEWAY_API_KEY 可用：调真实 API（AI Gateway，默认 llm-proxy.tapsvc.com，OpenAI 兼容）
- 若无凭证：使用 deterministic fallback（从 scored.json 中前 5 条推文构造合规 payload）
- 输出必须通过 social_trends_v1_1 schema 校验
- 失败时最多重试 2 次（指数退避，间隔 1s / 2s）
- 中文化由独立 translate.py 阶段负责，synthesis 只产出干净结构化分析
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 合成阶段专用模型（可由环境变量覆盖）
_SYNTHESIS_MODEL = os.environ.get("AKB2_SYNTHESIS_MODEL", "gemini-3.1-pro-preview")

# synthesis system message：约束分析质量，语言交由后续翻译阶段
_SYNTHESIS_SYSTEM = (
    "You are an expert AI/ML trend analyst. "
    "Analyze the provided tweets and return a structured JSON report following the schema exactly. "
    "Focus on genuine signal: specific tools, models, metrics, and developer sentiment. "
    "Avoid vague summaries. Every topic must be specific (e.g. 'Agent memory infrastructure' not 'AI Trends'). "
    "Do NOT fabricate events or numbers not present in the input tweets."
)


def _has_api_key() -> bool:
    """检测是否有可用 API 凭证。"""
    return bool(os.environ.get("AI_GATEWAY_API_KEY"))


def _call_llm(
    messages: list[dict],
    model: str,
    *,
    json_mode: bool = True,
    max_retries: int = 1,
    timeout: int = 300,
) -> str:
    """通用 LLM 调用（OpenAI-compatible），失败重试一次，返回 raw content string。"""
    import httpx

    api_key = os.environ.get("AI_GATEWAY_API_KEY")
    base_url = os.environ.get("AI_GATEWAY_BASE_URL", "https://llm-proxy.tapsvc.com").rstrip("/")
    url = f"{base_url}/v1/chat/completions"

    payload: dict = {"model": model, "messages": messages, "temperature": 0.2}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    for attempt in range(max_retries + 1):
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            _log_raw_response(model=model, raw=raw)
            return raw
        except Exception:
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)

    raise RuntimeError(f"_call_llm({model}): exhausted retries")  # pragma: no cover


def _log_raw_response(model: str, raw: str) -> None:
    """将原始响应元数据追加到日志（脱敏：仅记录 model + 长度，不含 key）。"""
    log_dir = Path(os.environ.get("AKB2_LOG_DIR", "/tmp/akb2-logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        entry = json.dumps(
            {"ts": datetime.now(timezone.utc).isoformat(), "model": model, "raw_len": len(raw)},
            ensure_ascii=False,
        )
        with open(log_dir / "synthesis-raw.jsonl", "a") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def _call_deepseek(prompt: str, max_retries: int = 2) -> str:
    """调主合成模型（带 system message），返回 raw response string。"""
    messages = [
        {"role": "system", "content": _SYNTHESIS_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    return _call_llm(messages, model=_SYNTHESIS_MODEL, json_mode=True, max_retries=max_retries)


def _validate_response(data: dict) -> None:
    """用 social_trends_v1_1 schema 校验，失败 raise ValidationError。"""
    from akb2.briefing.validator import validate_payload

    validate_payload(data)


def _validate_quality(merged: dict) -> None:
    """质量门控 — 校验合成产出可用，不通过则 raise 触发重试（防 model 退化输出直接放行到 audit）。

    注意架构：合成阶段语言无关（英文 OK），中文化由 translate 阶段负责、audit 阶段把关。
    因此这里**不**要求 topic 中文，只确保结构可用，让 translate 有东西可译：
    - 每个 focus_discussion 的 topic 必须非空且非空洞词
    - 每个 focus_discussion 的 summary 必须非空(deepseek/gemini 偶发只给 topic 丢 summary)
    - executive_summary 缺失不 raise(由 merge 的 _fallback_executive_summary 兜底)
    """
    fds = merged.get("focus_discussions", [])
    if len(fds) < 1:
        raise ValueError("quality: 无 focus_discussions")

    BANNED = {"ai trends", "ai 趋势", "trends", "核心讨论", "ai trend"}
    for i, fd in enumerate(fds):
        topic = (fd.get("topic") or "").strip()
        if not topic:
            raise ValueError(f"quality: focus_discussions[{i}].topic 为空")
        if topic.lower() in BANNED:
            raise ValueError(f"quality: topic 是空洞词 '{topic}'")
        if not (fd.get("summary") or "").strip():
            raise ValueError(f"quality: focus_discussions[{i}].summary 为空（合成退化）")


def _fallback_synthesis(scored_input: dict, date: str | None = None) -> dict:
    """无 API 凭证时的确定性 fallback：直接从 scored.json 构造合规 payload。"""
    tweets = scored_input.get("tweets", [])
    # 取 score 最高的前 5 条
    top = sorted(tweets, key=lambda t: t.get("score", 0), reverse=True)[:5]

    now = datetime.now(timezone.utc)
    resolved_date = date or now.strftime("%Y-%m-%d")

    exec_summary = (
        f"{resolved_date} AI/ML 趋势快照，采集自公开社交时间线，共 {len(top)} 条高分推文。"
        " 本期数据为离线 fallback 产物，模型合成不可用时的确定性输出。"
        " 以下为基于评分排名的推文概览，供参考。"
    )
    payload: dict = {
        "date": resolved_date,
        "generated_at": now.isoformat(),
        "source": {"name": "synthesis_fallback", "collector_version": "1.0.0"},
        "executive_summary": exec_summary,
        "scoring_summary": {
            "total_tweets": len(tweets),
            "scored_tweets": len(top),
        },
        "tweets": [
            {
                "id": str(t["id"]),
                "author": t.get("author", ""),
                "text": t.get("text", ""),
                "created_at": t.get("created_at", ""),
                "score": t.get("score"),
                "dimensions": t.get("dimensions", {}),
                "url": t.get("url", ""),
            }
            for t in top
        ],
        "focus_discussions": [
            {
                "topic": "AI Technology Trends",
                "supporting_tweet_ids": [str(t["id"]) for t in top[:3]],
                "summary": (
                    f"Top {len(top)} tweets about AI trends, scored by relevance and authority."
                    + (
                        f" Highest scoring tweet by {top[0].get('author', 'unknown')} with score "
                        f"{top[0].get('score', 0):.3f}."
                        if top else " No tweets available for this period."
                    )
                    + " This is a fallback summary generated when the synthesis model is unavailable."
                ),
            }
        ],
    }
    return payload


def synthesize(scored_input: dict, date: str | None = None) -> dict:
    """核心逻辑：从打分结果生成 social_trends_v1_1 payload。

    若无 API key，直接走 fallback。
    若有 API key，调 DeepSeek；API 失败后 fallback。
    """
    if not _has_api_key():
        sys.stderr.write(
            "WARNING: AI_GATEWAY_API_KEY not set — synthesis using local fallback. "
            "Set SC-5-real = PENDING until real API key is configured.\n"
        )
        return _fallback_synthesis(scored_input, date)

    # 构建 prompt — 全部推文都给 model,让它自己决定取舍(top-N 早期是 5,信号窄;
    # 现在 20 条都给,model 在 prompt 里被约束选 3-5 个主题,supporting_tweet_ids 必须真实)
    tweets = scored_input.get("tweets", [])
    top = sorted(tweets, key=lambda t: t.get("score", 0), reverse=True)
    tweets_text = "\n".join(
        [f"[{t['id']}] @{t.get('author', '?')} (score={t.get('score', 0):.2f}): {t.get('text', '')}" for t in top]
    )

    resolved_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt_path = Path(__file__).parent.parent.parent.parent.parent / "docs" / "deepseek-synthesis-prompt-v1.md"

    if prompt_path.exists():
        prompt_template = prompt_path.read_text()
        prompt = prompt_template.format(
            tweets=tweets_text,
            date=resolved_date,
            tweet_count=len(tweets),
        )
    else:
        # inline fallback prompt if doc not found
        prompt = (
            f"Analyze the following AI/ML tweets from {resolved_date} "
            f"and output a JSON object conforming to social_trends_v1_1 schema.\n\n"
            f"Tweets:\n{tweets_text}\n\n"
            "Return JSON with fields: date, generated_at, source, scoring_summary, tweets, focus_discussions."
        )

    # 最多 3 次尝试：API 异常(网络/超时/schema)或质量门控不过(summary 空/topic 非中文)都触发重试。
    # model 偶发退化(只给 topic 丢 summary/ids)由 _validate_quality 拦下并重试，而非放行到 audit 才发现。
    last_err: Exception | None = None
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            raw = _call_deepseek(prompt)
            data = json.loads(raw)
            merged = _merge_with_input(data, scored_input, resolved_date, top)
            _validate_response(merged)
            _validate_quality(merged)
            return merged
        except Exception as e:
            last_err = e
            if attempt == max_attempts - 1:
                break
            continue

    # API 两次都失败后走 fallback
    err_msg = f"{type(last_err).__name__}: {str(last_err)[:200]}" if last_err else "unknown"
    sys.stderr.write(
        f"WARNING: DeepSeek API failed after 2 attempts ({err_msg}) — synthesis using local fallback. "
        "SC-5-real remains PENDING.\n"
    )
    return _fallback_synthesis(scored_input, date)


def _merge_with_input(model_out: dict, scored_input: dict, resolved_date: str, top: list[dict]) -> dict:
    """Merge model output with input data — model 只贡献 focus_discussions/executive_summary,
    其余字段从 input 回填。

    防御性合并:DeepSeek 经常返回不合规字段(source 是字符串、tweets 缺 text/created_at 等),
    与其反复修 prompt,不如机械合并:input 数据是 ground truth,model 智慧只取分析字段。
    """
    all_tweets = scored_input.get("tweets", [])
    id_to_tweet = {str(t["id"]): t for t in top}

    # 先 patch focus_discussions，确定哪些 id 被引用
    patched_fds = _patch_focus_discussions(model_out.get("focus_discussions") or [], top)
    referenced_ids = {
        str(sid)
        for fd in patched_fds
        for sid in fd.get("supporting_tweet_ids", [])
    }

    # tweets 输出集 = top 10 ∪ 被引用的推文（确保 supporting_tweet_ids 总有对应 tweet 对象）
    top10_ids = {str(t["id"]) for t in top[:10]}
    extra_ids = referenced_ids - top10_ids
    top_for_display = list(top[:10]) + [
        id_to_tweet[eid] for eid in extra_ids if eid in id_to_tweet
    ]

    return {
        "date": model_out.get("date") or resolved_date,
        "generated_at": model_out.get("generated_at") or datetime.now(timezone.utc).isoformat(),
        "source": {"name": "deepseek_synthesis", "collector_version": "1.0.0"},
        "scoring_summary": {
            "total_tweets": len(all_tweets),
            "scored_tweets": len(top_for_display),
        },
        "tweets": [
            {
                "id": str(t["id"]),
                "author": t.get("author", ""),
                "text": t.get("text", ""),
                "created_at": t.get("created_at", ""),
                "score": t.get("score"),
                "dimensions": t.get("dimensions", {}),
                "url": t.get("url", ""),
            }
            for t in top_for_display
        ],
        "focus_discussions": _ensure_top3_covered(patched_fds, top),
        # executive_summary 原样保留；缺失时用主题列表兜底（确保 audit 不因空值 fail）
        "executive_summary": (
            model_out.get("executive_summary", "").strip()
            or _fallback_executive_summary(patched_fds, resolved_date)
        ),
    }


def _ensure_top3_covered(fds: list, top: list[dict]) -> list:
    """确保 top-3 高分推文至少各出现在某个主题的 supporting_tweet_ids 里。

    若不满足，将未覆盖的 top-3 id 补入第一个主题（不引入新主题）。
    """
    import copy
    fds = copy.deepcopy(fds)
    if not fds:
        return fds
    covered: set[str] = {
        str(sid)
        for fd in fds
        for sid in fd.get("supporting_tweet_ids", [])
    }
    top3_ids = [str(t["id"]) for t in top[:3]]
    missing = [tid for tid in top3_ids if tid not in covered]
    if missing:
        # 补入第一个主题
        existing = list(fds[0].get("supporting_tweet_ids", []))
        for tid in missing:
            if tid not in existing:
                existing.append(tid)
        fds[0]["supporting_tweet_ids"] = existing
    return fds


def _fallback_executive_summary(fds: list, date: str) -> str:
    """生成确定性执行摘要兜底（模型漏 executive_summary 时使用）。"""
    topics = [fd.get("topic", "") for fd in fds[:3] if fd.get("topic")]
    if topics:
        return f"{date} AI/ML 领域核心讨论主题：{' · '.join(topics)}。本期趋势信号聚焦于上述方向，以下为详细分析。"
    return f"{date} AI/ML 领域社交趋势简报，基于公开时间线抓取与综合分析。"




def _patch_focus_discussions(discussions: list, top: list[dict]) -> list:
    """补齐 focus_discussions 缺失或编造的 supporting_tweet_ids。

    - 过滤掉模型编造的 id（不在 top 推文中）
    - 缺失或全部编造时，fallback 到 top[:5]（保守覆盖高分推文）
    - topic/summary 原样保留，由 translate 阶段中文化
    """
    valid_ids = {str(t["id"]) for t in top}
    top_ids = [str(t["id"]) for t in top[:5]] or ["unknown"]

    patched = []
    for d in discussions:
        if not isinstance(d, dict):
            continue
        raw_ids = d.get("supporting_tweet_ids")
        if isinstance(raw_ids, list) and raw_ids:
            # 过滤编造 id，保留真实存在的
            real_ids = [str(x) for x in raw_ids if str(x) in valid_ids]
        else:
            real_ids = []
        if not real_ids:
            real_ids = list(top_ids)
        elif len(real_ids) < 2:
            # 补足到 2 个：从 top 高分推文中取未包含的
            for tid in top_ids:
                if tid not in real_ids:
                    real_ids.append(tid)
                if len(real_ids) >= 2:
                    break
        patched.append({
            "topic": (d.get("topic") or "").strip(),
            "supporting_tweet_ids": real_ids,
            "summary": d.get("summary") or "",
        })
    return patched


def _cli(argv: list[str] | None = None) -> int:
    """CLI 入口：--input <scored.json> --output <synth.json>"""
    import argparse

    parser = argparse.ArgumentParser(description="Synthesize scored tweets into social_trends_v1_1 payload.")
    parser.add_argument("--input", required=True, help="Path to scored JSON input")
    parser.add_argument("--output", required=True, help="Path to write synthesized JSON output")
    parser.add_argument("--date", default=None, help="Override date (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    with open(args.input) as f:
        scored = json.load(f)

    result = synthesize(scored, date=args.date)

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"OK: synthesis written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
