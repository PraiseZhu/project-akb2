"""GitHub repo scoring — 6-dimension weighted score with safety hard-downgrade.

v2 changes (2026-05-29):
- mention_count denominator 5→12 (de-saturate)
- mentioner_authority accepts curator tier (decoupled from mention_count)
- activity_score replaces recency_score (commits_7d + star velocity)
- Weights rearranged: mention 0.30 / authority 0.15 / stars 0.10 /
  activity 0.15 / readme 0.10 / topic 0.20 (sum = 1.0)
"""

import math
import re
from datetime import datetime, timezone
from typing import Any

# 6-dimension weights (sum = 1.0)
W_MENTION_COUNT = 0.30
W_MENTIONER_AUTHORITY = 0.15
W_STARS = 0.10
W_ACTIVITY = 0.15
W_README_QUALITY = 0.10
W_TOPIC_RELEVANCE = 0.20

# Authority tier weights
_TIER_WEIGHT = {1: 1.0, 2: 0.6, 3: 0.3}
_AUTHORITY_NORM = 3.0  # ~3 core curators = full score

# Safety keywords that force score = 0
_SAFETY_BLOCK_RE = re.compile(
    r"\b(crypto|nft|token\s*sale|airdrop|nsfw|porn|xxx|adult|escort"
    r"|casino|gambling|slot|betting|drug|marijuana|cannabis)\b",
    re.IGNORECASE,
)

# Topic relevance — curated list of AI/engineering topics
_RELEVANT_TOPICS = {
    "ai", "agent", "llm", "machine-learning", "deep-learning",
    "ml", "nlp", "computer-vision", "rag", "vector-database",
    "python", "rust", "go", "typescript", "javascript",
    "react", "nextjs", "vue", "svelte",
    "cli", "api", "sdk", "framework", "library",
    "developer-tools", "devtools", "tooling",
    "open-source", "infrastructure", "kubernetes", "docker",
    "testing", "security", "privacy",
}


def mention_count_score(mentions: int) -> float:
    """Score by mention frequency. Denominator 12 avoids saturation
    at the filter threshold (5), giving headroom for differentiation.

    mention=2→0.167, 5→0.417, 8→0.667, 12→1.0 (strictly monotonic).
    """
    return min(1.0, mentions / 12)


def mentioner_authority_score(
    mentioners: list[str] | list[dict],
    tier_map: dict[str, int] | None = None,
) -> float:
    """Score by curator tier authority, decoupled from mention count.

    With tier_map:     sum(tier_weight) / NORM  (tier: 1=1.0, 2=0.6, 3=0.3)
    Without tier_map:  len/5 proxy (backward compatible)

    Args:
        mentioners: List of curator handles (str) or dicts with 'handle' key.
        tier_map: Optional {handle: tier} mapping from curator pool config.
    """
    if not mentioners:
        return 0.0

    if tier_map:
        total = 0.0
        for m in mentioners:
            handle = m if isinstance(m, str) else m.get("handle", "")
            # Strip @ prefix for matching
            tier = tier_map.get(handle.lstrip("@"), tier_map.get(handle, 0))
            if tier > 0:
                total += _TIER_WEIGHT.get(tier, 0.3)  # unknown tier → 0.3
        return min(1.0, total / _AUTHORITY_NORM)

    # Fallback: simple count proxy
    return min(1.0, len(mentioners) / 5)


def stars_score(stars: int) -> float:
    if stars <= 0:
        return 0.0
    return min(1.0, math.log10(stars) / 5)


def activity_score(
    commits_7d: int | None = None,
    velocity_7d: int | None = None,
) -> float:
    """Activity score replacing recency_score.

    Fallback chain (first available wins):
    1. velocity_7d != None  → min(1.0, velocity_7d / 50)
    2. commits_7d != None   → min(1.0, commits_7d / 30)
       (commits_7d=0 means truly inactive → 0.0)
    3. Both None            → 0.5 (neutral — unknown)

    Critical: commits_7d=None (API not ready / 202 / error) ≠ commits_7d=0 (dead repo).
    """
    if velocity_7d is not None:
        return min(1.0, velocity_7d / 50)
    if commits_7d is not None:
        return min(1.0, commits_7d / 30)
    return 0.5  # neutral for unknown


def readme_quality_score(readme_excerpt: str | None) -> float:
    if readme_excerpt is None:
        return 0.0
    score = 0.0
    if len(readme_excerpt) > 1000:
        score += 0.5
    if "```" in readme_excerpt:
        score += 0.5
    return score


def topic_relevance_score(topic_tags: list[str]) -> float:
    if not topic_tags:
        return 0.0
    matched = sum(1 for t in topic_tags if t.lower() in _RELEVANT_TOPICS)
    return min(1.0, matched / 6)


def safety_check(repo_data: dict[str, Any]) -> bool:
    """Returns True if repo passes safety filter."""
    # Check topics
    for topic in repo_data.get("topic_tags", []):
        if _SAFETY_BLOCK_RE.search(topic):
            return False
    # Check readme excerpt
    readme = repo_data.get("readme_excerpt") or ""
    if _SAFETY_BLOCK_RE.search(readme):
        return False
    # Check repo name in URL
    url = repo_data.get("repo_url", "")
    if _SAFETY_BLOCK_RE.search(url):
        return False
    return True


def score(repo_data: dict[str, Any]) -> float:
    """Compute 6-dimension weighted score for a repository."""
    if not safety_check(repo_data):
        return 0.0

    mention_count = repo_data.get("mention_count", 0)
    mentioners = repo_data.get("mentioners", [])
    stars = repo_data.get("stars", 0)
    commits_7d = repo_data.get("commits_7d")  # int|None
    velocity_7d = repo_data.get("velocity_7d")  # int|None
    readme_excerpt = repo_data.get("readme_excerpt")
    topic_tags = repo_data.get("topic_tags", [])
    tier_map = repo_data.get("_tier_map")  # injected by pipeline

    s1 = W_MENTION_COUNT * mention_count_score(mention_count)
    s2 = W_MENTIONER_AUTHORITY * mentioner_authority_score(mentioners, tier_map)
    s3 = W_STARS * stars_score(stars)
    s4 = W_ACTIVITY * activity_score(commits_7d=commits_7d, velocity_7d=velocity_7d)
    s5 = W_README_QUALITY * readme_quality_score(readme_excerpt)
    s6 = W_TOPIC_RELEVANCE * topic_relevance_score(topic_tags)

    total = s1 + s2 + s3 + s4 + s5 + s6
    return round(min(1.0, total), 4)


def score_batch(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score and sort repos by descending score."""
    for r in repos:
        r["score"] = score(r)
    return sorted(repos, key=lambda r: r.get("score", 0), reverse=True)


def mention_threshold(active_curators_today: int) -> int:
    """Mention threshold = 2（固定）。

    历史：曾用 max(2, ceil(active/15))，57 个活跃博主算出 4，对真实单日数据过严
    （多数 repo 当天仅被 1 个博主提到）→ filter 后 <10 → 当天 skip 无简报。
    用户决策：固定为 2（被 ≥2 个博主提到即够格进候选，排序再靠 stars/活跃度等维度）。
    保留 active_curators_today 参数以兼容调用方签名。
    """
    return 2