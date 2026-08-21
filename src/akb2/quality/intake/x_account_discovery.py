"""X 账号发现层 — type=x_account 源走 twitter user-posts，产出 DiscoveredArticle。

公开函数:
    discover_x_account_articles(source, ledger_path, ...) -> DiscoverResult
    x_account_discovery — 同上（orchestrator 延迟 import 用这个名字）
    resolve_x_handles(source, curator_path=None) -> list[str]
    classify_tweet_content_path(tweet) -> ContentPath
    first_external_url(tweet) -> str | None
    fetch_x_article_markdown(tweet_id) -> str

采集端点（禁止 X API / WebFetch / twitter search）:
    twitter user-posts <handle> -n N --json
    twitter article <id> -m          # 仅 X Article 路径

内容三路（x4）:
    外链     → DiscoveredArticle.url = 首个非 x.com URL（下游 extract_content）
    Article  → DiscoveredArticle.url = 推文 URL；content_hash 对长文正文
    纯短推   → 跳过不归档，skipped_short_tweets 显式计数（fail-visible）
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from src.akb2.briefing.intake.l0_collector import (
    TWITTER_BIN,
    TWITTER_TIMEOUT_SEC,
    UpstreamCollectorBroken,
    diagnose_upstream_fatal,
    match_upstream_fatal,
)
from src.akb2.quality.intake.discovery import (
    DiscoveredArticle,
    DiscoverResult,
    content_fingerprint,
    is_duplicate_by_hash,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 具名常量（宪法 P4：预算显式）
# ---------------------------------------------------------------------------

DEFAULT_PER_ACCOUNT_CAP = 10
# 账号间停顿。历史实测 pacing 2.5s 跑到约 34 个账号触发 429。
DEFAULT_PACING_SEC = 3.0
# 开跑前探针 = 第 1 个正式账号；命中 UPSTREAM_FATAL 即断，不多付一次调用。
PREFLIGHT_PROBE_ACCOUNTS = 1
# user-posts 无服务端过滤，抓取量放大 2 倍再截断，补偿 RT/回复损耗。
FETCH_CAP_MULTIPLIER = 2
DEFAULT_CURATOR_CONFIG = Path("config/curator-accounts.yaml")
X_HOST_SUFFIXES = ("x.com", "twitter.com", "t.co")

# ---------------------------------------------------------------------------
# 内容路径
# ---------------------------------------------------------------------------


class ContentPath(str, Enum):  # noqa: UP042
    """推文 → raw 的分流路径。"""

    EXTERNAL = "external"  # 首个非 x.com URL → extract_content
    ARTICLE = "article"  # twitter article <id> -m
    SKIP_SHORT = "skip_short"  # 纯短推，不归档


class XAccountAuthError(RuntimeError):
    """twitter CLI 不可用 / 未认证 → fail-closed，不降级 fixture。"""


# ---------------------------------------------------------------------------
# 源解析
# ---------------------------------------------------------------------------


def resolve_x_handles(
    source: dict[str, Any],
    curator_path: str | Path | None = None,
) -> list[str]:
    """从 type=x_account 源解析 handle 列表（保序去重）。

    优先 ``x_handles``；否则按 ``fetch_tier``（en/zh/all，默认 all）
    读 curator-accounts.yaml 的 fetch_tier。
    """
    raw_handles = source.get("x_handles")
    if raw_handles:
        return _dedupe_handles(raw_handles)

    path = Path(curator_path) if curator_path else DEFAULT_CURATOR_CONFIG
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError as exc:
        raise XAccountAuthError(f"curator-accounts.yaml 不存在: {path}") from exc

    fetch = cfg.get("fetch_tier") or {}
    tier = str(source.get("fetch_tier") or "all").strip().lower()
    buckets: list[Any] = []
    if tier in {"en", "zh"}:
        buckets = list(fetch.get(tier) or [])
    else:
        buckets = list(fetch.get("en") or []) + list(fetch.get("zh") or [])
    return _dedupe_handles(buckets)


def _dedupe_handles(raw: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in raw or []:
        handle = str(item).strip().lstrip("@")
        if handle and handle not in seen:
            seen.add(handle)
            out.append(handle)
    return out


# ---------------------------------------------------------------------------
# 内容分流
# ---------------------------------------------------------------------------


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_x_host(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in X_HOST_SUFFIXES)


def first_external_url(tweet: dict[str, Any]) -> str | None:
    """抽推文里首个非 x.com / twitter.com / t.co URL。"""
    for raw in tweet.get("urls") or []:
        url = str(raw).strip()
        if not url:
            continue
        host = _host_of(url)
        if host and not _is_x_host(host):
            return url
    return None


def is_x_article(tweet: dict[str, Any]) -> bool:
    """X Article 判定：CLI 序列化的 articleTitle/articleText，或 i/article URL。"""
    if tweet.get("articleTitle") or tweet.get("articleText") or tweet.get("article_title"):
        return True
    for raw in tweet.get("urls") or []:
        url = str(raw).lower()
        if "x.com/i/article" in url or "twitter.com/i/article" in url:
            return True
    return False


def classify_tweet_content_path(tweet: dict[str, Any]) -> ContentPath:
    """外链优先于 Article；两者都无 → 短推跳过。"""
    if first_external_url(tweet):
        return ContentPath.EXTERNAL
    if is_x_article(tweet):
        return ContentPath.ARTICLE
    return ContentPath.SKIP_SHORT


def _tweet_url(tweet: dict[str, Any], handle: str) -> str:
    tweet_id = str(tweet.get("id") or "")
    author = tweet.get("author") or {}
    screen = ""
    if isinstance(author, dict):
        screen = str(author.get("screenName") or author.get("screen_name") or "")
    elif isinstance(author, str):
        screen = author.lstrip("@")
    screen = screen or handle
    if screen and tweet_id:
        return f"https://x.com/{screen}/status/{tweet_id}"
    return tweet_id


def _tweet_author(tweet: dict[str, Any], handle: str) -> str:
    author = tweet.get("author") or {}
    if isinstance(author, dict):
        screen = str(author.get("screenName") or author.get("screen_name") or handle)
        name = str(author.get("name") or "")
        return name or f"@{screen}" if screen else handle
    if isinstance(author, str) and author.strip():
        return author.strip()
    return f"@{handle}"


def _tweet_published(tweet: dict[str, Any]) -> str | None:
    iso = str(tweet.get("createdAtISO") or tweet.get("created_at") or "").strip()
    return iso or None


def _post_filter(handle: str, raw: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """user-posts 无 --exclude；raw 层剔 RT / 以 @ 开头的回复。"""
    kept: list[dict[str, Any]] = []
    for tweet in raw:
        if tweet.get("isRetweet"):
            continue
        if str(tweet.get("text", "")).startswith("@"):
            continue
        kept.append(tweet)
        if len(kept) >= cap:
            break
    logger.debug("x_account post-filter @%s kept %d/%d", handle, len(kept), len(raw))
    return kept


# ---------------------------------------------------------------------------
# twitter CLI
# ---------------------------------------------------------------------------


def _twitter_bin() -> str:
    return TWITTER_BIN


def _ensure_twitter_available() -> None:
    path = Path(_twitter_bin())
    if not path.exists():
        raise XAccountAuthError(f"twitter-cli 不存在于 {_twitter_bin()}，拒绝降级 fixture")


def _raise_broken(sig: str, count: int, sample: str = "") -> None:
    raise UpstreamCollectorBroken(
        sig, count, sample, diagnosis=diagnose_upstream_fatal(sig)
    )


def _run_twitter(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=TWITTER_TIMEOUT_SEC,
    )


def _fetch_user_posts(handle: str, cap: int) -> tuple[list[dict[str, Any]], str | None, str]:
    """抓单个账号。返回 (raw tweets, fatal_sig, sample)。

    单账号 0 条 / 瞬时失败 → ([], None, sample)，由调用方跳过。
    UPSTREAM_FATAL 签名 → fatal_sig 非空，调用方即断。
    认证失败 → XAccountAuthError。
    """
    cmd = [
        _twitter_bin(),
        "user-posts",
        handle,
        "-n",
        str(cap * FETCH_CAP_MULTIPLIER),
        "--json",
    ]
    try:
        result = _run_twitter(cmd)
    except subprocess.TimeoutExpired:
        logger.warning("@%s twitter user-posts timeout (%ss) — skip", handle, TWITTER_TIMEOUT_SEC)
        return [], None, f"timeout {TWITTER_TIMEOUT_SEC}s"
    except FileNotFoundError as exc:
        raise XAccountAuthError(f"twitter-cli 不可用: {exc}") from exc

    combined = (result.stderr or "") + (result.stdout or "")
    fatal = match_upstream_fatal(combined)
    if fatal:
        return [], fatal, combined[:300].replace("\n", " | ")

    lowered = combined.lower()
    if (
        "not_authenticated" in lowered
        or "not authenticated" in lowered
        or "login required" in lowered
    ):
        raise XAccountAuthError(f"twitter-cli 未认证 (@{handle}): {combined[:200]}")

    if result.returncode != 0:
        logger.warning(
            "@%s twitter-cli exit %s: %s — skip",
            handle,
            result.returncode,
            (result.stderr or "")[:200].replace("\n", " | "),
        )
        return [], None, combined[:300]

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        logger.warning("@%s user-posts output not JSON — skip", handle)
        return [], None, "output not JSON"

    if not payload.get("ok"):
        # payload.ok=false 也可能是全局故障下游（ClientTransaction → GraphQL 404）
        fatal = match_upstream_fatal((result.stderr or "") + (result.stdout or ""))
        if fatal:
            return [], fatal, combined[:300].replace("\n", " | ")
        logger.warning("@%s payload.ok=false — skip", handle)
        return [], None, "payload.ok=false"

    raw = payload.get("data") or []
    if not isinstance(raw, list):
        return [], None, "payload.data not list"
    return [t for t in raw if isinstance(t, dict)], None, ""


def fetch_x_article_markdown(tweet_id: str) -> str:
    """``twitter article <id> -m`` 取长文 Markdown。失败返回空串（调用方跳过）。"""
    cmd = [_twitter_bin(), "article", str(tweet_id), "-m"]
    try:
        result = _run_twitter(cmd)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("twitter article %s failed: %s", tweet_id, exc)
        return ""
    combined = (result.stderr or "") + (result.stdout or "")
    fatal = match_upstream_fatal(combined)
    if fatal:
        _raise_broken(fatal, 1, combined[:300].replace("\n", " | "))
    if result.returncode != 0:
        logger.warning(
            "twitter article %s exit %s: %s",
            tweet_id,
            result.returncode,
            (result.stderr or "")[:200].replace("\n", " | "),
        )
        return ""
    return (result.stdout or "").strip()


# ---------------------------------------------------------------------------
# 推文 → DiscoveredArticle
# ---------------------------------------------------------------------------


def _article_from_external(
    tweet: dict[str, Any],
    handle: str,
    source_slug: str,
    external_url: str,
) -> DiscoveredArticle:
    title = str(tweet.get("text") or "").strip() or external_url
    author = _tweet_author(tweet, handle)
    published = _tweet_published(tweet)
    combined = f"{external_url}|{title}|{author}"
    return DiscoveredArticle(
        url=external_url,
        title=title,
        content_hash=content_fingerprint(combined),
        source_slug=source_slug,
        published_at=published,
        feed_title=title,
        author=author,
    )


def _article_from_x_article(
    tweet: dict[str, Any],
    handle: str,
    source_slug: str,
    markdown: str,
) -> DiscoveredArticle:
    title = (
        str(tweet.get("articleTitle") or tweet.get("article_title") or "").strip()
        or str(tweet.get("text") or "").strip()
        or f"X Article {tweet.get('id', '')}"
    )
    author = _tweet_author(tweet, handle)
    published = _tweet_published(tweet)
    url = _tweet_url(tweet, handle)
    body = markdown or str(tweet.get("articleText") or "")
    return DiscoveredArticle(
        url=url,
        title=title,
        content_hash=content_fingerprint(body or url),
        source_slug=source_slug,
        published_at=published,
        feed_title=title,
        author=author,
        prefetched_content=body,
        prefetched_via="twitter_article",
    )


# ---------------------------------------------------------------------------
# 主发现
# ---------------------------------------------------------------------------


@dataclass
class XDiscoverStats:
    skipped_short_tweets: int = 0
    skipped_empty_accounts: int = 0
    skipped_duplicates: int = 0


def discover_x_account_articles(
    source: dict[str, Any],
    ledger_path: str | Path,
    *,
    curator_path: str | Path | None = None,
    dry_run: bool = False,
    pacing_sec: float | None = None,
    per_account_cap: int | None = None,
    sleep: Any = time.sleep,
    fetch_one: Any = None,
    fetch_article: Any = None,
) -> DiscoverResult:
    """对单个 type=x_account 源执行发现。

    Args:
        source: 源配置（slug / x_handles 或 fetch_tier）
        ledger_path: jsonl 账本（指纹去重）
        curator_path: curator-accounts.yaml 覆盖（测试注入）
        dry_run: True 时不打 twitter CLI，返回空结果 method=x_account
        pacing_sec: 账号间停顿，默认 DEFAULT_PACING_SEC（≥3s）
        per_account_cap: 每账号最多保留条数
        sleep / fetch_one / fetch_article: 测试注入点
    """
    slug = source.get("slug", "unknown")
    result = DiscoverResult(source_slug=slug, method="x_account")
    stats = XDiscoverStats()

    status = str(source.get("status", "trial") or "trial").strip().lower()
    if status in {"down", "disabled"}:
        result.method = "none"
        return result

    handles = resolve_x_handles(source, curator_path=curator_path)
    if not handles:
        result.error = "x_account source has no handles (x_handles / fetch_tier empty)"
        return result

    if dry_run:
        return result

    cap = int(per_account_cap or source.get("per_account_cap") or DEFAULT_PER_ACCOUNT_CAP)
    pace = float(pacing_sec if pacing_sec is not None else DEFAULT_PACING_SEC)
    if pace < DEFAULT_PACING_SEC:
        # 生产下限 3s；测试可显式传 0 跳过 sleep。
        if pacing_sec is None:
            pace = DEFAULT_PACING_SEC

    # 注入 fetch_one 时不探 twitter 二进制（测试/CI 无 CLI）；真实路径 fail-closed。
    if fetch_one is None:
        _ensure_twitter_available()

        def fetch_posts(handle: str) -> tuple[list[dict[str, Any]], str | None, str]:
            return _fetch_user_posts(handle, cap)
    else:
        fetch_posts = fetch_one
    fetch_md = fetch_article or fetch_x_article_markdown

    seen_urls: set[str] = set()
    for i, handle in enumerate(handles):
        if i > 0 and pace > 0:
            sleep(pace)

        raw, fatal_sig, sample = fetch_posts(handle)
        if i < PREFLIGHT_PROBE_ACCOUNTS and fatal_sig:
            logger.error(
                "preflight 探针（@%s，预算 %s 账号）命中上游全局故障签名，中止本轮",
                handle,
                PREFLIGHT_PROBE_ACCOUNTS,
            )
            _raise_broken(fatal_sig, PREFLIGHT_PROBE_ACCOUNTS, sample)
        if fatal_sig:
            # 非探针账号命中同一签名也即断（SC：UPSTREAM_FATAL 即断）
            _raise_broken(fatal_sig, i + 1, sample)

        filtered = _post_filter(handle, raw, cap)
        if not filtered:
            stats.skipped_empty_accounts += 1
            logger.info("x_account @%s yielded 0 original posts — skip account", handle)
            continue

        for tweet in filtered:
            path = classify_tweet_content_path(tweet)
            if path is ContentPath.SKIP_SHORT:
                stats.skipped_short_tweets += 1
                logger.info(
                    "x_account skip short tweet id=%s handle=@%s (no external URL, not Article)",
                    tweet.get("id"),
                    handle,
                )
                continue

            article: DiscoveredArticle | None = None
            if path is ContentPath.EXTERNAL:
                external = first_external_url(tweet)
                if not external:
                    stats.skipped_short_tweets += 1
                    continue
                article = _article_from_external(tweet, handle, slug, external)
            else:
                tweet_id = str(tweet.get("id") or "")
                markdown = ""
                if tweet_id:
                    markdown = fetch_md(tweet_id)
                if not markdown and not tweet.get("articleText"):
                    stats.skipped_short_tweets += 1
                    logger.warning(
                        "x_account article fetch empty id=%s handle=@%s — skip",
                        tweet_id,
                        handle,
                    )
                    continue
                article = _article_from_x_article(tweet, handle, slug, markdown)

            if article.url in seen_urls:
                stats.skipped_duplicates += 1
                continue
            seen_urls.add(article.url)

            if is_duplicate_by_hash(article.content_hash, ledger_path):
                stats.skipped_duplicates += 1
                continue
            result.articles.append(article)

    result.skipped_duplicates = stats.skipped_duplicates
    result.skipped_short_tweets = stats.skipped_short_tweets
    result.skipped_empty_accounts = stats.skipped_empty_accounts
    if stats.skipped_short_tweets or stats.skipped_empty_accounts:
        logger.warning(
            "x_account source=%s skipped_short_tweets=%d skipped_empty_accounts=%d "
            "discovered=%d (fail-visible; short tweets not archived)",
            slug,
            stats.skipped_short_tweets,
            stats.skipped_empty_accounts,
            len(result.articles),
        )
    return result


# orchestrator 延迟 import 用这个名字；实现函数是 discover_x_account_articles。
x_account_discovery = discover_x_account_articles
