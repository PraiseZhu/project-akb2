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


def _call_deepseek(messages: list[dict], max_retries: int = 2) -> str:
    """调 DeepSeek API（经由 OpenRouter），失败自动重试，返回 raw response string。

    messages 格式：[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
    system+user 拆分让模型更稳定遵守角色约束，减少语言漂移。
    """
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
                    "messages": messages,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=300,
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


def _validate_quality(merged: dict) -> None:
    """额外质量校验 — 只校验"中文 topic"这个最关键的约束,其他缺失字段由 _merge_with_input 兜底合成。

    - 至少 50% 的 topic 含中文字符(防 model 全英文输出),不通过 raise 触发重试
    - executive_summary 和 key_quotes 缺失不 raise(由 merge 兜底合成),不浪费 API 重试
    """
    fds = merged.get("focus_discussions", [])
    if len(fds) < 1:
        raise ValueError("quality: 无 focus_discussions")

    BANNED = {"ai trends", "ai 趋势", "trends", "核心讨论", "ai trend"}
    chinese_count = 0
    for fd in fds:
        topic = (fd.get("topic") or "").strip()
        if topic.lower() in BANNED:
            raise ValueError(f"quality: topic 是空洞词 '{topic}'")
        if sum(1 for c in topic if "一" <= c <= "鿿") >= 2:
            chinese_count += 1
    if chinese_count < len(fds) * 0.5:
        raise ValueError(f"quality: 仅 {chinese_count}/{len(fds)} 个 topic 是中文,违反语言要求")


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

    # top-10：信号足够且 prompt 不超长；20 条时 prompt ~8K+ 字符反而让 model 更容易漂移
    tweets = scored_input.get("tweets", [])
    top = sorted(tweets, key=lambda t: t.get("score", 0), reverse=True)[:10]
    tweets_text = "\n".join(
        [f"[{t['id']}] @{t.get('author', '?')} (score={t.get('score', 0):.2f}): {t.get('text', '')}" for t in top]
    )

    resolved_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt_path = Path(__file__).parent.parent.parent.parent.parent / "docs" / "deepseek-synthesis-prompt-v1.md"

    # 构建 system+user messages — 从 prompt 文档以 "\n## USER\n" 分割
    FALLBACK_SYSTEM = (
        "你是 AI/ML 社交趋势分析师。基于输入推文输出结构化中文趋势简报 JSON。"
        "所有 string 字段必须简体中文。不编造，所有 ID 必须来自输入。"
    )
    FALLBACK_USER = (
        f"分析以下 AI/ML 推文（{resolved_date}，共 {len(top)} 条），"
        f"输出 JSON，含 executive_summary、focus_discussions（3-5条）、key_quotes（2-4条）。\n\n{tweets_text}"
    )

    if prompt_path.exists():
        template = prompt_path.read_text()
        if "\n## USER\n" in template:
            sys_raw, usr_raw = template.split("\n## USER\n", 1)
            system_content = sys_raw.replace("## SYSTEM\n", "").strip()
            user_content = usr_raw.strip().format(
                tweets=tweets_text,
                date=resolved_date,
                tweet_count=len(top),
            )
        else:
            # 旧格式 prompt 整体作为 user message
            system_content = FALLBACK_SYSTEM
            user_content = template.format(
                tweets=tweets_text, date=resolved_date, tweet_count=len(top)
            )
    else:
        system_content = FALLBACK_SYSTEM
        user_content = FALLBACK_USER

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

    # 仅 API 异常时重试一次；质量问题由 _merge_with_input + _patch_* + _validate_anti_fill 代码兜底
    input_tweet_ids = {str(t["id"]) for t in tweets}
    input_authors = {t.get("author", "") for t in tweets}
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            raw = _call_deepseek(messages)
            data = json.loads(raw)
            merged = _merge_with_input(data, scored_input, resolved_date, top)
            _validate_response(merged)
            _validate_anti_fill(merged, input_tweet_ids, input_authors)
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
    """Merge model output with input data — model 只贡献 focus_discussions/executive_summary/key_quotes,
    其余字段从 input 回填。

    防御性合并:DeepSeek 经常返回不合规字段(source 是字符串、tweets 缺 text/created_at 等),
    与其反复修 prompt,不如机械合并:input 数据是 ground truth,model 智慧只取分析字段。
    """
    all_tweets = scored_input.get("tweets", [])
    # tweets 字段只渲染前 10 条最高分(markdown 模板也只展示 top 10),减少简报噪声
    top_for_display = top[:10]
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
        "focus_discussions": (patched_fds := _patch_focus_discussions(
            model_out.get("focus_discussions") or [], top
        )),
        # 扩展字段 — model 经常漏,由代码兜底合成确保 markdown 章节完整;
        # executive_summary fallback 用已 patched 的(中文化后的)focus_discussions
        "executive_summary": (
            model_out.get("executive_summary", "").strip()
            or _synthesize_executive_summary(patched_fds)
        ),
        "key_quotes": (
            _patch_key_quotes(model_out.get("key_quotes") or [], top)
            or _synthesize_key_quotes(top_for_display)
        ),
    }


def _synthesize_executive_summary(focus_discussions: list) -> str:
    """model 漏 executive_summary 时,从 focus_discussions 拼接合成。"""
    if not focus_discussions:
        return ""
    topics = [(fd.get("topic") or "").strip() for fd in focus_discussions if isinstance(fd, dict)]
    topics = [t for t in topics if t]
    if not topics:
        return ""
    return "今日 AI/ML 讨论聚焦：" + "、".join(topics[:5]) + "。详见下方核心讨论与关键推文。"


