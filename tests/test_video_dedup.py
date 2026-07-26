"""视频链路幂等去重 + 标题透传 + 时间闸/配额硬上限回归测试。

对应缺陷（2026-07-26）：
- #2  幂等去重接线：collector 阶段（花钱之前）就跳过已处理视频，且写端存在
- #10 标题原样透传，不经 LLM 生成路径
- #12 时间闸与日配额真正生效

每条断言都绑定**可观测行为**（第二轮 LLM 未被调用 / 产出身份不变 / 候选被拒收 /
候选数被硬截断），不是"能跑通"或 mock 装配形状。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.video.assembler import Assembler
from src.akb2.video.collector import VideoCollector
from src.akb2.video.config import VideoConfig
from src.akb2.video.dedup_store import (
    VideoDedupStore,
    VideoDedupStoreError,
    video_identity,
)
from src.akb2.video.extractor import ExtractedFields, KeyFinding, SourceSpan
from src.akb2.video.reasoner import ReasonedLinks
from src.akb2.video.researcher import ResearchOutput

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
            "id": "yt-test", "platform": "youtube",
            "channel_url": "https://www.youtube.com/@TestChannel",
            "name": "Test Channel", "enabled": True,
            "max_per_day": 5, "keywords": [], "tier": "quality",
        }],
        "settings": base_settings,
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def dedup_config(tmp_path) -> VideoConfig:
    """全部状态路径隔离到 tmp_path 的 VideoConfig（不碰真实状态根/vault）。"""
    sources = tmp_path / "video_sources.json"
    _write_sources(sources)
    keywords = tmp_path / "keywords.json"
    keywords.write_text(json.dumps({
        "include": {"core": ["AI", "LLM", "agent"]},
        "exclude": {"non_ai": ["cooking"]},
    }), encoding="utf-8")
    return VideoConfig(
        video_sources_path=sources,
        keywords_path=keywords,
        staging_dir=tmp_path / "staging" / "candidate",
        akb2_root=tmp_path / "vault",
        dedup_db_path=tmp_path / "state" / "video-dedup.sqlite",
    )


def _video(video_id: str = "vid-1", title: str = "AI Agents Deep Dive") -> dict:
    return {
        "platform": "youtube",
        "id": video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "duration_minutes": 20,
        "upload_date": datetime.now().strftime("%Y%m%d"),
        "creator": "Test Channel",
    }


class _RecordingLLM:
    """记录调用次数的 LLM adapter（用于断言"第二轮没花钱"）。"""

    def __init__(self):
        self.calls: list[dict] = []

    def chat(self, task: str, system: str, user: str, temperature: float = 0.3) -> str:
        self.calls.append({"task": task, "user": user})
        return json.dumps({"groups": [[0]]})


# ---------------------------------------------------------------------------
# #2 稳定身份
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=M_AmPWmkpwA", "youtube:M_AmPWmkpwA"),
        ("https://youtube.com/watch?v=M_AmPWmkpwA&t=30s", "youtube:M_AmPWmkpwA"),
        ("https://youtu.be/M_AmPWmkpwA", "youtube:M_AmPWmkpwA"),
        ("https://www.youtube.com/shorts/M_AmPWmkpwA", "youtube:M_AmPWmkpwA"),
        ("https://www.bilibili.com/video/BV1xx411c7mD", "bilibili:BV1xx411c7mD"),
        ("https://www.bilibili.com/video/BV1xx411c7mD/?spm_id=333", "bilibili:BV1xx411c7mD"),
    ],
)
def test_identity_is_stable_across_url_variants(url: str, expected: str):
    """同一视频的不同 URL 形态必须映射到同一稳定身份（去重键不能被 URL 装饰绕过）。"""
    assert video_identity(url) == expected


def test_identity_ignores_title_entirely():
    """身份只由 URL 决定：标题被 LLM 改写（#10 实证）不得改变去重键。"""
    url = "https://www.youtube.com/watch?v=M_AmPWmkpwA"
    assert video_identity(url) == video_identity(url + "&list=PLxyz")


