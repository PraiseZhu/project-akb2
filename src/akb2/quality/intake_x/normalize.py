"""把 X 通道条目归一成 ExtractionContext。外链优先于 Article，短推跳过。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.akb2.quality.intake.discovery import content_fingerprint
from src.akb2.quality.intake.extract import extract_content
from src.akb2.quality.intake.models import ExtractionContext, compute_article_id
from src.akb2.quality.intake.x_account_discovery import (
    ContentPath,
    _tweet_author,
    _tweet_published,
    _tweet_url,
    classify_tweet_content_path,
    fetch_x_article_markdown,
    first_external_url,
)
from src.akb2.quality.intake_x.sources import X_MIN_ARTICLE_WORDS, XChannel

logger = logging.getLogger(__name__)


@dataclass
class NormalizedItem:
    context: ExtractionContext
    article_id: str
    fingerprint: str
    path: ContentPath
    tweet: dict[str, Any]


def _handle_of(tweet: dict[str, Any]) -> str:
    author = tweet.get("author") or {}
    if isinstance(author, dict):
        screen = str(author.get("screenName") or author.get("screen_name") or "").lstrip("@")
        if screen:
            return screen
    if isinstance(author, str) and author.strip():
        return author.lstrip("@")
    return "unknown"


def normalize_tweet(
    tweet: dict[str, Any],
    channel: XChannel,
    *,
    fetch_article: Any = fetch_x_article_markdown,
) -> NormalizedItem | None:
    """条目 → ExtractionContext。SKIP_SHORT 返回 None。"""
    path = classify_tweet_content_path(tweet)
    if path is ContentPath.SKIP_SHORT:
        return None

    handle = _handle_of(tweet)
    author = _tweet_author(tweet, handle)
    published = _tweet_published(tweet) or ""
    title = str(tweet.get("articleTitle") or tweet.get("text") or tweet.get("title") or "").strip()

    if path is ContentPath.EXTERNAL:
        url = first_external_url(tweet) or ""
        if not url:
            return None
        fingerprint = content_fingerprint(f"{url}|{title}|{author}")
        context = ExtractionContext(
            source_slug=channel.slug,
            source_publisher="X",
            feed_title=title or url,
            feed_author=author,
            feed_published=published,
            url=url,
            content_hash=fingerprint,
        )
        return NormalizedItem(
            context=context,
            article_id=compute_article_id(url),
            fingerprint=fingerprint,
            path=path,
            tweet=tweet,
        )

    tweet_id = str(tweet.get("id") or "")
    markdown = ""
    if tweet_id:
        markdown = fetch_article(tweet_id) or ""
    if not markdown:
        markdown = str(tweet.get("articleText") or "")
    if not markdown.strip():
        return None
    url = _tweet_url(tweet, handle)
    fingerprint = content_fingerprint(markdown)
    context = ExtractionContext(
        source_slug=channel.slug,
        source_publisher="X",
        feed_title=title or url,
        feed_author=author,
        feed_published=published,
        url=url,
        content_hash=fingerprint,
        prefetched_content=markdown,
        prefetched_via="twitter_article",
    )
    return NormalizedItem(
        context=context,
        article_id=compute_article_id(url),
        fingerprint=fingerprint,
        path=path,
        tweet=tweet,
    )


def extract_normalized(
    item: NormalizedItem,
    *,
    min_words: int = X_MIN_ARTICLE_WORDS,
    extract_fn: Any = extract_content,
) -> Any | None:
    result = extract_fn(url=item.context.url, extraction_config=item.context)
    if not getattr(result, "extraction_success", False):
        logger.info("extract failed %s: %s", item.context.url, getattr(result, "error", ""))
        return None
    word_count = int(getattr(result, "word_count", 0) or 0)
    if word_count < min_words:
        logger.info(
            "skip short extract %s words=%s min=%s",
            item.context.url,
            word_count,
            min_words,
        )
        return None
    return result
