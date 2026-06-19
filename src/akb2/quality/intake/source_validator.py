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
import json
import os
import re
import sys
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
_RSS_ITEM_RE = re.compile(
    r"<item[^>]*>.*?<link[^>]*>([^<]+)</link>", re.DOTALL | re.IGNORECASE
)
_GENERIC_LINK_RE = re.compile(
    r"<link[^>]*>\s*(https?://[^\s<]+)\s*</link>", re.IGNORECASE
)

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
    "/feed.xml", "/rss.xml", "/atom.xml", "/index.xml",
    "/feed", "/rss", "/feed/", "/blog/feed.xml", "/blog/rss.xml",
)
_LIVE_TIMEOUT = 10


def _looks_like_feed(text: str) -> bool:
    """粗判响应体是否为 RSS/Atom feed（区分真 feed 与 404/HTML 页）。"""
    head = text[:1500].lower()
    return any(
        t in head for t in ("<rss", "<feed", "<?xml", "<atom", "rss version", "xmlns")
    )


def _extract_latest_url(feed_text: str) -> str:
    """从 feed XML 文本中提取最新文章 URL。"""
    if not feed_text:
        return ""
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


def _try_feed(url: str) -> tuple[int, bool, str, str]:
    """试抓一个 URL（模块级 httpx.get，便于测试 patch）。

    返回 (status_code, is_feed, text, error_kind)。
    error_kind ∈ {"", "timeout", "error"}；正常响应为 ""，异常时 status=-1。
    """
    import httpx

    try:
        resp = httpx.get(
            url, timeout=_LIVE_TIMEOUT, follow_redirects=True, headers=_LIVE_HEADERS
        )
        text = resp.text
        return resp.status_code, (resp.status_code < 400 and _looks_like_feed(text)), text, ""
    except httpx.TimeoutException:
        return -1, False, "", "timeout"
    except Exception:
        return -1, False, "", "error"


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


def _validate_source_live(source: dict[str, Any], run_id: str) -> dict[str, Any]:
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

    feed_url = source.get("feed_url", "")
    listing_url = source.get("listing_url", "")
    if not feed_url and not listing_url:
        record["error"] = "no_url"
        return record

    # 阶梯 1: 原 feed + 真实 UA（trial_fetch_status 锚定首次尝试，保留 timeout 语义）
    if feed_url:
        status, is_feed, text, err = _try_feed(feed_url)
        if err:
            record["trial_fetch_status"] = err
            record["error"] = err
        else:
            record["trial_fetch_status"] = f"http_{status}"
        if is_feed:
            record["reachable"] = True
            record["working_strategy"] = "ua_original_feed"
            record["working_url"] = feed_url
            latest = _extract_latest_url(text)
            record["latest_post"] = latest
            record["trial_fetch"] = latest
            return record
        if status == 403:
            record["needs_browser"] = True

    # 阶梯 2: 候选 feed 路径
    for cand in _feed_path_candidates(feed_url, listing_url):
        status, is_feed, text, _err = _try_feed(cand)
        if is_feed:
            record["reachable"] = True
            record["working_strategy"] = "alternate_feed_path"
            record["working_url"] = cand
            record["trial_fetch_status"] = f"http_{status}"
            record["error"] = ""
            latest = _extract_latest_url(text)
            record["latest_post"] = latest
            record["trial_fetch"] = latest
            return record
        if status == 403:
            record["needs_browser"] = True

    # 阶梯 3: listing 页可达性（无 feed 但页面 2xx → 可走列表抓取）
    if listing_url:
        status, _is_feed, _text, _err = _try_feed(listing_url)
        if 0 < status < 400:
            record["reachable"] = True
            record["working_strategy"] = "listing_reachable"
            record["working_url"] = listing_url
            record["trial_fetch_status"] = f"http_{status}"
            record["error"] = ""
            return record
        if status == 403:
            record["needs_browser"] = True

    # 阶梯 4-5: 全部 httpx 失败
    if record["needs_browser"]:
        record["working_strategy"] = "browser_needed"
        record["error"] = "all httpx strategies 403 — needs scrapling browser"
    elif not record.get("error"):
        record["error"] = "no reachable feed/listing"
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

    for i, source in enumerate(sources):
        if dry_run:
            record = _validate_source_dry(source, run_id)
        else:
            record = _validate_source_live(source, run_id)

        records.append(record)

        slug = source.get("slug", "unknown")
        print(f"  [{i+1}/{len(sources)}] {slug}: reachable={record['reachable']}")

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
