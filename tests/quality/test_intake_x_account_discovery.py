"""x5-config-plist：x_account trial 源 + 独立 twitter plist。

首批 handle 以 reports/x-account-smoke.json first_batch 为唯一真相。
MengTo/rileybrown/nickfloats 为 rate_limited，不得入首批。
"""

from __future__ import annotations

import json
import plistlib
from pathlib import Path
from unittest.mock import patch

import yaml

from src.akb2.quality.intake.orchestrator import (
    RSS_SOURCE_TYPE,
    X_ACCOUNT_SOURCE_TYPE,
    resolve_source_type,
    run_intake,
    source_matches_run_type,
)

REPO = Path(__file__).resolve().parents[2]
SOURCES_PATH = REPO / "config" / "main_sources.yaml"
SMOKE_PATH = REPO / "reports" / "x-account-smoke.json"
TWITTER_PLIST = REPO / "launchd" / "com.akb2.intake-twitter.plist"
RSS_PLIST = REPO / "launchd" / "com.akb2.intake.plist"
BLOCKED_HANDLES = ("MengTo", "rileybrown", "nickfloats")


def _load_sources() -> list[dict]:
    data = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return list(data["sources"])


def _x_account_trial(sources: list[dict]) -> list[dict]:
    return [
        s
        for s in sources
        if str(s.get("type") or "").strip() == "x_account"
        and str(s.get("status") or "").strip() == "trial"
    ]


def _load_plist(path: Path) -> dict:
    with path.open("rb") as fh:
        data = plistlib.load(fh)
    assert isinstance(data, dict)
    return data


def test_config_sources_and_plist_yaml_parses_and_first_batch_is_truth():
    smoke = json.loads(SMOKE_PATH.read_text(encoding="utf-8"))
    first_batch = list(smoke["first_batch"])
    assert first_batch, "smoke first_batch 不得为空"
    assert len(first_batch) == 19

    sources = _load_sources()
    x_trial = _x_account_trial(sources)
    assert len(x_trial) >= 8
    handles = [str(s.get("handle") or "") for s in x_trial]
    assert sorted(handles) == sorted(first_batch)
    assert set(handles) == set(first_batch)
    for blocked in BLOCKED_HANDLES:
        assert blocked not in handles
    for src in x_trial:
        assert src.get("lane") in {"concept", "design"}, src.get("slug")
        assert src.get("status") == "trial"


def test_config_sources_and_plist_twitter_plist_independent_of_rss_limit():
    twitter = _load_plist(TWITTER_PLIST)
    rss = _load_plist(RSS_PLIST)

    assert twitter["Label"] == "com.akb2.intake-twitter"
    assert twitter["StartInterval"] == 900
    t_args = twitter["ProgramArguments"]
    assert t_args[t_args.index("--limit") + 1] == "20"
    assert t_args[t_args.index("--state-file") + 1].endswith(
        "reports/intake/intake-twitter-state.jsonl"
    )
    assert "/tmp" not in t_args[t_args.index("--state-file") + 1]
    assert t_args[t_args.index("--source-type") + 1] == "x_account"

    r_args = rss["ProgramArguments"]
    assert r_args[r_args.index("--limit") + 1] == "30"
    assert r_args[r_args.index("--state-file") + 1].endswith(
        "reports/intake/intake-state.jsonl"
    )
    assert rss["StartInterval"] == 900


def test_config_sources_and_plist_rss_run_skips_x_account(tmp_path, monkeypatch):
    x_src = {"slug": "x-karpathy", "type": "x_account", "status": "trial"}
    rss_src = {"slug": "openai-blog", "status": "trial"}
    monkeypatch.delenv("AKB2_INTAKE_SOURCE_TYPE", raising=False)
    assert resolve_source_type(None) == RSS_SOURCE_TYPE
    assert not source_matches_run_type(x_src, RSS_SOURCE_TYPE)
    assert source_matches_run_type(rss_src, RSS_SOURCE_TYPE)
    assert source_matches_run_type(x_src, X_ACCOUNT_SOURCE_TYPE)
    assert not source_matches_run_type(rss_src, X_ACCOUNT_SOURCE_TYPE)

    sources_yaml = tmp_path / "sources.yaml"
    sources_yaml.write_text(
        "sources:\n"
        "  - slug: openai-blog\n"
        "    domain: openai.com\n"
        "    feed_url: https://openai.com/blog/rss.xml\n"
        "    listing_url: https://openai.com/blog\n"
        "    rate_limit: 60\n"
        "    status: trial\n"
        "    category: core\n"
        "  - slug: x-karpathy\n"
        "    type: x_account\n"
        "    handle: karpathy\n"
        "    domain: x.com\n"
        "    listing_url: https://x.com/karpathy\n"
        "    rate_limit: 60\n"
        "    status: trial\n"
        "    category: authority\n"
        "    lane: concept\n",
        encoding="utf-8",
    )
    seen: list[str] = []

    def _discover(source, ledger_path, dry_run=False):  # noqa: ARG001
        seen.append(source["slug"])
        from src.akb2.quality.intake.discovery import DiscoverResult

        return DiscoverResult(source_slug=source["slug"], method="none")

    with patch(
        "src.akb2.quality.intake.orchestrator.discover_articles",
        side_effect=_discover,
    ):
        run_intake(
            sources_config=sources_yaml,
            state_file=tmp_path / "state.jsonl",
            output_dir=tmp_path / "raw",
            dry_run=True,
            limit=30,
            source_offset=0,
            source_type=RSS_SOURCE_TYPE,
        )
    assert seen == ["openai-blog"]
