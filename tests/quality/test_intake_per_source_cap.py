"""tests/quality/test_intake_per_source_cap.py — F22 每源每轮限额 + 轮转公平测试。

工单 F22:修单源垄断(openai-blog 78/78)+ 轮转公平。两个修复:
  1. PER_SOURCE_PER_RUN_CAP=5:每源每轮上限(防单源吃满全局 limit 30)
  2. _source_rotation_offset:按时间轮转起点(防后排源饿死)

测试覆盖(工单要求:单源不超 cap / 全局不超 limit / 多源都有份 / 轮转不饿死后排):
  - cap 常量 + _process_article cap 触发/不触发
  - run_intake 单源 cap 限制 + 多源有份 + 全局 limit
  - _source_rotation_offset 函数 + run_intake 轮转改首源 + 后排源不饿死

全部 mock,不打真网;tmp_path 隔离不写 vault。
"""

from __future__ import annotations

import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.akb2.quality.intake.orchestrator import (
    PER_SOURCE_PER_RUN_CAP,
    RATE_LIMIT_PER_RUN,
    _process_article,
    _source_rotation_offset,
    run_intake,
)

# ---------------------------------------------------------------------------
# Fixtures(抄 test_intake_orchestrator 模式)
# ---------------------------------------------------------------------------


def _write_multi_sources_yaml(tmp_path: Path, slugs: list[str]) -> Path:
    """写多源 sources.yaml(每源一个 feed_url)。"""
    srcs = "\n".join(
        textwrap.dedent(
            f"""\
          - slug: {slug}
            domain: {slug}.example.com
            feed_url: https://{slug}.example.com/feed.xml
            listing_url: https://{slug}.example.com
            rate_limit: 60
            status: trial
            category: core
        """
        )
        for slug in slugs
    )
    yaml_content = f"sources:\n{srcs}"
    p = tmp_path / "sources.yaml"
    p.write_text(yaml_content, encoding="utf-8")
    return p


def _make_discover_mock(urls_titles: list[tuple[str, str]], slug: str):
    from src.akb2.quality.intake.discovery import DiscoveredArticle, DiscoverResult

    articles = [
        DiscoveredArticle(
            url=url,
            title=title,
            content_hash=f"{slug}{i:04d}",
            source_slug=slug,
            published_at="2026-06-14T00:00:00Z",
            feed_title=title,
        )
        for i, (url, title) in enumerate(urls_titles)
    ]
    return DiscoverResult(source_slug=slug, articles=articles, method="feed")


def _mock_extract_factory():
    """返回一个 mock extract_content,extraction_success=True。"""
    m = MagicMock()
    m.extraction_success = True
    m.url = "https://example.com/x"
    m.method = MagicMock()
    m.method.value = "defuddle"
    m.title = "T"
    m.author = "A"
    m.published = "2026-06-14"
    m.content = "Content " * 50
    m.word_count = 200
    return m


def _mock_archive_factory(output_dir: Path):
    """返回一个 mock archive_from_extract_result,success=True。"""
    m = MagicMock()
    m.success = True
    m.output_path = str(output_dir / "mock.md")
    return m


def _patch_extract_archive(output_dir: Path):
    """返回 patch context manager(discover_articles 由调用方单独 patch)。"""
    return (
        patch(
            "src.akb2.quality.intake.orchestrator.extract_content",
            return_value=_mock_extract_factory(),
        ),
        patch(
            "src.akb2.quality.intake.orchestrator.archive_from_extract_result",
            return_value=_mock_archive_factory(output_dir),
        ),
    )


# ---------------------------------------------------------------------------
# cap 常量 + _process_article cap 逻辑
# ---------------------------------------------------------------------------