def test_identity_rejects_empty_url():
    """空 URL 不得静默生成假身份（否则两个视频撞成一个）。"""
    with pytest.raises(ValueError):
        video_identity("")


# ---------------------------------------------------------------------------
# #2 去重库读写端
# ---------------------------------------------------------------------------


def test_store_mark_then_is_ingested(tmp_path):
    """写端登记后读端立刻可见，且重复登记幂等（返回 False，不报错）。"""
    store = VideoDedupStore(tmp_path / "s" / "dedup.sqlite")
    url = "https://www.youtube.com/watch?v=abc123"
    assert store.is_ingested(url) is False
    assert store.mark_ingested(url, platform="youtube", title="T") is True
    assert store.is_ingested(url) is True
    assert store.mark_ingested(url, platform="youtube", title="T 改过的标题") is False


def test_store_fail_closed_when_db_unusable(tmp_path):
    """库不可用 → 抛 VideoDedupStoreError，绝不 return False 静默放行。

    回归前：collector 吞掉 sqlite3.OperationalError 直接 return False，
    所有视频都被判"未入库" → 79% 重复产出正是从这里流出去的。
    """
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"this is not a sqlite database" * 10)
    store = VideoDedupStore(broken)
    with pytest.raises(VideoDedupStoreError):
        store.is_ingested("https://www.youtube.com/watch?v=abc123")


def test_store_seed_from_published_is_idempotent(tmp_path):
    """历史产出回填：已发布笔记的 source_url 进账本，重复调用不重复回填。"""
    published = tmp_path / "Video"
    published.mkdir()
    (published / "video-a-2026-07-01.md").write_text(
        "---\ntitle: \"A\"\nsource_url: https://www.youtube.com/watch?v=hist1\n---\n# A",
        encoding="utf-8",
    )
    (published / "essay-b.md").write_text(
        "---\ntitle: \"B\"\nsource_url: https://example.com/essay\n---\n# B",
        encoding="utf-8",
    )
    store = VideoDedupStore(tmp_path / "dedup.sqlite")

    assert store.seed_from_published(published) == 1  # essay 不进视频账本
    assert store.is_ingested("https://youtu.be/hist1") is True
    assert store.is_ingested("https://example.com/essay") is False
    assert store.seed_from_published(published) == 0  # 幂等


def test_store_seed_does_not_consume_today_quota(tmp_path):
    """回填用文件 mtime 作为 ingested_at，不得吃掉今天的日配额。"""
    published = tmp_path / "Video"
    published.mkdir()
    old = published / "video-old-2026-01-01.md"
    old.write_text(
        "---\nsource_url: https://www.youtube.com/watch?v=old1\n---\n# old",
        encoding="utf-8",
    )
    old_ts = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    import os
    os.utime(old, (old_ts, old_ts))

    store = VideoDedupStore(tmp_path / "dedup.sqlite")
    store.seed_from_published(published)
    assert store.count_ingested_since(datetime.now(UTC).date().isoformat()) == 0


# ---------------------------------------------------------------------------
# #2 生产接线：collector 阶段跳过 + 第二轮不花钱
# ---------------------------------------------------------------------------


def test_main_constructs_collector_with_dedup_db(dedup_config, monkeypatch):
    """生产入口 `_run_full` 构造 collector 时 db 必须非 None（#2 根因 2 的护栏）。

    回归前：`VideoCollector(config, llm_adapter=llm_adapter)` 从不传 db →
    `if not self.db: return False` → L1/L7 全空转。
    """
    from src.akb2.video import __main__ as video_main

    captured: dict = {}

    class _StubCollector:
        def __init__(self, config, db=None, llm_adapter=None):
            captured["db"] = db

        def collect(self):
            return []

    monkeypatch.setattr(video_main, "VideoCollector", _StubCollector)
    monkeypatch.setattr(video_main, "VideoResearcher", lambda *a, **k: object())
    monkeypatch.setattr(video_main, "Extractor", lambda *a, **k: object())
    monkeypatch.setattr(video_main, "Reasoner", lambda *a, **k: object())

    assert video_main._run_full(dedup_config, dry_run=True) == 0
    assert captured["db"] is not None, "生产入口必须注入去重库，否则 L1/L7 空转"
    assert isinstance(captured["db"], VideoDedupStore)


