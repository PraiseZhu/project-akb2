"""Preflight HTTP-layer probe for calibration corpus.

诊断 CLI（非门控）：对 corpus 中非 fixture URL 发 HTTP 请求，产 per-entry
HTTP 层分类报告，供校准报告区分死链 / 元数据失败 / 抽取失败三类根因。

公开接口:
    run_preflight(corpus_path) -> dict  — 核心探活
    main(argv) -> int                   — CLI 入口（exit 0，诊断非门）

字段契约（SC-3）:
    每条 entry 含 http_status / final_url / content_type / redirect /
    canonical_drift / soft_404 / short_body / missing_metadata + 回显
    expected / source(fixture|live)。

soft_404 派生（B3-3）:
    (a) title/body 命中 not-found pattern → True
    (b) final_url 域 ≠ 请求域 / canonical_drift 指向异域 → True
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

# ---------------------------------------------------------------------------
# 具名常量
# ---------------------------------------------------------------------------

# 复用 extract.py 的 UA（follow_redirects 一致）
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Soft-404 pattern 检测（B3-3a）：title/body 命中即标 True
NOT_FOUND_PATTERNS = [
    r"404",
    r"not found",
    r"page not found",
    r"no longer available",
]
_NOT_FOUND_RE = re.compile("|".join(NOT_FOUND_PATTERNS), re.IGNORECASE)

# HTTP 层正文最短阈值（字符数，保守下限；extract.py MIN_CONTENT_LENGTH=200 词）
MIN_BODY_LENGTH = 500

# not-found pattern 检查窗口（前 5K 字符，避免大页面全扫）
_PATTERN_SCAN_WINDOW = 5000

# HTTP 超时
_HTTP_TIMEOUT = 30


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _domain(url: str) -> str:
    """提取 URL 的 hostname（去掉前导 www.），用于域漂移判定。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _check_soft_404(
    html: str, request_url: str, final_url: str, canonical_drift: bool
) -> bool:
    """派生 soft_404（B3-3）。

    (a) title/body 命中 not-found pattern → True
    (b) final_url 域 ≠ 请求域 / canonical_drift 指向异域 → True
    """
    # (a) pattern 命中
    if _NOT_FOUND_RE.search(html[:_PATTERN_SCAN_WINDOW]):
        return True
    # (b) 域漂移
    req_domain = _domain(request_url)
    final_domain = _domain(final_url)
    if req_domain and final_domain and req_domain != final_domain:
        return True
    if canonical_drift:
        return True
    return False


def _check_short_body(html: str) -> bool:
    """HTTP 层正文过短判定（字符数 < MIN_BODY_LENGTH）。"""
    return len(html.strip()) < MIN_BODY_LENGTH


def _check_missing_metadata(html: str) -> bool:
    """轻量 HTML 扫描：title/author/published 元数据是否有缺。

    非全量抽取——仅检查 meta 标签/JSON-LD 是否存在，供 SC-2b 判定
    metadata_complete 预期。任一缺失 → True。
    """
    has_title = bool(re.search(r"<title[^>]*>\s*\S", html, re.IGNORECASE))
    has_author = bool(
        re.search(r'<meta\s[^>]*name=["\']author["\']', html, re.IGNORECASE)
        or re.search(r'"author"\s*:', html, re.IGNORECASE)  # JSON-LD
    )
    has_published = bool(
        re.search(
            r'<meta\s[^>]*(?:property|name)=["\']article:published_time["\']',
            html,
            re.IGNORECASE,
        )
        or re.search(r'<time\s[^>]*datetime=', html, re.IGNORECASE)
        or re.search(r'"datePublished"\s*:', html, re.IGNORECASE)  # JSON-LD
    )
    return not (has_title and has_author and has_published)


