"""P2-C must-cover 同口径 + residue 收窄直测。

must-cover：渲染侧把 literal 反引号 ` 换成 '（防破坏 markdown code span），校验侧
归一化须同样消掉 '，否则 `Conv`→'Conv' 后字面量匹配不上（满仓 #6/#16 误报缺失）。
residue：去掉裸字"占位"（过宽，误伤正文"占位/占位符"，满仓 #16），保留明确模板标记。
"""

from __future__ import annotations


def test_normalize_must_cover_backtick_and_quote_same():
    """反引号与单引号同口径：`Conv`（渲染成 'Conv'）归一化后与原文一致。"""
    from src.akb2.quality.checks_structural import _normalize_must_cover_text

    raw_literal = "(`Conv`, `MatMul`, `Add`)"        # 原文（含反引号）
    rendered = "('Conv', 'MatMul', 'Add')"            # 渲染后（反引号→'）
    assert _normalize_must_cover_text(raw_literal) == _normalize_must_cover_text(rendered)


def test_normalize_still_fails_on_genuinely_missing_content():
    """同口径不放松真缺失：完全不同的内容归一化后仍不相等（fail-closed 不破）。"""
    from src.akb2.quality.checks_structural import _normalize_must_cover_text

    assert _normalize_must_cover_text("`Conv`") != _normalize_must_cover_text("completely different")


def test_residue_markers_drop_bare_placeholder_keep_explicit():
    """去裸"占位"（误伤正文）；明确模板残留标记仍保留（不削弱真检测）。"""
    from src.akb2.quality.checks_structural import TEMPLATE_RESIDUE_MARKERS

    assert "占位" not in TEMPLATE_RESIDUE_MARKERS  # 过宽裸字已移除
    # 明确模板残留仍检测
    for marker in ("{{", "}}", "<TODO", "TODO:", "待补充", "lorem ipsum"):
        assert marker in TEMPLATE_RESIDUE_MARKERS
