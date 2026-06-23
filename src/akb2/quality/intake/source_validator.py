"""逐源验证 CLI — feed 可达性 + 最近发文 + 试抓。

用法:
    python3 -m akb2.quality.intake.source_validator \\
        --config config/main_sources.yaml \\
        --out reports/source-validation/sources-ledger.jsonl \\
        [--dry-run | --live]

逐源验证动作（--live 模式）:
    1. feed 可达性: httpx.get(feed_url) 检测是否返回 2xx
    2. 最近发文: 从 feed 响应解析最新文章 URL
    3. 试抓一篇: 记录 trial_fetch（最新文章 URL）

产出:
    sources-ledger.jsonl — 每行一条源记录（run_id 原子写入，非追加）:
        {run_id, url, feed, reachable, latest_post, trial_fetch, ...}

开发期 --dry-run: 仅验证 CLI 框架可运行，不真抓（默认模式）。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _load_sources(config_path: str) -> list[dict[str, Any]]:
    """从 YAML 配置加载源列表。"""
    import yaml

    with open(config_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "sources" not in data:
        raise ValueError(f"Invalid config format: expected 'sources' key in {config_path}")
    return data["sources"]


def _make_base_record(source: dict[str, Any], run_id: str, method: str) -> dict[str, Any]:
    """构建源验证记录的公共骨架，dry/live 两路共用。"""
    feed_url = source.get("feed_url", source.get("listing_url", ""))
    return {
        "run_id": run_id,
        "slug": source.get("slug", "unknown"),
        "url": source.get("listing_url", feed_url),
        "feed": feed_url,
        "reachable": False,
        "latest_post": "",
        "trial_fetch": "",
        "trial_fetch_status": "not_run",
        "working_strategy": "not_run",
        "working_url": "",
        "needs_browser": False,
        "failure_class": "",
        "status": source.get("status", "trial"),
        "category": source.get("category", "unknown"),
        "validated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": method,
        "notes": source.get("notes", ""),
    }


def _validate_source_dry(source: dict[str, Any], run_id: str) -> dict[str, Any]:
    """dry-run 模式：生成占位验证记录（不执行真实网络请求）。"""
    return _make_base_record(source, run_id, "dry-run")


_ATOM_LINK_RE = re.compile(r'<link[^>]+href=["\']([^"\']+)["\']', re.IGNORECASE)
_ATOM_ENTRY_RE = re.compile(r"<entry[^>]*>.*?</entry>", re.DOTALL | re.IGNORECASE)
_RSS_ITEM_RE = re.compile(
    r"<item[^>]*>.*?<link[^>]*>([^<]+)</link>", re.DOTALL | re.IGNORECASE
)
_GENERIC_LINK_RE = re.compile(
    r"<link[^>]*>\s*(https?://[^\s<]+)\s*</link>", re.IGNORECASE
)
_ANCHOR_HREF_RE = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*([\"'])(.*?)\1", re.DOTALL | re.IGNORECASE
)
_RAW_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_DATE_PATH_RE = re.compile(r"/20\d{2}(?:/|-)")
_ARTICLE_PATH_HINTS = (
    "/blog/",
    "/posts/",
    "/post/",
    "/news/",
    "/articles/",
    "/article/",
    "/engineering/",
    "/research/",
    "/changelog/",
    "/learn/",
    "/guides/",
)
_SKIP_PATH_PARTS = {
    "",
    "feed",
    "rss",
    "atom",
    "tag",
    "tags",
    "category",
    "categories",
    "author",
    "authors",
    "archive",
    "archives",
    "search",
    "login",
    "signin",
    "signup",
    "privacy",
    "terms",
}

# 真实浏览器 UA — 多数站点对默认/爬虫 UA 返回 403/404，对浏览器 UA 正常响应
_REALISTIC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_LIVE_HEADERS = {
    "User-Agent": _REALISTIC_UA,
    "Accept": (
        "application/rss+xml, application/atom+xml, application/xml, "
        "text/xml, text/html;q=0.9, */*;q=0.8"
    ),
}
# 候选 feed 路径 — 配置的 feed_url 失效时按域名根/listing 目录试这些标准路径
_FEED_PATH_CANDIDATES = (
    "/feed.xml",
    "/rss.xml",
    "/atom.xml",
    "/feed",
    "/rss",
)
_EDGE_BLOCK_STATUSES = {400, 402, 403}
_CONFIG_STALE_STATUSES = {404, 410}
_RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
_GENERIC_FAILURE_VALUES = {
    "error",
    "timeout",
    "no reachable feed/listing",
    "all httpx strategies 403 — needs scrapling browser",
}
_SLOW_RECHECK_SLUGS = {
    "netflix-tech-blog",
    "eric-elliott",
    "meta-ai-blog",
    "mistral-ai-blog",
}
_LIVE_TIMEOUT = float(os.environ.get("AKB2_SOURCE_VALIDATOR_TIMEOUT", "8"))
_LIVE_CONNECT_TIMEOUT = float(os.environ.get("AKB2_SOURCE_VALIDATOR_CONNECT_TIMEOUT", "2"))
_LIVE_RETRIES = int(os.environ.get("AKB2_SOURCE_VALIDATOR_RETRIES", "2"))
_SLOW_RECHECK_RETRIES = int(os.environ.get("AKB2_SOURCE_VALIDATOR_SLOW_RETRIES", "3"))
_RETRY_DELAY_SECONDS = float(os.environ.get("AKB2_SOURCE_VALIDATOR_RETRY_DELAY", "0.2"))
_SOURCE_INTERVAL_SECONDS = float(os.environ.get("AKB2_SOURCE_VALIDATOR_INTERVAL", "0.2"))


def _looks_like_feed(text: str) -> bool:
    """粗判响应体是否为 RSS/Atom feed（区分真 feed 与 404/HTML 页）。"""
    head = text.lstrip()[:1500].lower()
    return bool(
        re.search(r"<rss\b", head)
        or re.search(r"<feed\b", head)
        or "rss version" in head
        or "application/atom+xml" in head
    )


def _extract_latest_url(feed_text: str) -> str:
    """从 feed XML 文本中提取最新文章 URL。"""
    if not feed_text:
        return ""
    entry = _ATOM_ENTRY_RE.search(feed_text)
    if entry:
        m = _ATOM_LINK_RE.search(entry.group(0))
        if m:
            url = m.group(1).strip()
            if url.startswith("http"):
                return url
    m = _ATOM_LINK_RE.search(feed_text)
    if m:
        url = m.group(1).strip()
        if url.startswith("http"):
            return url
    m = _RSS_ITEM_RE.search(feed_text)
    if m:
        url = m.group(1).strip()
        if url.startswith("http"):
            return url
    m = _GENERIC_LINK_RE.search(feed_text)
    if m:
        return m.group(1).strip()
    return ""


def _classify_http_status(status: int) -> str:
    """把 HTTP 状态码归到源验证失败类。"""
    if status in _CONFIG_STALE_STATUSES:
        return "config_stale"
    if status in _EDGE_BLOCK_STATUSES:
        return "anti_bot_or_edge_block"
    if status in _RETRYABLE_HTTP_STATUSES:
        return "network_timeout"
    if status >= 400:
        return "dead"
    return ""


def _classify_exception(exc: Exception) -> str:
    """把 httpx/连接异常归到源验证失败类。"""
    import httpx

    message = str(exc).lower()
    dns_markers = (
        "nodename nor servname",
        "name or service not known",
        "no address associated",
        "temporary failure in name resolution",
        "name resolution",
        "dns",
    )
    if any(marker in message for marker in dns_markers):
        return "config_stale"
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return "network_timeout"
    return "dead"


def _try_feed(
    url: str,
    retries: int | None = None,
    client: Any | None = None,
) -> tuple[int, bool, str, str]:
    """试抓一个 URL（模块级 httpx.get，便于测试 patch）。

    返回 (status_code, is_feed, text, error_kind)。
    error_kind 为类型化失败类；正常 HTTP 响应为 ""，异常时 status=-1。
    """
    import httpx

    max_attempts = max(1, retries if retries is not None else _LIVE_RETRIES)
    last_error = "dead"
    for attempt in range(max_attempts):
        try:
            timeout = httpx.Timeout(
                _LIVE_TIMEOUT,
                connect=_LIVE_CONNECT_TIMEOUT,
                pool=_LIVE_CONNECT_TIMEOUT,
            )
            get = client.get if client is not None else httpx.get
            resp = get(url, timeout=timeout, follow_redirects=True, headers=_LIVE_HEADERS)
            text = resp.text
            is_feed = resp.status_code < 400 and _looks_like_feed(text)
            if resp.status_code not in _RETRYABLE_HTTP_STATUSES or attempt == max_attempts - 1:
                return resp.status_code, is_feed, text, ""
        except httpx.TimeoutException as exc:
            last_error = _classify_exception(exc)
        except Exception as exc:
            last_error = _classify_exception(exc)

        if attempt < max_attempts - 1 and _RETRY_DELAY_SECONDS > 0:
            time.sleep(_RETRY_DELAY_SECONDS)
    return -1, False, "", last_error


def _feed_path_candidates(feed_url: str, listing_url: str) -> list[str]:
    """基于 feed/listing 的域名根，生成候选 feed URL 列表（去重，排除原 feed）。"""
    from urllib.parse import urlparse

    bases: list[str] = []
    for src in (feed_url, listing_url):
        if src:
            p = urlparse(src)
            root = f"{p.scheme}://{p.netloc}"
            if root and root not in bases:
                bases.append(root)
    cands: list[str] = []
    for base in bases:
        for path in _FEED_PATH_CANDIDATES:
            u = base + path
            if u != feed_url and u not in cands:
                cands.append(u)
    return cands


def _same_site(candidate: str, listing_url: str) -> bool:
    """候选文章 URL 必须留在同站点或同注册主域的 www 变体内。"""
    from urllib.parse import urlparse

    try:
        cand_host = urlparse(candidate).netloc.lower().removeprefix("www.")
        listing_host = urlparse(listing_url).netloc.lower().removeprefix("www.")
    except ValueError:
        return False
    return cand_host == listing_host or cand_host.endswith("." + listing_host)


def _clean_candidate_url(raw_url: str, listing_url: str) -> str:
    """规范化 listing 页里抽出的 href/raw URL。"""
    from urllib.parse import urldefrag, urljoin

    value = html.unescape(raw_url.strip())
    if not value or value.startswith(("#", "mailto:", "tel:", "javascript:")):
        return ""
    try:
        absolute = urljoin(listing_url, value)
        absolute, _fragment = urldefrag(absolute)
    except ValueError:
        return ""
    return absolute


def _listing_candidate_score(candidate: str, listing_url: str) -> int:
    """为 listing 页候选链接打分，越像文章越靠前。"""
    from urllib.parse import urlparse

    if not candidate.startswith("http") or not _same_site(candidate, listing_url):
        return -1

    try:
        parsed = urlparse(candidate)
        listing_parsed = urlparse(listing_url)
    except ValueError:
        return -1
    path = parsed.path.rstrip("/")
    listing_path = listing_parsed.path.rstrip("/")
    if path == listing_path or path in ("", "/"):
        return -1
    if re.search(r"\.(css|js|json|xml|png|jpe?g|gif|svg|webp|ico|pdf)$", path, re.I):
        return -1

    parts = [part.lower() for part in path.split("/") if part]
    if not parts or any(part in _SKIP_PATH_PARTS for part in parts[-2:]):
        return -1

    score = 0
    lower_path = path.lower() + "/"
    if _DATE_PATH_RE.search(lower_path):
        score += 40
    if any(hint in lower_path for hint in _ARTICLE_PATH_HINTS):
        score += 25
    if len(parts) >= 2:
        score += 10
    if len(path) > len(listing_path) + 6:
        score += 5
    return score


def _extract_listing_candidate_url(listing_html: str, listing_url: str) -> str:
    """从 listing HTML 中抽一个最像最新文章的同站链接。"""
    if not listing_html:
        return ""

    seen: set[str] = set()
    scored: list[tuple[int, int, str]] = []

    def add(raw_url: str, order: int) -> None:
        candidate = _clean_candidate_url(raw_url, listing_url)
        if not candidate or candidate in seen:
            return
        seen.add(candidate)
        score = _listing_candidate_score(candidate, listing_url)
        if score >= 0:
            scored.append((score, -order, candidate))

    for order, match in enumerate(_ANCHOR_HREF_RE.finditer(listing_html)):
        add(match.group(2), order)
    for order, match in enumerate(_RAW_URL_RE.finditer(listing_html), start=len(seen) + 1):
        add(match.group(0), order)

    if not scored:
        return ""
    scored.sort(reverse=True)
    return scored[0][2]


def _source_retry_count(source: dict[str, Any]) -> int:
    """少数不稳定源走更慢复验，其余源保持轻量。"""
    if source.get("slug") in _SLOW_RECHECK_SLUGS:
        return max(_LIVE_RETRIES, _SLOW_RECHECK_RETRIES)
    return _LIVE_RETRIES


def _remember_attempt(
    attempts: list[dict[str, Any]], url: str, status: int, err: str, text: str
) -> None:
    """记录一次探测结果，用于最终失败分类。"""
    attempts.append(
        {
            "url": url,
            "status": status,
            "error_kind": err,
            "failure_class": err or _classify_http_status(status),
            "browser_challenge": status == 403 and _looks_like_browser_challenge(text),
        }
    )


def _looks_like_browser_challenge(text: str) -> bool:
    """识别需要浏览器/JS 的边缘挑战页。"""
    lower = text[:4000].lower()
    return any(
        marker in lower
        for marker in (
            "cloudflare",
            "captcha",
            "enable javascript",
            "enable js",
            "checking your browser",
            "please enable cookies",
            "cf-browser-verification",
        )
    )


def _choose_failure_class(attempts: list[dict[str, Any]], needs_browser: bool) -> str:
    """从所有探测结果中选最终失败类。"""
    classes = {a.get("failure_class", "") for a in attempts if a.get("failure_class")}
    statuses = {a.get("status") for a in attempts}
    if any(a.get("browser_challenge") for a in attempts) or (
        needs_browser and statuses <= {403, -1}
    ):
        return "browser_needed"
    if classes & {"anti_bot_or_edge_block"}:
        return "anti_bot_or_edge_block"
    if classes and classes <= {"config_stale"}:
        return "config_stale"
    if classes and classes <= {"network_timeout"}:
        return "network_timeout"
    if "config_stale" in classes and "network_timeout" not in classes:
        return "config_stale"
    if "network_timeout" in classes and not classes & {"dead", "anti_bot_or_edge_block"}:
        return "network_timeout"
    return "dead"


def _set_failure(
    record: dict[str, Any], failure_class: str, attempts: list[dict[str, Any]]
) -> None:
    """落失败分类，避免旧版泛化 error。"""
    record["failure_class"] = failure_class
    if failure_class == "browser_needed":
        record["working_strategy"] = "browser_needed"
        record["needs_browser"] = True
    else:
        record["working_strategy"] = "dead"

    status_bits = []
    for attempt in attempts:
        status = attempt.get("status")
        err = attempt.get("error_kind")
        if err:
            status_bits.append(err)
        elif isinstance(status, int) and status > 0:
            status_bits.append(f"http_{status}")
    detail = ",".join(sorted(set(status_bits))) if status_bits else "no_url"
    record["error"] = f"{failure_class}:{detail}"


def _validate_source_live(
    source: dict[str, Any],
    run_id: str,
    client: Any | None = None,
) -> dict[str, Any]:
    """真网验证（抓取工具阶梯，匹配每源合适策略 + 反爬 UA）。

    阶梯（任一命中即停，记录 working_strategy/working_url）:
        1. ua_original_feed   — 真实浏览器 UA 抓配置的 feed_url，是有效 feed
        2. alternate_feed_path — 原 feed 失效时，按标准路径(/feed.xml /rss.xml 等)探测
        3. listing_reachable   — 无可用 feed，但 listing 页 2xx（可走列表抓取）
        4. browser_needed      — 全部 403（Cloudflare/反爬），需 scrapling 浏览器
        5. dead                — 非 403 的硬失败（402/404/400/连接失败）

    reachable=True 涵盖 1-3（httpx 可直达）；browser_needed 单独标记
    （需浏览器层，reachable=False 但 needs_browser=True，非真死）。
    """
    record: dict[str, Any] = _make_base_record(source, run_id, "live")
    record["error"] = ""
    record["working_strategy"] = "dead"
    record["working_url"] = ""
    record["needs_browser"] = False
    record["failure_class"] = ""

    feed_url = source.get("feed_url", "")
    listing_url = source.get("listing_url", "")
    if not feed_url and not listing_url:
        _set_failure(record, "config_stale", [])
        return record

    attempts: list[dict[str, Any]] = []
    retries = _source_retry_count(source)
    original_feed_failure_class = ""

    # 阶梯 1: 原 feed + 真实 UA（trial_fetch_status 锚定首次尝试，保留 timeout 语义）
    if feed_url:
        status, is_feed, text, err = _try_feed(feed_url, retries=retries, client=client)
        _remember_attempt(attempts, feed_url, status, err, text)
        if err:
            record["trial_fetch_status"] = err
            record["failure_class"] = err
            original_feed_failure_class = err
        else:
            record["trial_fetch_status"] = f"http_{status}"
            original_feed_failure_class = _classify_http_status(status)
        if is_feed:
            record["reachable"] = True
            record["working_strategy"] = "ua_original_feed"
            record["working_url"] = feed_url
            record["failure_class"] = ""
            record["error"] = ""
            latest = _extract_latest_url(text)
            record["latest_post"] = latest
            record["trial_fetch"] = latest
            return record
        if status == 403 and _looks_like_browser_challenge(text):
            record["needs_browser"] = True

    # 阶梯 2: 候选 feed 路径。无配置 feed 时不猜 feed，直接进入 listing 探测。
    if feed_url and original_feed_failure_class != "network_timeout":
        for cand in _feed_path_candidates(feed_url, listing_url):
            status, is_feed, text, err = _try_feed(cand, retries=retries, client=client)
            _remember_attempt(attempts, cand, status, err, text)
            if is_feed:
                record["reachable"] = True
                record["working_strategy"] = "alternate_feed_path"
                record["working_url"] = cand
                record["trial_fetch_status"] = f"http_{status}"
                record["error"] = ""
                record["failure_class"] = ""
                latest = _extract_latest_url(text)
                record["latest_post"] = latest
                record["trial_fetch"] = latest
                return record
            if status == 403 and _looks_like_browser_challenge(text):
                record["needs_browser"] = True

    # 阶梯 3: listing 页可达性（无 feed 但页面 2xx → 可走列表抓取）
    if listing_url:
        status, _is_feed, text, err = _try_feed(listing_url, retries=retries, client=client)
        _remember_attempt(attempts, listing_url, status, err, text)
        if 0 < status < 400:
            record["reachable"] = True
            record["working_strategy"] = "listing_reachable"
            record["working_url"] = listing_url
            record["trial_fetch_status"] = f"http_{status}"
            record["error"] = ""
            record["failure_class"] = ""
            latest = _extract_listing_candidate_url(text, listing_url)
            record["latest_post"] = latest
            record["trial_fetch"] = latest
            return record
        if status == 403 and _looks_like_browser_challenge(text):
            record["needs_browser"] = True

    # 阶梯 4-5: 全部 httpx 失败
    failure_class = _choose_failure_class(attempts, bool(record["needs_browser"]))
    _set_failure(record, failure_class, attempts)
    return record


def _write_jsonl_atomic(path: str, records: list[dict[str, Any]]) -> None:
    """原子写入 jsonl：先写 .tmp 临时文件，成功后 rename（覆盖，非追加）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = p.with_suffix(".jsonl.tmp")

    with open(tmp_path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")

    # 原子替换（POSIX rename 是原子的；Windows 需 replace）
    os.replace(tmp_path, p)


def validate_sources(
    config_path: str,
    output_path: str,
    dry_run: bool = True,
) -> int:
    """逐源验证主函数。

    Args:
        config_path: main_sources.yaml 路径
        output_path: sources-ledger.jsonl 输出路径
        dry_run: True 时不真抓

    Returns:
        0 成功，1 失败。
    """
    try:
        sources = _load_sources(config_path)
    except Exception as exc:
        print(f"ERROR: Failed to load config: {exc}", file=sys.stderr)
        return 1

    if not sources:
        print("ERROR: No sources found in config", file=sys.stderr)
        return 1

    print(f"Validating {len(sources)} sources (dry_run={dry_run})...")

    # 同一次运行的所有记录共享同一个 run_id
    run_id = str(uuid.uuid4())
    records: list[dict[str, Any]] = []

    client = None
    if not dry_run:
        import httpx

        client = httpx.Client(trust_env=False)

    try:
        for i, source in enumerate(sources):
            if not dry_run and i > 0 and _SOURCE_INTERVAL_SECONDS > 0:
                time.sleep(_SOURCE_INTERVAL_SECONDS)
            if dry_run:
                record = _validate_source_dry(source, run_id)
            else:
                record = _validate_source_live(source, run_id, client=client)

            records.append(record)

            slug = source.get("slug", "unknown")
            print(f"  [{i+1}/{len(sources)}] {slug}: reachable={record['reachable']}")
    finally:
        if client is not None:
            client.close()

    # 原子写入（非追加，覆盖旧 ledger）
    _write_jsonl_atomic(output_path, records)

    reachable_count = sum(1 for r in records if r.get("reachable"))
    print(f"Done. {reachable_count}/{len(records)} reachable. Ledger written to {output_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(
        description="AKB2 逐源验证 CLI — 76 源 feed 可达性 + 试抓",
    )
    parser.add_argument(
        "--config",
        default="config/main_sources.yaml",
        help="源配置文件路径",
    )
    parser.add_argument(
        "--out",
        default="reports/source-validation/sources-ledger.jsonl",
        help="输出 jsonl 路径",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="dry-run 模式，不真抓",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="真网验证模式（需 AKB2_LIVE_VALIDATE=1）",
    )

    args = parser.parse_args(argv)

    # --live 需要环境变量门控（防误触真网请求）
    if args.live and not os.environ.get("AKB2_LIVE_VALIDATE"):
        print(
            "ERROR: --live requires AKB2_LIVE_VALIDATE=1 env var. "
            "Set it to confirm live network requests.",
            file=sys.stderr,
        )
        return 1

    dry_run = not args.live
    return validate_sources(args.config, args.out, dry_run=dry_run)


if __name__ == "__main__":
    sys.exit(main())
