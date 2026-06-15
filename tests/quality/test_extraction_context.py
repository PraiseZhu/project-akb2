"""test_extraction_context.py — ExtractionContext dataclass + fallback 行为单测。

覆盖（SC-1 5 用例）:
    1. extractor 缺 title，但 feed_title 存在 → fallback 后可归档（extraction_success=True）
    2. author / published / publisher 同理各自 fallback
    3. 四者皆缺（extractor 和 context 均为空）→ fail-closed，不产 raw
    4. ExtractionContext 字段完整性（≥8 个字段）
    5. compute_article_id 确定性（相同 URL 永远返回相同 ID）

全 fixture，不打真网。
"""

from __future__ import annotations

import hashlib

from src.akb2.quality.intake.extract import ExtractMethod, extract_content
from src.akb2.quality.intake.models import ExtractionContext, compute_article_id

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 足够长的 HTML 正文，能通过字数门（>50 字）
_LONG_HTML = " ".join(["word"] * 250)  # 250 个 "word"，超过 MIN_CONTENT_LENGTH=200


def _make_context(
    *,
    feed_title: str = "Feed Title",
    feed_author: str = "Feed Author",
    feed_published: str = "2026-06-14",
    source_publisher: str = "Feed Publisher",
    source_slug: str = "test-source",
    url: str = "https://example.com/article",
) -> ExtractionContext:
    """创建一个默认填满的 ExtractionContext 用于 fallback 测试。"""
    return ExtractionContext(
        source_slug=source_slug,
        source_publisher=source_publisher,
        feed_title=feed_title,
        feed_author=feed_author,
        feed_published=feed_published,
        url=url,
        content_type="text/html",
        content_hash="",
    )


# ---------------------------------------------------------------------------
# SC-1 用例 1: extractor 缺 title，feed_title 存在 → fallback 后可归档
# ---------------------------------------------------------------------------


class TestFeedTitleFallback:
    """extractor 返回的 title 为空，从 context.feed_title 补充后应通过元数据校验。"""

    def test_missing_title_falls_back_to_feed_title(self):
        """抽取器产出 title=""，context.feed_title 非空 → extraction_success=True。"""
        # 构造一段 HTML，使 defuddle 占位实现返回正常内容
        # 注意：占位实现固定返回 "Defuddle Extracted Title"；
        # 为了模拟 "title 缺失" 场景，我们直接调用 extract_content 并用
        # monkeypatch 覆盖 _extract_via_defuddle 返回 title=""。
        from src.akb2.quality.intake import extract as extract_mod

        original = extract_mod._extract_via_defuddle

        def no_title_defuddle(url, html=None):
            return {
                "title": "",          # 模拟抽取器无法获取 title
                "author": "Some Author",
                "published": "2026-06-01",
                "content": _LONG_HTML,
                "word_count": 250,
            }

        try:
            extract_mod._extract_via_defuddle = no_title_defuddle
            ctx = _make_context(feed_title="Feed Provided Title")
            result = extract_content(
                url="https://example.com/article",
                html=_LONG_HTML,
                extraction_config=ctx,
            )
        finally:
            extract_mod._extract_via_defuddle = original

        assert result.extraction_success, f"应该成功，error={result.error}"
        assert result.title == "Feed Provided Title", "应从 feed_title fallback 填充"


# ---------------------------------------------------------------------------
# SC-1 用例 2: author / published / publisher 各自 fallback
# ---------------------------------------------------------------------------


