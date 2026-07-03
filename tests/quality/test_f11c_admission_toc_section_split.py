"""test_f11c_admission_toc_section_split.py — F11-C admission 误拒 idx0/idx8 修复测试。

probe-p9b B 臂 idx0/idx8 admission 误拒 no_mechanism_signal(mechanism_present=True
矛盾)。根因两处:
1. _TOC_BLOCK_RE 只停 --- 或 ## §X.,scraped blog(## 锚点目录 无 --- 分隔、body
   普通 ## H2)→ 正则从 ## 锚点目录 吃到结尾,只剩 frontmatter → snippet=325 字符
   → LLM 收空 body 误判(idx8 lilian-weng)。
2. _split_heading_sections 只按 ## §X 锚点切,scraped blog(普通 ## 标题)整篇 1 节
   → snippet 取前 8000(intro),机制段在末尾被截 → LLM 误判(idx0 long-running-agents)。
F11-C:TOC 正则加 ^##\s+\S(任意 H2)停边界;_split_heading_sections 改任意 H2 切。
"""

from __future__ import annotations

from pathlib import Path

from src.akb2.quality.admission import (
    _build_mechanism_snippet,
    _remove_anchor_toc,
    _split_heading_sections,
    _section_is_mechanism_candidate,
)

_WT = Path(__file__).resolve().parents[2]
_IDX0_RAW = (
    _WT / "reports/akb2-quality-repair/_tmp/raw-store/"
    "bbb2f4114c50e4f97faa186d8c951671678f23de2dd5ed119444856cd4187d63.md"
)
_IDX8_RAW = (
    _WT / "reports/akb2-quality-repair/_tmp/raw-store/"
    "ef27e7f29195e0f8f18dd9e5e53b2d8c28a5979635a2240e670a710ae23ff39f.md"
)


class TestF11CTOCRegexNoOvereat:
    def test_toc_no_separator_not_eat_body(self):
        """TOC 后无 --- 但有普通 ## H2 → 正则停在 H2,不吃 body(idx8 形态)。"""
        raw = (
            "---\nsource_slug: test\n---\n\n"
            "## 锚点目录\n\n- §A: x\n- §B: y\n\n"
            "## Introduction\n\nbody content here.\n\n"
            "## Mechanism\n\nthe loop runs.\n"
        )
        out = _remove_anchor_toc(raw).strip()
        # body 保留(Introduction / Mechanism 节在)
        assert "Introduction" in out
        assert "Mechanism" in out
        assert "body content here" in out
        assert "the loop runs" in out
        # TOC 被去(- §A: x 不在)
        assert "§A: x" not in out

    def test_toc_with_separator_still_works(self):
        """fixture 形态(TOC 后 ---)仍正确去 TOC 不破。"""
        raw = (
            "---\nsource_slug: test\n---\n\n"
            "## 锚点目录\n\n| §A | x |\n\n---\n\n"
            "§A_CONTENT_START\n\nbody.\n"
        )
        out = _remove_anchor_toc(raw).strip()
        assert "body" in out
        assert "锚点目录" not in out  # TOC 去掉


class TestF11CSplitHeadingSectionsAnyH2:
    def test_scraped_blog_h2_split(self):
        """普通 ## H2 标题(非 ## §X)也能切(idx0 形态)。"""
        text = (
            "intro paragraph.\n\n"
            "## Introduction\n\nintro body.\n\n"
            "## The Ralph loop\n\nloop steps.\n\n"
            "## Conclusion\n\nconclusion.\n"
        )
        secs = _split_heading_sections(text)
        assert len(secs) >= 3, f"应切 ≥3 节(含 intro 首节),实际 {len(secs)}"
        # 各 H2 节单独
        heads = [s.split("\n")[0] for s in secs]
        assert any("Introduction" in h for h in heads)
        assert any("Ralph loop" in h for h in heads)
        assert any("Conclusion" in h for h in heads)

    def test_anchor_headings_still_split(self):
        """## §X 锚点标题(fixture)仍切(不回归)。"""
        text = (
            "## §A_INTRODUCTION\n\nintro.\n\n"
            "## §B_CONTEXT\n\ncontext.\n"
        )
        secs = _split_heading_sections(text)
        assert len(secs) >= 2


class TestF11CIdx0Idx8SnippetCapturesMechanism:
    def test_idx0_snippet_includes_mechanism_section(self):
        """idx0 long-running-agents snippet 含机制节(Ralph loop),非只 intro。"""
        if not _IDX0_RAW.exists():
            import pytest
            pytest.skip("idx0 raw fixture 不存在")
        raw = _IDX0_RAW.read_text()
        snip = _build_mechanism_snippet(raw)
        assert len(snip) >= 4000, f"snippet 过短(可能只 intro): {len(snip)}"
        # 含机制内容(Ralph loop / 长运行 agent 实质)
        assert any(kw in snip.lower() for kw in ("ralph", "loop", "long-running", "agent")), (
            "snippet 未含机制内容"
        )

    def test_idx8_snippet_not_just_frontmatter(self):
        """idx8 lilian-weng snippet 不只 frontmatter(旧版 325 字符只 frontmatter)。"""
        if not _IDX8_RAW.exists():
            import pytest
            pytest.skip("idx8 raw fixture 不存在")
        raw = _IDX8_RAW.read_text()
        snip = _build_mechanism_snippet(raw)
        assert len(snip) > 1000, f"snippet 过短(可能只 frontmatter): {len(snip)}"
        # 不以 frontmatter 结尾(含 body 内容)
        assert "source_slug" not in snip[-200:], "snippet 末尾仍在前 frontmatter(未抓 body)"
