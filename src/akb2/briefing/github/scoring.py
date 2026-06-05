"""GitHub repo scoring — 6-dimension weighted score with safety hard-downgrade.

v2 changes (2026-05-29):
- mention_count denominator 5→12 (de-saturate)
- mentioner_authority accepts curator tier (decoupled from mention_count)
- activity_score replaces recency_score (commits_7d + star velocity)

v3 changes (2026-06-04): mention_count 退出加权和。真实链路验收发现 curator pool 内
多人撞车极罕见（全天 61 博主仅 1 个 repo 被 ≥2 人提到）→ mention 频次 ≈ 常数 1，
旧 0.30 权重变死权重，top10 实质随机。改为：
- engagement（推文热度，likes+2rt+3bm log 归一）入维，curator 质量的次要信号
- mentioner_authority 升为主信号 0.35（"谁推荐的" = curator pool 真正买到的东西）
- mention 共识改为罕见 bonus（2+ 独立博主 = 最强信号，+0.08 不进加权和）
- 权重：authority 0.35 / topic 0.20 / engagement 0.15 / activity 0.15 /
  stars 0.10 / readme 0.05 (sum = 1.0)
"""

import math
import re
from typing import Any

# 6 weighted dims (sum = 1.0) — v3
W_MENTIONER_AUTHORITY = 0.35
W_TOPIC_RELEVANCE = 0.20
W_ENGAGEMENT = 0.15
W_ACTIVITY = 0.15
W_STARS = 0.10
W_README_QUALITY = 0.05

# 独立共识 bonus：2+ 个博主独立提到同一 repo = 所有信号里最强（罕见但高价值）。
# 不进加权和，直接给最终分加成（封顶 1.0）。
MULTI_MENTION_BONUS = 0.08

# engagement log 归一化分母：min(1.0, log10(raw+1)/NORM)。raw=1k→0.75, 1万→1.0。
_ENGAGEMENT_NORM = 4.0

# Authority tier weights
_TIER_WEIGHT = {1: 1.0, 2: 0.6, 3: 0.3}
_AUTHORITY_NORM = 3.0  # ~3 core curators = full score

# Safety keywords that force score = 0
_SAFETY_BLOCK_RE = re.compile(
    r"\b(crypto|nft|token\s*sale|airdrop|nsfw|porn|xxx|adult|escort"
    r"|casino|gambling|slot|betting|drug|marijuana|cannabis"
    r"|face[\s_-]?swap|deep[\s_-]?fake|nudify|deepfake)\b",
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


def engagement_raw(metrics: dict | None) -> int:
    """Weighted raw tweet engagement.

    bookmarks×3（"存下来以后用" = 真实使用意图，最强 repo 质量代理）
    > retweets×2（放大/背书）> likes×1（廉价随手）。
    views 不算（≈粉丝数噪声，已被 authority 维捕获）。
    Layer2/3 或缺失 metrics → 0。

    脏值防御：metrics 值若为 None / 字符串 / 负数（上游 twitter-cli 字段不稳时可能出现），
    coerce 为 0，不让脏数据 raise TypeError 崩掉整条打分（fail-safe，非 fail-silent —
    脏值本就该计 0 而非中断）。
    """
    if not metrics:
        return 0

    def _n(key: str) -> int:
        try:
            return max(0, int(metrics.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    return _n("likes") + 2 * _n("retweets") + 3 * _n("bookmarks")


def engagement_score(metrics: dict | None) -> float:
    """Tweet engagement score, log10-normalized.

    raw=5→0.19, 100→0.5, 1k→0.75, 1万→1.0。多博主聚合时上游取 MAX（最火一次背书），
    不取 sum（sum 会重新引入 mention_count 偏置）。
    """
    raw = engagement_raw(metrics)
    if raw <= 0:
        return 0.0
    return min(1.0, math.log10(raw + 1) / _ENGAGEMENT_NORM)


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
    """Compute weighted score (6 dims) + 独立共识 bonus for a repository."""
    if not safety_check(repo_data):
        return 0.0

    mentioners = repo_data.get("mentioners", [])
    mention_count = repo_data.get("mention_count", 0)
    stars = repo_data.get("stars", 0)
    commits_7d = repo_data.get("commits_7d")  # int|None
    velocity_7d = repo_data.get("velocity_7d")  # int|None
    readme_excerpt = repo_data.get("readme_excerpt")
    topic_tags = repo_data.get("topic_tags", [])
    engagement = repo_data.get("engagement")  # dict|None: aggregated tweet metrics (MAX)
    tier_map = repo_data.get("_tier_map")  # injected by pipeline

    s_auth = W_MENTIONER_AUTHORITY * mentioner_authority_score(mentioners, tier_map)
    s_topic = W_TOPIC_RELEVANCE * topic_relevance_score(topic_tags)
    s_eng = W_ENGAGEMENT * engagement_score(engagement)
    s_act = W_ACTIVITY * activity_score(commits_7d=commits_7d, velocity_7d=velocity_7d)
    s_stars = W_STARS * stars_score(stars)
    s_readme = W_README_QUALITY * readme_quality_score(readme_excerpt)

    total = s_auth + s_topic + s_eng + s_act + s_stars + s_readme
    # 独立共识 bonus：2+ 个博主独立提到 = 最强信号（罕见但高价值），不进加权和
    if mention_count >= 2:
        total += MULTI_MENTION_BONUS
    return round(min(1.0, total), 4)


def score_batch(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score and sort repos by descending score."""
    for r in repos:
        r["score"] = score(r)
    return sorted(repos, key=lambda r: r.get("score", 0), reverse=True)


def mention_threshold(active_curators_today: int) -> int:
    """Mention threshold = 1（v3）。

    历史：曾用 max(2, ceil(active/15))→ 固定 2。真实链路验收发现 curator pool 内多人
    撞车极罕见（全天 61 博主仅 1 个 repo 被 ≥2 人提到）→ 门槛=2 时 filter 后候选 <10
    → 每天 skip 无简报，博主腿出不了活。
    用户决策（方案 A）：降到 1（任何 1 个博主提及即够格进候选），排序交给
    authority/engagement/质量维；共识不再当门槛，改为 score 内 +0.08 bonus。
    保留 active_curators_today 参数以兼容调用方签名。
    """
    return 1
