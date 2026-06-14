"""RSS/Atom 发现层 — 源轮询、指纹去重、产量隔离。

公开函数:
    discover_articles(source: dict, ledger_path: str) -> DiscoverResult
    check_quarantine(source_slug: str, daily_counts: dict) -> bool

架构:
    - RSS/Atom 轮询优先（feed_url 存在时）
    - 无 feed 走 Scrapling 列表页（listing_url，占位接口）
    - 正文 SHA-256 内容指纹幂等去重（与 ledger 一致）
    - 单源单日产量 > QUARANTINE_MULTIPLIER × 中位数 → 该批进隔离区
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any
from xml.etree import ElementTree

# ---------------------------------------------------------------------------
# 具名常量（宪法 P4：预算显式）
# ---------------------------------------------------------------------------

QUARANTINE_MULTIPLIER = 3.0  # 单源单日产量超过中位数倍数触发隔离
MIN_DAILY_SOURCES_FOR_MEDIAN = 3  # 至少 N 个源当日有产出才计算中位数
MAX_FEED_ENTRIES = 100  # 单次 feed 解析最多取条目数
FEED_TIMEOUT_SEC = 30  # feed 请求超时
USER_AGENT = "AKB2-ContentQuality/1.0 (RSS/Atom discovery; +https://akb2.local)"

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class DiscoveredArticle:
    """发现层产出的一条文章条目。"""

    url: str
    title: str
    content_hash: str  # SHA-256 of raw content
    source_slug: str
    published_at: str | None  # ISO-8601 or None
    feed_title: str | None  # 来源 feed 标题


@dataclass
class DiscoverResult:
    """发现层一次轮询的完整结果。"""

    source_slug: str
    articles: list[DiscoveredArticle] = field(default_factory=list)
    skipped_duplicates: int = 0
    quarantine_triggered: bool = False
    error: str | None = None
    feed_url_used: str | None = None
    method: str = "feed"  # "feed" | "listing" | "none"


# ---------------------------------------------------------------------------
# 内容指纹
# ---------------------------------------------------------------------------


def content_fingerprint(text: str) -> str:
    """计算正文 SHA-256 内容指纹。

    与 ledger 的 SHA-256 一致，用于幂等去重。输入的 text 应是
    规范化后的正文内容（不含元数据），确保同一篇文章不同次抓取
    得到相同指纹。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# RSS/Atom 解析
# ---------------------------------------------------------------------------


def _parse_rss_feed(xml_text: str) -> list[dict[str, str]]:
    """解析 RSS 2.0 feed，返回条目列表。

    每项含: title, link, description, pubDate
    """
    root = ElementTree.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []

    entries: list[dict[str, str]] = []
    for item in channel.findall("item"):
        entry: dict[str, str] = {}
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")
        pubdate_el = item.find("pubDate")

        entry["title"] = (title_el.text or "").strip() if title_el is not None else ""
        entry["link"] = (link_el.text or "").strip() if link_el is not None else ""
        entry["description"] = (desc_el.text or "").strip() if desc_el is not None else ""
        entry["pubDate"] = (pubdate_el.text or "").strip() if pubdate_el is not None else ""
        entries.append(entry)

    return entries[:MAX_FEED_ENTRIES]


def _parse_atom_feed(xml_text: str) -> list[dict[str, str]]:
    """解析 Atom 1.0 feed，返回条目列表。

    每项含: title, link, summary, published/updated

    ElementTree 对命名空间的 XPath 支持有限，使用 Clark 符号
    {http://www.w3.org/2005/Atom}tag 做显式匹配，并用不带命名空间的
    tag 做 fallback（部分 feed 省略命名空间声明）。
    """
    ATOM_NS = "http://www.w3.org/2005/Atom"

    def _find_child(parent, tag):
        """查找子元素：先试 Clark 符号，再试无前缀。"""
        el = parent.find(f"{{{ATOM_NS}}}{tag}")
        if el is not None:
            return el
        return parent.find(tag)

    def _findall_children(parent, tag):
        """查找所有子元素：先试 Clark 符号，再试无前缀。"""
        els = parent.findall(f"{{{ATOM_NS}}}{tag}")
        if els:
            return els
        return parent.findall(tag)

    root = ElementTree.fromstring(xml_text)

    # 根元素可能是 {ATOM_NS}feed 或直接 <feed>
    if root.tag == f"{{{ATOM_NS}}}feed":
        feed_elem = root
    else:
        feed_elem = root

    entries: list[dict[str, str]] = []
    for entry in _findall_children(feed_elem, "entry"):
        entry_data: dict[str, str] = {}

        for tag_name in ("title", "summary", "content"):
            el = _find_child(entry, tag_name)
            if el is not None:
                text_parts = list(el.itertext())
                entry_data[tag_name] = " ".join(text_parts).strip()
            else:
                entry_data[tag_name] = ""

        # link: 取 rel="alternate" 或第一个 link
        link_els = _findall_children(entry, "link")
        link_href = ""
        if link_els:
            for le in link_els:
                if le.attrib.get("rel", "alternate") == "alternate":
                    link_href = le.attrib.get("href", "")
                    break
            if not link_href:
                link_href = link_els[0].attrib.get("href", "")
        entry_data["link"] = link_href

        # published / updated
        for date_tag in ("published", "updated"):
            dt_el = _find_child(entry, date_tag)
            entry_data[date_tag] = (
                (dt_el.text or "").strip() if dt_el is not None else ""
            )

        entries.append(entry_data)

    return entries[:MAX_FEED_ENTRIES]


