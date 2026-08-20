"""x_account 发现层测试 — mock twitter CLI，不打真网、不改 main_sources.yaml。

覆盖 SC x2-collector / x4-content-paths:
    - mock user-posts 正常路径产出 DiscoveredArticle
    - 探针 fatal 即断（第 1 账号）
    - 0 条账号跳过
    - 认证失败 fail-closed 不降级 fixture
    - 外链路径：url = 首个非 x.com URL
    - article 路径：twitter article -m
    - 短推跳过计数 fail-visible
"""

from __future__ import annotations

import plistlib

import os
import subprocess
import sys
from akb2.briefing.intake import l0_collector as l0
from akb2.shared import twitter_cli_constants as shared

import json
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from src.akb2.briefing.intake.l0_collector import (
    TWITTER_BIN,
    UpstreamCollectorBroken,
)
from src.akb2.quality.intake.discovery import (
    DiscoveredArticle,
    discover_articles,
)
from src.akb2.quality.intake.extract import ExtractMethod, extract_content
from src.akb2.quality.intake.models import ExtractionContext
from src.akb2.quality.intake.orchestrator import run_intake
from src.akb2.quality.intake.raw_archiver import REQUIRED_ANCHORS, archive_from_extract_result
from src.akb2.quality.intake.x_account_discovery import (
    DEFAULT_PACING_SEC,
    PREFLIGHT_PROBE_ACCOUNTS,
    ContentPath,
    XAccountAuthError,
    classify_tweet_content_path,
    discover_x_account_articles,
    first_external_url,
    resolve_x_handles,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_FATAL_STDERR = (
    "WARNING twitter_cli.client: Failed to init ClientTransaction: "
    "'NoneType' object has no attribute 'group'"
)

_LONG_ARTICLE_MD = "\n\n".join(
    [
        "Opening claim about loop engineering in production agents.",
        "Evidence from a 12-day fixture incident and the fail-closed gate.",
        "How to wire twitter user-posts into intake without search.",
        "Named constants: pacing 3s, preflight 1 account, cap 10.",
        "Closing: short tweets must not enter raw with missing anchors.",
    ]
    * 12
)


def _tweet(
    tid: str,
    *,
    handle: str = "karpathy",
    text: str = "hello ai",
    urls: list[str] | None = None,
    article_title: str | None = None,
    article_text: str | None = None,
    is_rt: bool = False,
    iso: str = "2026-08-20T08:00:00+00:00",
) -> dict:
    payload: dict = {
        "id": tid,
        "text": text,
        "author": {"screenName": handle, "name": handle},
        "createdAtISO": iso,
        "isRetweet": is_rt,
        "urls": list(urls or []),
        "metrics": {"likes": 1},
    }
    if article_title is not None:
        payload["articleTitle"] = article_title
    if article_text is not None:
        payload["articleText"] = article_text
    return payload


def _ok_payload(tweets: list[dict]) -> str:
    return json.dumps({"ok": True, "data": tweets})


def _write_curator(tmp_path: Path, en: list[str], zh: list[str] | None = None) -> Path:
    path = tmp_path / "curator-accounts.yaml"
    path.write_text(
        yaml.safe_dump({"fetch_tier": {"en": en, "zh": zh or []}}, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _x_source(
    slug: str = "x-karpathy",
    handles: list[str] | None = None,
    fetch_tier: str | None = None,
) -> dict:
    src: dict = {
        "slug": slug,
        "type": "x_account",
        "domain": "x.com",
        "status": "trial",
        "category": "core",
    }
    if handles is not None:
        src["x_handles"] = handles
    if fetch_tier is not None:
        src["fetch_tier"] = fetch_tier
    return src


# ---------------------------------------------------------------------------
# resolve handles
# ---------------------------------------------------------------------------


def test_resolve_x_handles_from_source_list():
    handles = resolve_x_handles(_x_source(handles=["karpathy", "@simonw", "karpathy"]))
    assert handles == ["karpathy", "simonw"]


def test_resolve_x_handles_from_curator_fetch_tier(tmp_path: Path):
    curator = _write_curator(tmp_path, en=["OpenAI", "karpathy"], zh=["dotey"])
    src = _x_source(handles=None, fetch_tier="en")
    assert resolve_x_handles(src, curator_path=curator) == ["OpenAI", "karpathy"]
    src_all = _x_source(handles=None, fetch_tier="all")
    assert resolve_x_handles(src_all, curator_path=curator) == ["OpenAI", "karpathy", "dotey"]


# ---------------------------------------------------------------------------
# content_path classification
# ---------------------------------------------------------------------------


def test_content_path_external_url_preferred_over_article():
    tweet = _tweet(
        "1",
        urls=["https://x.com/i/article/1", "https://example.com/blog/loop"],
        article_title="Long form",
    )
    assert first_external_url(tweet) == "https://example.com/blog/loop"
    assert classify_tweet_content_path(tweet) is ContentPath.EXTERNAL


def test_content_path_article_when_no_external():
    tweet = _tweet("2", urls=["https://x.com/i/article/99"], article_title="Notes")
    assert classify_tweet_content_path(tweet) is ContentPath.ARTICLE


def test_content_path_skip_short_plain_tweet():
    tweet = _tweet("3", text="just a thought", urls=["https://x.com/karpathy/status/3"])
    assert classify_tweet_content_path(tweet) is ContentPath.SKIP_SHORT


# ---------------------------------------------------------------------------
# x2-collector: mock user-posts happy path
# ---------------------------------------------------------------------------


def test_user_posts_happy_path_discovered_article_contract(tmp_path: Path):
    """mock user-posts 正常路径：产出与 DiscoveredArticle 字段 exact 兼容。"""
    ledger = tmp_path / "ledger.jsonl"
    src = _x_source(handles=["karpathy"])
    external = "https://example.com/loop-engineering"
    raw = [_tweet("111", urls=[external], text="Loop engineering notes")]

    def _fetch(handle: str):
        assert handle == "karpathy"
        return raw, None, ""

    result = discover_x_account_articles(
        src,
        ledger,
        dry_run=False,
        pacing_sec=0.0,
        fetch_one=_fetch,
        fetch_article=lambda _tid: "",
    )
    assert result.method == "x_account"
    assert result.error is None
    assert len(result.articles) == 1
    article = result.articles[0]
    assert isinstance(article, DiscoveredArticle)
    assert article.url == external
    assert article.title
    assert article.content_hash
    assert article.source_slug == "x-karpathy"
    assert article.published_at == "2026-08-20T08:00:00+00:00"
    assert "karpathy" in article.author.lower() or article.author
    assert set(article.__dataclass_fields__) >= {
        "url",
        "title",
        "content_hash",
        "source_slug",
        "published_at",
        "author",
    }


def test_discover_articles_dispatches_x_account_type(tmp_path: Path):
    """discover_articles 对 type=x_account 走 X collector，不走 RSS。"""
    ledger = tmp_path / "ledger.jsonl"
    src = _x_source(handles=["simonw"])
    raw = [_tweet("9", handle="simonw", urls=["https://simonwillison.net/2026/aug/post"])]

    with patch(
        "src.akb2.quality.intake.x_account_discovery._fetch_user_posts",
        return_value=(raw, None, ""),
    ), patch(
        "src.akb2.quality.intake.x_account_discovery._ensure_twitter_available",
        lambda: None,
    ):
        result = discover_articles(src, ledger)
    assert result.method == "x_account"
    assert result.articles[0].url == "https://simonwillison.net/2026/aug/post"


def test_user_posts_command_shape_not_search(tmp_path: Path, monkeypatch):
    """生产命令必须是 user-posts，禁止 twitter search 当采集。"""
    ledger = tmp_path / "ledger.jsonl"
    src = _x_source(handles=["karpathy"])
    cmds: list[list[str]] = []

    def _run(cmd, **kwargs):
        cmds.append(list(cmd))
        return SimpleNamespace(
            returncode=0,
            stdout=_ok_payload(
                [_tweet("1", urls=["https://example.com/a"])]
            ),
            stderr="",
        )

    monkeypatch.setattr(
        "src.akb2.quality.intake.x_account_discovery._ensure_twitter_available",
        lambda: None,
    )
    monkeypatch.setattr(
        "src.akb2.quality.intake.x_account_discovery._run_twitter",
        _run,
    )
    discover_x_account_articles(src, ledger, pacing_sec=0.0)
    assert cmds, "应至少调用一次 twitter CLI"
    cmd = cmds[0]
    assert cmd[1] == "user-posts"
    assert cmd[2] == "karpathy"
    assert "--json" in cmd
    assert "-n" in cmd
    assert "search" not in cmd
    assert "--from" not in cmd


def test_pacing_default_at_least_3s():
    assert DEFAULT_PACING_SEC >= 3.0
    assert PREFLIGHT_PROBE_ACCOUNTS == 1


def test_pacing_sleeps_between_accounts(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"
    src = _x_source(handles=["a", "b"])
    slept: list[float] = []

    def _fetch(handle: str):
        return [_tweet(handle, handle=handle, urls=["https://example.com/" + handle])], None, ""

    discover_x_account_articles(
        src,
        ledger,
        pacing_sec=DEFAULT_PACING_SEC,
        fetch_one=_fetch,
        sleep=slept.append,
    )
    assert slept == [DEFAULT_PACING_SEC]


# ---------------------------------------------------------------------------
# x2-collector: preflight fatal 即断
# ---------------------------------------------------------------------------


def test_preflight_fatal_aborts_after_first_account(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"
    src = _x_source(handles=["acct00", "acct01", "acct02"])
    called: list[str] = []

    def _fetch(handle: str):
        called.append(handle)
        return [], "Failed to init ClientTransaction", _FATAL_STDERR

    with pytest.raises(UpstreamCollectorBroken) as exc:
        discover_x_account_articles(
            src,
            ledger,
            pacing_sec=0.0,
            fetch_one=_fetch,
        )
    assert called == ["acct00"]
    assert "Failed to init ClientTransaction" in str(exc.value)


# ---------------------------------------------------------------------------
# x2-collector: 0 条账号跳过
# ---------------------------------------------------------------------------


def test_empty_account_skipped_does_not_abort(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"
    src = _x_source(handles=["empty", "alive"])

    def _fetch(handle: str):
        if handle == "empty":
            return [], None, ""
        return [_tweet("7", handle="alive", urls=["https://example.com/ok"])], None, ""

    result = discover_x_account_articles(
        src, ledger, pacing_sec=0.0, fetch_one=_fetch
    )
    assert result.skipped_empty_accounts == 1
    assert len(result.articles) == 1
    assert result.articles[0].url == "https://example.com/ok"


# ---------------------------------------------------------------------------
# x2-collector: 认证失败 fail-closed
# ---------------------------------------------------------------------------


def test_auth_failure_fail_closed_no_fixture(tmp_path: Path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    src = _x_source(handles=["karpathy"])

    def _run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ERROR: not_authenticated — please twitter login",
        )

    monkeypatch.setattr(
        "src.akb2.quality.intake.x_account_discovery._ensure_twitter_available",
        lambda: None,
    )
    monkeypatch.setattr(
        "src.akb2.quality.intake.x_account_discovery._run_twitter",
        _run,
    )
    with pytest.raises(XAccountAuthError, match="未认证"):
        discover_x_account_articles(src, ledger, pacing_sec=0.0)


def test_missing_twitter_bin_fail_closed(tmp_path: Path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    src = _x_source(handles=["karpathy"])
    monkeypatch.setattr(
        "src.akb2.quality.intake.x_account_discovery._twitter_bin",
        lambda: str(tmp_path / "no-such-twitter"),
    )
    with pytest.raises(XAccountAuthError, match="不存在"):
        discover_x_account_articles(src, ledger, pacing_sec=0.0)


def test_l0_constants_reexport_used_not_new_module():
    """常量从 l0_collector re-export 读，不新建 twitter_cli_constants.py。"""
    from src.akb2.quality.intake import x_account_discovery as xd

    assert xd.TWITTER_BIN is TWITTER_BIN
    assert not Path("src/akb2/shared/twitter_cli_constants.py").exists()


# ---------------------------------------------------------------------------
# x4-content-paths
# ---------------------------------------------------------------------------


def test_content_path_external_uses_first_non_x_url(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"
    src = _x_source(handles=["swyx"])
    raw = [
        _tweet(
            "21",
            handle="swyx",
            urls=[
                "https://t.co/abc",
                "https://x.com/swyx/status/21",
                "https://latent.space/post",
            ],
            text="new post",
        )
    ]
    result = discover_x_account_articles(
        src, ledger, pacing_sec=0.0, fetch_one=lambda _h: (raw, None, "")
    )
    assert result.articles[0].url == "https://latent.space/post"
    assert result.skipped_short_tweets == 0


def test_content_path_article_calls_twitter_article(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"
    src = _x_source(handles=["karpathy"])
    raw = [
        _tweet(
            "42",
            article_title="On agents",
            article_text="short stub",
            urls=["https://x.com/i/article/42"],
        )
    ]
    article_ids: list[str] = []

    def _article(tid: str) -> str:
        article_ids.append(tid)
        return _LONG_ARTICLE_MD

    result = discover_x_account_articles(
        src,
        ledger,
        pacing_sec=0.0,
        fetch_one=lambda _h: (raw, None, ""),
        fetch_article=_article,
    )
    assert article_ids == ["42"]
    assert len(result.articles) == 1
    article = result.articles[0]
    assert article.url.startswith("https://x.com/karpathy/status/42")
    assert article.prefetched_via == "twitter_article"
    assert "fail-closed" in article.prefetched_content
    assert article.title == "On agents"


def test_content_path_skip_short_counted_fail_visible(tmp_path: Path, caplog):
    ledger = tmp_path / "ledger.jsonl"
    src = _x_source(handles=["nickfloats"])
    raw = [
        _tweet("s1", handle="nickfloats", text="gm"),
        _tweet("s2", handle="nickfloats", text="nice shot", urls=["https://x.com/n/status/s2"]),
        _tweet("ok", handle="nickfloats", urls=["https://example.com/design-post"]),
    ]
    with caplog.at_level("WARNING"):
        result = discover_x_account_articles(
            src, ledger, pacing_sec=0.0, fetch_one=lambda _h: (raw, None, "")
        )
    assert result.skipped_short_tweets == 2
    assert len(result.articles) == 1
    assert "skipped_short_tweets=2" in caplog.text
    assert "short tweets not archived" in caplog.text


def test_skip_short_does_not_archive_raw(tmp_path: Path):
    """纯短推不进 raw：宁可显式跳过也不产出锚点不足的碎片。"""
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        textwrap.dedent(
            """\
            sources:
              - slug: x-short
                type: x_account
                domain: x.com
                status: trial
                x_handles: [nickfloats]
            """
        ),
        encoding="utf-8",
    )
    state = tmp_path / "state.jsonl"
    out = tmp_path / "raw"
    raw = [_tweet("s1", handle="nickfloats", text="gm")]

    with patch(
        "src.akb2.quality.intake.x_account_discovery._fetch_user_posts",
        return_value=(raw, None, ""),
    ), patch(
        "src.akb2.quality.intake.x_account_discovery._ensure_twitter_available",
        lambda: None,
    ):
        stats = run_intake(
            sources_config=sources,
            state_file=state,
            output_dir=out,
            dry_run=False,
            source_offset=0,
        )
    assert stats["discovered"] == 0
    assert not out.exists() or not any(out.iterdir())


def test_article_path_archives_with_required_anchors(tmp_path: Path):
    """X Article 预取正文走 extract → archive，五锚点 fail-closed 不被绕过。"""
    ctx = ExtractionContext(
        source_slug="x-karpathy",
        source_publisher="x.com",
        feed_title="On agents",
        feed_author="karpathy",
        feed_published="2026-08-20",
        url="https://x.com/karpathy/status/42",
        prefetched_content=_LONG_ARTICLE_MD,
        prefetched_via="twitter_article",
    )
    extracted = extract_content(url=ctx.url, extraction_config=ctx, html=None)
    assert extracted.extraction_success
    assert extracted.method is ExtractMethod.TWITTER_ARTICLE
    out = tmp_path / "raw"
    archived = archive_from_extract_result(
        extract_result=extracted,
        output_dir=out,
        source_metadata=ctx,
        date_str="2026-08-20",
    )
    assert archived.success, archived.error
    text = Path(archived.output_path).read_text(encoding="utf-8")
    for key in REQUIRED_ANCHORS:
        assert f"§{key}_" in text


def test_short_content_does_not_bypass_anchor_gate(tmp_path: Path):
    """短正文不得硬塞锚点绕过 raw_archiver 五锚点门。"""
    from src.akb2.quality.intake.extract import ExtractResult

    short = ExtractResult(
        url="https://x.com/n/status/1",
        method=ExtractMethod.TWITTER_ARTICLE,
        title="gm",
        author="n",
        published="2026-08-20",
        content="gm",
        word_count=1,
        metadata_complete=True,
        extraction_success=True,
    )
    ctx = ExtractionContext(
        source_slug="x-short",
        source_publisher="x.com",
        feed_title="gm",
        feed_author="n",
        feed_published="2026-08-20",
        url=short.url,
    )
    archived = archive_from_extract_result(
        extract_result=short,
        output_dir=tmp_path / "raw",
        source_metadata=ctx,
    )
    assert not archived.success
    assert archived.missing_anchors
    assert set(REQUIRED_ANCHORS) & set(archived.missing_anchors)
    assert not (tmp_path / "raw").exists() or not any((tmp_path / "raw").iterdir())

_REEXPORT_NAMES = (
    "UPSTREAM_FATAL_SIGNATURES",
    "UPSTREAM_FATAL_FAILFAST_STREAK",
    "UPSTREAM_HARD_FAILURE_FAILFAST_STREAK",
    "HARD_FAILURE_TIMEOUT",
    "HARD_FAILURE_BAD_OUTPUT",
    "_twitter_cli_to_akb2",
)


def test_shared_constants_reexport_identity() -> None:
    """akb2.shared 与 briefing l0_collector 的签名/阈值/适配器是同一对象。"""
    for name in _REEXPORT_NAMES:
        assert getattr(shared, name) is getattr(l0, name), name
    assert shared.twitter_cli_to_akb2 is l0._twitter_cli_to_akb2
    assert shared.twitter_cli_to_akb2 is shared._twitter_cli_to_akb2


def test_shared_constants_reexport_quality_import_skips_briefing() -> None:
    """quality 侧 import 共享常量不得把 akb2.briefing 拖进 sys.modules。"""
    repo = Path(__file__).resolve().parents[2]
    script = (
        "import sys\n"
        "from akb2.shared.twitter_cli_constants import UPSTREAM_FATAL_SIGNATURES\n"
        "assert UPSTREAM_FATAL_SIGNATURES\n"
        "loaded = [\n"
        "    n for n in sys.modules\n"
        "    if n == 'akb2.briefing' or n.startswith('akb2.briefing.')\n"
        "]\n"
        "assert not loaded, loaded\n"
    )
    env = os.environ.copy()
    src = str(repo / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo),
    )
    assert result.returncode == 0, result.stdout + result.stderr

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
