"""7 维加权打分模块。

7 维定义与默认权重：
  recency          (0.10) — 按 created_at 衰减：0h=1.0, 24h=0.5, 72h=0.1
  engagement       (0.15) — log(likes+retweets+replies+1)/log(10000) clamp[0,1]
  author_authority (0.30) — 白名单账号=1.0, 未知=0.3
  topic_relevance  (0.30) — AI 关键词命中比例
  signal_density   (0.07) — 文本长度/链接数/数字密度复合
  novelty          (0.05) — 文本 SHA1 前缀去重
  safety           (0.03) — 命中违禁词强制=0.0

阈值常量：
  NOVELTY_BAND = 6  # SHA1 前缀长度（十六进制字符数）
  SAFETY_FLOOR = 0.0  # safety=0 时 score 上限约 0.15-0.3（取决于其他维度）

CLI: python -m akb2.briefing.scoring --input <intake.json> --output <scored.json>
     [--weights-json <json-str>]

输出格式：
{
  "tweets": [
    {
      ... 原始字段 ...,
      "score": 0.85,
      "dimensions": {
        "recency": 0.9,
        "engagement": 0.5,
        "author_authority": 1.0,
        "topic_relevance": 0.8,
        "signal_density": 0.7,
        "novelty": 1.0,
        "safety": 1.0
      }
    }
  ]
}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

NOVELTY_BAND = 6  # SHA1 前缀长度（十六进制字符数）
SAFETY_FLOOR = 0.0  # safety 命中违禁词时的返回值

AUTHORITY_WHITELIST = {
    "openai", "sama", "anthropic", "anthropicai", "darioamodei",
    "goodfellow_ia", "ylecun", "karpathy", "geoffreyhinton",
    "demishassabis", "deepmind", "googledeepmind", "googleai",
    "huggingface", "mistralai", "xai", "deepseek_ai", "aiatmeta",
    "paulfchristiano", "mckaywrigley", "github", "swyx",
}

TOPIC_KEYWORDS = {
    "ai", "llm", "agi", "gpt", "claude", "llama", "deepseek",
    "transformer", "neural", "model", "agent", "multimodal",
    "reasoning", "inference", "fine-tuning", "alignment",
}

BANNED_KEYWORDS = {
    "violence", "hack", "exploit", "malware", "ransomware",
    "scam", "phishing", "spam", "nsfw", "illegal",
}

DEFAULT_WEIGHTS = {
    "recency": 0.10,
    "engagement": 0.15,
    "author_authority": 0.30,
    "topic_relevance": 0.30,
    "signal_density": 0.07,
    "novelty": 0.05,
    "safety": 0.03,
}


# ---------------------------------------------------------------------------
# 7 维打分函数
# ---------------------------------------------------------------------------

def recency(tweet: dict) -> float:
    """按 created_at 衰减：0h=1.0, 24h=0.5, 72h+=0.1。"""
    try:
        created = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        hours = (now - created).total_seconds() / 3600
        if hours <= 0:
            return 1.0
        if hours >= 72:
            return 0.1
        # 线性插值：0h=1.0, 24h=0.5, 72h=0.1
        if hours <= 24:
            return 1.0 - 0.5 * (hours / 24)
        else:
            return 0.5 - 0.4 * ((hours - 24) / 48)
    except Exception:
        return 0.5


def engagement(tweet: dict) -> float:
    """log(likes+retweets+replies+1)/log(10000) clamp[0,1]。"""
    m = tweet.get("metrics", {})
    total = m.get("likes", 0) + m.get("retweets", 0) + m.get("replies", 0)
    return min(1.0, math.log(total + 1) / math.log(10000))


def author_authority(tweet: dict) -> float:
    """白名单账号=1.0，未知=0.3。"""
    author = tweet.get("author", "").lower().lstrip("@")
    return 1.0 if author in AUTHORITY_WHITELIST else 0.3


def topic_relevance(tweet: dict) -> float:
    """AI 关键词命中比例，3 个关键词命中 = 满分。"""
    text = tweet.get("text", "").lower()
    hits = sum(1 for kw in TOPIC_KEYWORDS if kw in text)
    return min(1.0, hits / 3)


def signal_density(tweet: dict) -> float:
    """文本长度/链接数/数字密度复合评分。"""
    text = tweet.get("text", "")
    if not text:
        return 0.0
    length_score = min(1.0, len(text) / 280)
    url_count = len(re.findall(r"https?://", text))
    url_penalty = max(0.0, 1.0 - url_count * 0.2)
    numbers = len(re.findall(r"\d+", text))
    number_bonus = min(0.3, numbers * 0.05)
    return min(1.0, length_score * 0.6 + url_penalty * 0.3 + number_bonus * 0.1)


def novelty(tweet: dict, seen_hashes: set) -> float:
    """文本 SHA1 前缀去重：首次出现=1.0，重复=0.0。"""
    text = tweet.get("text", "")
    h = hashlib.sha1(text.encode()).hexdigest()[:NOVELTY_BAND]
    if h in seen_hashes:
        return 0.0
    seen_hashes.add(h)
    return 1.0


def safety(tweet: dict) -> float:
    """命中违禁词返回 SAFETY_FLOOR=0.0，否则 1.0。"""
    text = tweet.get("text", "").lower()
    for kw in BANNED_KEYWORDS:
        if kw in text:
            return SAFETY_FLOOR
    return 1.0


# ---------------------------------------------------------------------------
# ScoreEngine
# ---------------------------------------------------------------------------

class ScoreEngine:
    def __init__(self, weights: dict | None = None) -> None:
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        self._seen_hashes: set = set()

    def score_tweet(self, tweet: dict) -> dict:
        dims = {
            "recency": recency(tweet),
            "engagement": engagement(tweet),
            "author_authority": author_authority(tweet),
            "topic_relevance": topic_relevance(tweet),
            "signal_density": signal_density(tweet),
            "novelty": novelty(tweet, self._seen_hashes),
            "safety": safety(tweet),
        }
        score = sum(self.weights[k] * v for k, v in dims.items())
        score = max(0.0, min(1.0, score))  # clamp [0,1]
        result = dict(tweet)
        result["score"] = round(score, 6)
        result["dimensions"] = {k: round(v, 6) for k, v in dims.items()}
        return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="7-dim tweet scorer")
    parser.add_argument("--input", required=True, help="Input JSON file (intake format)")
    parser.add_argument("--output", required=True, help="Output JSON file (scored format)")
    parser.add_argument(
        "--weights-json",
        default=None,
        help="JSON string or path to JSON file overriding default weights",
    )
    args = parser.parse_args()

    weights = None
    if args.weights_json:
        try:
            weights = json.loads(args.weights_json)
        except json.JSONDecodeError:
            with open(args.weights_json) as wf:
                weights = json.load(wf)

    with open(args.input) as f:
        data = json.load(f)

    engine = ScoreEngine(weights=weights)
    scored_tweets = [engine.score_tweet(t) for t in data.get("tweets", [])]

    result = {"tweets": scored_tweets}
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"Scored {len(scored_tweets)} tweets → {args.output}")


if __name__ == "__main__":
    main()