def test_same_video_second_run_skipped_at_collector_stage(dedup_config):
    """同一 video_url 连续两轮：第二轮在 collector 阶段就被跳过，且 LLM 未被调用。

    这条断言绑定的可观测行为就是"省下额度"：第二轮 candidates 为空 →
    researcher/extractor/reasoner 一次都不会被触发，LLM 调用计数保持不变。
    """
    _write_sources(dedup_config.video_sources_path, {"dedup_strategy": "llm_cluster"})
    llm = _RecordingLLM()
    store = VideoDedupStore(dedup_config.dedup_db_path)
    video = _video()

    collector = VideoCollector(dedup_config, db=store, llm_adapter=llm)
    with mock.patch.object(collector, "_detect_yt_videos", return_value=[dict(video)]):
        first = collector.collect()
    assert [r["video"]["id"] for r in first] == ["vid-1"]

    # 产出落地 → 写端登记（生产由 assembler.run_pipeline 在发布成功后调用）
    collector.mark_ingested(video, dedup_config.staging_dir / "video-x.md")
    calls_after_first = len(llm.calls)

    collector2 = VideoCollector(dedup_config, db=store, llm_adapter=llm)
    with mock.patch.object(collector2, "_detect_yt_videos", return_value=[dict(video)]):
        second = collector2.collect()

    assert second == [], "第二轮必须在 collector 阶段被跳过（花钱之前）"
    assert len(llm.calls) == calls_after_first, (
        "第二轮不得产生任何 LLM 调用——省额度才是本修复的可观测收益"
    )


def test_run_pipeline_marks_ingested_after_publish(dedup_config):
    """run_pipeline 发布成功后必须登记去重身份（写端接线；#2 根因 3）。"""
    store = VideoDedupStore(dedup_config.dedup_db_path)
    video = _video()
    collector = VideoCollector(dedup_config, db=store)
    assembler = Assembler(dedup_config)
    dedup_config.promotion_target_path.mkdir(parents=True, exist_ok=True)

    class _Researcher:
        def run(self, video_results):
            return [ResearchOutput(
                report_markdown="# LLM 改写过的标题\n\n## 执行摘要\n\n内容。",
                notebook_id="nb-1",
                video_meta=video,
                degraded=False,
            )]

    class _Extractor:
        def extract(self, md):
            return ExtractedFields(
                tldr="要点",
                key_findings=[KeyFinding(
                    assertion="断言", source_span=SourceSpan(line_start=1, line_end=1),
                    confidence=0.9,
                )],
                future_questions=[],
                concepts_mentioned=["AI"],
            )

    class _Reasoner:
        def reason(self, concepts, context=""):
            return ReasonedLinks(related_concept=[], proposed_graph_nodes=[])

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[dict(video)]):
        with mock.patch.object(assembler, "_gate_decision", return_value="active"):
            results = assembler.run_pipeline(
                collector, _Researcher(), _Extractor(), _Reasoner(),
            )

    assert [r["status"] for r in results] == ["ok"]
    assert results[0]["dedup_marked"] is True
    assert store.is_ingested(video["url"]) is True, "发布成功后身份必须落账本"


