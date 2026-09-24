"""test_f19_fallback_render_quality.py — F19 fallback/compose 确定性渲染缺陷修复测试。

工单 F19:修 fallback/compose 链路的确定性渲染缺陷。三个根因:
  1. must_cover 字面量归一化 bug:ASCII ' 与 U+2019 typographic quote 处理不一致,
     已渲染字面量被 SG 误报缺失(code.claude.com 3 条挂此)。
  2. 占位文本 1.1/2.2:scan_numbered_steps 扫编号即内容的空步骤当 source_operation。
  3. fallback 产导航/短文本 claim:_build_deterministic_extract_data 把 raw §A 导航文本
     ``[ Go back to blog`` / §C ``Share`` 当 source_grounded claim(填充凑数 + provenance 造假)。

测试不打真 API:离线验证归一化/scan/fallback 行为契约(宁缺勿假:不填充凑数,fail-closed)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# repo root for sys.path
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "src/akb2/toolbox_scan"))

from src.akb2.quality.checks_structural import _normalize_must_cover_text  # noqa: E402
from src.akb2.quality.distill import (  # noqa: E402
    _build_deterministic_extract_data,
    _is_vacuous_raw_text,
    _validate_extract_output,
)
from src.akb2.quality.source_ops_scanners.operations import (  # noqa: E402
    _is_vacuous_numbered_step,
    scan_numbered_steps,
)

# ---------------------------------------------------------------------------
# 根因 1:归一化对 ASCII ' 与 U+2019 typographic quote 同口径
# ---------------------------------------------------------------------------


class TestNormalizeUnicodeQuotes:
    def test_ascii_and_unicode_single_quote_both_stripped(self) -> None:
        """ASCII ' 与 U+2019 都去掉(不变空格),两侧同口径。"""
        ascii_lit = "The subagent definition's model"
        u2019_lit = "The subagent definition’s model"
        assert _normalize_must_cover_text(ascii_lit) == _normalize_must_cover_text(u2019_lit)

    def test_rendered_single_quote_literal_matches_original_backtick(self) -> None:
        """literal 含反引号(渲染侧 _sanitize 换 ASCII '),归一化后与渲染形式匹配。

        端到端复现 code.claude.com must_cover 3 条挂此的修复:渲染字面量含 ['MODEL']
        (单引号,来自 _sanitize 反引号→'),归一化去单引号后与原始字面量 [`MODEL`] 匹配。
        """
        original = "The [`CLAUDE_CODE_SUBAGENT_MODEL`](/docs/x) env var"
        rendered = "The ['CLAUDE_CODE_SUBAGENT_MODEL'](/docs/x) env var"
        section = f"1. [raw §A] (source_grounded) 原文操作 {rendered}"
        assert _normalize_must_cover_text(original) in _normalize_must_cover_text(section)

    def test_no_extra_inner_spaces_in_brackets(self) -> None:
        """['MODEL'] 归一化后 [model] 无内侧空格(旧版变 [ model ] 导致失配)。"""
        norm = _normalize_must_cover_text("The ['MODEL'] parameter")
        assert "[ model ]" not in norm  # 旧版 bug:单引号变空格 → 内侧空格
        assert "[model]" in norm

    def test_real_missing_still_fails(self) -> None:
        """真缺失(字面量根本没渲染)归一化后仍不匹配 → 仍 fail(严格度不降)。"""
        literal = "The [`CLAUDE_CODE_SUBAGENT_MODEL`](/docs/x) env var"
        section_without_it = "1. [raw §A] 别的完全无关内容"
        assert _normalize_must_cover_text(literal) not in _normalize_must_cover_text(
            section_without_it
        )


# ---------------------------------------------------------------------------
# 根因 2:scan_numbered_steps 过滤编号即内容的空步骤
# ---------------------------------------------------------------------------


class TestScanVacuousSteps:
    def test_vacuous_numbered_step_detection(self) -> None:
        """1.1 / 2.2 / Step 1: 1 是空步骤(剥编号后纯数字/太短)。"""
        assert _is_vacuous_numbered_step("1. 1")
        assert _is_vacuous_numbered_step("2. 2")
        assert _is_vacuous_numbered_step("Step 1: 1")
        assert _is_vacuous_numbered_step("3. 3")

    def test_real_steps_not_vacuous(self) -> None:
        """真步骤(剥编号后有实质内容)不是空步骤。"""
        assert not _is_vacuous_numbered_step("1. Define the work item schema before processing")
        assert not _is_vacuous_numbered_step("2. **Worktrees** so two agents dont step on each other")
        assert not _is_vacuous_numbered_step("Step 2: Configure trigger")

    def test_scan_filters_vacuous_steps(self) -> None:
        """scan_numbered_steps 过滤 1.1/2.2/Step 1: 1,保留真步骤。"""
        raw = (
            "Intro\n\n"
            "1. 1\n"
            "2. 2\n"
            "3. Define the work item schema before processing\n"
            "4. Run quality gate\n"
            "Step 1: 1\n"
            "Step 2: Configure trigger\n"
        )
        items = scan_numbered_steps(raw)
        literals = [it.literal for it in items]
        assert "1. 1" not in literals
        assert "2. 2" not in literals
        assert "Step 1: 1" not in literals
        assert "3. Define the work item schema before processing" in literals
        assert "Step 2: Configure trigger" in literals

    def test_scan_keeps_real_numbered_steps(self) -> None:
        """无空步骤时,真步骤全保留(零回归)。"""
        raw = "1. First step\n2. Second step\n3. Third step\n"
        items = scan_numbered_steps(raw)
        assert len(items) == 3