class TestFeedMetadataFallbacks:
    """author / published / publisher 三字段各自从 context 补充。"""

    def _run_with_missing_field(self, missing_field: str):
        """通用辅助：令抽取器对 missing_field 返回空串，期望 fallback 补充后成功。"""
        from src.akb2.quality.intake import extract as extract_mod

        original = extract_mod._extract_via_defuddle

        def partial_defuddle(url, html=None):
            base = {
                "title": "Some Title",
                "author": "Some Author",
                "published": "2026-06-01",
                "content": _LONG_HTML,
                "word_count": 250,
            }
            base[missing_field] = ""  # 模拟指定字段缺失
            return base

        ctx = _make_context(
            feed_title="Feed Title",
            feed_author="Feed Author",
            feed_published="2026-06-14",
            source_publisher="Feed Publisher",
        )

        try:
            extract_mod._extract_via_defuddle = partial_defuddle
            result = extract_content(
                url="https://example.com/article",
                html=_LONG_HTML,
                extraction_config=ctx,
            )
        finally:
            extract_mod._extract_via_defuddle = original

        return result

    def test_missing_author_falls_back(self):
        """缺 author，context.feed_author 补充后成功。"""
        result = self._run_with_missing_field("author")
        assert result.extraction_success, f"应成功，error={result.error}"
        assert result.author == "Feed Author"

    def test_missing_published_falls_back(self):
        """缺 published，context.feed_published 补充后成功。"""
        result = self._run_with_missing_field("published")
        assert result.extraction_success, f"应成功，error={result.error}"
        assert result.published == "2026-06-14"

    def test_missing_publisher_stored_in_raw_metadata(self):
        """publisher 通过 context.source_publisher 写入 raw_metadata["publisher"]。"""
        from src.akb2.quality.intake import extract as extract_mod

        original = extract_mod._extract_via_defuddle

        def defuddle_no_publisher(url, html=None):
            return {
                "title": "Some Title",
                "author": "Some Author",
                "published": "2026-06-01",
                "content": _LONG_HTML,
                "word_count": 250,
            }

        ctx = _make_context(source_publisher="Context Publisher")
        try:
            extract_mod._extract_via_defuddle = defuddle_no_publisher
            result = extract_content(
                url="https://example.com/article",
                html=_LONG_HTML,
                extraction_config=ctx,
            )
        finally:
            extract_mod._extract_via_defuddle = original

        assert result.raw_metadata.get("publisher") == "Context Publisher"

    def test_author_falls_back_to_publisher_when_byline_missing(self):
        """G3 守护：抽取器和 feed 都无 author 时，回退 source_publisher 作"可归属主体"，
        并标记 author_source=publisher_fallback。守护"公司博客无个人署名仍能入库"。
        缺这条时删掉 publisher 兜底逻辑，openai 类无署名源会全被 fail-closed 拦掉。
        """
        from src.akb2.quality.intake import extract as extract_mod

        original = extract_mod._extract_via_defuddle

        def no_author_defuddle(url, html=None):
            return {
                "title": "OpenAI Academy Courses",
                "author": "",  # 页面无个人署名
                "published": "2026-05-22",
                "content": _LONG_HTML,
                "word_count": 250,
            }

        # feed 也无 author（公司博客 feed 普遍如此），只有机构 publisher
        ctx = _make_context(
            feed_title="",
            feed_author="",
            feed_published="",
            source_publisher="openai.com",
        )
        try:
            extract_mod._extract_via_defuddle = no_author_defuddle
            result = extract_content(
                url="https://openai.com/index/academy-courses",
                html=_LONG_HTML,
                extraction_config=ctx,
            )
        finally:
            extract_mod._extract_via_defuddle = original

        assert result.extraction_success, f"应成功（机构兜底），error={result.error}"
        assert result.author == "openai.com", "author 应回退到 source_publisher"
        assert result.raw_metadata.get("author_source") == "publisher_fallback", (
            "必须标记 author_source 供 provenance 区分机构归属 vs 个人署名"
        )


# ---------------------------------------------------------------------------
# SC-1 用例 3: 四者皆缺 → fail-closed，不产 raw
# ---------------------------------------------------------------------------