class TestPerSourceCap:
    def test_cap_constant_is_5(self) -> None:
        """PER_SOURCE_PER_RUN_CAP=5(limit 30 下每轮至少 6 源有份)。"""
        assert PER_SOURCE_PER_RUN_CAP == 5

    def test_cap_less_than_rate_limit(self) -> None:
        """cap < RATE_LIMIT_PER_RUN(cap 先触发,RATE_LIMIT 兜底)。"""
        assert PER_SOURCE_PER_RUN_CAP < RATE_LIMIT_PER_RUN

    def test_process_article_cap_triggers(self, tmp_path: Path) -> None:
        """_process_article: source_daily_count >= cap → skipped_rate_limit。"""
        from src.akb2.quality.intake.discovery import DiscoveredArticle

        article = DiscoveredArticle(
            url="https://example.com/a",
            title="A",
            content_hash="abc123",
            source_slug="test-src",
            published_at="2026-06-14T00:00:00Z",
            feed_title="A",
        )
        source = {"slug": "test-src"}
        state_file = tmp_path / "state.jsonl"
        seen: set[str] = set()
        status = _process_article(
            article=article,
            source=source,
            run_id="r1",
            state_file=state_file,
            output_dir=tmp_path / "raw",
            seen_ids=seen,
            source_daily_count=PER_SOURCE_PER_RUN_CAP,  # =cap,触发
            quarantine_triggered=False,
            dry_run=True,  # dry_run 下 cap 仍检查(在 dry_run 前)
            failures_path=None,
        )
        assert status == "skipped_rate_limit", f"cap 触发应 skipped_rate_limit,实际 {status}"

    def test_process_article_cap_not_triggered(self, tmp_path: Path) -> None:
        """_process_article: source_daily_count < cap → 不 skipped(到 dry_run_discovered)。"""
        from src.akb2.quality.intake.discovery import DiscoveredArticle

        article = DiscoveredArticle(
            url="https://example.com/a",
            title="A",
            content_hash="abc123",
            source_slug="test-src",
            published_at="2026-06-14T00:00:00Z",
            feed_title="A",
        )
        source = {"slug": "test-src"}
        state_file = tmp_path / "state.jsonl"
        seen: set[str] = set()
        status = _process_article(
            article=article,
            source=source,
            run_id="r1",
            state_file=state_file,
            output_dir=tmp_path / "raw",
            seen_ids=seen,
            source_daily_count=PER_SOURCE_PER_RUN_CAP - 1,  # < cap,不触发
            quarantine_triggered=False,
            dry_run=True,
            failures_path=None,
        )
        assert status != "skipped_rate_limit", "cap 未触发不该 skipped_rate_limit"
        assert status == "dry_run_discovered"


# ---------------------------------------------------------------------------
# run_intake cap + 多源 + 全局 limit
# ---------------------------------------------------------------------------


