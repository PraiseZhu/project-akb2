"""test_f6c_must_cover_injection.py — F6-C must_cover 确定性渲染注入测试。

覆盖:
    - 缺失字面量被注入 + SG 过(归一化比对通过)
    - 已存在不重复注入
    - 注入内容逐字一致(含链接语法)
    - 空/超长(>500)字面量跳过(现有 fail 路径不变)
"""

from __future__ import annotations

from types import SimpleNamespace

from src.akb2.quality.distill import _inject_missing_must_cover


def _item(literal: str, kind: str = "source_operation") -> SimpleNamespace:
    return SimpleNamespace(literal=literal, kind=kind)


class TestF6CMustCoverInjection:
    def test_missing_literal_injected(self):
        """缺失字面量被注入 → section_text 含该字面量(归一化后)。"""
        section = "Some existing content here."
        items = [_item("The missing literal with [link](/docs/x)")]
        result = _inject_missing_must_cover(section, items)
        assert "The missing literal with [link](/docs/x)" in result

    def test_existing_literal_not_reinjected(self):
        """已存在(归一化后)→ 不重复注入。"""
        section = "The literal is here already."
        items = [_item("The literal is here already.")]
        result = _inject_missing_must_cover(section, items)
        # 不重复(只出现一次)
        assert result.count("The literal is here already.") == 1

    def test_injection_verbatim_with_links(self):
        """注入内容逐字一致(含 markdown 链接语法)。"""
        literal = "1. The [`ENV_VAR`](/docs/env) setting controls behavior."
        section = "Other content."
        items = [_item(literal)]
        result = _inject_missing_must_cover(section, items)
        assert literal in result  # 逐字(含链接)

    def test_empty_literal_skipped(self):
        """空字面量 → 跳过(不注入)。"""
        section = "Content."
        items = [_item("")]
        result = _inject_missing_must_cover(section, items)
        assert result == "Content."

    def test_overlong_literal_skipped(self):
        """超长(>500 字符)字面量 → 跳过(现有 fail 路径不变)。"""
        section = "Content."
        items = [_item("x" * 501)]
        result = _inject_missing_must_cover(section, items)
        assert result == "Content."

    def test_multiple_items_some_missing(self):
        """多条 must_cover,部分缺失 → 只注入缺失的。"""
        section = "Existing literal one."
        items = [_item("Existing literal one."), _item("Missing literal two.")]
        result = _inject_missing_must_cover(section, items)
        assert "Missing literal two." in result
        assert result.count("Existing literal one.") == 1  # 不重复
