"""数据契约与身份契约 — step-1。

公开类型:
    ExtractionContext   — 抓取上下文，携带 feed 元数据供 extract.py fallback
    compute_article_id  — sha256(canonical_url).hexdigest()[:12]
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# ExtractionContext — 8 字段最小契约
# ---------------------------------------------------------------------------


@dataclass
class ExtractionContext:
    """抓取上下文，承载 feed 来源元数据，供 extract_content fallback。

    字段:
        source_slug      — 源标识（如 "addy-osmani-blog"）
        source_publisher — 源的出版方名称（如 "Addy Osmani's Blog"）
        feed_title       — RSS/Atom 条目中的 title（可为空串，非 None）
        feed_author      — RSS/Atom 条目中的 author（可为空串，非 None）
        feed_published   — RSS/Atom 条目中的 published 日期字符串（可为空串）
        url              — 文章规范 URL（canonical URL），必填
        content_type     — HTTP Content-Type（如 "text/html"，可为空串）
        content_hash     — 原始响应体 sha256 hex（可为空串；用于幂等判断）
    """

    source_slug: str
    source_publisher: str
    feed_title: str
    feed_author: str
    feed_published: str
    url: str
    content_type: str = ""
    content_hash: str = field(default="")
    # x_account Article 路径：twitter article -m 预取的 markdown，extract 跳过网页抓取
    prefetched_content: str = field(default="")
    prefetched_via: str = field(default="")


# ---------------------------------------------------------------------------
# article_id — 确定性唯一标识
# ---------------------------------------------------------------------------


def compute_article_id(canonical_url: str) -> str:
    """返回 sha256(canonical_url).hexdigest()[:12]。

    确定性：相同 URL 永远返回相同 ID；不依赖时间戳或随机性。

    Args:
        canonical_url: 文章规范 URL（已归一化，去掉 UTM 参数等）

    Returns:
        12 位十六进制字符串（96 bits 碰撞空间，百万级别实例足够唯一）
    """
    return hashlib.sha256(canonical_url.encode()).hexdigest()[:12]
