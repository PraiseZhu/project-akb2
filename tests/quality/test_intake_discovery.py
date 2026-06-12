"""test_intake_discovery.py — 发现层单测。

覆盖:
    - RSS/Atom feed 解析
    - 指纹去重幂等
    - 产量隔离触发与不触发
    - 批量发现与 daily_counts
    - 无 feed 源 listing 模式（占位）

全 fixture，不打真网。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.akb2.quality.intake.discovery import (
    QUARANTINE_MULTIPLIER,
    batch_discover,
    build_daily_counts,
    check_quarantine,
    content_fingerprint,
    discover_articles,
    is_duplicate_by_hash,
    parse_feed,
)

# ---------------------------------------------------------------------------
# 指纹去重
# ---------------------------------------------------------------------------


class TestContentFingerprint:
    """内容指纹计算测试。"""

    def test_fingerprint_deterministic(self):
        """同一内容产生相同指纹。"""
        text = "Hello world, this is a test article."
        fp1 = content_fingerprint(text)
        fp2 = content_fingerprint(text)
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex

    def test_fingerprint_different_content(self):
        """不同内容产生不同指纹。"""
        fp1 = content_fingerprint("Article A content")
        fp2 = content_fingerprint("Article B content")
        assert fp1 != fp2

    def test_fingerprint_empty_string(self):
        """空字符串也有指纹。"""
        fp = content_fingerprint("")
        assert len(fp) == 64


class TestDedup:
    """去重测试。"""

    def test_no_ledger_no_duplicate(self, temp_ledger_path: Path):
        """账本不存在时，任何 hash 都不算重复。"""
        assert not is_duplicate_by_hash("abc123", temp_ledger_path)

    def test_ledger_has_hash(self, temp_ledger_path: Path):
        """账本中存在 hash 时返回 True。"""
        temp_ledger_path.write_text(
            json.dumps({"content_hash": "abc123", "url": "https://x.com/a"}) + "\n"
        )
        assert is_duplicate_by_hash("abc123", temp_ledger_path)

    def test_ledger_no_match(self, temp_ledger_path: Path):
        """账本中不存在 hash 时返回 False。"""
        temp_ledger_path.write_text(
            json.dumps({"content_hash": "abc123", "url": "https://x.com/a"}) + "\n"
        )
        assert not is_duplicate_by_hash("xyz789", temp_ledger_path)

    def test_ledger_empty(self, temp_ledger_path: Path):
        """空账本不报错。"""
        temp_ledger_path.write_text("")
        assert not is_duplicate_by_hash("abc123", temp_ledger_path)

    def test_ledger_corrupted_line_skipped(self, temp_ledger_path: Path):
        """损坏行跳过不报错。"""
        temp_ledger_path.write_text(
            "not valid json\n"
            + json.dumps({"content_hash": "abc123", "url": "https://x.com/a"})
            + "\n"
        )
        assert is_duplicate_by_hash("abc123", temp_ledger_path)


# ---------------------------------------------------------------------------
# Feed 解析
# ---------------------------------------------------------------------------


class TestFeedParsing:
    """RSS/Atom feed 解析测试。"""

    def test_parse_rss(self, sample_rss_xml: str):
        """解析 RSS 2.0 feed。"""
        entries = parse_feed(sample_rss_xml)
        assert len(entries) == 3
        assert entries[0]["title"] == "Building AI Agents with Loop Engineering"
        assert entries[0]["link"] == "https://example.com/blog/ai-agents-loop-engineering"

    def test_parse_atom(self, sample_atom_xml: str):
        """解析 Atom 1.0 feed。"""
        entries = parse_feed(sample_atom_xml)
        assert len(entries) == 2
        assert entries[0]["title"] == "Understanding Context Engineering"
        assert "example.com" in entries[0]["link"]

    def test_parse_empty_string(self):
        """空字符串返回空列表。"""
        entries = parse_feed("")
        assert entries == []

    def test_parse_invalid_xml(self):
        """非法 XML 抛出 XML 解析异常。"""
        from xml.etree.ElementTree import ParseError

        import pytest as _pytest

        with _pytest.raises(ParseError):
            parse_feed("not xml at all <<<>>>")

    def test_rss_entries_have_required_fields(self, sample_rss_xml: str):
        """RSS 条目含 title/link/description。"""
        entries = parse_feed(sample_rss_xml)
        for entry in entries:
            assert "title" in entry
            assert "link" in entry
            assert "description" in entry


# ---------------------------------------------------------------------------
# 发现主流程
# ---------------------------------------------------------------------------


class TestDiscoverArticles:
    """发现层主流程测试。"""

    def test_discover_from_rss_feed(
        self, sample_source_config: dict, sample_rss_xml: str, temp_ledger_path: Path
    ):
        """从 RSS feed 发现文章。"""
        result = discover_articles(
            sample_source_config,
            temp_ledger_path,
            feed_content=sample_rss_xml,
        )
        assert result.method == "feed"
        assert len(result.articles) == 3
        assert result.skipped_duplicates == 0
        assert result.error is None

    def test_discover_from_atom_feed(
        self, sample_source_config: dict, sample_atom_xml: str, temp_ledger_path: Path
    ):
        """从 Atom feed 发现文章。"""
        result = discover_articles(
            sample_source_config,
            temp_ledger_path,
            feed_content=sample_atom_xml,
        )
        assert result.method == "feed"
        assert len(result.articles) == 2

    def test_discover_dedup_existing_articles(
        self, sample_source_config: dict, sample_rss_xml: str, temp_ledger_path: Path
    ):
        """已抓取过的文章被跳过（幂等）。"""
        # 第一次抓取
        result1 = discover_articles(
            sample_source_config,
            temp_ledger_path,
            feed_content=sample_rss_xml,
        )
        assert len(result1.articles) == 3

        # 将第一次的所有文章写入 ledger
        with open(temp_ledger_path, "a", encoding="utf-8") as fh:
            for article in result1.articles:
                fh.write(
                    json.dumps({
                        "content_hash": article.content_hash,
                        "url": article.url,
                        "source_slug": article.source_slug,
                    })
                    + "\n"
                )

        # 第二次抓取：全部重复
        result2 = discover_articles(
            sample_source_config,
            temp_ledger_path,
            feed_content=sample_rss_xml,
        )
        assert len(result2.articles) == 0
        assert result2.skipped_duplicates == 3

    def test_discover_no_feed_content_returns_error(
        self, sample_source_config: dict, temp_ledger_path: Path
    ):
        """无 feed content 注入时返回空结果。"""
        result = discover_articles(sample_source_config, temp_ledger_path, feed_content=None)
        assert len(result.articles) == 0

    def test_discover_listing_mode(
        self, sample_source_config_no_feed: dict, temp_ledger_path: Path
    ):
        """无 feed_url 的源走 listing 模式。"""
        result = discover_articles(
            sample_source_config_no_feed, temp_ledger_path, feed_content=None
        )
        assert result.method == "listing"
        assert "not yet implemented" in (result.error or "")

    def test_discover_no_feed_no_listing(
        self, temp_ledger_path: Path
    ):
        """无 feed 也无 listing 的源。"""
        source = {
            "slug": "empty-source",
            "domain": "empty.example.com",
            "rate_limit": 3600,
            "status": "trial",
        }
        result = discover_articles(source, temp_ledger_path, feed_content=None)
        assert result.method == "none"
        assert result.error is not None


# ---------------------------------------------------------------------------
# 产量隔离
# ---------------------------------------------------------------------------


class TestQuarantine:
    """产量隔离逻辑测试。"""

    def test_quarantine_triggered(self):
        """单源产量远超中位数 → 触发隔离。"""
        daily_counts = {
            "source-a": 20,  # 远超中位数
            "source-b": 2,
            "source-c": 3,
            "source-d": 1,
            "source-e": 2,
        }
        assert check_quarantine("source-a", daily_counts)

    def test_quarantine_not_triggered_normal(self):
        """产量正常 → 不触发隔离。"""
        daily_counts = {
            "source-a": 3,
            "source-b": 2,
            "source-c": 3,
            "source-d": 4,
            "source-e": 2,
        }
        assert not check_quarantine("source-a", daily_counts)

    def test_quarantine_not_enough_sources(self):
        """源数不足最小阈值 → 不触发隔离。"""
        daily_counts = {
            "source-a": 20,
            "source-b": 1,
        }
        assert not check_quarantine("source-a", daily_counts)

    def test_quarantine_source_not_in_counts(self):
        """源不在统计中 → 不触发。"""
        daily_counts = {
            "source-b": 2,
            "source-c": 3,
            "source-d": 1,
            "source-e": 2,
        }
        assert not check_quarantine("source-a", daily_counts)

    def test_quarantine_source_zero_count(self):
        """源产量为 0 → 不触发。"""
        daily_counts = {
            "source-a": 0,
            "source-b": 2,
            "source-c": 3,
            "source-d": 1,
            "source-e": 2,
        }
        assert not check_quarantine("source-a", daily_counts)

    def test_quarantine_median_zero(self):
        """中位数为 0 → 不触发。"""
        daily_counts = {
            "source-a": 5,
            "source-b": 0,
            "source-c": 0,
            "source-d": 0,
            "source-e": 0,
        }
        assert not check_quarantine("source-a", daily_counts)

    def test_quarantine_multiplier_respected(self):
        """产量刚好等于 multiplier × median 时不触发（> 才触发）。"""
        daily_counts = {
            "source-a": 6,  # 3 * 2 = 6, not > 6
            "source-b": 2,
            "source-c": 2,
            "source-d": 2,
            "source-e": 2,
        }
        assert not check_quarantine("source-a", daily_counts)

    def test_quarantine_custom_multiplier(self):
        """自定义 multiplier。"""
        daily_counts = {
            "source-a": 5,
            "source-b": 2,
            "source-c": 2,
            "source-d": 2,
            "source-e": 2,
        }
        # median=2, 5 > 2*2 → trigger
        assert check_quarantine("source-a", daily_counts, multiplier=2.0)
        # median=2, 5 < 2*3 → not trigger
        assert not check_quarantine("source-a", daily_counts, multiplier=3.0)


# ---------------------------------------------------------------------------
# 批量发现
# ---------------------------------------------------------------------------


class TestBatchDiscover:
    """批量发现测试。"""

    def test_batch_discover_multiple_sources(
        self, sample_rss_xml: str, temp_ledger_path: Path
    ):
        """多源批量发现。"""
        sources = [
            {
                "slug": "blog-a",
                "domain": "a.example.com",
                "feed_url": "https://a.example.com/feed.xml",
                "rate_limit": 3600,
                "status": "trial",
            },
            {
                "slug": "blog-b",
                "domain": "b.example.com",
                "feed_url": "https://b.example.com/feed.xml",
                "rate_limit": 3600,
                "status": "trial",
            },
        ]
        feed_contents = {
            "blog-a": sample_rss_xml,
            "blog-b": sample_rss_xml,
        }
        results = batch_discover(sources, temp_ledger_path, feed_contents=feed_contents)
        assert len(results) == 2
        assert results["blog-a"].method == "feed"
        assert results["blog-b"].method == "feed"
        assert len(results["blog-a"].articles) == 3

    def test_build_daily_counts(self, sample_rss_xml: str, temp_ledger_path: Path):
        """从批量结果构建 daily_counts。"""
        sources = [
            {
                "slug": "blog-a",
                "domain": "a.example.com",
                "feed_url": "https://a.example.com/feed.xml",
                "rate_limit": 3600,
                "status": "trial",
            },
            {
                "slug": "blog-b",
                "domain": "b.example.com",
                "feed_url": "https://b.example.com/feed.xml",
                "rate_limit": 3600,
                "status": "trial",
            },
        ]
        feed_contents = {"blog-a": sample_rss_xml, "blog-b": sample_rss_xml}
        results = batch_discover(sources, temp_ledger_path, feed_contents=feed_contents)
        counts = build_daily_counts(results)
        assert counts["blog-a"] == 3
        assert counts["blog-b"] == 3


# ---------------------------------------------------------------------------
# 具名常量验证
# ---------------------------------------------------------------------------


class TestConstants:
    """具名常量验证。"""

    def test_quarantine_multiplier_exists(self):
        """QUARANTINE_MULTIPLIER 是具名常量。"""
        assert isinstance(QUARANTINE_MULTIPLIER, float)
        assert QUARANTINE_MULTIPLIER == 3.0
