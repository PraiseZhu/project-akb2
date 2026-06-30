"""test_intake_chrome_strip.py — WP-1 上游 raw 去 chrome 测试 (SC-1)。

覆盖:
    - chrome_strip helper: 剥离 Go back to / Share / (#) / 纯图 / 空 href / 站内导航链接块
    - 干净页零回归（clean prose 不被误剥）
    - extract_content 抽取输出后剥 chrome + 重算 word_count（production 链）
    - 剥离后 <200 重新 fail-closed（对剥离后字数判定）
    - calibration _compose_raw_content / _inject_anchors_into_raw 注入锚点前剥 chrome
    - chrome 不进锚点（§A 指向真实正文，非 chrome）

mock 边界：_extract_via_defuddle / _extract_via_scrapling 返回值（不发网络）。
断言绑可观测行为（chrome 字面量消失 / wc 重算 / fail-closed 翻转 / 锚点指向正文）。
"""

from __future__ import annotations

from src.akb2.quality.chrome_strip import strip_chrome_blocks
from src.akb2.quality.intake.extract import ExtractMethod, ExtractResult, _apply_chrome_strip

# ---------------------------------------------------------------------------
# chrome_strip helper 单元
# ---------------------------------------------------------------------------


class TestStripChromeHelper:
    """strip_chrome_blocks 各模式命中 + 干净页零回归。"""

    def test_strips_go_back(self):
        clean, diag = strip_chrome_blocks("Go back to blog\n\nreal body content here")
        assert "Go back to blog" not in clean
        assert "real body content" in clean
        assert diag.go_back == 1

    def test_strips_share_button(self):
        clean, diag = strip_chrome_blocks("Share\n\nreal body content here")
        assert "Share" not in clean.split("\n")
        assert "real body content" in clean
        assert diag.share == 1

    def test_strips_empty_anchor(self):
        clean, diag = strip_chrome_blocks("(#)\n\nreal body content here")
        assert "(#)" not in clean
        assert diag.empty_anchor == 1

    def test_strips_pure_image(self):
        clean, diag = strip_chrome_blocks(
            "![Frame 2](https://cdn.example.com/img.png)\n\nreal body content here"
        )
        assert "cdn.example.com/img.png" not in clean
        assert diag.pure_image == 1

    def test_strips_empty_href_link_line(self):
        """[Create agents](#) Share 这类空 href 短行被剥。"""
        clean, diag = strip_chrome_blocks("[Create agents](#) Share\n\nreal body here")
        assert "[Create agents](#)" not in clean
        assert "real body" in clean
        assert diag.empty_href_link == 1

    def test_strips_nav_link_block_contiguous(self):
        """连续 ≥2 个内部 href 链接行（站内导航块）被整体剥。"""
        nav = "[Home](/)\n[Blog](/blog)\n[About](/about)\n\nreal body here"
        clean, diag = strip_chrome_blocks(nav)
        assert "[Home](/)" not in clean
        assert "[Blog](/blog)" not in clean
        assert "real body" in clean
        assert diag.nav_link_block_lines == 3

    def test_single_internal_link_kept(self):
        """孤立单行内部链接保留（可能是正文合法引用，零回归）。"""
        content = "See [the docs](/docs) for more.\n\nreal body here"
        clean, diag = strip_chrome_blocks(content)
        # 单行链接不是整行只有链接（有 "See" 和 "for more"）→ 不剥
        assert "[the docs](/docs)" in clean
        assert diag.stripped_lines == 0

    def test_clean_prose_zero_regression(self):
        """干净散文页零剥离（SC-1 硬约束）。"""
        clean_prose = "\n".join([
            "# Title",
            "",
            "This is a real paragraph about prompt caching mechanism.",
            "It has multiple sentences discussing KV-cache hit rates.",
            "",
            "## Section",
            "",
            "Another paragraph with [external link](https://example.com).",
            "- list item one",
            "- list item two",
        ])
        clean, diag = strip_chrome_blocks(clean_prose)
        assert clean == clean_prose, "干净页不应被剥任何行"
        assert diag.stripped_lines == 0

    def test_idempotent(self):
        """已剥过的内容再剥不变。"""
        once, _ = strip_chrome_blocks("Go back to blog\n\nreal body")
        twice, _ = strip_chrome_blocks(once)
        assert once == twice

    def test_external_link_block_kept(self):
        """外链引用列表（href 为 https）不被当站内导航剥。"""
        refs = "[1](https://a.com)\n[2](https://b.com)\n\nreal body"
        clean, diag = strip_chrome_blocks(refs)
        # 外链不在 _INTERNAL_LINK_ONLY_RE 范围（href 非 / 或 #）→ 保留
        assert diag.nav_link_block_lines == 0