def parse_feed(xml_text: str) -> list[dict[str, str]]:
    """解析 RSS 或 Atom feed，自动检测格式。

    检测逻辑：根元素包含 "rss" → RSS；包含 "feed" → Atom。
    返回条目列表，每项至少含 title/link/description。
    """
    if not xml_text.strip():
        return []

    # auto-detect
    lower = xml_text[:2000].lower()
    if "rss" in lower or "<rss" in lower:
        return _parse_rss_feed(xml_text)
    elif "feed" in lower or "xmlns" in lower:
        return _parse_atom_feed(xml_text)
    else:
        # 尝试 RSS 优先
        result = _parse_rss_feed(xml_text)
        if result:
            return result
        return _parse_atom_feed(xml_text)


# ---------------------------------------------------------------------------
# 去重
# ---------------------------------------------------------------------------


def is_duplicate_by_hash(content_hash: str, ledger_path: str | Path) -> bool:
    """检查 content_hash 是否已在 ledger 中存在。

    扫描 ledger jsonl 中的 content_hash 字段，判定是否已抓取过。
    """
    path = Path(ledger_path)
    if not path.exists():
        return False

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("content_hash") == content_hash:
                    return True
            except json.JSONDecodeError:
                continue
    return False


# ---------------------------------------------------------------------------
# 产量隔离
# ---------------------------------------------------------------------------


def check_quarantine(
    source_slug: str,
    daily_counts: dict[str, int],
    multiplier: float = QUARANTINE_MULTIPLIER,
    min_sources: int = MIN_DAILY_SOURCES_FOR_MEDIAN,
) -> bool:
    """检查某源当日产量是否触发隔离。

    算法:
        1. 至少 min_sources 个源当日有产出才计算中位数
        2. 本源产量 > multiplier × 中位数 → 触发隔离

    Args:
        source_slug: 源标识
        daily_counts: {source_slug: count} 当日产量映射
        multiplier: 隔离倍数阈值
        min_sources: 计算中位数所需最少源数

    Returns:
        True 触发隔离，False 正常。
    """
    if source_slug not in daily_counts:
        return False

    source_count = daily_counts[source_slug]
    if source_count == 0:
        return False

    # 排除本源的其余源产量
    others = [c for s, c in daily_counts.items() if s != source_slug and c > 0]
    if len(others) < min_sources:
        return False

    median_count = median(others)
    if median_count == 0:
        return False

    return source_count > multiplier * median_count


# ---------------------------------------------------------------------------
# Scrapling listing 抓取（无 feed 路径）
# ---------------------------------------------------------------------------


