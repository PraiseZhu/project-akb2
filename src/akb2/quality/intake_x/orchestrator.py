"""loop2 X 采集编排：轮转 → 抓取 → 预筛 → 归一 → archive_raw。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.akb2.briefing.intake.l0_collector import (
    UPSTREAM_FATAL_FAILFAST_STREAK,
    UPSTREAM_HARD_FAILURE_FAILFAST_STREAK,
    match_upstream_fatal,
)
from src.akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file
from src.akb2.quality.intake.orchestrator import (
    DEFAULT_OUTPUT_DIR,
    _source_rotation_offset,
    load_seen_article_ids,
    write_ledger_entry,
)
from src.akb2.quality.intake.raw_archiver import (
    REQUIRED_ANCHORS,
    archive_from_extract_result,
)
from src.akb2.quality.intake.x_account_discovery import XAccountAuthError
from src.akb2.quality.intake_x.channels import fetch_channel
from src.akb2.quality.intake_x.normalize import extract_normalized, normalize_tweet
from src.akb2.quality.intake_x.prefilter import (
    X_PREFILTER_BATCH_SIZE,
    X_PREFILTER_MAX_BATCHES_PER_RUN,
    prefilter_batch,
)
from src.akb2.quality.intake_x.sources import (
    DEFAULT_SOURCES_CONFIG,
    XChannel,
    enabled_channels,
    load_x_sources,
)
from src.akb2.quality.runner import _read_frontmatter_value

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = "reports/intake/loop2-intake-state.jsonl"
LOOP2_LOOP_ID = "loop2"
QUALITY_LEDGER_FILE = "quality-ledger.jsonl"

FetchChannelFn = Callable[..., tuple[list[dict[str, Any]], str | None, str]]
PrefilterFn = Callable[..., Any]


class IntakeXFailFast(RuntimeError):
    """连续 fatal / timeout / non-JSON 达到 streak 后停轮。"""


def _rotate(channels: tuple[XChannel, ...]) -> tuple[XChannel, ...]:
    if not channels:
        return channels
    offset = _source_rotation_offset(len(channels))
    return channels[offset:] + channels[:offset]


def _is_hard_failure(sample: str) -> bool:
    text = (sample or "").lower()
    return text in {"timeout", "non-json"} or "non-json" in text or text.startswith("timeout")


def _utc_today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _count_channel_archived_today(state_file: str | Path, slug: str, day: str) -> int:
    path = Path(state_file)
    if not path.exists():
        return 0
    counted = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("source_slug") != slug:
                continue
            if record.get("status") != "archived":
                continue
            fetched = str(record.get("last_fetch_at") or "")
            if fetched.startswith(day):
                counted += 1
    return counted


def _append_quality_event(ledger_path: str | Path, event: str, details: dict[str, Any]) -> None:
    try:
        from src.akb2.loops.ledger import append_event
    except ImportError:
        return
    append_event(
        str(ledger_path),
        {
            "loop_id": LOOP2_LOOP_ID,
            "node": "intake_x",
            "event": event,
            "content_hash": details.get("article_id") or details.get("slug") or "",
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "details": details,
        },
    )


def _cross_loop_exists(
    *,
    url: str,
    vault_root: str | Path | None,
) -> bool:
    if not vault_root:
        return False
    source_dir = Path(vault_root) / "wiki" / "sources"
    if not source_dir.exists():
        return False
    for page in sorted(source_dir.glob("*.md")):
        if _read_frontmatter_value(page, "source_url") == url:
            return True
    return False


def run_intake_x(
    sources_config: str | Path = DEFAULT_SOURCES_CONFIG,
    state_file: str | Path = DEFAULT_STATE_FILE,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    vault_root: str | Path | None = None,
    quality_ledger_path: str | Path | None = None,
    dry_run: bool = False,
    limit: int = 0,
    fetch_fn: FetchChannelFn = fetch_channel,
    prefilter_fn: PrefilterFn = prefilter_batch,
) -> dict[str, int]:
    """跑一轮 X 三通道采集。C3 list_id 空且 enabled=false 时该通道不进 enabled_channels。"""
    config = load_x_sources(sources_config)
    channels = _rotate(enabled_channels(config))
    seen_ids = load_seen_article_ids(state_file)
    seen_fingerprints: set[str] = set()
    run_id = str(uuid.uuid4())
    stats = {
        "fetched": 0,
        "kept": 0,
        "archived": 0,
        "skipped_seen": 0,
        "skipped_short": 0,
        "dropped_prefilter": 0,
        "dropped_batch": 0,
        "skipped_day_cap": 0,
        "failed": 0,
    }
    if quality_ledger_path is None:
        quality_ledger_path = resolve_state_file(QUALITY_LEDGER_FILE, subdir=LEDGER_SUBDIR)
    elif str(quality_ledger_path) == "":
        quality_ledger_path = None
    fatal_streak = 0
    hard_streak = 0
    last_fetch: float | None = None
    batches_used = 0
    archived = 0

    for channel in channels:
        remaining_today = None
        if channel.per_day_cap is not None:
            used_today = _count_channel_archived_today(
                state_file, channel.slug, _utc_today()
            )
            remaining_today = max(0, int(channel.per_day_cap) - used_today)
            if remaining_today == 0:
                stats["skipped_day_cap"] += 1
                continue
        items, fatal, sample = fetch_fn(channel, last_fetch_monotonic=last_fetch)
        last_fetch = time.monotonic()
        if remaining_today is not None:
            items = items[:remaining_today]
        stats["fetched"] += len(items)
        if fatal or match_upstream_fatal(sample or ""):
            fatal_streak += 1
            if quality_ledger_path:
                _append_quality_event(
                    quality_ledger_path,
                    "intake_x_upstream_fatal",
                    {"slug": channel.slug, "sample": sample, "streak": fatal_streak},
                )
            if fatal_streak >= UPSTREAM_FATAL_FAILFAST_STREAK:
                raise IntakeXFailFast(f"upstream fatal streak={fatal_streak}: {sample}")
            continue
        fatal_streak = 0
        if _is_hard_failure(sample) and not items:
            hard_streak += 1
            if quality_ledger_path:
                _append_quality_event(
                    quality_ledger_path,
                    "intake_x_hard_failure",
                    {"slug": channel.slug, "sample": sample, "streak": hard_streak},
                )
            if hard_streak >= UPSTREAM_HARD_FAILURE_FAILFAST_STREAK:
                raise IntakeXFailFast(f"hard failure streak={hard_streak}: {sample}")
            continue
        hard_streak = 0

        pending: list[dict[str, Any]] = []
        for tweet in items:
            if limit and archived >= limit:
                break
            pending.append(tweet)
            if len(pending) < X_PREFILTER_BATCH_SIZE:
                continue
            if batches_used >= X_PREFILTER_MAX_BATCHES_PER_RUN:
                pending = []
                break
            archived += _consume_batch(
                pending,
                channel=channel,
                run_id=run_id,
                state_file=state_file,
                output_dir=output_dir,
                seen_ids=seen_ids,
                seen_fingerprints=seen_fingerprints,
                vault_root=vault_root,
                quality_ledger_path=quality_ledger_path,
                dry_run=dry_run,
                limit=limit,
                archived=archived,
                stats=stats,
                prefilter_fn=prefilter_fn,
            )
            batches_used += 1
            pending = []
        still_room = not limit or archived < limit
        if pending and batches_used < X_PREFILTER_MAX_BATCHES_PER_RUN and still_room:
            archived += _consume_batch(
                pending,
                channel=channel,
                run_id=run_id,
                state_file=state_file,
                output_dir=output_dir,
                seen_ids=seen_ids,
                seen_fingerprints=seen_fingerprints,
                vault_root=vault_root,
                quality_ledger_path=quality_ledger_path,
                dry_run=dry_run,
                limit=limit,
                archived=archived,
                stats=stats,
                prefilter_fn=prefilter_fn,
            )
            batches_used += 1
        if limit and archived >= limit:
            break
    stats["archived"] = archived
    return stats


def _consume_batch(
    tweets: list[dict[str, Any]],
    *,
    channel: XChannel,
    run_id: str,
    state_file: str | Path,
    output_dir: str | Path,
    seen_ids: set[str],
    seen_fingerprints: set[str],
    vault_root: str | Path | None,
    quality_ledger_path: str | Path | None,
    dry_run: bool,
    limit: int,
    archived: int,
    stats: dict[str, int],
    prefilter_fn: PrefilterFn,
) -> int:
    result = prefilter_fn(tweets, loop_id=LOOP2_LOOP_ID)
    if getattr(result, "dropped_batch", False):
        stats["dropped_batch"] += 1
        if quality_ledger_path:
            _append_quality_event(
                quality_ledger_path,
                "intake_x_prefilter_drop_batch",
                {
                    "slug": channel.slug,
                    "reason": getattr(result, "reason", "illegal_json"),
                    "size": len(tweets),
                },
            )
        return 0

    gained = 0
    for tweet, _decision in getattr(result, "kept", []):
        if limit and archived + gained >= limit:
            break
        stats["kept"] += 1
        normalized = normalize_tweet(tweet, channel)
        if normalized is None:
            stats["skipped_short"] += 1
            continue
        if normalized.article_id in seen_ids or normalized.fingerprint in seen_fingerprints:
            stats["skipped_seen"] += 1
            if not dry_run:
                write_ledger_entry(
                    state_file,
                    run_id=run_id,
                    source_slug=channel.slug,
                    article_id=normalized.article_id,
                    status="skipped_seen",
                    daily_count=0,
                    quarantine_status=False,
                )
            continue
        if vault_root and _cross_loop_exists(
            url=normalized.context.url,
            vault_root=vault_root,
        ):
            stats["skipped_seen"] += 1
            if not dry_run:
                write_ledger_entry(
                    state_file,
                    run_id=run_id,
                    source_slug=channel.slug,
                    article_id=normalized.article_id,
                    status="skipped_seen",
                    daily_count=0,
                    quarantine_status=False,
                )
            continue
        if dry_run:
            seen_ids.add(normalized.article_id)
            seen_fingerprints.add(normalized.fingerprint)
            gained += 1
            continue
        extract_result = extract_normalized(
            normalized, min_words=channel.min_article_words
        )
        if extract_result is None:
            stats["failed"] += 1
            write_ledger_entry(
                state_file,
                run_id=run_id,
                source_slug=channel.slug,
                article_id=normalized.article_id,
                status="failed",
                daily_count=0,
                quarantine_status=False,
            )
            continue
        archive_result = archive_from_extract_result(
            extract_result=extract_result,
            output_dir=output_dir,
            source_metadata=normalized.context,
        )
        if not archive_result.success:
            stats["failed"] += 1
            write_ledger_entry(
                state_file,
                run_id=run_id,
                source_slug=channel.slug,
                article_id=normalized.article_id,
                status="failed",
                daily_count=0,
                quarantine_status=False,
            )
            continue
        write_ledger_entry(
            state_file,
            run_id=run_id,
            source_slug=channel.slug,
            article_id=normalized.article_id,
            status="archived",
            daily_count=archived + gained + 1,
            quarantine_status=False,
        )
        seen_ids.add(normalized.article_id)
        seen_fingerprints.add(normalized.fingerprint)
        gained += 1
    stats["dropped_prefilter"] += len(getattr(result, "dropped", []) or [])
    return gained


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AKB2 loop2 X intake")
    parser.add_argument("--sources-config", default=str(DEFAULT_SOURCES_CONFIG))
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--vault-root", default="")
    parser.add_argument("--quality-ledger", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        stats = run_intake_x(
            sources_config=args.sources_config,
            state_file=args.state_file,
            output_dir=args.output_dir,
            vault_root=args.vault_root or None,
            quality_ledger_path=args.quality_ledger or None,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    except XAccountAuthError as exc:
        logger.error("intake_x auth fail-closed: %s", exc)
        return 2
    except IntakeXFailFast as exc:
        logger.error("intake_x fail-fast: %s", exc)
        return 3
    print(stats)
    return 0


REQUIRED_ANCHORS_EXPORT = REQUIRED_ANCHORS


if __name__ == "__main__":
    sys.exit(main())