# ---------------------------------------------------------------------------
# 根因 3:fallback 不产导航/短文本 claim(宁缺勿假,不填充凑数)
# ---------------------------------------------------------------------------


class TestVacuousRawText:
    def test_navigation_text_vacuous(self) -> None:
        """导航文本 [ Go back to blog 是 vacuous。"""
        assert _is_vacuous_raw_text("[ Go back to blog")
        assert _is_vacuous_raw_text("](/blog)[Create agents](#)")

    def test_image_video_vacuous(self) -> None:
        """图片 ![](url) / 视频 ![[id]] 是 vacuous。"""
        assert _is_vacuous_raw_text("![](https://cdn.prod.website-files.com/x.png)")
        assert _is_vacuous_raw_text("![[uCKhOmth2ms]]")

    def test_short_text_vacuous(self) -> None:
        """短文本(<20 字符)如 'Share' 是 vacuous。"""
        assert _is_vacuous_raw_text("Share")
        assert _is_vacuous_raw_text("## Timestamps")

    def test_real_content_not_vacuous(self) -> None:
        """真实内容(>=20 字符,非导航/图片)不是 vacuous。"""
        assert not _is_vacuous_raw_text(
            "In this conversation with Harrison Chase, he argues agentic commerce"
        )
        assert not _is_vacuous_raw_text("Most teams pick a model and commit.")


class TestDeterministicFallbackVacuousClaims:
    def test_fallback_skips_vacuous_claims_c234(self) -> None:
        """c234 raw(§A 导航/§C Share/§D 图片)fallback 不产 vacuous claim。

        c234 raw §A=[ Go back to blog, §C=Share, §D=图片, §E=真实内容。
        fallback primary 选 §E(第一个非 vacuous),claim text 用 §E 真实内容,
        不产 [ Go back to blog / Share / 图片 URL 当 claim。
        """
        # 固化为仓内 fixture（原样拷自 vault raw/langchain-blog--c234f0aa9f55--raw--2026-06-29.md）。
        # 不再读真实 vault：2026-09-24 Argus 用未授权的 node 跑到这里，触发 macOS「可移除宗卷」
        # 授权弹窗，锁屏没人点，整机所有进程访问 /Volumes/AKB2 被挂起 7.5 小时。
        c234_raw = _REPO / "tests/quality/fixtures/regression/c234-langchain-blog--raw.md"
        raw = c234_raw.read_text(encoding="utf-8")
        data = _build_deterministic_extract_data(raw, raw_path="c234", content_lane="")
        # 所有 claim text 非空且非 vacuous(>=20 字符,非导航/图片)
        assert len(data["claims"]) >= 1
        for c in data["claims"]:
            assert not _is_vacuous_raw_text(c["text"]), (
                f"claim text 不该是 vacuous: {c['text']!r}"
            )
            assert len(c["text"]) >= 20, f"claim text 不该 <20: {c['text']!r}"
        # 验证通过(无 'Share' too short)
        _validate_extract_output(data, content_lane="")

    def test_fallback_fails_when_all_anchors_vacuous(self) -> None:
        """raw 全 vacuous(纯导航/图片)→ fallback raise fail-closed(不填充凑数)。"""
        vacuous_raw = (
            "---\nartifact_type: raw\n---\n\n"
            "§A\n[ Go back to blog\n\n"
            "§B\n![](https://x.com/img.png)\n\n"
            "§C\nShare\n"
        )
        with pytest.raises(ValueError, match="fail-closed"):
            _build_deterministic_extract_data(vacuous_raw, raw_path="vac", content_lane="")

    def test_fallback_picks_non_vacuous_primary(self) -> None:
        """raw §A 导航 + §B 真实内容 → primary 选 §B(第一个非 vacuous)。"""
        raw = (
            "---\nartifact_type: raw\n---\n\n"
            "§A\n[ Go back to blog\n\n"
            "§B\nThis is real substantive content about agent loops and engineering.\n"
        )
        data = _build_deterministic_extract_data(raw, raw_path="t", content_lane="")
        # 至少 1 条 claim,且 text 来自 §B 真实内容(非 §A 导航)
        assert len(data["claims"]) >= 1
        assert all("[ Go back to blog" not in c["text"] for c in data["claims"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
