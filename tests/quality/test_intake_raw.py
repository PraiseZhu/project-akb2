"""test_intake_raw.py — raw 归档层单测。

覆盖:
    - frontmatter 9 字段齐全 → 成功产出 raw 文件
    - frontmatter 缺失字段 → 拒收，不产出文件
    - 锚点 §A-§Z 注入 + assert 命中
    - 必需锚点缺失 → fail-closed，不写文件
    - 锚点目录表
    - 文件名格式 <slug>--<article_id>--raw--<YYYY-MM-DD>.md（F3 唯一化）
    - failure 签名含 details/sample_error 字段（P3 排障）
    - archive_from_extract_result 支持 source_metadata 参数
    - 原文不翻译

全 fixture，不打真网。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.akb2.quality.intake.models import ExtractionContext, compute_article_id
from src.akb2.quality.intake.raw_archiver import (
    ANCHOR_DEFINITIONS,
    FRONTMATTER_FIELDS,
    REQUIRED_ANCHORS,
    _assert_anchors,
    _build_frontmatter,
    _format_frontmatter_yaml,
    _inject_anchors,
    _validate_frontmatter,
    archive_from_extract_result,
    archive_raw,
)

# ---------------------------------------------------------------------------
# frontmatter 构建与验证
# ---------------------------------------------------------------------------


class TestFrontmatter:
    """frontmatter 构建与验证测试。"""

    def test_build_frontmatter_all_fields(self):
        """构建 9 字段 frontmatter。"""
        fm = _build_frontmatter(
            slug="test-blog",
            url="https://example.com/article",
            title="Test Article",
            author="Jane Author",
            publisher="Example Blog",
            published_at="2026-06-12",
            fetched_via="defuddle",
        )
        assert fm["artifact_type"] == "raw"
        assert fm["source_slug"] == "test-blog"
        assert fm["source_url"] == "https://example.com/article"
        assert fm["source_title"] == "Test Article"
        assert fm["source_authors"] == "Jane Author"
        assert fm["source_publisher"] == "Example Blog"
        assert fm["source_published_at"] == "2026-06-12"
        assert fm["fetched_at"] != ""  # dynamic
        assert fm["fetched_via"] == "defuddle"

    def test_validate_frontmatter_all_present(self):
        """9 字段齐全 → 验证通过。"""
        fm = {
            "artifact_type": "raw",
            "source_slug": "test",
            "source_url": "https://x.com",
            "source_title": "T",
            "source_authors": "A",
            "source_publisher": "P",
            "source_published_at": "2026-01-01",
            "fetched_at": "2026-01-01T00:00:00Z",
            "fetched_via": "defuddle",
        }
        missing = _validate_frontmatter(fm)
        assert missing == []

    def test_validate_frontmatter_missing_fields(self):
        """缺失字段被检测。"""
        fm = {
            "artifact_type": "raw",
            "source_slug": "test",
            # missing many fields
        }
        missing = _validate_frontmatter(fm)
        assert len(missing) > 0
        assert "source_url" in missing
        assert "source_title" in missing

    def test_validate_frontmatter_empty_fields(self):
        """空字符串字段被视为缺失。"""
        fm = {
            "artifact_type": "raw",
            "source_slug": "test",
            "source_url": "",
            "source_title": "T",
            "source_authors": "A",
            "source_publisher": "P",
            "source_published_at": "2026-01-01",
            "fetched_at": "2026-01-01T00:00:00Z",
            "fetched_via": "defuddle",
        }
        missing = _validate_frontmatter(fm)
        assert "source_url" in missing

    def test_format_frontmatter_yaml(self):
        """YAML frontmatter 格式正确。"""
        fm = {
            "artifact_type": "raw",
            "source_slug": "test",
            "source_url": "https://x.com",
            "source_title": "T",
            "source_authors": "A",
            "source_publisher": "P",
            "source_published_at": "2026-01-01",
            "fetched_at": "2026-01-01T00:00:00Z",
            "fetched_via": "defuddle",
        }
        yaml_str = _format_frontmatter_yaml(fm)
        assert yaml_str.startswith("---")
        assert yaml_str.endswith("---")
        assert "artifact_type: raw" in yaml_str
        assert "source_slug: test" in yaml_str

    def test_frontmatter_fields_count(self):
        """确认 frontmatter 字段数 = 9。"""
        assert len(FRONTMATTER_FIELDS) == 9


# ---------------------------------------------------------------------------
# 锚点注入
# ---------------------------------------------------------------------------


class TestAnchorInjection:
    """锚点注入测试。"""

    def test_inject_anchors_sufficient_paragraphs(self, sample_article_md: str):
        """段落足够时注入全部锚点（优先级优先必需锚点）。"""
        content, toc_entries, hit_counts = _inject_anchors(sample_article_md)
        # 至少必需锚点全部命中（段落足够时 REQUIRED_ANCHORS 都被注入）
        for key in REQUIRED_ANCHORS:
            assert hit_counts.get(key, 0) > 0, f"Required anchor §{key} not hit"
        # 锚点目录不为空
        assert len(toc_entries) > 0

    def test_inject_anchors_short_content(self, sample_short_md: str):
        """短内容只注入能注入的锚点。"""
        content, toc_entries, hit_counts = _inject_anchors(sample_short_md)
        # 短内容只有 1 段，最多注入 §A 和 §Z
        assert len(hit_counts) <= 2

    def test_inject_anchors_empty_content(self):
        """空内容不报错。"""
        content, toc_entries, hit_counts = _inject_anchors("")
        assert content == ""
        assert toc_entries == []

    def test_assert_anchors_all_hit(self):
        """全部必需锚点命中。"""
        hit_counts = {"A": 1, "E": 1, "H": 1, "M": 1, "Z": 1}
        missing = _assert_anchors(hit_counts, REQUIRED_ANCHORS)
        assert missing == []

    def test_assert_anchors_missing(self):
        """锚点缺失被检测。"""
        hit_counts = {"A": 1, "Z": 1}  # missing E, H, M
        missing = _assert_anchors(hit_counts, REQUIRED_ANCHORS)
        assert "E" in missing
        assert "H" in missing
        assert "M" in missing

    def test_assert_anchors_all_missing(self):
        """全部锚点缺失。"""
        hit_counts = {}
        missing = _assert_anchors(hit_counts, REQUIRED_ANCHORS)
        assert len(missing) == len(REQUIRED_ANCHORS)

    def test_anchor_definitions_has_all_letters(self):
        """ANCHOR_DEFINITIONS 包含 §A-§Z 全部 26 个。"""
        assert len(ANCHOR_DEFINITIONS) == 26
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            assert letter in ANCHOR_DEFINITIONS, f"Missing anchor §{letter}"


# ---------------------------------------------------------------------------
# 主归档流程
# ---------------------------------------------------------------------------


class TestArchiveRaw:
    """主归档流程测试。"""

    def test_archive_success(
        self, sample_article_md: str, temp_output_dir: Path, temp_failures_path: Path
    ):
        """正常归档产出 raw 文件。"""
        result = archive_raw(
            slug="test-blog",
            url="https://example.com/article",
            title="Loop Engineering Article",
            author="Jane Author",
            content=sample_article_md,
            published_at="2026-06-12",
            publisher="Example Blog",
            fetched_via="defuddle",
            output_dir=temp_output_dir,
            failures_path=str(temp_failures_path),
            date_str="2026-06-13",
        )
        assert result.success
        assert result.output_path is not None
        assert result.missing_anchors == []
        assert result.missing_fields == []

        # 验证文件存在
        out_path = Path(result.output_path)
        assert out_path.exists()

        # 验证文件内容
        raw_content = out_path.read_text(encoding="utf-8")
        assert "---" in raw_content  # frontmatter
        assert "artifact_type: raw" in raw_content
        assert "source_slug: test-blog" in raw_content
        assert "## 锚点目录" in raw_content  # 锚点目录表
        assert "§A" in raw_content  # 锚点注入
        assert "§Z" in raw_content

    def test_archive_filename_format(
        self, sample_article_md: str, temp_output_dir: Path
    ):
        """文件名格式为 <slug>--<article_id>--raw--<YYYY-MM-DD>.md（F3 唯一化）。"""
        url = "https://example.com/article"
        expected_article_id = compute_article_id(url)
        result = archive_raw(
            slug="test-blog",
            url=url,
            title="Test",
            author="A",
            content=sample_article_md,
            published_at="2026-06-12",
            publisher="Example Blog",
            output_dir=temp_output_dir,
            date_str="2026-06-13",
        )
        assert result.success
        expected_name = f"test-blog--{expected_article_id}--raw--2026-06-13.md"
        assert Path(result.output_path).name == expected_name

    def test_archive_filename_contains_article_id(
        self, sample_article_md: str, temp_output_dir: Path
    ):
        """文件名包含 article_id 短码，同源同日不同 URL 产生不同文件名。"""
        url_a = "https://example.com/article-a"
        url_b = "https://example.com/article-b"
        result_a = archive_raw(
            slug="test-blog",
            url=url_a,
            title="Article A",
            author="A",
            content=sample_article_md,
            published_at="2026-06-12",
            publisher="Example Blog",
            output_dir=temp_output_dir,
            date_str="2026-06-13",
        )
        result_b = archive_raw(
            slug="test-blog",
            url=url_b,
            title="Article B",
            author="A",
            content=sample_article_md,
            published_at="2026-06-12",
            publisher="Example Blog",
            output_dir=temp_output_dir,
            date_str="2026-06-13",
        )
        assert result_a.success
        assert result_b.success
        # 两个不同 URL 产生不同文件名（F3 唯一化）
        assert Path(result_a.output_path).name != Path(result_b.output_path).name
        # article_id 与 compute_article_id 一致
        assert compute_article_id(url_a) in Path(result_a.output_path).name
        assert compute_article_id(url_b) in Path(result_b.output_path).name

    def test_archive_explicit_article_id(
        self, sample_article_md: str, temp_output_dir: Path
    ):
        """显式传入 article_id 时使用该值。"""
        result = archive_raw(
            slug="test-blog",
            url="https://example.com/article",
            title="Test",
            author="A",
            content=sample_article_md,
            published_at="2026-06-12",
            publisher="Example Blog",
            output_dir=temp_output_dir,
            date_str="2026-06-13",
            article_id="abc123def456",
        )
        assert result.success
        assert Path(result.output_path).name == "test-blog--abc123def456--raw--2026-06-13.md"

    def test_archive_missing_frontmatter_fields(
        self, sample_article_md: str, temp_output_dir: Path, temp_failures_path: Path
    ):
        """frontmatter 字段缺失 → 拒收，不产出文件。"""
        result = archive_raw(
            slug="",  # empty slug → missing source_slug
            url="",  # empty url → missing source_url
            title="",
            author="",
            content=sample_article_md,
            publisher="",
            published_at="",
            output_dir=temp_output_dir,
            failures_path=str(temp_failures_path),
        )
        assert not result.success
        assert result.missing_fields
        # 不应产出文件
        out_files = list(temp_output_dir.glob("*.md"))
        assert len(out_files) == 0

    def test_archive_missing_anchors_short_content(
        self, sample_short_md: str, temp_output_dir: Path, temp_failures_path: Path
    ):
        """锚点不足 → fail-closed，不产出文件。"""
        result = archive_raw(
            slug="test-blog",
            url="https://example.com/short",
            title="Short Article",
            author="Jane",
            content=sample_short_md,
            published_at="2026-06-12",
            publisher="Example Blog",
            output_dir=temp_output_dir,
            failures_path=str(temp_failures_path),
        )
        assert not result.success
        assert result.missing_anchors
        # 不应产出文件
        out_files = list(temp_output_dir.glob("*.md"))
        assert len(out_files) == 0

    def test_archive_with_custom_anchors(
        self, sample_article_md: str, temp_output_dir: Path
    ):
        """自定义锚点集。"""
        custom_anchors = {
            "A": "§A_START",
            "Z": "§Z_END",
        }
        result = archive_raw(
            slug="test-blog",
            url="https://example.com/article",
            title="Test",
            author="A",
            content=sample_article_md,
            published_at="2026-06-12",
            publisher="Example Blog",
            output_dir=temp_output_dir,
            anchors=custom_anchors,
            required_anchors=("A", "Z"),
        )
        assert result.success
        raw_content = Path(result.output_path).read_text(encoding="utf-8")
        assert "§A_START" in raw_content
        assert "§Z_END" in raw_content

    def test_archive_preserves_original_content(
        self, sample_article_md: str, temp_output_dir: Path
    ):
        """原文内容保留（不翻译）。"""
        result = archive_raw(
            slug="test-blog",
            url="https://example.com/article",
            title="Test",
            author="A",
            content=sample_article_md,
            published_at="2026-06-12",
            publisher="Example Blog",
            output_dir=temp_output_dir,
        )
        assert result.success
        raw_content = Path(result.output_path).read_text(encoding="utf-8")
        assert "loop engineering" in raw_content.lower()

    def test_archive_failure_signature(
        self, sample_short_md: str, temp_output_dir: Path, temp_failures_path: Path
    ):
        """归档失败时写入 anchor_mismatch 签名。"""
        archive_raw(
            slug="test-blog",
            url="https://example.com/short",
            title="Short",
            author="Jane",
            content=sample_short_md,
            published_at="2026-06-12",
            publisher="Example Blog",
            output_dir=temp_output_dir,
            failures_path=str(temp_failures_path),
        )
        assert temp_failures_path.exists()
        content = temp_failures_path.read_text()
        assert "anchor_mismatch" in content

    def test_archive_without_failures_path_no_error(
        self, sample_short_md: str, temp_output_dir: Path
    ):
        """没有 failures_path 时失败不抛异常。"""
        result = archive_raw(
            slug="test-blog",
            url="https://example.com/short",
            title="Short",
            author="Jane",
            content=sample_short_md,
            published_at="2026-06-12",
            publisher="Example Blog",
            output_dir=temp_output_dir,
            failures_path=None,
        )
        assert not result.success

    def test_failure_signature_has_details_field(
        self, sample_short_md: str, temp_output_dir: Path, temp_failures_path: Path
    ):
        """failure 签名含 details 字段（P3 排障）— 锚点缺失场景。"""
        archive_raw(
            slug="test-blog",
            url="https://example.com/short",
            title="Short",
            author="Jane",
            content=sample_short_md,
            published_at="2026-06-12",
            publisher="Example Blog",
            output_dir=temp_output_dir,
            failures_path=str(temp_failures_path),
        )
        assert temp_failures_path.exists()
        lines = [
            json.loads(line)
            for line in temp_failures_path.read_text().splitlines()
            if line.strip()
        ]
        assert len(lines) >= 1
        record = lines[0]
        assert "details" in record, "failure 签名缺少 details 字段"
        assert "sample_error" in record, "failure 签名缺少 sample_error 字段"
        # details 含有上下文信息
        assert record["details"] != ""

    def test_failure_signature_has_details_field_frontmatter(
        self, sample_article_md: str, temp_output_dir: Path, temp_failures_path: Path
    ):
        """failure 签名含 details 字段（P3 排障）— frontmatter 缺失场景。"""
        archive_raw(
            slug="",  # empty slug → missing source_slug
            url="",   # empty url → missing source_url
            title="",
            author="",
            content=sample_article_md,
            publisher="",
            published_at="",
            output_dir=temp_output_dir,
            failures_path=str(temp_failures_path),
        )
        assert temp_failures_path.exists()
        lines = [
            json.loads(line)
            for line in temp_failures_path.read_text().splitlines()
            if line.strip()
        ]
        assert len(lines) >= 1
        record = lines[0]
        assert "details" in record, "failure 签名缺少 details 字段"
        assert "sample_error" in record, "failure 签名缺少 sample_error 字段"
        # details 包含缺失字段信息
        assert "frontmatter" in record["details"].lower() or "missing" in record["details"].lower()


# ---------------------------------------------------------------------------
# archive_from_extract_result 测试
# ---------------------------------------------------------------------------


class TestArchiveFromExtractResult:
    """archive_from_extract_result 与 source_metadata 参数测试。"""

    def _make_extract_result(
        self,
        url: str = "https://example.com/article",
        title: str = "Test Article",
        author: str = "Jane Author",
        content: str = "",
        published: str = "2026-06-12",
        source_slug: str = "test-blog",
        method_value: str = "defuddle",
    ):
        """构造最小兼容 ExtractResult duck-type 对象。"""
        class MockMethod:
            value = method_value

        class MockExtractResult:
            pass

        r = MockExtractResult()
        r.url = url
        r.title = title
        r.author = author
        r.content = content
        r.published = published
        r.source_slug = source_slug
        r.method = MockMethod()
        return r

    def test_archive_from_extract_result_basic(
        self, sample_article_md: str, temp_output_dir: Path
    ):
        """无 source_metadata 时基本归档成功。"""
        er = self._make_extract_result(content=sample_article_md)
        result = archive_from_extract_result(
            extract_result=er,
            output_dir=temp_output_dir,
            publisher="Example Blog",
            date_str="2026-06-13",
        )
        assert result.success
        assert result.output_path is not None

    def test_archive_from_extract_result_with_source_metadata(
        self, sample_article_md: str, temp_output_dir: Path
    ):
        """source_metadata 传入时，frontmatter 使用 metadata 中的 publisher/slug。"""
        er = self._make_extract_result(
            content=sample_article_md,
            author="",       # 空 author，由 metadata 填补
            published="",    # 空 published，由 metadata 填补
        )
        metadata = ExtractionContext(
            source_slug="metadata-slug",
            source_publisher="Metadata Publisher",
            feed_title="Feed Title",
            feed_author="Feed Author",
            feed_published="2026-05-20",
            url="https://example.com/article",
            content_type="text/html",
            content_hash="abc123def456xyz",
        )
        result = archive_from_extract_result(
            extract_result=er,
            output_dir=temp_output_dir,
            date_str="2026-06-13",
            source_metadata=metadata,
        )
        assert result.success
        assert result.output_path is not None
        raw_content = Path(result.output_path).read_text(encoding="utf-8")
        # publisher 来自 metadata
        assert "Metadata Publisher" in raw_content
        # slug 来自 metadata
        assert "metadata-slug" in raw_content
        # filename 含 article_id（URL-based sha256[:12]）
        assert "--raw--" in Path(result.output_path).name

    def test_archive_from_extract_result_metadata_author_fallback(
        self, sample_article_md: str, temp_output_dir: Path
    ):
        """extract_result.author 为空时从 source_metadata.feed_author 取值。"""
        er = self._make_extract_result(
            content=sample_article_md,
            author="",
        )
        metadata = ExtractionContext(
            source_slug="test-blog",
            source_publisher="Test Publisher",
            feed_title="",
            feed_author="Feed Author from Metadata",
            feed_published="",
            url="https://example.com/article",
        )
        result = archive_from_extract_result(
            extract_result=er,
            output_dir=temp_output_dir,
            date_str="2026-06-13",
            source_metadata=metadata,
        )
        assert result.success
        raw_content = Path(result.output_path).read_text(encoding="utf-8")
        assert "Feed Author from Metadata" in raw_content

    def test_archive_from_extract_result_metadata_published_fallback(
        self, sample_article_md: str, temp_output_dir: Path
    ):
        """extract_result.published 为空时从 source_metadata.feed_published 取值。"""
        er = self._make_extract_result(
            content=sample_article_md,
            published="",
        )
        metadata = ExtractionContext(
            source_slug="test-blog",
            source_publisher="Test Publisher",
            feed_title="",
            feed_author="A",
            feed_published="2026-04-01",
            url="https://example.com/article",
        )
        result = archive_from_extract_result(
            extract_result=er,
            output_dir=temp_output_dir,
            date_str="2026-06-13",
            source_metadata=metadata,
        )
        assert result.success
        raw_content = Path(result.output_path).read_text(encoding="utf-8")
        assert "2026-04-01" in raw_content


# ---------------------------------------------------------------------------
# 具名常量验证
# ---------------------------------------------------------------------------


class TestConstants:
    """具名常量验证。"""

    def test_required_anchors(self):
        """REQUIRED_ANCHORS 至少包含 §A, §E, §H, §M, §Z。"""
        for key in ("A", "E", "H", "M", "Z"):
            assert key in REQUIRED_ANCHORS, f"Missing required anchor §{key}"