# ---------------------------------------------------------------------------
# _apply_chrome_strip — extract 抽取输出后剥 + 重算 wc
# ---------------------------------------------------------------------------


class TestApplyChromeStripToExtracted:
    """_apply_chrome_strip 原地改 content/word_count + 返回 diagnostics。"""

    def test_recomputes_word_count_after_strip(self):
        """剥 chrome 后 word_count 按剥离后正文重算。"""
        extracted = {
            "content": "Go back to blog\n\n" + "word " * 150,
            "word_count": 154,  # 含 chrome 词
        }
        diag = _apply_chrome_strip(extracted)
        assert "Go back to blog" not in extracted["content"]
        assert extracted["word_count"] == 150  # 剥离后重算
        assert diag["post_strip_word_count"] == 150
        assert diag["pre_strip_word_count"] == 154

    def test_clean_content_unchanged(self):
        """干净正文 wc 不变（零回归）。"""
        clean = "word " * 250
        extracted = {"content": clean, "word_count": 250}
        diag = _apply_chrome_strip(extracted)
        assert extracted["content"] == clean
        assert extracted["word_count"] == 250
        assert diag["stripped_lines"] == 0


# ---------------------------------------------------------------------------
# SC-1: extract_content production 链 — 剥离后 <200 重新 fail-closed
# ---------------------------------------------------------------------------


def _ctx():
    from src.akb2.quality.intake.models import ExtractionContext

    return ExtractionContext(
        source_slug="test-slug",
        source_publisher="Test Pub",
        feed_title="Test",
        feed_author="Author",
        feed_published="2026-01-01",
        url="https://example.com/article",
    )


class TestExtractContentChromeStrip:
    """extract_content 抽取输出后剥 chrome + fail-closed（production 链）。"""

    def test_chrome_stripped_from_extract_output(self, monkeypatch):
        """defuddle 返回含 chrome 的内容 → result.content 不含 chrome，wc 重算。"""
        import src.akb2.quality.intake.extract as extract_mod
        from src.akb2.quality.intake.extract import extract_content

        content = "Go back to blog\nShare\n\n" + "real content word " * 80
        mock_return = {
            "title": "T", "author": "A", "published": "2026-01-01",
            "content": content, "word_count": 250,
        }
        monkeypatch.setattr(
            extract_mod, "_extract_via_defuddle", lambda url, html=None: mock_return
        )
        monkeypatch.setattr(
            extract_mod, "_extract_via_scrapling",
            lambda url, html=None, browser_mode=False: {},
        )

        result = extract_content(
            "https://example.com/article",
            html="<html><body>clean</body></html>",
            failures_path=None,
            extraction_config=_ctx(),
        )
        assert result.extraction_success is True
        assert "Go back to blog" not in result.content
        assert "Share" not in result.content.split("\n")  # Share 不独占一行
        assert "real content" in result.content
        # wc 按剥离后重算
        assert result.word_count == len(result.content.split())

    def test_fail_closed_when_strip_drops_below_200(self, monkeypatch):
        """原 wc>=200 但剥 chrome 后 <200 → fail-closed（对剥离后字数判定）。"""
        import src.akb2.quality.intake.extract as extract_mod
        from src.akb2.quality.intake.extract import extract_content

        # 20 行 "Go back to blog"（80 词）+ 150 个 word → 原 wc=230，剥后 150<200
        chrome_block = "\n".join(["Go back to blog"] * 20)
        content = chrome_block + "\n" + "word " * 150
        mock_return = {
            "title": "T", "author": "A", "published": "2026-01-01",
            "content": content, "word_count": 230,
        }
        monkeypatch.setattr(
            extract_mod, "_extract_via_defuddle", lambda url, html=None: mock_return
        )
        monkeypatch.setattr(
            extract_mod, "_extract_via_scrapling",
            lambda url, html=None, browser_mode=False: {},
        )

        result = extract_content(
            "https://example.com/article",
            html="<html><body>clean</body></html>",
            failures_path=None,
            extraction_config=_ctx(),
        )
        assert result.extraction_success is False, (
            "剥 chrome 后 <200 必须 fail-closed，不允许 chrome 词数补 200"
        )
        # chrome_strip 诊断在场（fail-visible）
        assert result.raw_metadata.get("chrome_strip") is not None
        assert result.raw_metadata["chrome_strip"]["post_strip_word_count"] < 200

    def test_clean_page_passes_zero_regression(self, monkeypatch):
        """干净正文 ≥200 → extraction_success=True，content 不变。"""
        import src.akb2.quality.intake.extract as extract_mod
        from src.akb2.quality.intake.extract import extract_content

        clean = "word " * 250
        mock_return = {
            "title": "T", "author": "A", "published": "2026-01-01",
            "content": clean, "word_count": 250,
        }
        monkeypatch.setattr(
            extract_mod, "_extract_via_defuddle", lambda url, html=None: mock_return
        )
        monkeypatch.setattr(
            extract_mod, "_extract_via_scrapling",
            lambda url, html=None, browser_mode=False: {},
        )

        result = extract_content(
            "https://example.com/article",
            html="<html><body>clean</body></html>",
            failures_path=None,
            extraction_config=_ctx(),
        )
        assert result.extraction_success is True
        assert result.content == clean
        assert result.word_count == 250


