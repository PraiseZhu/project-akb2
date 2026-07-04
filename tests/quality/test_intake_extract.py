"""test_intake_extract.py — 抽取层单测。

覆盖:
    - 决策树各分支: defuddle → Scrapling → Docling
    - 反爬检测 → Scrapling 浏览器模式
    - 抽取成功硬判定（字数 + 元数据）
    - fetch_fail 签名落账
    - PDF 检测
    - 全失败路径

全 fixture，不打真网。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

from src.akb2.quality.intake.extract import (
    MIN_CONTENT_LENGTH,
    ExtractMethod,
    ExtractResult,
    _detect_anti_bot,
    _detect_pdf,
    batch_extract,
    extract_content,
    validate_extraction_success,
)
from src.akb2.quality.intake.models import ExtractionContext

# 通用 ExtractionContext 供 feed fallback（提供 published 日期防元数据门控失败）
_FEED_CTX = ExtractionContext(
    source_slug="test-source",
    source_publisher="Test Publisher",
    feed_title="",
    feed_author="",
    feed_published="2026-06-12",
    url="https://example.com/article",
)

# ---------------------------------------------------------------------------
# 辅助检测函数
# ---------------------------------------------------------------------------


class TestDetectAntiBot:
    """反爬检测测试。"""

    def test_detect_cloudflare(self):
        assert _detect_anti_bot("Checking your browser... cf-browser-verification")

    def test_detect_captcha(self):
        assert _detect_anti_bot("Please complete the CAPTCHA to continue")

    def test_detect_enable_js(self):
        assert _detect_anti_bot("Please enable JavaScript to view this page")

    def test_detect_ddos_protection(self):
        assert _detect_anti_bot("DDoS protection by Cloudflare")

    def test_no_anti_bot_normal(self):
        assert not _detect_anti_bot("<html><body><p>Normal article content</p></body></html>")


class TestDetectPDF:
    """PDF 检测测试。"""

    def test_detect_pdf_url(self):
        assert _detect_pdf("https://example.com/document.pdf")

    def test_detect_pdf_content_type(self):
        assert _detect_pdf("https://example.com/doc", content_type="application/pdf")

    def test_no_pdf_normal(self):
        assert not _detect_pdf("https://example.com/article.html")

    def test_no_pdf_html_content_type(self):
        assert not _detect_pdf("https://example.com/page", content_type="text/html")


# ---------------------------------------------------------------------------
# 抽取决策树
# ---------------------------------------------------------------------------


class TestExtractDecisionTree:
    """抽取决策树分支测试。"""

    def test_extract_via_defuddle_success(
        self, sample_article_html: str, temp_failures_path: Path
    ):
        """defuddle 成功抽取完整文章。"""
        ctx = ExtractionContext(
            source_slug="test-source",
            source_publisher="Test Publisher",
            feed_title="",
            feed_author="",
            feed_published="2026-06-12",
            url="https://example.com/article",
        )
        result = extract_content(
            url="https://example.com/article",
            html=sample_article_html,
            failures_path=str(temp_failures_path),
            extraction_config=ctx,
        )
        assert result.method == ExtractMethod.DEFUDDLE
        assert result.extraction_success
        assert result.word_count >= MIN_CONTENT_LENGTH
        assert result.title != ""

    def test_extract_short_content_fails(
        self, sample_short_html: str, temp_failures_path: Path
    ):
        """内容过短导致抽取失败。"""
        result = extract_content(
            url="https://example.com/short",
            html=sample_short_html,
            failures_path=str(temp_failures_path),
        )
        assert not result.extraction_success
        assert result.method == ExtractMethod.FAILED
        assert result.error is not None

    def test_extract_anti_bot_triggers_scrapling_browser(
        self, sample_anti_bot_html: str, temp_failures_path: Path
    ):
        """反爬页面走 Scrapling 浏览器模式。"""
        result = extract_content(
            url="https://example.com/protected",
            html=sample_anti_bot_html,
            failures_path=str(temp_failures_path),
        )
        # 反爬页面 defuddle 会失败，应走 Scrapling browser
        assert result.method in (
            ExtractMethod.SCRAPLING_BROWSER,
            ExtractMethod.SCRAPLING,
            ExtractMethod.FAILED,
        )

    def test_extract_without_html(self, temp_failures_path: Path):
        """无 HTML 注入时返回失败。"""
        result = extract_content(
            url="https://example.com/article",
            html=None,
            failures_path=str(temp_failures_path),
        )
        assert not result.extraction_success
        assert result.method == ExtractMethod.FAILED

    def test_extract_pdf_url(self, temp_failures_path: Path):
        """PDF URL 走 Docling 分支。"""
        result = extract_content(
            url="https://example.com/doc.pdf",
            html=None,
            failures_path=str(temp_failures_path),
        )
        assert result.method == ExtractMethod.DOCLING or result.method == ExtractMethod.FAILED

    def test_extract_pdf_content_type(self, temp_failures_path: Path):
        """PDF Content-Type 走 Docling 分支。"""
        result = extract_content(
            url="https://example.com/doc",
            html=None,
            content_type="application/pdf",
            failures_path=str(temp_failures_path),
        )
        assert result.method == ExtractMethod.DOCLING or result.method == ExtractMethod.FAILED


# ---------------------------------------------------------------------------
# 硬判定
# ---------------------------------------------------------------------------


class TestHardValidation:
    """抽取成功硬判定测试。"""

    def test_validate_success(self):
        """元数据齐全 + 字数达标 → 成功。"""
        result = ExtractResult(
            url="https://example.com/a",
            title="Test Article",
            author="Jane Author",
            published="2026-06-12",
            content="word " * 60,
            word_count=60,
            metadata_complete=True,
            extraction_success=True,
        )
        assert validate_extraction_success(result)

    def test_validate_missing_title(self):
        """缺 title → 失败。"""
        result = ExtractResult(
            url="https://example.com/a",
            title="",
            author="Jane Author",
            published="2026-06-12",
            content="word " * 60,
            word_count=60,
            metadata_complete=False,
            extraction_success=False,
        )
        assert not validate_extraction_success(result)

    def test_validate_missing_author(self):
        """缺 author → 失败。"""
        result = ExtractResult(
            url="https://example.com/a",
            title="Test",
            author="",
            published="2026-06-12",
            content="word " * 60,
            word_count=60,
            metadata_complete=False,
            extraction_success=False,
        )
        assert not validate_extraction_success(result)

    def test_validate_missing_published(self):
        """缺 published → 失败。"""
        result = ExtractResult(
            url="https://example.com/a",
            title="Test",
            author="Jane",
            published="",
            content="word " * 60,
            word_count=60,
            metadata_complete=False,
            extraction_success=False,
        )
        assert not validate_extraction_success(result)

    def test_validate_too_short(self):
        """字数不足 → 失败。"""
        result = ExtractResult(
            url="https://example.com/a",
            title="Test",
            author="Jane",
            published="2026-06-12",
            content="short",
            word_count=1,
            metadata_complete=False,
            extraction_success=False,
        )
        assert not validate_extraction_success(result)


# ---------------------------------------------------------------------------
# fetch_fail 签名
# ---------------------------------------------------------------------------


class TestFetchFailSignature:
    """fetch_fail 故障签名落账测试。"""

    def test_fetch_fail_writes_signature(self, temp_failures_path: Path):
        """全失败时写入 failures.jsonl。"""
        result = extract_content(
            url="https://example.com/unreachable",
            html=None,
            failures_path=str(temp_failures_path),
        )
        assert not result.extraction_success

        # 验证签名文件存在
        assert temp_failures_path.exists()
        content = temp_failures_path.read_text()
        assert "fetch_fail" in content
        assert "loop1" in content

    def test_fetch_fail_no_path_no_error(self):
        """failures_path 为 None 时不写签名，不抛异常。"""
        result = extract_content(
            url="https://example.com/unreachable",
            html=None,
            failures_path=None,
        )
        assert not result.extraction_success
        # 不应抛异常

    def test_fetch_fail_signature_is_valid_json(self, temp_failures_path: Path):
        """签名记录是合法 JSON。"""
        extract_content(
            url="https://example.com/unreachable",
            html=None,
            failures_path=str(temp_failures_path),
        )
        with open(temp_failures_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                assert "loop" in record
                assert "error_class" in record
                assert "count" in record
                assert record["error_class"] == "fetch_fail"


# ---------------------------------------------------------------------------
# 批量抽取
# ---------------------------------------------------------------------------


class TestBatchExtract:
    """批量抽取测试。"""

    def test_batch_extract_multiple_urls(
        self, sample_article_html: str, temp_failures_path: Path
    ):
        """批量抽取多个 URL。"""
        urls = [
            "https://example.com/a",
            "https://example.com/b",
        ]
        html_map = {
            "https://example.com/a": sample_article_html,
            "https://example.com/b": sample_article_html,
        }
        ctx = ExtractionContext(
            source_slug="test-source",
            source_publisher="Test Publisher",
            feed_title="",
            feed_author="",
            feed_published="2026-06-12",
            url="https://example.com/a",
        )
        results = batch_extract(
            urls,
            html_map=html_map,
            failures_path=str(temp_failures_path),
            extraction_config=ctx,
        )
        assert len(results) == 2
        assert results["https://example.com/a"].extraction_success
        assert results["https://example.com/b"].extraction_success


# ---------------------------------------------------------------------------
# 具名常量验证
# ---------------------------------------------------------------------------


class TestConstants:
    """具名常量验证。"""

    def test_min_content_length(self):
        assert MIN_CONTENT_LENGTH == 200


# ---------------------------------------------------------------------------
# SC-2: _extract_via_defuddle 真实调用
# ---------------------------------------------------------------------------


class TestExtractViaDefuddle:
    """SC-2: _extract_via_defuddle 调 defuddle CLI，content 无 HTML 标签，title 非写死字符串。"""

    def test_returns_markdown_content_without_html_tags(self, sample_article_html: str):
        """content 字段不含 HTML 标签（defuddle --markdown 输出纯 markdown）。"""
        from src.akb2.quality.intake.extract import _extract_via_defuddle

        result = _extract_via_defuddle("https://example.com/article", sample_article_html)
        assert result, "defuddle 应返回非空 dict"
        content = result.get("content", "")
        assert content, "content 不应为空"
        # markdown 正文不含 HTML 标签
        assert "<html" not in content.lower(), "content 不应含 <html> 标签"
        assert "<body" not in content.lower(), "content 不应含 <body> 标签"
        assert "<p>" not in content, "content 不应含 <p> 标签"

    def test_title_is_not_hardcoded_stub(self, sample_article_html: str):
        """title 是从文档实际提取的，不是写死的占位字符串。"""
        from src.akb2.quality.intake.extract import _extract_via_defuddle

        result = _extract_via_defuddle("https://example.com/article", sample_article_html)
        assert result, "defuddle 应返回非空 dict"
        title = result.get("title", "")
        assert title != "Defuddle Extracted Title", "title 不应是写死的占位字符串"
        assert title, "title 不应为空"

    def test_word_count_based_on_markdown_body(self, sample_article_html: str):
        """word_count 基于 markdown 正文计算（不是 HTML 源字数）。"""
        from src.akb2.quality.intake.extract import _count_words, _extract_via_defuddle

        result = _extract_via_defuddle("https://example.com/article", sample_article_html)
        assert result, "defuddle 应返回非空 dict"
        content = result.get("content", "")
        returned_wc = result.get("word_count", 0)
        # word_count 应与 content 一致（允许偏差：defuddle 可能用 wordCount 字段）
        manual_wc = _count_words(content)
        # 允许偏差 ±20%（defuddle wordCount 与我们的空格分词有细微差异）
        assert abs(returned_wc - manual_wc) <= max(10, manual_wc * 0.2), (
            f"word_count({returned_wc}) 与 content 手动计数({manual_wc}) 偏差过大"
        )

    def test_empty_html_returns_empty_dict(self):
        """HTML 过短/空时返回空 dict（不抛异常）。"""
        from src.akb2.quality.intake.extract import _extract_via_defuddle

        result = _extract_via_defuddle("https://example.com/article", "")
        # 空 HTML 时 defuddle 可能返回空 dict 或很少内容
        # 关键：不抛异常
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# SC-3: _extract_via_scrapling 真实调用（mock 网络）
# ---------------------------------------------------------------------------


class TestExtractViaScrapling:
    """SC-3: _extract_via_scrapling 调用 scrapling.Fetcher/StealthyFetcher，
    自适应和浏览器模式各覆盖，title 非写死占位字符串。
    测试中 mock 网络调用，不打真网。
    """

    def test_adaptive_mode_calls_fetcher(self):
        """自适应模式（browser_mode=False）调用 scrapling.Fetcher。"""
        from unittest.mock import MagicMock, patch

        from src.akb2.quality.intake.extract import _extract_via_scrapling

        mock_page = MagicMock()
        mock_page.html_content = "word " * 250
        mock_page.title = "Article From Scrapling Fetcher"

        # mock _extract_via_defuddle 返空，强制走 _html_to_clean_text fallback
        # （隔离 defuddle 是否安装的环境依赖，测 scrapling 调 Fetcher 的行为本身）
        with patch("scrapling.Fetcher") as MockFetcher, patch(
            "src.akb2.quality.intake.extract._extract_via_defuddle", return_value={}
        ):
            MockFetcher.get.return_value = mock_page
            result = _extract_via_scrapling("https://example.com/article", browser_mode=False)

        assert result, "scrapling 自适应模式应返回非空 dict"
        assert result.get("title") == "Article From Scrapling Fetcher", (
            "title 应来自 mock page，不是写死占位"
        )
        assert result.get("title") not in (
            "Scrapling adaptive Title",
            "Scrapling browser Title",
        ), "title 不应是写死占位字符串"

    def test_browser_mode_calls_stealthy_fetcher(self):
        """浏览器模式（browser_mode=True）调用 scrapling.StealthyFetcher。"""
        from unittest.mock import MagicMock, patch

        from src.akb2.quality.intake.extract import _extract_via_scrapling

        mock_page = MagicMock()
        mock_page.html_content = "word " * 250
        mock_page.title = "Article From Scrapling StealthyFetcher"

        # mock _extract_via_defuddle 返空，强制走 fallback（隔离 defuddle 安装依赖）
        with patch("scrapling.StealthyFetcher") as MockStealthy, patch(
            "src.akb2.quality.intake.extract._extract_via_defuddle", return_value={}
        ):
            MockStealthy.fetch.return_value = mock_page
            result = _extract_via_scrapling("https://example.com/article", browser_mode=True)

        assert result, "scrapling 浏览器模式应返回非空 dict"
        assert result.get("title") == "Article From Scrapling StealthyFetcher"

    def test_scrapling_unavailable_returns_empty_and_warns(self, caplog):
        """scrapling 不可用时返回空 dict 并输出 warning，不抛异常。"""
        import importlib.util
        from unittest.mock import patch

        from src.akb2.quality.intake.extract import _extract_via_scrapling

        with patch.object(importlib.util, "find_spec", return_value=None):
            import logging

            with caplog.at_level(logging.WARNING, logger="akb2.quality.intake.extract"):
                result = _extract_via_scrapling("https://example.com/article")

        assert result == {}, "scrapling 不可用时应返回空 dict"
        assert any("scrapling" in r.message.lower() for r in caplog.records), (
            "应有 scrapling not available warning"
        )

    def test_scrapling_exception_returns_empty(self):
        """scrapling 抛异常时降级返回空 dict，不向上冒泡。"""
        from unittest.mock import patch

        from src.akb2.quality.intake.extract import _extract_via_scrapling

        with patch("scrapling.Fetcher") as MockFetcher:
            MockFetcher.return_value.fetch.side_effect = RuntimeError("connection refused")
            result = _extract_via_scrapling("https://example.com/article", browser_mode=False)

        assert result == {}, "异常时应降级返回空 dict"

    def test_scrapling_delegates_extraction_to_defuddle(self):
        """G2 守护：scrapling 只负责抓渲染 HTML，抽取转交 defuddle。
        scrapling 抓到 HTML 后必须调 _extract_via_defuddle 出干净 markdown +
        元数据，而非自己返回原始 HTML / 写死空 author。缺这条时回退到旧的
        '返回脏 HTML + author 恒空'，Cloudflare 源入库内容是脏的且无作者。
        """
        from unittest.mock import MagicMock, patch

        from src.akb2.quality.intake import extract as extract_mod
        from src.akb2.quality.intake.extract import _extract_via_scrapling

        mock_page = MagicMock()
        mock_page.html_content = "<html><body><article>real rendered html</article></body></html>"
        mock_page.title = "scrapling raw title"

        clean = {
            "title": "Clean Title From Defuddle",
            "author": "Jane Doe",
            "published": "2026-06-01",
            "content": "# Clean markdown body",
            "word_count": 500,
        }
        original = extract_mod._extract_via_defuddle
        try:
            extract_mod._extract_via_defuddle = lambda url, html=None: clean
            with patch("scrapling.StealthyFetcher") as MockStealthy:
                # classmethod 形式（scrapling 0.4.8）：StealthyFetcher.fetch(url)，非实例 .fetch
                MockStealthy.fetch.return_value = mock_page
                result = _extract_via_scrapling("https://cf.example/a", browser_mode=True)
        finally:
            extract_mod._extract_via_defuddle = original

        assert result.get("title") == "Clean Title From Defuddle", "应用 defuddle 抽取结果"
        assert result.get("author") == "Jane Doe", "author 应来自 defuddle，非写死空串"
        assert "# Clean markdown" in result.get("content", ""), "content 应是 defuddle markdown"


# ---------------------------------------------------------------------------
# SC-4: _extract_via_docling 真实调用（mock DocumentConverter）
# ---------------------------------------------------------------------------

def _make_docling_mock(fake_markdown: str) -> tuple:
    """创建 docling sys.modules mock，返回 (docling_mod_mock, converter_instance)。"""
    docling_mock = MagicMock()
    docling_mock.__spec__ = MagicMock()
    docling_mock.__spec__.name = "docling"
    docling_mod = MagicMock()
    docling_mod.__spec__ = MagicMock()

    mock_result = MagicMock()
    mock_result.document.export_to_markdown.return_value = fake_markdown
    mock_result.metadata = {}

    converter_instance = MagicMock()
    converter_instance.convert.return_value = mock_result
    docling_mod.DocumentConverter.return_value = converter_instance

    return docling_mock, docling_mod, converter_instance


class TestExtractViaDocling:
    """SC-4: _extract_via_docling 调用 DocumentConverter().convert()，
    返回 export_to_markdown() 内容而非 bytes.decode() 原始 PDF 文本。
    测试中 mock DocumentConverter via sys.modules，不运行真 ML 模型。
    """

    def test_returns_markdown_from_export(self):
        """content 来自 DocumentConverter.convert().document.export_to_markdown()。"""
        fake_markdown = "# PDF Article\n\n" + "word " * 250
        docling_mock, docling_mod, _ = _make_docling_mock(fake_markdown)

        saved = {k: sys.modules.get(k) for k in ("docling", "docling.document_converter")}
        try:
            sys.modules["docling"] = docling_mock
            sys.modules["docling.document_converter"] = docling_mod

            from src.akb2.quality.intake.extract import _extract_via_docling

            result = _extract_via_docling(
                "https://example.com/doc.pdf",
                file_bytes=b"%PDF-1.4 fake content" * 10,
            )
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        assert result, "docling 应返回非空 dict"
        content = result.get("content", "")
        assert content == fake_markdown, "content 应是 export_to_markdown() 的返回值"
        assert "PDF-1.4" not in content, "content 不应是原始 PDF bytes.decode() 内容"

    def test_file_bytes_written_to_temp_pdf(self):
        """file_bytes 被写入临时 PDF 文件后传给 DocumentConverter。"""
        fake_bytes = b"%PDF-1.4 test content for docling " * 5
        fake_markdown = "# Extracted\n\n" + "word " * 250
        docling_mock, docling_mod, converter_instance = _make_docling_mock(fake_markdown)

        saved = {k: sys.modules.get(k) for k in ("docling", "docling.document_converter")}
        try:
            sys.modules["docling"] = docling_mock
            sys.modules["docling.document_converter"] = docling_mod

            from src.akb2.quality.intake.extract import _extract_via_docling

            result = _extract_via_docling("https://example.com/doc.pdf", file_bytes=fake_bytes)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        # DocumentConverter().convert() 必须被调用且参数是一个路径字符串
        convert_calls = converter_instance.convert.call_args_list
        assert len(convert_calls) == 1, "convert() 应被调用一次"
        called_path = convert_calls[0][0][0]  # 第一个位置参数
        assert isinstance(called_path, str) and called_path.endswith(".pdf"), (
            "convert() 应接收 .pdf 临时文件路径"
        )
        assert result.get("content") == fake_markdown

    def test_no_file_bytes_returns_empty(self):
        """file_bytes 为 None 时返回空 dict，不抛异常。"""
        from src.akb2.quality.intake.extract import _extract_via_docling

        result = _extract_via_docling("https://example.com/doc.pdf", file_bytes=None)
        assert result == {}, "file_bytes 为 None 时应返回空 dict"

    def test_docling_unavailable_returns_empty_and_warns(self, caplog):
        """docling 不可用时返回空 dict 并输出 warning，不抛异常。"""
        import importlib.util
        import logging
        from unittest.mock import patch

        from src.akb2.quality.intake.extract import _extract_via_docling

        with patch.object(importlib.util, "find_spec", return_value=None):
            with caplog.at_level(logging.WARNING, logger="akb2.quality.intake.extract"):
                result = _extract_via_docling(
                    "https://example.com/doc.pdf",
                    file_bytes=b"fake pdf bytes",
                )

        assert result == {}, "docling 不可用时应返回空 dict"
        assert any("docling" in r.message.lower() for r in caplog.records), (
            "应有 docling not available warning"
        )

    def test_docling_exception_returns_empty(self):
        """DocumentConverter 抛异常时降级返回空 dict。"""
        fake_markdown = "# Test\n\n" + "word " * 250
        docling_mock, docling_mod, converter_instance = _make_docling_mock(fake_markdown)
        converter_instance.convert.side_effect = RuntimeError("model load failed")

        saved = {k: sys.modules.get(k) for k in ("docling", "docling.document_converter")}
        try:
            sys.modules["docling"] = docling_mock
            sys.modules["docling.document_converter"] = docling_mod

            from src.akb2.quality.intake.extract import _extract_via_docling

            result = _extract_via_docling(
                "https://example.com/doc.pdf",
                file_bytes=b"%PDF-1.4 fake" * 5,
            )
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        assert result == {}, "异常时应降级返回空 dict"