def _probe_url(url: str) -> dict[str, Any]:
    """HTTP 探活单个 URL，返回分类 dict。"""
    result: dict[str, Any] = {
        "http_status": 0,
        "final_url": "",
        "content_type": "",
        "redirect": False,
        "canonical_drift": False,
        "soft_404": False,
        "short_body": False,
        "missing_metadata": False,
    }
    try:
        resp = httpx.get(
            url,
            follow_redirects=True,
            timeout=_HTTP_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        result["http_status"] = resp.status_code
        final_url = str(resp.url)
        result["final_url"] = final_url
        result["content_type"] = resp.headers.get("content-type", "")
        result["redirect"] = final_url != url
        result["canonical_drift"] = _domain(final_url) != _domain(url)

        html = resp.text
        result["short_body"] = _check_short_body(html)
        result["missing_metadata"] = _check_missing_metadata(html)
        result["soft_404"] = _check_soft_404(
            html, url, final_url, result["canonical_drift"]
        )
    except Exception as exc:  # noqa: BLE001 — 诊断工具，捕获全部网络异常
        result["http_status"] = 0
        result["final_url"] = url
        result["short_body"] = True
        result["soft_404"] = True
        result["missing_metadata"] = True
        result["_error"] = str(exc)[:200]

    return result


# ---------------------------------------------------------------------------
# 核心
# ---------------------------------------------------------------------------


def run_preflight(corpus_path: str) -> dict[str, Any]:
    """对 corpus 全 URL 执行 HTTP 层探活，返回分类报告。

    fixture: 前缀的 URL 跳过网络，标 source=fixture。
    其余 URL 发 HTTP（follow_redirects），产 per-entry 分类字段。
    """
    data = yaml.safe_load(Path(corpus_path).read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = data.get("entries", [])

    results: list[dict[str, Any]] = []
    dead_links = 0
    redirects = 0
    soft_404_count = 0

    for entry in entries:
        url = entry.get("url", "")
        expected = entry.get("expected", "ingest")

        if url.startswith("fixture:"):
            results.append(
                {
                    "url": url,
                    "expected": expected,
                    "source": "fixture",
                    "http_status": 0,
                    "final_url": "",
                    "content_type": "",
                    "redirect": False,
                    "canonical_drift": False,
                    "soft_404": False,
                    "short_body": False,
                    "missing_metadata": False,
                }
            )
            continue

        probe = _probe_url(url)
        probe["url"] = url
        probe["expected"] = expected
        probe["source"] = "live"
        results.append(probe)

        if (probe.get("http_status") or 0) >= 400:
            dead_links += 1
        if probe.get("redirect"):
            redirects += 1
        if probe.get("soft_404"):
            soft_404_count += 1

    return {
        "corpus_path": corpus_path,
        "total_entries": len(entries),
        "entries": results,
        "summary": {
            "dead_links": dead_links,
            "redirects": redirects,
            "soft_404": soft_404_count,
            "fixture_entries": sum(
                1 for r in results if r.get("source") == "fixture"
            ),
            "live_entries": sum(1 for r in results if r.get("source") == "live"),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Preflight CLI 入口（诊断非门，exit 0）。"""
    parser = argparse.ArgumentParser(
        description=(
            "AKB2 Preflight — corpus HTTP-layer classification "
            "(diagnostic, not a gate)"
        )
    )
    parser.add_argument("--corpus", required=True, help="corpus YAML path")
    parser.add_argument("--out", required=True, help="output JSON path")
    args = parser.parse_args(argv)

    report = run_preflight(args.corpus)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    s = report["summary"]
    print(
        f"Preflight: {report['total_entries']} entries "
        f"({s['live_entries']} live, {s['fixture_entries']} fixture)",
        file=sys.stderr,
    )
    print(
        f"  dead_links={s['dead_links']} redirects={s['redirects']} "
        f"soft_404={s['soft_404']}",
        file=sys.stderr,
    )

    return 0  # 诊断工具，始终 exit 0（原则 4 失败可见，但 preflight 本身不是门）


if __name__ == "__main__":
    sys.exit(main())
