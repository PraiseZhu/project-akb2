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
from pathlib import Path

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
        result = extract_content(
            url="https://example.com/article",
            html=sample_article_html,
            failures_path=str(temp_failures_path),
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
        results = batch_extract(urls, html_map=html_map, failures_path=str(temp_failures_path))
        assert len(results) == 2
        assert results["https://example.com/a"].extraction_success
        assert results["https://example.com/b"].extraction_success


# ---------------------------------------------------------------------------
# 具名常量验证
# ---------------------------------------------------------------------------


class TestConstants:
    """具名常量验证。"""

    def test_min_content_length(self):
        assert MIN_CONTENT_LENGTH == 50
