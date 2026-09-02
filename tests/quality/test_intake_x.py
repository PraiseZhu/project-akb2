"""loop2 intake_x：禁 user-posts/search、配额、failfast、非法 JSON、borderline、锚点、去重。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.akb2.briefing.intake.l0_collector import UPSTREAM_FATAL_FAILFAST_STREAK
from src.akb2.quality.intake.raw_archiver import REQUIRED_ANCHORS
from src.akb2.quality.intake_x.channels import FORBIDDEN_ARGV_TOKENS, fetch_feed, fetch_list
from src.akb2.quality.intake_x.normalize import normalize_tweet
from src.akb2.quality.intake_x.orchestrator import IntakeXFailFast, main, run_intake_x
from src.akb2.quality.intake_x.prefilter import (
    PrefilterBatchResult,
    PrefilterDecision,
    prefilter_batch,
)
from src.akb2.quality.intake_x.sources import (
    FORBIDDEN_CONFIG_FIELDS,
    XSourcesConfigError,
    enabled_channels,
    load_x_sources,
)
from src.akb2.quality.llm_router import LLMResponse


def _write_sources(tmp_path: Path, *, list_enabled: bool = False, list_id: str = "") -> Path:
    payload = {
        "version": 1,
        "defaults": {"per_run_cap": 40, "pacing_sec": 0.0, "min_article_words": 400},
        "channels": [
            {"channel": "feed", "slug": "x-home-feed", "enabled": True, "per_run_cap": 2},
            {
                "channel": "bookmarks",
                "slug": "x-bookmarks",
                "enabled": True,
                "per_run_cap": 30,
                "per_day_cap": 60,
            },
            {
                "channel": "list",
                "slug": "x-wiki-list",
                "enabled": list_enabled,
                "list_id": list_id,
            },
        ],
    }
    path = tmp_path / "x_sources.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def test_forbidden_config_fields_and_argv():
    assert "user-posts" in FORBIDDEN_CONFIG_FIELDS or "user_posts" in FORBIDDEN_CONFIG_FIELDS
    assert "search" in FORBIDDEN_CONFIG_FIELDS
    assert "user-posts" in FORBIDDEN_ARGV_TOKENS
    assert "search" in FORBIDDEN_ARGV_TOKENS
    src = Path("src/akb2/quality/intake_x")
    blob = "\n".join(p.read_text(encoding="utf-8") for p in src.glob("*.py"))
    assert "twitter user-posts" not in blob
    assert "twitter search" not in blob
    assert "_fetch_user_posts" not in blob


def test_c3_placeholder_disabled_list(tmp_path: Path):
    path = _write_sources(tmp_path, list_enabled=False, list_id="")
    config = load_x_sources(path)
    kinds = {c.channel for c in enabled_channels(config)}
    assert "list" not in kinds
    assert "feed" in kinds
    assert "bookmarks" in kinds


def test_enabled_list_requires_id(tmp_path: Path):
    path = _write_sources(tmp_path, list_enabled=True, list_id="")
    with pytest.raises(XSourcesConfigError):
        load_x_sources(path)


def test_enabled_list_rejects_handle_as_id(tmp_path: Path):
    path = _write_sources(tmp_path, list_enabled=True, list_id="PraiseZhu52")
    with pytest.raises(XSourcesConfigError, match="numeric"):
        load_x_sources(path)


def test_repo_x_sources_list_numeric_when_enabled():
    path = Path(__file__).resolve().parents[2] / "config/x_sources.yaml"
    config = load_x_sources(path)
    lists = [c for c in enabled_channels(config) if c.channel == "list"]
    assert len(lists) >= 4, "loop2 needs 4 unique List ids"
    ids = [channel.list_id for channel in lists]
    for list_id in ids:
        assert list_id.isdigit()
    assert len(ids) == len(set(ids))


def test_enabled_lists_reject_duplicate_id(tmp_path: Path):
    payload = {
        "version": 1,
        "defaults": {"per_run_cap": 40, "pacing_sec": 0.0, "min_article_words": 400},
        "channels": [
            {
                "channel": "list",
                "slug": "a",
                "enabled": True,
                "list_id": "111",
            },
            {
                "channel": "list",
                "slug": "b",
                "enabled": True,
                "list_id": "111",
            },
        ],
    }
    path = tmp_path / "x_sources.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    with pytest.raises(XSourcesConfigError, match="duplicate list_id"):
        load_x_sources(path)


def test_loop2_intake_raises_defuddle_timeout():
    """Mini 首轮 remixicon.com 在 30s 被掐。包装脚本须抬 AKB2_DEFUDDLE_TIMEOUT_S。"""
    wrapper = Path(__file__).resolve().parents[2] / "scripts/akb2-loop2-intake.sh"
    text = wrapper.read_text(encoding="utf-8")
    assert "AKB2_DEFUDDLE_TIMEOUT_S" in text
    assert "90" in text


def test_quota_constants_match_plan():
    from src.akb2.quality.intake_x.sources import (
        X_BOOKMARKS_PER_DAY_CAP,
        X_BOOKMARKS_PER_RUN_CAP,
        X_PER_CHANNEL_PER_RUN_CAP,
        X_RUNS_PER_DAY,
    )

    assert X_PER_CHANNEL_PER_RUN_CAP == 40
    assert X_BOOKMARKS_PER_RUN_CAP == 30
    assert X_BOOKMARKS_PER_DAY_CAP == 60
    assert X_RUNS_PER_DAY == 6


def test_channels_argv_never_include_forbidden(monkeypatch):
    captured: list[list[str]] = []

    def fake_run(cmd):
        captured.append(list(cmd))
        raise RuntimeError("stop")

    monkeypatch.setattr(
        "src.akb2.quality.intake_x.channels._run_twitter", fake_run
    )
    monkeypatch.setattr(
        "src.akb2.quality.intake_x.channels._twitter_bin", lambda: "twitter"
    )
    with pytest.raises(RuntimeError):
        fetch_feed(3)
    with pytest.raises(RuntimeError):
        fetch_list("123", 3)
    joined = " ".join(" ".join(cmd) for cmd in captured)
    assert "user-posts" not in joined
    assert " search " not in f" {joined} "


def test_client_transaction_warning_does_not_drop_ok_json():
    """stderr 有 Failed to init ClientTransaction 但 stdout 是 ok:true → 收条目。"""
    import json
    import subprocess

    from src.akb2.quality.intake_x.channels import _parse_twitter_payload

    payload = {
        "ok": True,
        "schema_version": "1",
        "data": [{"id": "1", "text": "hello"}, {"id": "2", "text": "world"}],
    }
    result = subprocess.CompletedProcess(
        args=["twitter", "feed"],
        returncode=0,
        stdout=json.dumps(payload),
        stderr="WARNING twitter_cli.client: Failed to init ClientTransaction: "
        "'NoneType' object has no attribute 'group'\n",
    )
    items, fatal, sample = _parse_twitter_payload(result)
    assert fatal is None
    assert sample == ""
    assert [row["id"] for row in items] == ["1", "2"]


def test_client_transaction_warning_still_fatal_without_json():
    import subprocess

    from src.akb2.quality.intake_x.channels import _parse_twitter_payload

    result = subprocess.CompletedProcess(
        args=["twitter", "feed"],
        returncode=1,
        stdout="",
        stderr="Failed to init ClientTransaction: boom",
    )
    items, fatal, sample = _parse_twitter_payload(result)
    assert items == []
    assert fatal == "Failed to init ClientTransaction"
    assert "ClientTransaction" in sample


def test_prefilter_illegal_json_drops_batch():
    def fake_route(*_args, **_kwargs):
        return LLMResponse(content="not-json", model_used="grok-4.6")

    result = prefilter_batch(
        [{"id": "1", "text": "hello world article"}],
        route_llm_fn=fake_route,
    )
    assert result.dropped_batch is True
    assert result.reason == "illegal_json"
    assert result.kept == []


def test_prefilter_borderline_medium_recheck_once():
    calls: list[str] = []

    def fake_route(*args, **kwargs):
        effort = kwargs.get("reasoning_effort")
        calls.append(effort)
        if effort == "low":
            payload = {
                "items": [
                    {
                        "keep": True,
                        "lane_hint": "concept",
                        "topic": "harness",
                        "why": "longform",
                        "confidence": 0.5,
                    }
                ]
            }
        else:
            payload = {
                "items": [
                    {
                        "keep": True,
                        "lane_hint": "concept",
                        "topic": "harness",
                        "why": "recheck",
                        "confidence": 0.8,
                    }
                ]
            }
        return LLMResponse(content=payload, model_used="grok-4.6")

    result = prefilter_batch(
        [{"id": "1", "text": "long form article about harness"}],
        route_llm_fn=fake_route,
    )
    assert result.dropped_batch is False
    assert result.rechecked == 1
    assert calls == ["low", "medium"]
    assert result.kept and result.kept[0][1].confidence == 0.8


def test_prefilter_borderline_unresolved_drops_batch():
    def fake_route(*args, **kwargs):
        payload = {
            "items": [
                {
                    "keep": True,
                    "lane_hint": "concept",
                    "topic": "x",
                    "why": "maybe",
                    "confidence": 0.5,
                }
            ]
        }
        return LLMResponse(content=payload, model_used="grok-4.6")

    result = prefilter_batch([{"id": "1", "text": "maybe"}], route_llm_fn=fake_route)
    assert result.dropped_batch is True
    assert result.reason == "borderline_unresolved"
    assert result.rechecked == 1


def test_required_anchors_unchanged():
    assert REQUIRED_ANCHORS == ("A", "E", "H", "M", "Z")


def test_failfast_streak(tmp_path: Path):
    path = _write_sources(tmp_path, list_enabled=True, list_id="42")
    calls = {"n": 0}

    def fetch(_channel, last_fetch_monotonic=None):
        calls["n"] += 1
        return [], "Failed to init ClientTransaction", "Failed to init ClientTransaction"

    with pytest.raises(IntakeXFailFast):
        run_intake_x(
            sources_config=path,
            state_file=tmp_path / "state.jsonl",
            output_dir=tmp_path / "raw",
            quality_ledger_path="",
            dry_run=True,
            fetch_fn=fetch,
            prefilter_fn=lambda items, **kwargs: PrefilterBatchResult(),
        )
    assert calls["n"] == UPSTREAM_FATAL_FAILFAST_STREAK


def test_cross_channel_and_cross_loop_dedup(tmp_path: Path):
    path = _write_sources(tmp_path)
    tweet = {
        "id": "99",
        "text": "https://example.com/long-article",
        "urls": ["https://example.com/long-article"],
        "author": {"screenName": "alice"},
        "createdAtISO": "2026-09-01T00:00:00Z",
    }
    vault = tmp_path / "vault"
    sources = vault / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "existing.md").write_text(
        "---\nsource_url: https://example.com/long-article\n---\n",
        encoding="utf-8",
    )
    seen: list[str] = []

    def fetch(channel, last_fetch_monotonic=None):
        seen.append(channel.slug)
        return [tweet], None, ""

    def keep_all(items, **kwargs):
        return PrefilterBatchResult(
            kept=[(item, PrefilterDecision(keep=True, confidence=0.9)) for item in items]
        )

    stats = run_intake_x(
        sources_config=path,
        state_file=tmp_path / "state.jsonl",
        output_dir=tmp_path / "raw",
        vault_root=vault,
        quality_ledger_path="",
        dry_run=False,
        fetch_fn=fetch,
        prefilter_fn=keep_all,
    )
    assert stats["skipped_seen"] >= 1
    assert stats["archived"] == 0


def test_normalize_skips_short_tweets():
    from src.akb2.quality.intake_x.sources import XChannel

    channel = XChannel(channel="feed", slug="x-home-feed", enabled=True)
    tweet = {"id": "1", "text": "gm", "author": {"screenName": "bob"}}
    assert normalize_tweet(tweet, channel) is None


def test_bookmarks_day_cap_skips_fetch_when_full(tmp_path: Path):
    """per_day_cap=60 必须跨轮累计；今天已 archived 60 条后 bookmarks 不再 fetch。"""
    import json
    from datetime import UTC, datetime

    path = _write_sources(tmp_path)
    state = tmp_path / "state.jsonl"
    today = datetime.now(UTC).strftime("%Y-%m-%dT12:00:00Z")
    rows = [
        json.dumps(
            {
                "article_id": f"bm-{i}",
                "daily_count": i + 1,
                "last_fetch_at": today,
                "quarantine_status": False,
                "run_id": "old",
                "source_slug": "x-bookmarks",
                "status": "archived",
            },
            sort_keys=True,
        )
        for i in range(60)
    ]
    state.write_text("\n".join(rows) + "\n", encoding="utf-8")
    fetched_slugs: list[str] = []

    def fetch(channel, last_fetch_monotonic=None):
        fetched_slugs.append(channel.slug)
        return [], None, ""

    stats = run_intake_x(
        sources_config=path,
        state_file=state,
        output_dir=tmp_path / "raw",
        quality_ledger_path="",
        dry_run=True,
        fetch_fn=fetch,
        prefilter_fn=lambda items, **kwargs: PrefilterBatchResult(),
    )
    assert stats["skipped_day_cap"] == 1
    assert "x-bookmarks" not in fetched_slugs
    assert "x-home-feed" in fetched_slugs


def test_prefilter_budget_overflow_is_counted(tmp_path: Path, monkeypatch):
    """超过 X_PREFILTER_MAX_BATCHES_PER_RUN 的条目必须计入 skipped_prefilter_budget。"""
    from src.akb2.quality.intake_x import prefilter as prefilter_mod

    monkeypatch.setattr(prefilter_mod, "X_PREFILTER_BATCH_SIZE", 2)
    monkeypatch.setattr(prefilter_mod, "X_PREFILTER_MAX_BATCHES_PER_RUN", 1)
    monkeypatch.setattr(
        "src.akb2.quality.intake_x.orchestrator.X_PREFILTER_BATCH_SIZE", 2
    )
    monkeypatch.setattr(
        "src.akb2.quality.intake_x.orchestrator.X_PREFILTER_MAX_BATCHES_PER_RUN", 1
    )
    path = _write_sources(tmp_path)
    tweets = [
        {
            "id": str(i),
            "text": "https://example.com/long-article-enough-words",
            "urls": ["https://example.com/long-article-enough-words"],
            "author": {"screenName": "alice"},
        }
        for i in range(5)
    ]

    def fetch(channel, last_fetch_monotonic=None):
        if channel.slug != "x-home-feed":
            return [], None, ""
        return tweets, None, ""

    def keep_all(items, **kwargs):
        return PrefilterBatchResult(
            kept=[(item, PrefilterDecision(keep=True, confidence=0.9)) for item in items]
        )

    stats = run_intake_x(
        sources_config=path,
        state_file=tmp_path / "state.jsonl",
        output_dir=tmp_path / "raw",
        quality_ledger_path="",
        dry_run=True,
        fetch_fn=fetch,
        prefilter_fn=keep_all,
    )
    assert stats["skipped_prefilter_budget"] == 3
    assert stats["kept"] == 2


def test_orchestrator_has_main_guard():
    """plist 直调 python -m akb2.quality.intake_x.orchestrator 必须进 main。"""
    src = (
        Path(__file__).resolve().parents[2]
        / "src/akb2/quality/intake_x/orchestrator.py"
    ).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in src
    assert "sys.exit(main())" in src


def test_cli_empty_quality_ledger_disables_default(tmp_path: Path, monkeypatch):
    """--quality-ledger '' 必须关掉默认共享账本；省略该 flag 才走默认路径。"""
    from src.akb2.quality.intake_x import orchestrator as orch

    captured: list[object] = []

    def fake_run(**kwargs):
        captured.append(kwargs.get("quality_ledger_path"))
        return {"archived": 0}

    monkeypatch.setattr(orch, "run_intake_x", fake_run)
    assert main(["--quality-ledger", ""]) == 0
    assert captured[-1] == ""
    assert main([]) == 0
    assert captured[-1] is None
