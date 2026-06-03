"""Layer-independent data types shared across intake modules."""

from typing import Literal, NamedTuple


class Candidate(NamedTuple):
    repo_url: str  # 已归一化（url_normalizer 过）
    mentioner: str  # @handle
    tweet_id: str | None  # Layer 2/3 可能为 None
    mentioned_at: str  # ISO datetime
    tweet_text: str | None  # Layer 2/3 可能为 None


class CollectorResult(NamedTuple):
    layer: Literal["layer1", "layer2", "layer3"]
    candidates: list[Candidate]
    failed_handles: list[str]  # Layer 1 专属
    duration_sec: float
    ok: bool
    error: str | None