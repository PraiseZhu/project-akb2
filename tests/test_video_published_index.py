"""残余风险「视频存量覆盖」回归：第二道防线按**身份**判定，覆盖旧命名历史产出。

背景（2026-07-28 实测，只读统计 `/Volumes/AKB2/.../AI TRENDS/Video`）：

    $ ls | grep -cE '^video-.*-[0-9a-f]{8}\\.md$'                 → 0    新命名
    $ ls | grep -cE '^video-.*-20[0-9]{2}-[0-9]{2}-[0-9]{2}\\.md$' → 324  旧命名(带日期)
    $ ls *.md | wc -l                                            → 608

`commit()` 的 `promotion_path.exists()` 判文件名，对这 608 份旧命名恒不命中 →
存量 230 个视频身份的跨日防线只剩账本单点。本文件的断言全部绑定生产调用路径
（`assembler.run_pipeline` / `assembler.commit` / `collector.collect`），
并钉住"扫描有界、缓存生效"。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.video.assembler import (
    Assembler,
    CandidateTargetExists,
    PublishedIdentityExists,
)
from src.akb2.video.collector import VideoCollector
from src.akb2.video.config import VideoConfig
from src.akb2.video.dedup_store import VideoDedupStore
from src.akb2.video.extractor import ExtractedFields, KeyFinding, SourceSpan
from src.akb2.video.published_index import PublishedIdentityIndex
from src.akb2.video.reasoner import ReasonedLinks
from src.akb2.video.researcher import ResearchOutput
from tests.video_state_isolation import isolate_video_state  # noqa: F401

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# 真实历史产出的两种旧命名形态（都不含身份哈希，文件名层抓不到）
_LEGACY_NAME_CN = "{title} - 视频研究 2026-05-12.md"
_LEGACY_NAME_DATED = "video-{slug}-2026-05-12.md"


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


def _legacy_note(
    published_dir: Path,
    url: str,
    *,
    title: str = "本地 AI 自动化管线：技术分析与行业简报",
    key: str = "video_url",
    name_template: str = _LEGACY_NAME_CN,
) -> Path:
    """在已发布目录里造一份**旧命名**历史产出（frontmatter 形态取自真实笔记）。"""
    path = published_dir / name_template.format(title=title, slug="legacy-slug")
    path.write_text(
        "---\n"
        f'title_original: "Old Title Rewritten By LLM"\n'
        "source: notebooklm\n"
        "date: 2026-05-12\n"
        "tags:\n  - research\n  - video\n  - youtube\n"
        "notebook_id: 01554147-9bdc-4feb-a526-7ef1d9525e53\n"
        f"{key}: {url}\n"
        "video_creator: All About AI\n"
        "---\n\n# 正文\n\n内容。\n",
        encoding="utf-8",
    )
    return path


def _video(video_id: str = "ydUBYFlwhyk", title: str = "AI Agents Deep Dive") -> dict:
    return {
        "platform": "youtube",
        "id": video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "duration_minutes": 20,
        "upload_date": datetime.now().strftime("%Y%m%d"),
        "creator": "Test Channel",
    }


def _candidate_md(config: VideoConfig, video_meta: dict) -> str:
    return Assembler(config).assemble(
        video_meta,
        ResearchOutput(
            report_markdown="# LLM 改写过的标题\n\n## 执行摘要\n\n内容。",
            notebook_id="nb-1", video_meta=video_meta, degraded=False,
        ),
        ExtractedFields(
            tldr="要点",
            key_findings=[KeyFinding(
                assertion="断言", source_span=SourceSpan(line_start=1, line_end=1),
                confidence=0.9,
            )],
            future_questions=[], concepts_mentioned=["AI"],
        ),
        ReasonedLinks(related_concept=[], proposed_graph_nodes=[]),
    )


class _Researcher:
    def __init__(self, video_meta: dict):
        self.video_meta = video_meta
        self.calls = 0

    def run(self, video_results):
        self.calls += 1
        return [ResearchOutput(
            report_markdown="# LLM 改写过的标题\n\n## 执行摘要\n\n内容。",
            notebook_id="nb-1", video_meta=self.video_meta, degraded=False,
        )]


class _Extractor:
    def extract(self, md):
        return ExtractedFields(
            tldr="要点",
            key_findings=[KeyFinding(
                assertion="断言", source_span=SourceSpan(line_start=1, line_end=1),
                confidence=0.9,
            )],
            future_questions=[], concepts_mentioned=["AI"],
        )


class _Reasoner:
    def reason(self, concepts, context=""):
        return ReasonedLinks(related_concept=[], proposed_graph_nodes=[])


# ---------------------------------------------------------------------------
# 身份层 fail-closed：旧命名历史产出必须挡住同身份的第二次落地
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name_template", [_LEGACY_NAME_CN, _LEGACY_NAME_DATED])
def test_legacy_named_history_blocks_second_landing_via_commit(
    video_config, name_template
):
    """生产路径 `commit(slug=_slug_for_video(...))`：同身份第二次不得落地。

    缺陷版（只有 `promotion_path.exists()` 文件名判定）会把新产出写进 vault：
    历史产出叫 `… - 视频研究 2026-05-12.md` / `video-legacy-slug-2026-05-12.md`，
    新产出叫 `video-<slug>-<身份哈希>.md`，文件名永不相撞。
    """
    video_meta = _video()
    _legacy_note(
        video_config.promotion_target_path, video_meta["url"],
        name_template=name_template,
    )
    before = sorted(p.name for p in video_config.promotion_target_path.glob("*.md"))

    assembler = Assembler(video_config)
    candidate_md = _candidate_md(video_config, video_meta)
    with mock.patch.object(assembler, "_gate_decision", return_value="active"):
        with pytest.raises(PublishedIdentityExists) as excinfo:
            assembler.commit(
                candidate_md, slug=assembler._slug_for_video(video_meta),
                date_str="2026-07-28",
            )

    assert "youtube:ydUBYFlwhyk" in str(excinfo.value), "报错必须点名撞上的身份"
    after = sorted(p.name for p in video_config.promotion_target_path.glob("*.md"))
    assert after == before, f"同身份产出仍然落地了: {set(after) - set(before)}"
    assert not list(video_config.staging_dir.glob("*.md")), "staging 中转必须清掉"


def test_published_identity_exists_is_caught_by_existing_handlers():
    """新异常必须被既有 `except CandidateTargetExists` 接住（run_pipeline 的捕获元组）。"""
    assert issubclass(PublishedIdentityExists, CandidateTargetExists)


def test_legacy_named_history_blocks_second_landing_via_run_pipeline(video_config):
    """整链生产路径：账本被清空 + 历史产出是旧命名 → run_pipeline 不得再落地一份。

    模拟的正是残余风险描述的账本失守（`AKB2_STATE_ROOT` 指向空目录 / sqlite 被重建）：
    账本里没有该身份，文件名层因旧命名也抓不到，缺陷版会写出第二份产出。
    """
    video_meta = _video()
    _legacy_note(video_config.promotion_target_path, video_meta["url"])
    before = {p.name for p in video_config.promotion_target_path.glob("*.md")}

    store = VideoDedupStore(video_config.dedup_db_path)  # 空账本
    collector = VideoCollector(video_config, db=store)
    assembler = Assembler(video_config)
    researcher = _Researcher(video_meta)

    with mock.patch.object(
        collector, "_detect_yt_videos", return_value=[dict(video_meta)]
    ):
        with mock.patch.object(assembler, "_gate_decision", return_value="active"):
            results = assembler.run_pipeline(
                collector, researcher, _Extractor(), _Reasoner(),
            )

    after = {p.name for p in video_config.promotion_target_path.glob("*.md")}
    assert after == before, f"存量视频被重跑并落地: {after - before}"
    # 采集层就该拦住（不烧 NLM/LLM 额度）：researcher 一次都不该被调用
    assert researcher.calls == 0, "历史产出已存在，研究阶段不该被调用（省额度）"
    assert results == [], f"无新视频时应返回空结果，实得 {results}"


def test_collector_l1_skips_video_present_only_in_history(video_config):
    """L1 生产路径 `collect()`：账本没有、历史产出有 → 候选必须被拦在花钱之前。

    这是账本失守时唯一还起作用的省钱防线（landing 层只保证不写重复文件，
    NLM/LLM 额度那时已经烧掉了）。
    """
    video_meta = _video()
    _legacy_note(video_config.promotion_target_path, video_meta["url"])
    store = VideoDedupStore(video_config.dedup_db_path)
    assert store.is_ingested(video_meta["url"]) is False, "前提：账本里没有该身份"

    collector = VideoCollector(video_config, db=store)
    with mock.patch.object(
        collector, "_detect_yt_videos", return_value=[dict(video_meta)]
    ):
        results = collector.collect()

    assert results == [], "历史产出已存在的视频必须在 L1 被拦下"


def test_collector_still_accepts_video_absent_from_history(video_config):
    """反向：历史产出里没有的视频必须照常通过（防线不能变成"什么都拦"）。"""
    _legacy_note(
        video_config.promotion_target_path,
        "https://www.youtube.com/watch?v=OLDVIDEO001",
    )
    store = VideoDedupStore(video_config.dedup_db_path)
    collector = VideoCollector(video_config, db=store)
    fresh = _video(video_id="BRANDNEW001")

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[dict(fresh)]):
        results = collector.collect()

    assert [r["video"]["id"] for r in results] == ["BRANDNEW001"]


def test_index_ignores_non_video_notes(video_config):
    """同目录的非视频笔记（essay/论文链接）不得进索引 → 不得误拦真视频。

    过度合并的代价比多烧一次额度严重：把新视频误判成已入库 = 内容永久丢失。
    """
    essay = video_config.promotion_target_path / "某篇 essay 摘录 2026-05-01.md"
    essay.write_text(
        "---\nsource_url: https://example.com/blog/some-essay\n---\n\n正文\n",
        encoding="utf-8",
    )
    index = PublishedIdentityIndex(video_config.promotion_target_path)

    assert index.contains("https://example.com/blog/some-essay") is False
    assert index.identity_count == 0


# ---------------------------------------------------------------------------
# 扫描有界 / 缓存生效
# ---------------------------------------------------------------------------


def test_index_scan_is_bounded_across_many_lookups(video_config):
    """20 次查询只扫一遍目录：禁止"每候选全目录重扫"（宪法 P1 有界）。"""
    published = video_config.promotion_target_path
    for i in range(5):
        _legacy_note(
            published, f"https://www.youtube.com/watch?v=hist{i:03d}",
            title=f"历史产出 {i}", name_template="历史产出 {slug} " + f"{i}.md",
        )
    index = PublishedIdentityIndex(published)

    for i in range(20):
        index.contains(f"https://www.youtube.com/watch?v=probe{i:03d}")

    assert index.build_count == 1, f"索引被重建了 {index.build_count} 次（应为 1）"
    assert index.files_read_total == 5, (
        f"20 次查询读了 {index.files_read_total} 份文件（应为目录份数 5，"
        "逐候选重扫会是 100）"
    )


def test_index_lookup_is_cached_across_two_commits(video_config):
    """生产路径：同一 Assembler 连发两个视频，索引只建一次。"""
    assembler = Assembler(video_config)
    _legacy_note(
        video_config.promotion_target_path,
        "https://www.youtube.com/watch?v=hist000", title="历史产出",
    )

    with mock.patch.object(assembler, "_gate_decision", return_value="active"):
        for vid in ("newA0000001", "newB0000002"):
            meta = _video(video_id=vid)
            assembler.commit(
                _candidate_md(video_config, meta),
                slug=assembler._slug_for_video(meta), date_str="2026-07-28",
            )

    assert assembler.published_index.build_count == 1, (
        f"两次 commit 重建了 {assembler.published_index.build_count} 次索引"
    )
    assert assembler.published_index.files_read_total == 1, (
        "第二次 commit 不该重扫目录"
    )


def test_commit_marks_new_publish_into_index_without_rebuild(video_config):
    """刚发布的产出必须增量并入索引：同轮内换个 URL 形态也拦得住，且不重建。"""
    assembler = Assembler(video_config)
    meta = _video(video_id="sameVideo01")

    with mock.patch.object(assembler, "_gate_decision", return_value="active"):
        assembler.commit(
            _candidate_md(video_config, meta),
            slug=assembler._slug_for_video(meta), date_str="2026-07-28",
        )
        # 同一视频的另一种 URL 形态（youtu.be 短形式）→ 文件名 slug 相同，
        # 但这里要钉的是"索引已含该身份且没重建"。
        alt = dict(meta, url="https://youtu.be/sameVideo01", title="标题被改过")
        with pytest.raises(CandidateTargetExists):
            assembler.commit(
                _candidate_md(video_config, alt),
                slug="完全不同的-slug", date_str="2026-07-29",
            )

    assert assembler.published_index.build_count == 1, "增量登记不该触发重建"
    assert assembler.published_index.contains("https://youtu.be/sameVideo01") is True


def test_index_truncation_is_visible(video_config, caplog):
    """超出扫描上限 → 打 ERROR 并置 truncated（部分覆盖不得伪装成完整，宪法 P4）。"""
    published = video_config.promotion_target_path
    for i in range(5):
        _legacy_note(
            published, f"https://www.youtube.com/watch?v=hist{i:03d}",
            name_template="历史 {slug} " + f"{i}.md",
        )
    index = PublishedIdentityIndex(published, max_files=2)

    with caplog.at_level("ERROR"):
        index.ensure_built()

    assert index.truncated is True
    assert index.files_read_total == 2, "上限必须真的生效"
    assert any("不完整" in r.message for r in caplog.records), (
        "截断必须打 ERROR，否则「只索引了 2/5 份」会静默变成「没有重复」"
    )


def test_index_rebuilds_after_ttl_expiry(video_config, monkeypatch):
    """TTL 到期后重建（外部新增的产出最终能被看到），TTL 内不重建。"""
    published = video_config.promotion_target_path
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "src.akb2.video.published_index.time.monotonic", lambda: clock["now"]
    )
    index = PublishedIdentityIndex(published, ttl_sec=600.0)
    index.ensure_built()
    assert index.build_count == 1

    _legacy_note(published, "https://www.youtube.com/watch?v=laterAdd01")
    clock["now"] += 599.0
    assert index.contains("https://www.youtube.com/watch?v=laterAdd01") is False
    assert index.build_count == 1, "TTL 内不得重建"

    clock["now"] += 2.0
    assert index.contains("https://www.youtube.com/watch?v=laterAdd01") is True
    assert index.build_count == 2


def test_index_dir_unavailable_does_not_block_new_videos(video_config, caplog):
    """vault 未挂载 → 本层不生效但必须可见，且不得误拦新视频（fail-closed 不越界）。"""
    missing = video_config.akb2_root / "not-mounted" / "Video"
    index = PublishedIdentityIndex(missing)

    with caplog.at_level("WARNING"):
        result = index.contains("https://www.youtube.com/watch?v=anything001")

    assert result is False
    assert index.dir_unavailable is True
    assert any("不可用" in r.message for r in caplog.records)