# ---------------------------------------------------------------------------
# SC-1: calibration 链 — _compose_raw_content / _inject_anchors_into_raw 注入前剥 chrome
# ---------------------------------------------------------------------------


class TestCalibrationComposeChromeStrip:
    """calibration 两链注入锚点前剥 chrome，chrome 不进锚点。"""

    def test_compose_raw_strips_chrome_before_anchors(self):
        """_compose_raw_content 注入锚点前剥 chrome → raw 不含 chrome，§A 指向真实正文。"""
        from src.akb2.quality.calibration import _compose_raw_content

        content = (
            "Go back to blog\n[Create agents](#) Share\n\n"
            + "real paragraph about prompt caching mechanism. " * 20
        )
        result = ExtractResult(
            url="https://example.com/article",
            method=ExtractMethod.DEFUDDLE,
            title="Test", author="Author", published="2026-01-01",
            content=content, word_count=300,
            metadata_complete=True, extraction_success=True,
        )
        raw = _compose_raw_content(result, _ctx())

        # chrome 不进 raw
        assert "Go back to blog" not in raw
        assert "[Create agents](#)" not in raw
        # 锚点目录 + §A 在场
        assert "## 锚点目录" in raw
        assert "§A" in raw
        # §A 锚点行后紧跟真实正文（非 chrome）
        a_pos = raw.find("§A_CONTENT_START")
        assert a_pos >= 0
        after_anchor = raw[a_pos + len("§A_CONTENT_START"):]
        assert "prompt caching" in after_anchor
        assert "Go back to blog" not in after_anchor

    def test_inject_anchors_into_raw_strips_chrome(self):
        """_inject_anchors_into_raw（fixture 路径）注入前剥 chrome。"""
        from src.akb2.quality.calibration import _inject_anchors_into_raw

        fixture_raw = (
            "---\n"
            "artifact_type: raw\n"
            "source_slug: test\n"
            "fixture_kind: concept_positive\n"
            "---\n\n"
            "Go back to blog\nShare\n\n"
            + "real fixture body content about mechanism. " * 20
        )
        injected = _inject_anchors_into_raw(fixture_raw)

        # frontmatter 保留
        assert injected.startswith("---")
        assert "fixture_kind: concept_positive" in injected
        # chrome 剥离
        assert "Go back to blog" not in injected
        assert "## 锚点目录" in injected
        assert "§A" in injected

    def test_clean_fixture_zero_regression(self):
        """干净 fixture（无 chrome）注入锚点后正文完整保留。"""
        from src.akb2.quality.calibration import _inject_anchors_into_raw

        clean_body = "\n\n".join(
            f"paragraph {i} " + "word " * 50 for i in range(6)
        )
        fixture_raw = (
            "---\nartifact_type: raw\nsource_slug: test\n---\n\n" + clean_body
        )
        injected = _inject_anchors_into_raw(fixture_raw)
        # 全 6 段保留
        for i in range(6):
            assert f"paragraph {i}" in injected
        assert "## 锚点目录" in injected
