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


def _validate_source_dry(source: dict[str, Any], run_id: str) -> dict[str, Any]:
    """dry-run 模式：生成占位验证记录。

    不执行真实网络请求，仅验证配置结构完整性。
    """
    slug = source.get("slug", "unknown")
    feed_url = source.get("feed_url", source.get("listing_url", ""))
    url = source.get("listing_url", feed_url)

    return {
        "run_id": run_id,
        "slug": slug,
        "url": url,
        "feed": feed_url,
        "reachable": False,  # dry-run 不真抓
        "latest_post": "",
        "trial_fetch": "",
        "trial_fetch_status": "not_run",
        "status": source.get("status", "trial"),
        "category": source.get("category", "unknown"),
        "validated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": "dry-run",
        "notes": source.get("notes", ""),
    }


def _extract_latest_url(feed_text: str) -> str:
    """从 feed XML 文本中提取最新文章 URL。

    优先提取 RSS <item><link> 或 Atom <entry><link href="...">，
    返回第一个非空匹配值，失败返回空串。
    """
    if not feed_text:
        return ""

    # Atom: <link href="https://..." />
    atom_match = re.search(
        r'<link[^>]+href=["\']([^"\']+)["\']', feed_text, re.IGNORECASE
    )
    if atom_match:
        url = atom_match.group(1).strip()
        if url.startswith("http"):
            return url

    # RSS: <item>...<link>https://...</link>
    rss_match = re.search(
        r"<item[^>]*>.*?<link[^>]*>([^<]+)</link>", feed_text, re.DOTALL | re.IGNORECASE
    )
    if rss_match:
        url = rss_match.group(1).strip()
        if url.startswith("http"):
            return url

    # 通用 <link>...</link>（部分 RSS 在 channel 外）
    generic_match = re.search(
        r"<link[^>]*>\s*(https?://[^\s<]+)\s*</link>", feed_text, re.IGNORECASE
    )
    if generic_match:
        return generic_match.group(1).strip()

    return ""


def _validate_source_live(source: dict[str, Any], run_id: str) -> dict[str, Any]:
    """真网验证：feed 可达性 + 最近发文 URL + 记录试抓。

    使用 httpx 进行真实网络请求（timeout=10s）。
    reachable: 2xx 状态码 → True。
    latest_post: 从 feed 响应解析第一个文章 URL。
    trial_fetch: 与 latest_post 相同（记录试抓目标 URL）。
    """
    import httpx

    slug = source.get("slug", "unknown")
    feed_url = source.get("feed_url", source.get("listing_url", ""))
    url = source.get("listing_url", feed_url)

    record: dict[str, Any] = {
        "run_id": run_id,
        "slug": slug,
        "url": url,
        "feed": feed_url,
        "reachable": False,
        "latest_post": "",
        "trial_fetch": "",
        "trial_fetch_status": "not_run",
        "status": source.get("status", "trial"),
        "category": source.get("category", "unknown"),
        "validated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": "live",
        "notes": source.get("notes", ""),
        "error": "",
    }

    fetch_target = feed_url or url
    if not fetch_target:
        record["error"] = "no_url"
        return record

    try:
        headers = {
            "User-Agent": "AKB2-SourceValidator/1.0 (feed reachability check; +https://akb2.local)"
        }
        resp = httpx.get(fetch_target, timeout=10, follow_redirects=True, headers=headers)
        reachable = resp.status_code < 400
        record["reachable"] = reachable
        record["trial_fetch_status"] = f"http_{resp.status_code}"

        if reachable:
            feed_text = resp.text
            latest = _extract_latest_url(feed_text)
            record["latest_post"] = latest
            record["trial_fetch"] = latest

    except httpx.TimeoutException:
        record["error"] = "timeout"
        record["trial_fetch_status"] = "timeout"
    except Exception as exc:
        record["error"] = str(exc)[:200]
        record["trial_fetch_status"] = "error"

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
