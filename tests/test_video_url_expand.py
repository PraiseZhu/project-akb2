"""残余风险 2（第一条缝）回归：`b23.tv/<code>` 短链在采集阶段展开后再算身份。

不展开的后果是可观测的：`video_identity("https://b23.tv/AbCdEf")` 落 sha256 兜底，
与展开后的 `bilibili:BV1xx411c7mD` 是**两个身份** → 账本里已有该视频也查不到 →
同一视频再烧一次 NLM 深研 + 两次 LLM。

断言绑定生产调用路径：`collector.collect()`（L0.5 → L1）与
`__main__._run_single_source()`（人手粘贴短链的唯一入口）。
HTTP 解析层用本机 127.0.0.1 上的真 302 服务器验，不只验注入的假 resolver。
"""

from __future__ import annotations

import http.server
import json
import threading
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.video import url_expand as url_expand_mod
from src.akb2.video.collector import VideoCollector
from src.akb2.video.config import VideoConfig
from src.akb2.video.dedup_store import VideoDedupStore, video_identity
from src.akb2.video.url_expand import (
    ShortLinkExpander,
    _resolve_via_http,
    is_short_link,
)
from tests.video_state_isolation import isolate_video_state  # noqa: F401

_SHORT_URL = "https://b23.tv/AbCdEf"
_FULL_URL = "https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333.999"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_sources(path: Path, settings: dict | None = None) -> None:
    base_settings = {
        "min_video_duration_minutes": 3,
        "max_video_duration_minutes": 0,
        "days_lookback": 14,
        "dedup_strategy": "none",
        "skip_relevance_filter": True,
        "skip_keyword_filter": True,
        "coldstart": True,
        "coldstart_max_per_day": 5,
    }
    base_settings.update(settings or {})
    path.write_text(json.dumps({
        "creators": [{
            "id": "bili-test", "platform": "bilibili",
            "channel_url": "https://space.bilibili.com/1",
            "name": "Test UP", "enabled": True,
            "max_per_day": 5, "keywords": [], "tier": "quality",
        }],
        "settings": base_settings,
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def video_config(tmp_path) -> VideoConfig:
    sources = tmp_path / "video_sources.json"
    _write_sources(sources)
    keywords = tmp_path / "keywords.json"
    keywords.write_text(json.dumps({
        "include": {"core": ["AI"]}, "exclude": {"non_ai": []},
    }), encoding="utf-8")
    config = VideoConfig(
        video_sources_path=sources,
        keywords_path=keywords,
        staging_dir=tmp_path / "staging" / "candidate",
        akb2_root=tmp_path / "vault",
        dedup_db_path=tmp_path / "state" / "video-dedup.sqlite",
    )
    config.promotion_target_path.mkdir(parents=True, exist_ok=True)
    return config


def _fake_resolver(mapping: dict[str, str], calls: list[str] | None = None):
    def resolve(url: str, timeout: float) -> str | None:
        if calls is not None:
            calls.append(url)
        return mapping.get(url)
    return resolve


# ---------------------------------------------------------------------------
# 身份缝：展开前后必须是同一个身份
# ---------------------------------------------------------------------------


def test_short_link_and_full_url_are_two_identities_without_expansion():
    """前提复现：不展开时短链与完整 URL 确实是两个身份（这才是要修的缝）。"""
    assert video_identity(_SHORT_URL).startswith("sha256:")
    assert video_identity(_FULL_URL) == "bilibili:BV1xx411c7mD"
    assert video_identity(_SHORT_URL) != video_identity(_FULL_URL)


def test_expand_maps_short_link_to_platform_identity():
    """展开后身份收敛到平台原生 id（缺陷版返回原样 → 仍是 sha256 兜底）。"""
    expander = ShortLinkExpander(resolver=_fake_resolver({_SHORT_URL: _FULL_URL}))

    expanded = expander.expand(_SHORT_URL)

    assert video_identity(expanded) == "bilibili:BV1xx411c7mD"
    assert expander.expanded_ok == 1
    assert expander.failures == 0


def test_expand_does_not_touch_non_short_links():
    """非短链一律零网络开销（`video_identity` 已能解析的形态不该被展开）。"""
    def _boom(url, timeout):
        raise AssertionError(f"非短链不该发请求: {url}")

    expander = ShortLinkExpander(resolver=_boom)
    for url in (
        "https://www.youtube.com/watch?v=abc12345678",
        "https://youtu.be/abc12345678",
        "https://www.bilibili.com/video/BV1xx411c7mD",
    ):
        assert expander.expand(url) == url
    assert expander.expansions_used == 0


def test_expand_caches_per_url():
    """同一短链只解析一次（有界：同轮里多个 creator 撞同一链接不重复发请求）。"""
    calls: list[str] = []
    expander = ShortLinkExpander(
        resolver=_fake_resolver({_SHORT_URL: _FULL_URL}, calls)
    )

    first = expander.expand(_SHORT_URL)
    second = expander.expand(_SHORT_URL)

    assert first == second == _FULL_URL
    assert calls == [_SHORT_URL], f"缓存失效，重复请求: {calls}"
    assert expander.expansions_used == 1


def test_expand_budget_is_bounded_and_visible(caplog):
    """单轮展开预算有界，耗尽后不再发请求且必须可见（宪法 P1 + P4）。"""
    calls: list[str] = []
    mapping = {
        f"https://b23.tv/code{i}": f"https://www.bilibili.com/video/BV1xx41100{i}"
        for i in range(4)
    }
    expander = ShortLinkExpander(
        max_per_run=2, resolver=_fake_resolver(mapping, calls)
    )

    with caplog.at_level("WARNING"):
        results = [expander.expand(u) for u in mapping]

    assert calls == ["https://b23.tv/code0", "https://b23.tv/code1"], (
        f"预算 2 未生效，实际请求 {len(calls)} 次"
    )
    assert results[2] == "https://b23.tv/code2", "预算耗尽时必须原样返回"
    assert expander.budget_exhausted == 2
    assert any("预算耗尽" in r.message for r in caplog.records)


def test_expand_follows_chain_within_hop_budget():
    """多跳短链在跳数预算内跟到底。"""
    mid = "https://b23.tv/second"
    expander = ShortLinkExpander(
        max_hops=3, resolver=_fake_resolver({_SHORT_URL: mid, mid: _FULL_URL})
    )

    assert expander.expand(_SHORT_URL) == _FULL_URL


def test_expand_hop_budget_exhausted_is_visible(caplog):
    """跳数用尽仍是短链 → 计入 failures 并告警（不静默当成功）。"""
    chain = {
        "https://b23.tv/h0": "https://b23.tv/h1",
        "https://b23.tv/h1": "https://b23.tv/h2",
        "https://b23.tv/h2": _FULL_URL,
    }
    expander = ShortLinkExpander(max_hops=2, resolver=_fake_resolver(chain))

    with caplog.at_level("WARNING"):
        result = expander.expand("https://b23.tv/h0")

    assert is_short_link(result), "跳数用尽应停在短链上"
    assert expander.failures == 1
    assert expander.expanded_ok == 0
    assert any("跳数上限" in r.message for r in caplog.records)


def test_expand_failure_returns_original_and_is_visible(caplog):
    """解析失败 → 返回原始 URL + WARNING + failures 计数（不静默回退）。"""
    def _fail(url, timeout):
        raise OSError("connection reset")

    expander = ShortLinkExpander(resolver=_fail)

    with caplog.at_level("WARNING"):
        result = expander.expand(_SHORT_URL)

    assert result == _SHORT_URL
    assert expander.failures == 1
    assert any("未能展开" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# HTTP 解析层（真 302，不是假 resolver）
# ---------------------------------------------------------------------------


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    location = _FULL_URL

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
        self.send_response(302)
        self.send_header("Location", self.location)
        self.end_headers()

    def log_message(self, *args):  # 静音测试输出
        return


@pytest.fixture
def redirect_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/short"
    server.shutdown()
    server.server_close()


def test_resolve_via_http_reads_location_from_302(redirect_server):
    """生产解析器对真 302 必须取到 Location（不下载正文、不跟随）。"""
    assert _resolve_via_http(redirect_server, timeout=5.0) == _FULL_URL


def test_resolve_via_http_rejects_non_http_location(redirect_server, monkeypatch):
    """Location 指向非 http(s)（file: 等）一律不采信。"""
    monkeypatch.setattr(_RedirectHandler, "location", "file:///etc/passwd")

    assert _resolve_via_http(redirect_server, timeout=5.0) is None


def test_resolve_via_http_returns_none_on_unreachable_host():
    """不可达主机 → None（失败可见由调用方计数，不抛穿）。"""
    assert _resolve_via_http("http://127.0.0.1:1/short", timeout=1.0) is None


# ---------------------------------------------------------------------------
# 生产路径接线
# ---------------------------------------------------------------------------


def test_collect_expands_short_link_before_l1_dedup(video_config):
    """生产路径 `collect()`：账本里存的是展开后的完整 URL，短链候选必须被 L1 拦下。

    缺陷版（L0.5 不展开）下短链身份是 sha256 兜底 → 账本查不到 → 候选放行 →
    已研究过的视频再烧一次 NLM 深研。
    """
    store = VideoDedupStore(video_config.dedup_db_path)
    store.mark_ingested(_FULL_URL, platform="bilibili", title="已研究过")
    collector = VideoCollector(
        video_config,
        db=store,
        shortlink_expander=ShortLinkExpander(
            resolver=_fake_resolver({_SHORT_URL: _FULL_URL})
        ),
    )
    candidate = {
        "platform": "bilibili", "id": "BV1xx411c7mD", "title": "AI 视频",
        "url": _SHORT_URL, "duration_minutes": 20,
        "upload_date": datetime.now().strftime("%Y%m%d"), "creator": "Test UP",
    }

    with mock.patch.object(
        collector, "_detect_bili_videos", return_value=[candidate]
    ):
        results = collector.collect()

    assert results == [], "短链形态的已入库视频被放行 → 会重复烧 NLM/LLM 额度"


def test_collect_rewrites_candidate_url_to_expanded_form(video_config):
    """展开结果必须写回候选（下游 frontmatter/账本登记用的都是完整 URL）。"""
    store = VideoDedupStore(video_config.dedup_db_path)
    collector = VideoCollector(
        video_config,
        db=store,
        shortlink_expander=ShortLinkExpander(
            resolver=_fake_resolver({_SHORT_URL: _FULL_URL})
        ),
    )
    candidate = {
        "platform": "bilibili", "id": "BV1xx411c7mD", "title": "AI 视频",
        "url": _SHORT_URL, "duration_minutes": 20,
        "upload_date": datetime.now().strftime("%Y%m%d"), "creator": "Test UP",
    }

    with mock.patch.object(
        collector, "_detect_bili_videos", return_value=[candidate]
    ):
        results = collector.collect()

    assert [r["video"]["url"] for r in results] == [_FULL_URL]


def test_run_single_source_expands_before_ledger_check(video_config, monkeypatch):
    """生产路径 `--source`：短链必须在查账本之前展开，否则白烧一次深研。

    断言方式绑定"钱有没有被花"：把 `VideoResearcher` 换成一调用即炸的替身 ——
    缺陷版（不展开）查不到账本 → 构造 researcher → 炸 → 返回 1；
    修复版命中账本 → 直接返回 0，替身一次都不被碰。
    """
    from src.akb2.video import __main__ as video_main

    store = VideoDedupStore(video_config.dedup_db_path)
    store.mark_ingested(_FULL_URL, platform="bilibili", title="已研究过")

    class _ExplodingResearcher:
        def __init__(self, *args, **kwargs):
            raise AssertionError("已入库视频不该进入研究阶段（会重烧 NLM 深研）")

    monkeypatch.setattr(video_main, "VideoResearcher", _ExplodingResearcher)
    monkeypatch.setattr(
        url_expand_mod, "_resolve_via_http",
        lambda url, timeout: _FULL_URL if url == _SHORT_URL else None,
    )

    assert video_main._run_single_source(video_config, _SHORT_URL, dry_run=False) == 0