def _fetch_listing_via_scrapling(
    listing_url: str,
    source_slug: str,
    ledger_path: str | Path,
) -> list[DiscoveredArticle] | None:
    """用 Scrapling Fetcher 抓取列表页，提取文章链接。

    import gate：scrapling 不可用时返回 None（调用方记录 error）。
    成功时返回 DiscoveredArticle 列表（可为空列表）。

    抽取策略：
        1. 调用 Fetcher().fetch(listing_url) 获取页面
        2. 从 page.css("a[href]") 提取全部链接
        3. 过滤：保留同域名下路径含 slug/article/post/年份等特征的 URL
        4. 幂等去重（is_duplicate_by_hash）
    """
    import importlib.util

    if importlib.util.find_spec("scrapling") is None:
        return None

    try:
        from scrapling import Fetcher

        page = Fetcher().fetch(listing_url)
    except Exception:
        return None

    # 提取所有 href 属性
    try:
        link_elements = page.css("a[href]")
        raw_hrefs: list[str] = []
        for el in link_elements:
            href = el.attrib.get("href", "").strip()
            if href:
                raw_hrefs.append(href)
    except Exception:
        return []

    # 规范化为绝对 URL（过滤非 http 链接）
    from urllib.parse import urljoin, urlparse

    base = listing_url
    abs_urls: list[str] = []
    _parsed_listing = urlparse(listing_url)
    listing_domain = _parsed_listing.netloc
    listing_path = _parsed_listing.path.rstrip("/")

    for href in raw_hrefs:
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        abs_url = urljoin(base, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        # 同域过滤（子域名也接受）
        if listing_domain not in parsed.netloc and parsed.netloc not in listing_domain:
            continue
        path = parsed.path.rstrip("/")
        # 路径深度 >= 1（排除列表页本身）
        if not path or path == listing_path:
            continue
        abs_urls.append(abs_url)

    # 去重（按 URL 自身去重，再按 content_hash 幂等）
    seen_urls: set[str] = set()
    articles: list[DiscoveredArticle] = []

    for article_url in abs_urls:
        if article_url in seen_urls:
            continue
        seen_urls.add(article_url)

        fp = content_fingerprint(article_url)
        if is_duplicate_by_hash(fp, ledger_path):
            continue

        articles.append(
            DiscoveredArticle(
                url=article_url,
                title="",
                content_hash=fp,
                source_slug=source_slug,
                published_at=None,
                feed_title=None,
            )
        )

    return articles[:MAX_FEED_ENTRIES]


# ---------------------------------------------------------------------------
# 主发现函数
# ---------------------------------------------------------------------------


def discover_articles(
    source: dict[str, Any],
    ledger_path: str | Path,
    feed_content: str | None = None,
) -> DiscoverResult:
    """对单个源执行发现轮询。

    Args:
        source: 源配置 dict（含 slug/domain/feed_url/listing_url/rate_limit）
        ledger_path: jsonl 账本路径（用于去重扫描）
        feed_content: 注入的 feed XML 文本（测试用；为 None 时实际抓取）

    Returns:
        DiscoverResult 含发现的文章列表与统计信息。
    """
    slug = source.get("slug", "unknown")
    feed_url = source.get("feed_url", "")
    listing_url = source.get("listing_url", "")

    result = DiscoverResult(source_slug=slug)

    # 路径 1: RSS/Atom feed
    if feed_url and feed_content is not None:
        result.method = "feed"
        result.feed_url_used = feed_url
        try:
            entries = parse_feed(feed_content)
        except ElementTree.ParseError as exc:
            result.error = f"Feed parse error: {exc}"
            return result
        except Exception as exc:
            result.error = f"Feed error: {exc}"
            return result

        for entry in entries:
            article_url = entry.get("link", "")
            if not article_url:
                continue

            title = entry.get("title", "")
            desc = entry.get("description", entry.get("summary", entry.get("content", "")))
            pub_date = entry.get("pubDate", entry.get("published", entry.get("updated", "")))

            # 用 URL + title + description 组合做指纹（抓取前预判）
            combined = f"{article_url}|{title}|{desc}"
            fp = content_fingerprint(combined)

            if is_duplicate_by_hash(fp, ledger_path):
                result.skipped_duplicates += 1
                continue

            result.articles.append(
                DiscoveredArticle(
                    url=article_url,
                    title=title,
                    content_hash=fp,
                    source_slug=slug,
                    published_at=pub_date if pub_date else None,
                    feed_title=None,
                )
            )

    # 路径 2: 无 feed，走 listing_url（Scrapling 自适应抓取）
    elif listing_url and not feed_url:
        result.method = "listing"
        articles = _fetch_listing_via_scrapling(listing_url, slug, ledger_path)
        if articles is None:
            # scrapling 不可用或抓取失败
            result.error = "listing fetch failed (scrapling unavailable or error)"
        else:
            result.articles = articles

    # 路径 3: 无 feed 也无 listing
    else:
        result.method = "none"
        result.error = "No feed_url or listing_url configured"

    return result


# ---------------------------------------------------------------------------
# 批量发现
# ---------------------------------------------------------------------------


def batch_discover(
    sources: list[dict[str, Any]],
    ledger_path: str | Path,
    feed_contents: dict[str, str] | None = None,
) -> dict[str, DiscoverResult]:
    """批量执行发现轮询。

    Args:
        sources: 源配置列表
        ledger_path: jsonl 账本路径
        feed_contents: {slug: feed_xml_text} 注入（测试用）

    Returns:
        {slug: DiscoverResult} 每个源的发现结果。
    """
    results: dict[str, DiscoverResult] = {}
    for source in sources:
        slug = source.get("slug", "unknown")
        fc = feed_contents.get(slug) if feed_contents else None
        results[slug] = discover_articles(source, ledger_path, feed_content=fc)
    return results


def build_daily_counts(results: dict[str, DiscoverResult]) -> dict[str, int]:
    """从批量发现结果提取当日产量计数。

    Returns:
        {source_slug: article_count} 不含跳过重复。
    """
    return {slug: len(r.articles) for slug, r in results.items()}