class TestFailClosed:
    """extractor 和 context 都没有足够元数据 → extraction_success=False，不产 raw。"""

    def test_all_missing_fail_closed(self):
        """title/author/published 在抽取器和 context 中均为空 → fail-closed。"""
        from src.akb2.quality.intake import extract as extract_mod

        original = extract_mod._extract_via_defuddle

        def empty_meta_defuddle(url, html=None):
            return {
                "title": "",
                "author": "",
                "published": "",
                "content": _LONG_HTML,
                "word_count": 250,
            }

        # context 同样没有 feed 元数据
        ctx = ExtractionContext(
            source_slug="test",
            source_publisher="",
            feed_title="",
            feed_author="",
            feed_published="",
            url="https://example.com/article",
            content_type="text/html",
            content_hash="",
        )

        try:
            extract_mod._extract_via_defuddle = empty_meta_defuddle
            result = extract_content(
                url="https://example.com/article",
                html=_LONG_HTML,
                extraction_config=ctx,
                failures_path=None,  # 不产签名文件
            )
        finally:
            extract_mod._extract_via_defuddle = original

        assert not result.extraction_success, "四字段皆缺时应 fail-closed"
        assert result.method == ExtractMethod.FAILED
        assert result.error is not None and "incomplete" in result.error

    def test_no_context_no_metadata_fail_closed(self):
        """无 context（extraction_config=None）且抽取器也缺 title → fail-closed。"""
        from src.akb2.quality.intake import extract as extract_mod

        original = extract_mod._extract_via_defuddle

        def missing_title_defuddle(url, html=None):
            return {
                "title": "",
                "author": "Author",
                "published": "2026-06-01",
                "content": _LONG_HTML,
                "word_count": 250,
            }

        try:
            extract_mod._extract_via_defuddle = missing_title_defuddle
            result = extract_content(
                url="https://example.com/article",
                html=_LONG_HTML,
                extraction_config=None,
            )
        finally:
            extract_mod._extract_via_defuddle = original

        assert not result.extraction_success
        assert result.method == ExtractMethod.FAILED


# ---------------------------------------------------------------------------
# SC-1 用例 4: ExtractionContext 字段完整性（≥8 个字段）
# ---------------------------------------------------------------------------


class TestExtractionContextFields:
    """ExtractionContext 必须有 ≥8 个 dataclass 字段。"""

    def test_field_count_at_least_8(self):
        import dataclasses

        fields = dataclasses.fields(ExtractionContext)
        field_names = {f.name for f in fields}
        required = {
            "source_slug",
            "source_publisher",
            "feed_title",
            "feed_author",
            "feed_published",
            "url",
            "content_type",
            "content_hash",
        }
        assert len(fields) >= 8, f"ExtractionContext 只有 {len(fields)} 个字段，不足 8 个"
        missing = required - field_names
        assert not missing, f"ExtractionContext 缺少字段: {missing}"

    def test_instantiation_with_all_fields(self):
        """可以完整实例化，无默认值崩溃。"""
        ctx = ExtractionContext(
            source_slug="my-source",
            source_publisher="My Publisher",
            feed_title="My Title",
            feed_author="My Author",
            feed_published="2026-06-14",
            url="https://example.com/article",
            content_type="text/html",
            content_hash="abc123",
        )
        assert ctx.source_slug == "my-source"
        assert ctx.content_hash == "abc123"


# ---------------------------------------------------------------------------
# SC-1 用例 5: compute_article_id 确定性
# ---------------------------------------------------------------------------


class TestArticleId:
    """compute_article_id 必须是纯函数，相同输入永远返回相同值。"""

    def test_deterministic_same_url(self):
        """相同 URL 两次调用结果相同。"""
        url = "https://example.com/article/loop-engineering"
        assert compute_article_id(url) == compute_article_id(url)

    def test_length_is_12(self):
        """返回恰好 12 个十六进制字符。"""
        result = compute_article_id("https://example.com/test")
        assert len(result) == 12
        assert all(c in "0123456789abcdef" for c in result)

    def test_different_urls_different_ids(self):
        """不同 URL 通常产生不同 ID（碰撞概率极低）。"""
        url_a = "https://example.com/article/a"
        url_b = "https://example.com/article/b"
        assert compute_article_id(url_a) != compute_article_id(url_b)

    def test_matches_manual_sha256(self):
        """与手动计算 sha256 对比，确认算法正确。"""
        url = "https://example.com/verify"
        expected = hashlib.sha256(url.encode()).hexdigest()[:12]
        assert compute_article_id(url) == expected