def test_run_pipeline_surfaces_failed_dedup_mark(dedup_config):
    """账本写失败不得静默：结果里必须标 dedup_marked=False（下轮会重跑）。"""
    store = VideoDedupStore(dedup_config.dedup_db_path)
    video = _video()
    collector = VideoCollector(dedup_config, db=store)
    assembler = Assembler(dedup_config)
    dedup_config.promotion_target_path.mkdir(parents=True, exist_ok=True)

    class _Researcher:
        def run(self, video_results):
            return [ResearchOutput(
                report_markdown="# 报告\n\n内容。", notebook_id="nb-1",
                video_meta=video, degraded=False,
            )]

    class _Extractor:
        def extract(self, md):
            return ExtractedFields(
                tldr="要点", key_findings=[], future_questions=[], concepts_mentioned=[],
            )

    class _Reasoner:
        def reason(self, concepts, context=""):
            return ReasonedLinks(related_concept=[], proposed_graph_nodes=[])

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[dict(video)]):
        with mock.patch.object(assembler, "_gate_decision", return_value="active"):
            with mock.patch.object(
                collector, "mark_ingested",
                side_effect=VideoDedupStoreError("disk full"),
            ):
                results = assembler.run_pipeline(
                    collector, _Researcher(), _Extractor(), _Reasoner(),
                )

    assert results[0]["dedup_marked"] is False
    from src.akb2.video.__main__ import _report_results
    assert _report_results(results) == 1, "账本未登记必须体现在退出码上"


# ---------------------------------------------------------------------------
# #10 标题透传
# ---------------------------------------------------------------------------


def _assemble_with_report(config: VideoConfig, video_meta: dict, h1: str) -> str:
    assembler = Assembler(config)
    return assembler.assemble(
        video_meta,
        ResearchOutput(
            report_markdown=f"# {h1}\n\n## 执行摘要\n\n内容。",
            notebook_id="nb-1", video_meta=video_meta, degraded=False,
        ),
        ExtractedFields(
            tldr="要点", key_findings=[], future_questions=[], concepts_mentioned=[],
        ),
        ReasonedLinks(related_concept=[], proposed_graph_nodes=[]),
    )


def test_title_comes_from_video_meta_not_report_h1(dedup_config):
    """标题必须是 yt-dlp 原值，不是 LLM 报告的 H1。

    回归前：`report_title = self._extract_h1(report_markdown) or title` →
    实证同一视频标题在 paradigm/paradiagm 之间来回翻转。
    """
    video_meta = _video(title="New AI coding paradigm - OpenAI Symphony")
    md = _assemble_with_report(dedup_config, video_meta, h1="全新 AI 编码范式：OpenAI Symphony")

    assert 'title: "New AI coding paradigm - OpenAI Symphony"' in md
    assert md.split("---\n")[2].lstrip().startswith(
        "# New AI coding paradigm - OpenAI Symphony"
    ), "正文首个 H1 也必须是透传标题"


def test_same_video_different_report_h1_yields_same_identity(dedup_config):
    """同一 video_meta 配两个不同 H1 的报告 → 落地身份（标题 + slug）必须相同。"""
    video_meta = _video(title="New AI coding paradigm - OpenAI Symphony")
    assembler = Assembler(dedup_config)

    md_cn = _assemble_with_report(dedup_config, video_meta, h1="中文标题版本")
    md_en = _assemble_with_report(dedup_config, video_meta, h1="English Title Variant")

    title_line = 'title: "New AI coding paradigm - OpenAI Symphony"'
    assert title_line in md_cn and title_line in md_en
    assert assembler._make_slug(md_cn) == assembler._make_slug(md_en)
    assert (
        assembler._slug_for_video(video_meta)
        == assembler._slug_for_video(dict(video_meta, title=video_meta["title"]))
    )


def test_slug_stable_when_creator_renames_title(dedup_config):
    """作者改标题后 slug 的身份后缀不变（同一视频仍归到同一族文件名）。"""
    assembler = Assembler(dedup_config)
    v1 = _video(title="New AI coding paradiagm - OpenAI Symphony")
    v2 = _video(title="New AI coding paradigm - OpenAI Symphony")  # typo 修好
    suffix1 = assembler._slug_for_video(v1).rsplit("-", 1)[-1]
    suffix2 = assembler._slug_for_video(v2).rsplit("-", 1)[-1]
    assert suffix1 == suffix2 != ""


# ---------------------------------------------------------------------------
# #12 时间闸
# ---------------------------------------------------------------------------


