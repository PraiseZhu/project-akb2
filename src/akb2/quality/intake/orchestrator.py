"""Intake orchestrator — 读 main_sources.yaml → discover → extract → archive。

CLI 入口:
    python -m akb2.quality.intake.orchestrator [options]

选项:
    --dry-run               不写任何文件，仅统计（不调 extract/archive，不写 ledger）
    --limit N               最多处理 N 篇文章（跨所有源；0=不限）
    --sources-config PATH   yaml 配置文件路径（默认 config/main_sources.yaml）
    --state-file PATH       intake-state.jsonl 路径（默认 reports/intake/intake-state.jsonl）
    --output-dir PATH       raw 归档输出目录（默认 reports/intake/raw/）
    --log-level LEVEL       日志等级（默认 WARNING）

ledger 行格式（每行一个 JSON）:
    {
      "run_id": "<uuid4>",
      "source_slug": "<slug>",
      "article_id": "<sha256[:12]>",
      "last_fetch_at": "<ISO8601>",
      "daily_count": <int>,
      "quarantine_status": <bool>,
      "status": "archived|skipped_seen|skipped_rate_limit|skipped_quarantine|failed"
    }

幂等性（SC-5）:
    同一 URL 的 article_id 确定性不变；ledger 存在该 article_id 则跳过，status=skipped_seen。

rate_limit（SC-8）:
    每源设 rate_limit_per_run（默认 50 篇/次），超限 status=skipped_rate_limit。
    注：yaml 的 rate_limit 字段为秒间隔（留作未来请求节流），
    本 orchestrator 使用源级 per-run 产量上限（RATE_LIMIT_PER_RUN）。

quarantine（SC-8）:
    利用 discovery.check_quarantine 判断该源当日产量是否异常；触发则 status=skipped_quarantine，
    不调用 extract/archive。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from src.akb2.quality.intake.discovery import (
    DiscoveredArticle,
    check_quarantine,
    discover_articles,
)
from src.akb2.quality.intake.extract import extract_content
from src.akb2.quality.intake.models import ExtractionContext, compute_article_id
from src.akb2.quality.intake.raw_archiver import archive_from_extract_result

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 具名常量（宪法 P4：预算显式）
# ---------------------------------------------------------------------------

RATE_LIMIT_PER_RUN = 50  # 单源单次运行最多处理文章数
DEFAULT_SOURCES_CONFIG = "config/main_sources.yaml"
DEFAULT_STATE_FILE = "reports/intake/intake-state.jsonl"
DEFAULT_OUTPUT_DIR = "reports/intake/raw/"

# ---------------------------------------------------------------------------
# ledger 读写
# ---------------------------------------------------------------------------


def load_seen_article_ids(state_file: str | Path) -> set[str]:
    """从 ledger 加载已处理的 article_id 集合（用于幂等跳过）。"""
    path = Path(state_file)
    if not path.exists():
        return set()

    seen: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                article_id = record.get("article_id")
                # 只有终态 archived 才阻止重试（failed/skipped_* 允许下次重处理）
                if article_id and record.get("status") == "archived":
                    seen.add(article_id)
            except json.JSONDecodeError:
                logger.warning("Malformed ledger line, skipping: %s", line[:80])
    return seen


def write_ledger_entry(
    state_file: str | Path,
    run_id: str,
    source_slug: str,
    article_id: str,
    status: str,
    daily_count: int,
    quarantine_status: bool,
) -> None:
    """向 ledger 追加一条记录。dry_run 时不调用此函数。"""
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    record: dict[str, Any] = {
        "run_id": run_id,
        "source_slug": source_slug,
        "article_id": article_id,
        "last_fetch_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "daily_count": daily_count,
        "quarantine_status": quarantine_status,
        "status": status,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 源配置加载
# ---------------------------------------------------------------------------


def load_sources_config(config_path: str | Path) -> list[dict[str, Any]]:
    """读取 main_sources.yaml，返回 sources 列表。"""
    path = Path(config_path)
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    sources: list[dict[str, Any]] = data.get("sources", [])
    return sources


# ---------------------------------------------------------------------------
# 单篇文章处理
# ---------------------------------------------------------------------------


def _process_article(
    article: DiscoveredArticle,
    source: dict[str, Any],
    run_id: str,
    state_file: str | Path,
    output_dir: str | Path,
    seen_ids: set[str],
    source_daily_count: int,
    quarantine_triggered: bool,
    dry_run: bool,
    failures_path: str | Path | None = None,
) -> str:
    """处理单篇文章，返回 status 字符串。

    副作用：
      - 非 dry_run 时写 ledger
      - 非 dry_run 且 status=archived 时写 raw 文件
    """
    article_id = compute_article_id(article.url)

    # 幂等检查（SC-5）
    if article_id in seen_ids:
        logger.debug("Skipping seen article_id=%s url=%s", article_id, article.url)
        return "skipped_seen"

    # quarantine 检查（SC-8）
    if quarantine_triggered:
        logger.info(
            "Source %s quarantined, skipping article %s", article.source_slug, article_id
        )
        if not dry_run:
            write_ledger_entry(
                state_file,
                run_id=run_id,
                source_slug=article.source_slug,
                article_id=article_id,
                status="skipped_quarantine",
                daily_count=source_daily_count,
                quarantine_status=True,
            )
        seen_ids.add(article_id)
        return "skipped_quarantine"

    # rate_limit 检查（SC-8）
    if source_daily_count >= RATE_LIMIT_PER_RUN:
        logger.info(
            "Source %s rate limited (count=%d), skipping %s",
            article.source_slug,
            source_daily_count,
            article_id,
        )
        if not dry_run:
            write_ledger_entry(
                state_file,
                run_id=run_id,
                source_slug=article.source_slug,
                article_id=article_id,
                status="skipped_rate_limit",
                daily_count=source_daily_count,
                quarantine_status=False,
            )
        seen_ids.add(article_id)
        return "skipped_rate_limit"

    # dry_run：跳过 extract/archive，仅统计 discovered
    if dry_run:
        seen_ids.add(article_id)
        return "dry_run_discovered"

    # extract + archive（真实路径）
    ctx = ExtractionContext(
        source_slug=article.source_slug,
        source_publisher=source.get("domain", ""),
        feed_title=article.title,
        feed_author="",
        feed_published=article.published_at or "",
        url=article.url,
        content_hash=article.content_hash,
    )

    extract_result = extract_content(
        url=article.url,
        extraction_config=ctx,
    )

    if not extract_result.extraction_success:
        logger.warning(
            "Extraction failed for %s: %s", article.url, extract_result.error
        )
        write_ledger_entry(
            state_file,
            run_id=run_id,
            source_slug=article.source_slug,
            article_id=article_id,
            status="failed",
            daily_count=source_daily_count,
            quarantine_status=False,
        )
        seen_ids.add(article_id)
        return "failed"

    archive_result = archive_from_extract_result(
        extract_result=extract_result,
        output_dir=output_dir,
        source_metadata=ctx,
        failures_path=str(failures_path) if failures_path else None,
    )

    if archive_result.success:
        write_ledger_entry(
            state_file,
            run_id=run_id,
            source_slug=article.source_slug,
            article_id=article_id,
            status="archived",
            daily_count=source_daily_count + 1,
            quarantine_status=False,
        )
        seen_ids.add(article_id)
        return "archived"
    else:
        logger.warning(
            "Archive failed for %s: %s", article.url, archive_result.error
        )
        write_ledger_entry(
            state_file,
            run_id=run_id,
            source_slug=article.source_slug,
            article_id=article_id,
            status="failed",
            daily_count=source_daily_count,
            quarantine_status=False,
        )
        seen_ids.add(article_id)
        return "failed"


# ---------------------------------------------------------------------------
# 主运行函数
# ---------------------------------------------------------------------------


def run_intake(
    sources_config: str | Path = DEFAULT_SOURCES_CONFIG,
    state_file: str | Path = DEFAULT_STATE_FILE,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    dry_run: bool = False,
    limit: int = 0,
    failures_path: str | Path | None = None,
) -> dict[str, int]:
    """执行一次完整的 intake 流程。

    Args:
        sources_config: main_sources.yaml 路径
        state_file: intake-state.jsonl 路径
        output_dir: raw 归档输出目录
        dry_run: 不写任何文件，仅统计
        failures_path: 归档失败签名 jsonl 路径（None 时不写签名）
        limit: 最多处理文章总数（0=不限）

    Returns:
        {"discovered": N, "skipped": M, "archived": K} 统计字典
    """
    run_id = str(uuid.uuid4())
    logger.info("Intake run started run_id=%s dry_run=%s limit=%d", run_id, dry_run, limit)

    # 加载源配置
    try:
        sources = load_sources_config(sources_config)
    except FileNotFoundError as exc:
        logger.error("Sources config not found: %s", exc)
        raise

    # 加载已处理 article_id（幂等）
    seen_ids = load_seen_article_ids(state_file)
    logger.debug("Loaded %d seen article_ids from ledger", len(seen_ids))

    # 统计计数器
    total_discovered = 0
    total_skipped = 0
    total_archived = 0
    total_processed = 0  # 已处理（不含 skipped_seen）

    # 每源当日产量追踪（用于 quarantine 计算）
    source_daily_counts: dict[str, int] = {}

    for source in sources:
        slug = source.get("slug", "unknown")

        # 发现文章（RSS feed；feed_content=None 表示真实抓取）
        try:
            discover_result = discover_articles(source, ledger_path=state_file, dry_run=dry_run)
        except Exception as exc:
            logger.warning("Discovery failed for source=%s: %s", slug, exc)
            continue

        if discover_result.error:
            logger.warning(
                "Discovery error for source=%s: %s", slug, discover_result.error
            )
            # listing 模式未实现，skip 此源但不报致命错误
            if discover_result.method in ("listing", "none"):
                continue

        articles = discover_result.articles
        if not articles:
            logger.debug("No articles discovered for source=%s", slug)
            continue

        total_discovered += len(articles)

        # 当日产量（用于 quarantine 检测）
        source_daily_counts[slug] = len(articles)

        # quarantine 检测
        quarantine_triggered = check_quarantine(slug, source_daily_counts)
        if quarantine_triggered:
            logger.info("Quarantine triggered for source=%s", slug)

        # 每源当次归档计数
        source_run_count = 0

        for article in articles:
            # 全局 limit 检查
            if limit > 0 and total_processed >= limit:
                break

            status = _process_article(
                article=article,
                source=source,
                run_id=run_id,
                state_file=state_file,
                output_dir=output_dir,
                seen_ids=seen_ids,
                source_daily_count=source_run_count,
                quarantine_triggered=quarantine_triggered,
                dry_run=dry_run,
                failures_path=failures_path,
            )

            if status == "skipped_seen":
                total_skipped += 1
            elif status == "archived":
                total_archived += 1
                source_run_count += 1
                total_processed += 1
            elif status == "dry_run_discovered":
                # dry_run 模式：discovered 已计入，不计 archived
                total_processed += 1
            else:
                # skipped_rate_limit / skipped_quarantine / failed
                total_skipped += 1
                total_processed += 1

        # 全局 limit 达到，退出源循环
        if limit > 0 and total_processed >= limit:
            logger.info("Global limit %d reached, stopping.", limit)
            break

    stats = {
        "discovered": total_discovered,
        "skipped": total_skipped,
        "archived": total_archived,
    }
    logger.info("Intake run complete: %s", stats)
    return stats


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AKB2 Intake Orchestrator — 读源配置，发现文章，提取归档。",
        prog="python -m akb2.quality.intake.orchestrator",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="不写任何文件，仅统计（不调 extract/archive，不写 ledger）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="最多处理 N 篇文章（0=不限，默认 0）",
    )
    parser.add_argument(
        "--sources-config",
        default=DEFAULT_SOURCES_CONFIG,
        metavar="PATH",
        help=f"yaml 配置文件路径（默认 {DEFAULT_SOURCES_CONFIG}）",
    )
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        metavar="PATH",
        help=f"intake-state.jsonl 路径（默认 {DEFAULT_STATE_FILE}）",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        metavar="PATH",
        help=f"raw 归档输出目录（默认 {DEFAULT_OUTPUT_DIR}）",
    )
    parser.add_argument(
        "--failures-path",
        default=None,
        metavar="PATH",
        help="归档失败签名 jsonl 路径（不传则不写签名）",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志等级（默认 WARNING）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主函数，返回退出码（0=成功）。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        stats = run_intake(
            sources_config=args.sources_config,
            state_file=args.state_file,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
            limit=args.limit,
            failures_path=args.failures_path,
        )
    except Exception as exc:
        logger.error("Intake run failed: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # SC-7: stdout 含 discovered=/skipped=/archived= 计数
    print(
        f"discovered={stats['discovered']} "
        f"skipped={stats['skipped']} "
        f"archived={stats['archived']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
