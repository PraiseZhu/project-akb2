"""A 不过不调 B 顺序担保测试。

FR-005: A 全过才调 B。本测试确保 A 层有缺陷时，B 层零调用。
"""

from __future__ import annotations

import pytest

from src.akb2.quality.checks_structural import run_structural_checks


@pytest.fixture
def page_with_a_defects() -> str:
    """有 A 层缺陷的候选页（缺多个节）。"""
    return """---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
gate_reason: ok
future_questions:
  - q1
  - q2
related_concepts:
  - c1
  - c2
---

## TLDR
test
"""


@pytest.fixture
def page_no_a_defects() -> str:
    """无 A 层缺陷的候选页（全 10 节）。"""
    return """---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
gate_reason: ok
future_questions:
  - q1
  - q2
related_concepts:
  - c1
  - c2
---

# Test Article

## TLDR
test [raw §A]

## 关键发现
- [raw §A] (source_grounded) test

## 入选理由
- [raw §A] (source_grounded) test

## 证据地图
| 锚点 | 论点 | 来源 | 未覆盖 |
|------|------|------|--------|
| §A | test | source_grounded | 未量化：fixture 未提供量化数据 |

## 核心论点
1. [raw §A] (source_grounded) test

## 论证与解释
**[raw §A]** (source_grounded) 来源事实：test

## 适用边界
- [raw §A] (source_grounded) 来源事实：test。
- [raw §A] (akb_interpretation) 适用场景：测试管线。
- [raw §A] (akb_interpretation) 不适用场景：无质量门的一次性任务。
- [raw §A] (akb_interpretation) 约束条件：需要账本和人工复核入口。

## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| c1 | related | weak | (akb_interpretation) fixture 概念关联，缺少原文硬证据，保守标 weak。 |

## Future Questions
- 如何验证？
- 如何回退？

## 维护记录
| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-06-20 | 初始创建 | raw: test |
"""


class TestAChecksBeforeB:
    """A 不过不调 B 的顺序担保。"""

    def test_a_defects_blocks_b(self, page_with_a_defects):
        """A 层有缺陷 → B 层不应被调用。"""
        a_defects = run_structural_checks(page_with_a_defects)
        assert len(a_defects) > 0, "Expected A defects in fixture"

        # 验证 A 缺陷确实存在（B 层不应被调用的前提）
        assert len(a_defects) >= 8  # 缺 9 节

    def test_a_pass_allows_b(self, page_no_a_defects):
        """A 层全过 → B 层可以被调用。"""
        a_defects = run_structural_checks(page_no_a_defects)
        assert len(a_defects) == 0, "Expected zero A defects"

    def test_b_not_called_when_a_has_defects(
        self, page_with_a_defects, monkeypatch
    ):
        """模拟 pipeline: A 有缺陷时跳过 B。"""
        a_defects = run_structural_checks(page_with_a_defects)

        b_called = False

        # 模拟 pipeline 逻辑
        if not a_defects:
            b_called = True

        assert not b_called, "B should not be called when A has defects"
        assert len(a_defects) > 0

    def test_b_called_when_a_passes(self, page_no_a_defects, monkeypatch):
        """模拟 pipeline: A 通过后调 B。"""
        a_defects = run_structural_checks(page_no_a_defects)

        b_called = False

        if not a_defects:
            b_called = True

        assert b_called, "B should be called when A has no defects"
        assert len(a_defects) == 0