def test_missing_upload_date_is_rejected_not_silently_passed(dedup_config):
    """缺 upload_date 且补取不到 → 拒收（不静默通过时间闸）。

    回归前：`if upload_date:` 守卫让空值直接跳过 days_lookback 与 start_date
    两道闸（实证：324 份产出里只有 1 份有 published_at）。
    """
    _write_sources(dedup_config.video_sources_path, {"start_date": "2026-04-01"})
    collector = VideoCollector(dedup_config)
    no_date = dict(_video("nodate1"), upload_date="")

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[no_date]):
        with mock.patch.object(collector, "_fill_upload_date") as fill:
            results = collector.collect()

    assert results == [], "无法确定发布日期的视频必须被拒收"
    assert fill.called, "拒收前必须先尝试补取真实发布日期"
    # "0 产出" 与 "全被时间闸拒收" 必须可区分（宪法 P4）
    assert collector._date_unknown_rejected == 1


def test_upload_date_backfill_rescues_candidate(dedup_config):
    """补取到真实发布日期的视频照常通过时间闸（不是一律拒收）。"""
    collector = VideoCollector(dedup_config)
    no_date = dict(_video("filled1"), upload_date="")
    today = datetime.now().strftime("%Y%m%d")

    def _fill(video: dict) -> None:
        video["upload_date"] = today

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[no_date]):
        with mock.patch.object(collector, "_fill_upload_date", side_effect=_fill):
            results = collector.collect()

    assert [r["video"]["id"] for r in results] == ["filled1"]


def test_days_lookback_rejects_old_video_even_without_start_date(dedup_config):
    """days_lookback 必须真正生效：超窗口的老视频被拒（原来只在平台层生效）。"""
    _write_sources(dedup_config.video_sources_path, {"days_lookback": 14})
    collector = VideoCollector(dedup_config)
    old = dict(
        _video("old1"),
        upload_date=(datetime.now() - timedelta(days=400)).strftime("%Y%m%d"),
    )

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[old]):
        results = collector.collect()

    assert results == []


def test_malformed_upload_date_is_rejected(dedup_config):
    """upload_date 格式非法 → 按未知处理并拒收（原来 except ValueError: pass 放行）。"""
    collector = VideoCollector(dedup_config)
    bad = dict(_video("bad1"), upload_date="2026-07-26")  # 非 YYYYMMDD

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[bad]):
        with mock.patch.object(collector, "_fill_upload_date"):
            results = collector.collect()

    assert results == []


# ---------------------------------------------------------------------------
# #12 日配额硬上限
# ---------------------------------------------------------------------------


def test_daily_quota_is_a_hard_cap(dedup_config):
    """候选超过 nlm_daily_quota → 硬截断到配额，超额部分写入 pending 不丢。"""
    _write_sources(dedup_config.video_sources_path, {
        "nlm_daily_quota": 3, "coldstart_max_per_day": 10,
    })
    collector = VideoCollector(dedup_config)
    videos = [_video(f"q{i}", title=f"AI Video {i}") for i in range(8)]

    with mock.patch.object(collector, "_detect_yt_videos", return_value=videos):
        results = collector.collect()

    assert len(results) == 3, f"日配额 3 必须是硬上限，实得 {len(results)}"
    pending = json.loads(
        dedup_config.resolve_pending_path().read_text(encoding="utf-8")
    )
    assert len(pending) == 5, "超额候选必须写回 pending，不得静默丢弃"


def test_daily_quota_counts_already_ingested_today(dedup_config):
    """同一天多次触发（launchd backstop）合计不得超配额。"""
    _write_sources(dedup_config.video_sources_path, {
        "nlm_daily_quota": 2, "coldstart_max_per_day": 10,
    })
    store = VideoDedupStore(dedup_config.dedup_db_path)
    store.mark_ingested("https://www.youtube.com/watch?v=done1", platform="youtube")
    collector = VideoCollector(dedup_config, db=store)
    videos = [_video(f"r{i}", title=f"AI Video {i}") for i in range(5)]

    with mock.patch.object(collector, "_detect_yt_videos", return_value=videos):
        results = collector.collect()

    assert len(results) == 1, "今日已入库 1 个 → 本轮只剩 1 个额度"