class TestRunIntakeCap:
    def test_single_source_capped_at_5(self, tmp_path: Path) -> None:
        """单源 10 篇 + limit 30 + cap 5 → archived=5(不超 cap),其余 skipped_rate_limit。"""
        sources_config = _write_multi_sources_yaml(tmp_path, ["src-a"])
        state_file = tmp_path / "state.jsonl"
        output_dir = tmp_path / "raw"
        mock_result = _make_discover_mock(
            [(f"https://src-a.example.com/{i}", f"A{i}") for i in range(10)],
            "src-a",
        )
        p_extract, p_archive = _patch_extract_archive(output_dir)
        with (
            patch("src.akb2.quality.intake.orchestrator.discover_articles", return_value=mock_result),
            p_extract,
            p_archive,
        ):
            stats = run_intake(
                sources_config=sources_config,
                state_file=state_file,
                output_dir=output_dir,
                dry_run=False,
                limit=30,
                source_offset=0,  # 不轮转(测 cap)
            )
        assert stats["archived"] == PER_SOURCE_PER_RUN_CAP, (
            f"单源应 cap 在 {PER_SOURCE_PER_RUN_CAP},实际 archived={stats['archived']}"
        )

    def test_multiple_sources_share_limit(self, tmp_path: Path) -> None:
        """3 源各 10 篇 + limit 30 + cap 5 → 每源 archived<=5,总 archived=15(3×5),多源都有份。"""
        slugs = ["src-a", "src-b", "src-c"]
        sources_config = _write_multi_sources_yaml(tmp_path, slugs)
        state_file = tmp_path / "state.jsonl"
        output_dir = tmp_path / "raw"
        # 每源 10 篇
        mock_results = {
            slug: _make_discover_mock(
                [(f"https://{slug}.example.com/{i}", f"{slug}-{i}") for i in range(10)],
                slug,
            )
            for slug in slugs
        }

        def _discover_side_effect(source, *args, **kwargs):
            return mock_results[source["slug"]]

        p_extract, p_archive = _patch_extract_archive(output_dir)
        with (
            patch("src.akb2.quality.intake.orchestrator.discover_articles", side_effect=_discover_side_effect),
            p_extract,
            p_archive,
        ):
            stats = run_intake(
                sources_config=sources_config,
                state_file=state_file,
                output_dir=output_dir,
                dry_run=False,
                limit=30,
                source_offset=0,  # 不轮转(测 cap+多源)
            )
        # 每源 archived<=cap=5,总 archived=15(3 源 × 5),不超 limit 30
        assert stats["archived"] <= 30, f"全局不超 limit 30,实际 {stats['archived']}"
        assert stats["archived"] == 15, (
            f"3 源各 cap 5 = 15,实际 {stats['archived']}(单源垄断说明 cap 没生效)"
        )

    def test_global_limit_not_exceeded(self, tmp_path: Path) -> None:
        """10 源各 10 篇 + limit 30 + cap 5 → archived<=30(全局有界)。"""
        slugs = [f"src-{i}" for i in range(10)]
        sources_config = _write_multi_sources_yaml(tmp_path, slugs)
        state_file = tmp_path / "state.jsonl"
        output_dir = tmp_path / "raw"
        mock_results = {
            slug: _make_discover_mock(
                [(f"https://{slug}.example.com/{i}", f"{slug}-{i}") for i in range(10)],
                slug,
            )
            for slug in slugs
        }

        def _discover_side_effect(source, *args, **kwargs):
            return mock_results[source["slug"]]

        p_extract, p_archive = _patch_extract_archive(output_dir)
        with (
            patch("src.akb2.quality.intake.orchestrator.discover_articles", side_effect=_discover_side_effect),
            p_extract,
            p_archive,
        ):
            stats = run_intake(
                sources_config=sources_config,
                state_file=state_file,
                output_dir=output_dir,
                dry_run=False,
                limit=30,
                source_offset=0,
            )
        assert stats["archived"] <= 30, f"全局 limit 30,实际 archived={stats['archived']}"


# ---------------------------------------------------------------------------
# 轮转公平
# ---------------------------------------------------------------------------


