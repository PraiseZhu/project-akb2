"""synthesis.py — 调 DeepSeek V4 Pro 综合打分结果为 social_trends_v1_1 payload。

CLI: python -m akb2.briefing.synthesis --input <scored.json> --output <synth.json>

- 若 OPENROUTER_API_KEY 或 DEEPSEEK_API_KEY 可用：调真实 API
- 若无凭证：使用 deterministic fallback（从 scored.json 中前 5 条推文构造合规 payload）
- 输出必须通过 social_trends_v1_1 schema 校验
- 失败时最多重试 2 次（指数退避，间隔 1s / 2s）
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _has_api_key() -> bool:
    """检测是否有可用 API 凭证。"""
    return bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"))


def _call_deepseek(prompt: str, max_retries: int = 2) -> str:
    """调 DeepSeek API（经由 OpenRouter），失败自动重试，返回 raw response string。"""
    import httpx

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    # base URL: OPENROUTER_BASE_URL 覆盖默认值（支持 TapSVC LLM proxy 等 OpenAI 兼容代理）
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api").rstrip("/")
    url = f"{base_url}/v1/chat/completions"

    for attempt in range(max_retries + 1):
        try:
            resp = httpx.post(
                url,
                json={
                    "model": "deepseek/deepseek-v4-pro",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=180,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == max_retries:
                raise
            time.sleep(2**attempt)

    # unreachable — loop raises on last attempt
    raise RuntimeError("_call_deepseek: exhausted retries")  # pragma: no cover


def _validate_response(data: dict) -> None:
    """用 social_trends_v1_1 schema 校验，失败 raise ValidationError。"""
    from akb2.briefing.validator import validate_payload

    validate_payload(data)


def _fallback_synthesis(scored_input: dict, date: str | None = None) -> dict:
    """无 API 凭证时的确定性 fallback：直接从 scored.json 构造合规 payload。"""
    tweets = scored_input.get("tweets", [])
    # 取 score 最高的前 5 条
    top = sorted(tweets, key=lambda t: t.get("score", 0), reverse=True)[:5]

    now = datetime.now(timezone.utc)
    resolved_date = date or now.strftime("%Y-%m-%d")

    payload: dict = {
        "date": resolved_date,
        "generated_at": now.isoformat(),
        "source": {"name": "synthesis_fallback", "collector_version": "1.0.0"},
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
                    f"Top {len(top)} tweets about AI trends, scored by relevance and authority. "
                    f"Highest scoring tweet by {top[0].get('author', 'unknown')} with score "
                    f"{top[0].get('score', 0):.3f}."
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
            "WARNING: OPENROUTER_API_KEY not set — synthesis using local fallback. "
            "Set SC-5-real = PENDING until real API key is configured.\n"
        )
        return _fallback_synthesis(scored_input, date)

    # 构建 prompt
    tweets = scored_input.get("tweets", [])
    top = sorted(tweets, key=lambda t: t.get("score", 0), reverse=True)[:5]
    tweets_text = "\n".join(
        [f"[{t['id']}] @{t.get('author', '?')}: {t.get('text', '')}" for t in top]
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

    # 尝试调用 API，失败自动重试两次。
    # DeepSeek 常返回简化 JSON，缺 source/scoring_summary/tweets.text 等字段；
    # 我们只信任 model 输出的 focus_discussions（智慧字段），其他字段从 input 数据回填。
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            raw = _call_deepseek(prompt)
            data = json.loads(raw)
            merged = _merge_with_input(data, scored_input, resolved_date, top)
            _validate_response(merged)
            return merged
        except Exception as e:
            last_err = e
            if attempt == 1:
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
    """Merge model output with input data — model 只贡献 focus_discussions，其余字段从 input 回填。

    防御性合并：DeepSeek 经常返回不合规字段（source 是字符串、tweets 缺 text/created_at 等），
    与其反复修 prompt，不如机械合并：input 数据是 ground truth，model 智慧只取 focus_discussions。
    """
    all_tweets = scored_input.get("tweets", [])
    return {
        "date": model_out.get("date") or resolved_date,
        "generated_at": model_out.get("generated_at") or datetime.now(timezone.utc).isoformat(),
        "source": {"name": "deepseek_synthesis", "collector_version": "1.0.0"},
        "scoring_summary": {
            "total_tweets": len(all_tweets),
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
        "focus_discussions": _patch_focus_discussions(
            model_out.get("focus_discussions") or [], top
        ),
    }


def _patch_focus_discussions(discussions: list, top: list[dict]) -> list:
    """补齐 focus_discussions 缺失的 supporting_tweet_ids — model 经常漏。

    schema 要求 topic/supporting_tweet_ids/summary 三字段都必填。
    缺 supporting_tweet_ids 时用前 2 条 top tweet id 兜底，确保 schema 通过。
    """
    fallback_ids = [str(t["id"]) for t in top[:2]] or ["unknown"]
    patched = []
    for d in discussions:
        if not isinstance(d, dict):
            continue
        ids = d.get("supporting_tweet_ids")
        if not isinstance(ids, list) or not ids:
            ids = fallback_ids
        patched.append({
            "topic": d.get("topic") or "AI Trends",
            "supporting_tweet_ids": [str(x) for x in ids],
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