def _synthesize_key_quotes(top_tweets: list[dict]) -> list:
    """model 漏 key_quotes 时,从 top 推文取前 3 条作引述兜底,确保章节完整。"""
    quotes = []
    for t in top_tweets[:3]:
        text = (t.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        snippet = text[:180] + ("…" if len(text) > 180 else "")
        quotes.append({
            "author": t.get("author", ""),
            "quote": snippet,
            "tweet_id": str(t.get("id", "")),
            "url": t.get("url", ""),
            "context": f"评分 {t.get('score', 0):.2f}",
        })
    return quotes


def _patch_key_quotes(quotes: list, top: list[dict]) -> list:
    """补齐 key_quotes — 防御 model 漏字段,过滤掉非 dict / 缺 tweet_id 的条目,
    给每条 quote 找对应 tweet 的 url 字段(model 一般不会给 url)。
    """
    id_to_url = {str(t["id"]): t.get("url", "") for t in top}
    id_to_author = {str(t["id"]): t.get("author", "") for t in top}
    patched = []
    for q in quotes:
        if not isinstance(q, dict):
            continue
        tid = str(q.get("tweet_id", ""))
        # 必须能 trace 到真实 tweet,否则丢弃(防 model 编造 id)
        if tid and tid in id_to_url:
            patched.append({
                "author": q.get("author") or id_to_author.get(tid, ""),
                "quote": q.get("quote", ""),
                "tweet_id": tid,
                "url": id_to_url.get(tid, ""),
                "context": q.get("context", ""),
            })
    return patched


def _patch_focus_discussions(discussions: list, top: list[dict]) -> list:
    """补齐 focus_discussions 缺失的 supporting_tweet_ids + 空洞 topic 自动改写。

    - schema 要求 topic/supporting_tweet_ids/summary 三字段都必填
    - 缺 supporting_tweet_ids 时用前 2 条 top tweet id 兜底
    - topic 是 "AI Trends" 等空洞词时,从 summary 前 25 字提取作 topic(去掉句末标点)
    """
    fallback_ids = [str(t["id"]) for t in top[:2]] or ["unknown"]
    BANNED = {"ai trends", "ai 趋势", "trends", "ai trend", "today's trends", "今日趋势", ""}
    patched = []
    for i, d in enumerate(discussions, 1):
        if not isinstance(d, dict):
            continue
        ids = d.get("supporting_tweet_ids")
        if not isinstance(ids, list) or not ids:
            ids = fallback_ids
        topic = (d.get("topic") or "").strip()
        # 含中文字符数(≥2 视为合格中文 topic)
        chinese_chars = sum(1 for c in topic if "一" <= c <= "鿿")
        is_banned = topic.lower() in BANNED
        is_english = chinese_chars < 2  # 全英文或几乎全英文
        if is_banned or is_english:
            summary = (d.get("summary") or "").strip()
            # summary 也优先取中文段:扫描第一句中文(到首个句号/分号/换行)
            if summary and any("一" <= c <= "鿿" for c in summary):
                # 取 summary 第一段(中文优先),≤30 字
                first_seg = summary.split("。")[0].split("\n")[0].split(";")[0].strip()
                if first_seg and any("一" <= c <= "鿿" for c in first_seg):
                    topic = first_seg[:30].rstrip("，。；,;.：:").rstrip()
                    if len(first_seg) > 30:
                        topic = topic + "…"
                else:
                    topic = f"AI 趋势观察 #{i}"
            else:
                topic = f"AI 趋势观察 #{i}"
        patched.append({
            "topic": topic,
            "supporting_tweet_ids": [str(x) for x in ids],
            "summary": d.get("summary") or "",
        })
    return patched


def _validate_anti_fill(merged: dict, input_tweet_ids: set[str], input_authors: set[str]) -> dict:
    """后验 anti-fill 校验：检测 model 编造的 ID 和 @mention。

    不抛出，只写 stderr 警告 + 返回报告 {passed, violations}。
    _patch_key_quotes 已经过滤掉编造 ID，这里是第二道净化记录层。
    """
    import re

    violations: list[str] = []
    normalised_authors = {a.lstrip("@").lower() for a in input_authors if a}

    # 1. focus_discussions supporting_tweet_ids 必须在 input 中
    for fd in merged.get("focus_discussions", []):
        for tid in fd.get("supporting_tweet_ids", []):
            if str(tid) not in input_tweet_ids:
                violations.append(f"fabricated tweet_id in focus_discussions: {tid}")

    # 2. key_quotes tweet_id 必须在 input 中（_patch_key_quotes 已过滤，这里做双重记录）
    for q in merged.get("key_quotes", []):
        tid = str(q.get("tweet_id", ""))
        if tid and tid not in input_tweet_ids:
            violations.append(f"fabricated tweet_id in key_quotes: {tid}")

    # 3. key_quotes.author 必须在 input author 集合内
    for q in merged.get("key_quotes", []):
        author = q.get("author", "").lstrip("@").lower()
        if author and author not in normalised_authors:
            violations.append(f"key_quotes author not in input authors: {q.get('author', '')}")

    # 4. @mention 在 summary / quote / context 正文里也必须是真实 author
    all_text = " ".join(
        fd.get("summary", "") for fd in merged.get("focus_discussions", [])
    ) + " " + " ".join(
        (q.get("quote", "") + " " + q.get("context", ""))
        for q in merged.get("key_quotes", [])
    )
    for m in re.findall(r"@(\w+)", all_text):
        if m.lower() not in normalised_authors:
            violations.append(f"@mention not in input authors: @{m}")

    if violations:
        sys.stderr.write(f"[anti-fill] {len(violations)} violation(s) detected:\n")
        for v in violations:
            sys.stderr.write(f"  - {v}\n")

    return {"passed": len(violations) == 0, "violations": violations}


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