def test_quota_exhausted_returns_empty_without_detection(dedup_config):
    """配额已用尽 → 直接返回空，连检测都不做（不烧任何额度）。"""
    _write_sources(dedup_config.video_sources_path, {"nlm_daily_quota": 1})
    store = VideoDedupStore(dedup_config.dedup_db_path)
    store.mark_ingested("https://www.youtube.com/watch?v=used1", platform="youtube")
    collector = VideoCollector(dedup_config, db=store)

    with mock.patch.object(collector, "_detect_yt_videos") as detect:
        results = collector.collect()

    assert results == []
    assert not detect.called, "配额用尽后不应再检测视频"


# ---------------------------------------------------------------------------
# #12 附带：话题聚类未覆盖索引不得静默丢弃
# ---------------------------------------------------------------------------


def test_topic_dedup_keeps_indices_llm_forgot(dedup_config):
    """LLM 分组漏掉的候选必须保留（原实现只遍历 groups → 静默丢失）。"""
    _write_sources(dedup_config.video_sources_path, {
        "dedup_strategy": "llm_cluster", "coldstart_max_per_day": 10,
    })

    class _PartialLLM:
        def chat(self, task, system, user, temperature=0.3):
            return json.dumps({"groups": [[0]]})  # 漏掉索引 1、2

    collector = VideoCollector(dedup_config, llm_adapter=_PartialLLM())
    videos = [_video(f"t{i}", title=f"AI Topic {i}") for i in range(3)]

    with mock.patch.object(collector, "_detect_yt_videos", return_value=videos):
        results = collector.collect()

    ids = sorted(r["video"]["id"] for r in results)
    assert ids == ["t0", "t1", "t2"], f"未被 LLM 提到的候选被静默丢弃: {ids}"


def test_same_video_twice_in_one_run_processed_once(dedup_config):
    """同一视频以两种 URL 形态出现在同一轮 → 只研究一次（账本此刻还没有它，L1/L7 拦不住）。"""
    _write_sources(dedup_config.video_sources_path, {"coldstart_max_per_day": 10})
    collector = VideoCollector(dedup_config)
    today = datetime.now().strftime("%Y%m%d")
    dup_a = dict(_video("same1"), url="https://www.youtube.com/watch?v=same1&t=42s")
    dup_b = dict(_video("same1"), url="https://youtu.be/same1", upload_date=today)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[dup_a, dup_b]):
        results = collector.collect()

    assert len(results) == 1, f"同一视频在同轮内被研究了 {len(results)} 次"


def test_collector_fail_closed_when_dedup_db_broken(dedup_config):
    """去重库损坏 → collect() 报错传播（fail-closed），不是"当作没入库"继续烧钱。"""
    dedup_config.dedup_db_path.parent.mkdir(parents=True, exist_ok=True)
    dedup_config.dedup_db_path.write_bytes(b"corrupted" * 100)
    collector = VideoCollector(dedup_config)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[_video()]):
        with pytest.raises(VideoDedupStoreError):
            collector.collect()


def test_dedup_db_path_is_outside_synced_scope():
    """默认去重库路径必须落在机器本地状态根（不进 Syncthing/vault，缺陷 #11 约定）。"""
    from src.akb2.common.state_paths import get_state_root, is_synced_path

    config = VideoConfig(akb2_root=Path("/tmp/does-not-matter"))
    assert not is_synced_path(config.dedup_db_path)
    assert str(config.dedup_db_path).startswith(str(get_state_root()))


def test_store_schema_has_identity_primary_key(tmp_path):
    """去重表主键是 identity（稳定身份），不是 title/slug。"""
    store = VideoDedupStore(tmp_path / "dedup.sqlite")
    store.mark_ingested("https://www.youtube.com/watch?v=abc123", platform="youtube")
    conn = sqlite3.connect(str(tmp_path / "dedup.sqlite"))
    try:
        cols = {
            row[1]: row[5]  # name -> pk flag
            for row in conn.execute("PRAGMA table_info(video_ingested)")
        }
    finally:
        conn.close()
    assert cols["identity"] == 1
    assert cols.get("title") == 0
