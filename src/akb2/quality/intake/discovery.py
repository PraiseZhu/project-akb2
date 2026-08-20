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
import re
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

# Dublin Core namespace — 用于 RSS <dc:creator> 抽取
_DC_NS = "http://purl.org/dc/elements/1.1/"
_DC_CREATOR_TAG = f"{{{_DC_NS}}}creator"  # "{...}creator" 预格式化，避免循环内重复分配


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _el_text(el: ElementTree.Element | None) -> str:
    """安全取 XML 元素的 text，strip 后返回；None 元素或空 text 返回空串。"""
    return (el.text or "").strip() if el is not None else ""

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
    author: str = ""  # 来自 RSS <dc:creator>/<author> 或 Atom <author><name>
    # x_account Article 路径：twitter article -m 预取正文；RSS 路径保持空串
    prefetched_content: str = ""
    prefetched_via: str = ""


@dataclass
class DiscoverResult:
    """发现层一次轮询的完整结果。"""

    source_slug: str
    articles: list[DiscoveredArticle] = field(default_factory=list)
    skipped_duplicates: int = 0
    skipped_short_tweets: int = 0  # x_account：纯短推无外链非 Article，不归档
    skipped_empty_accounts: int = 0  # x_account：0 条原创账号跳过
    quarantine_triggered: bool = False
    error: str | None = None
    feed_url_used: str | None = None
    method: str = "feed"  # "feed" | "listing" | "x_account" | "none"


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
        entry["title"] = _el_text(item.find("title"))
        entry["link"] = _el_text(item.find("link"))
        entry["description"] = _el_text(item.find("description"))
        entry["pubDate"] = _el_text(item.find("pubDate"))
        # author: dc:creator 优先，次选 plain <author>
        entry["author"] = _el_text(item.find(_DC_CREATOR_TAG)) or _el_text(item.find("author"))
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
            entry_data[date_tag] = _el_text(_find_child(entry, date_tag))

        # author: <author><name>...</name></author>
        author_el = _find_child(entry, "author")
        name_el = _find_child(author_el, "name") if author_el is not None else None
        entry_data["author"] = _el_text(name_el)

        entries.append(entry_data)

    return entries[:MAX_FEED_ENTRIES]


def parse_feed(xml_text: str) -> list[dict[str, str]]:
    """解析 RSS 或 Atom feed，自动检测格式。

    检测逻辑：按**根元素名**判定——首个真实 XML 元素为 <rss>/<rdf:RDF> → RSS，
    <feed> → Atom（去命名空间前缀，大小写不敏感）。
    不再用「前 2000 字符含 'rss' 子串」检测：Atom feed 的标题（如
    "Cursor Blog | RSS Feed"）或自链接 type="application/rss+xml" 会误命中，
    把 Atom 当 RSS 解析 → 找不到 <channel> → 静默返回 0 条（宪法 P12 静默失败）。
    返回条目列表，每项至少含 title/link/description。
    """
    if not xml_text.strip():
        return []

    # 取首个元素标签的本地名：正则跳过 <?xml?> 声明、注释 <!-- -->、DOCTYPE，
    # 匹配 `<` 后紧跟的（可选命名空间前缀:）本地名。
    root_match = re.search(r"<(?:[A-Za-z_][\w.\-]*:)?([A-Za-z_][\w.\-]*)", xml_text)
    root_local = root_match.group(1).lower() if root_match else ""
    if root_local in ("rss", "rdf"):
        return _parse_rss_feed(xml_text)
    if root_local == "feed":
        return _parse_atom_feed(xml_text)
    # 未知/无法识别根元素：先试 RSS，空则试 Atom（非法 XML 在此抛 ParseError）。
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

        # SC-3（CI-2 同款）：Fetcher.get 是 classmethod（非 .fetch，scrapling 0.4+），
        # 原 Fetcher().fetch 抛 AttributeError 被 try/except 吞成静默 None。
        page = Fetcher.get(listing_url)
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

    # 文章链接打分过滤（复用 source_validator 的 listing 评分器，与其可达性探测同源）：
    # 日期路径(/2026/…)、文章路径特征(/blog//posts//articles/…)加分；导航/资源/skip
    # 路径(search/tags/login/.css/.js/.png…)判 -1。只保留 score>=0 并按分数降序。
    # 堵住本函数 docstring 承诺"保留 article/post/年份特征 URL"却从未实现的缺口——
    # 原实现把 /docs、/product、/enterprise 等导航页也当文章抓（宪法 P6 质量锚定）。
    from .source_validator import _listing_candidate_score

    scored_urls: list[tuple[int, int, str]] = []
    for order, candidate in enumerate(abs_urls):
        score = _listing_candidate_score(candidate, listing_url)
        if score >= 0:
            # -order 作次键：同分保持列表页出现顺序（越靠前越新）。
            scored_urls.append((score, -order, candidate))
    scored_urls.sort(reverse=True)
    abs_urls = [candidate for _score, _order, candidate in scored_urls]

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
    dry_run: bool = False,
) -> DiscoverResult:
    """对单个源执行发现轮询。

    Args:
        source: 源配置 dict（含 slug/domain/feed_url/listing_url/rate_limit）
        ledger_path: jsonl 账本路径（用于去重扫描）
        feed_content: 注入的 feed XML 文本（测试用；为 None 时真实 httpx 抓取）
        dry_run: True 时跳过真实网络请求，返回空 DiscoverResult

    Returns:
        DiscoverResult 含发现的文章列表与统计信息。
    """
    slug = source.get("slug", "unknown")
    feed_url = source.get("feed_url", "")
    listing_url = source.get("listing_url", "")
    source_type = str(source.get("type") or "").strip().lower()

    result = DiscoverResult(source_slug=slug)
    status = str(source.get("status", "trial") or "trial").strip().lower()
    if status in {"down", "disabled"}:
        result.method = "none"
        return result
    source_type = str(source.get("type") or "rss").strip().lower()
    if source_type == "x_account":
        # RSS/listing 发现层不消费 X 账号源；避免 listing_url 被 scrapling 误抓。
        result.method = "none"
        return result

    # 路径 0: X 账号时间线（type=x_account）。产出 DiscoveredArticle，
    # orchestrator 的 cap/quarantine/ledger 零改动透明生效。
    if source_type == "x_account":
        from src.akb2.quality.intake.x_account_discovery import (
            discover_x_account_articles,
        )

        return discover_x_account_articles(
            source,
            ledger_path,
            dry_run=dry_run,
        )

    # 路径 1: RSS/Atom feed
    if feed_url:
        # 无注入内容时真实抓取；dry_run 时跳过
        if feed_content is None:
            if dry_run:
                result.method = "feed"
                result.feed_url_used = feed_url
                return result  # dry_run: 不发真网，返回空列表
            try:
                import httpx
                resp = httpx.get(
                    feed_url,
                    timeout=15,
                    follow_redirects=True,
                    headers={"User-Agent": "AKB2-FeedFetcher/1.0"},
                )
                if resp.status_code >= 400:
                    result.method = "feed"
                    result.error = f"Feed HTTP {resp.status_code}: {feed_url}"
                    return result
                feed_content = resp.text
            except Exception as exc:
                result.method = "feed"
                result.error = f"Feed fetch failed: {exc}"
                return result
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
                    author=entry.get("author", ""),
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