class TestSourceRotation:
    def test_rotation_offset_function(self) -> None:
        """_source_rotation_offset 返回 (time//interval)%num。"""
        num = 10
        interval = 7200
        expected = (int(time.time()) // interval) % num
        assert _source_rotation_offset(num, interval) == expected

    def test_rotation_offset_zero_for_no_sources(self) -> None:
        """num=0 → offset=0(不崩)。"""
        assert _source_rotation_offset(0) == 0

    def test_rotation_offset_in_range(self) -> None:
        """offset 在 [0, num)。"""
        for num in (1, 5, 163):
            off = _source_rotation_offset(num)
            assert 0 <= off < num, f"num={num} offset={off} 越界"

    def test_rotation_changes_first_source(self, tmp_path: Path) -> None:
        """source_offset=1 vs 0 → run_intake 先处理的源不同(轮转生效)。"""
        slugs = ["src-a", "src-b", "src-c"]
        sources_config = _write_multi_sources_yaml(tmp_path, slugs)
        state_file = tmp_path / "state.jsonl"
        output_dir = tmp_path / "raw"
        mock_results = {
            slug: _make_discover_mock(
                [(f"https://{slug}.example.com/{i}", f"{slug}-{i}") for i in range(2)],
                slug,
            )
            for slug in slugs
        }

        def _discover_side_effect(source, *args, **kwargs):
            return mock_results[source["slug"]]

        # 用 discover_articles 调用顺序验证轮转
        call_order: list[str] = []

        def _tracking_side_effect(source, *args, **kwargs):
            call_order.append(source["slug"])
            return mock_results[source["slug"]]

        p_extract, p_archive = _patch_extract_archive(output_dir)
        with (
            patch("src.akb2.quality.intake.orchestrator.discover_articles", side_effect=_tracking_side_effect),
            p_extract,
            p_archive,
        ):
            run_intake(
                sources_config=sources_config,
                state_file=state_file,
                output_dir=output_dir,
                dry_run=False,
                limit=3,
                source_offset=0,  # 从 src-a 开始
            )
        assert call_order[0] == "src-a", f"offset=0 应从 src-a 开始,实际 {call_order[0]}"

        # 第二次:offset=1,从 src-b 开始
        call_order.clear()
        state_file2 = tmp_path / "state2.jsonl"
        p_extract2, p_archive2 = _patch_extract_archive(output_dir)
        with (
            patch("src.akb2.quality.intake.orchestrator.discover_articles", side_effect=_tracking_side_effect),
            p_extract2,
            p_archive2,
        ):
            run_intake(
                sources_config=sources_config,
                state_file=state_file2,
                output_dir=output_dir,
                dry_run=False,
                limit=3,
                source_offset=1,  # 从 src-b 开始
            )
        assert call_order[0] == "src-b", f"offset=1 应从 src-b 开始,实际 {call_order[0]}"

    def test_rotation_back_sources_not_starved(self, tmp_path: Path) -> None:
        """6 源各 10 篇 + limit 30 + cap 5 + offset=5 → 后排源(src-f)排上(不饿死)。"""
        slugs = ["src-a", "src-b", "src-c", "src-d", "src-e", "src-f"]
        sources_config = _write_multi_sources_yaml(tmp_path, slugs)
        state_file = tmp_path / "state.jsonl"
        output_dir = tmp_path / "raw"
        mock_results = {
            slug: _make_discover_mock(
                [(f"https://{slug}.example.com/{i}", f"{slug}-{i}") for i in range(10)],
                slug,
            )
            for slug in slugs
        }

        def _tracking_side_effect(source, *args, **kwargs):
            return mock_results[source["slug"]]

        # mock archive 记录 source_slug
        mock_archive = MagicMock()
        mock_archive.success = True
        mock_archive.output_path = str(output_dir / "mock.md")

        p_extract = patch(
            "src.akb2.quality.intake.orchestrator.extract_content",
            return_value=_mock_extract_factory(),
        )
        p_archive = patch(
            "src.akb2.quality.intake.orchestrator.archive_from_extract_result",
            return_value=mock_archive,
        )
        with (
            patch("src.akb2.quality.intake.orchestrator.discover_articles", side_effect=_tracking_side_effect),
            p_extract,
            p_archive,
        ):
            stats = run_intake(
                sources_config=sources_config,
                state_file=state_file,
                output_dir=output_dir,
                dry_run=False,
                limit=30,
                source_offset=5,  # 从 src-f 开始(后排源)
            )
        # offset=5 → 首源 src-f(后排源),limit 30 含 skipped_rate_limit(total_processed
        # 含 skipped),6 源各 10 篇:每源 5 archived + 5 skipped = 10 processed,3 源 = 30
        # processed 触发全局 limit break → archived=15(3 源各 5)。src-f 被处理(不饿死)。
        assert stats["archived"] == 15, (
            f"3 源各 cap 5 = 15(limit 30 含 skipped,3 源后 total_processed=30 break),"
            f"实际 {stats['archived']}"
        )
        # 验证 src-f(后排源,offset=5 首源)被处理——通过 ledger 检查
        if state_file.exists():
            ledger_text = state_file.read_text(encoding="utf-8")
            assert "src-f" in ledger_text, "src-f(后排源,offset=5 首源)应被处理(轮转不饿死)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
