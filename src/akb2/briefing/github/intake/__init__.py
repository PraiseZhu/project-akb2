"""Layer-independent data types shared across intake modules."""

from typing import Literal, NamedTuple


class Candidate(NamedTuple):
    repo_url: str  # 已归一化（url_normalizer 过）
    mentioner: str  # @handle
    tweet_id: str | None  # Layer 2/3 可能为 None
    mentioned_at: str  # ISO datetime
    tweet_text: str | None  # Layer 2/3 可能为 None
    metrics: dict | None = None  # 推文互动 {likes,retweets,bookmarks,...}；Layer 2/3 为 None


class CollectorResult(NamedTuple):
    layer: Literal["layer1", "layer2", "layer3"]
    candidates: list[Candidate]
    failed_handles: list[str]  # Layer 1 专属
    duration_sec: float
    ok: bool
    error: str | None
    recency_stats: dict  # SC-7 计数器载体；layer2/3 填 0 占位保证键恒在


# layer2/3 collector 无 recency 过滤，填 0 占位保证键恒在
_ZERO_RECENCY_STATS: dict = {
    "candidates_before_recency": 0,
    "candidates_after_recency": 0,
    "recency_dropped": 0,
    "timestamp_missing": 0,
    "timestamp_parse_failed": 0,
}
