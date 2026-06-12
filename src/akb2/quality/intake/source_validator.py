"""逐源验证 CLI — feed 可达性 + 最近发文 + 试抓。

用法:
    python3 -m akb2.quality.intake.source_validator \\
        --config config/main_sources.yaml \\
        --out reports/source-validation/sources-ledger.jsonl \\
        [--dry-run]

逐源验证动作:
    1. feed 可达性: HEAD 请求检测 feed_url 是否可达
    2. 最近发文: 解析 feed 获取最新条目
    3. 试抓一篇: 尝试抽取最新文章正文

产出:
    sources-ledger.jsonl — 每行一条源记录:
        {url, feed, reachable, latest_post, trial_fetch, ...}

开发期 --dry-run: 仅验证 CLI 框架可运行，不真抓。
"""

from __future__ import annotations

import argparse
import json
import sys
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


def _validate_source_dry(source: dict[str, Any]) -> dict[str, Any]:
    """dry-run 模式：生成占位验证记录。

    不执行真实网络请求，仅验证配置结构完整性。
    """
    slug = source.get("slug", "unknown")
    feed_url = source.get("feed_url", source.get("listing_url", ""))
    url = source.get("listing_url", feed_url)

    return {
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


def _validate_source_live(source: dict[str, Any]) -> dict[str, Any]:
    """真网验证：feed 可达性 + 最近发文 + 试抓一篇。

    执行期真网动作，遵守 rate_limit。
    """
    slug = source.get("slug", "unknown")
    feed_url = source.get("feed_url", source.get("listing_url", ""))
    url = source.get("listing_url", feed_url)

    record: dict[str, Any] = {
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
    }

    # 真实抓取逻辑在执行期实现
    # 当前为 skeleton CLI，仅返回占位记录
    return record


def _append_jsonl(path: str, record: dict[str, Any]) -> None:
    """追加一行 jsonl 记录。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


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

    for i, source in enumerate(sources):
        if dry_run:
            record = _validate_source_dry(source)
        else:
            record = _validate_source_live(source)

        _append_jsonl(output_path, record)

        slug = source.get("slug", "unknown")
        print(f"  [{i+1}/{len(sources)}] {slug}: reachable={record['reachable']}")

    print(f"Done. Ledger written to {output_path}")
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
        default=True,
        help="dry-run 模式，不真抓（默认）",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="真网验证模式",
    )

    args = parser.parse_args(argv)
    dry_run = not args.live
    return validate_sources(args.config, args.out, dry_run=dry_run)


if __name__ == "__main__":
    sys.exit(main())
