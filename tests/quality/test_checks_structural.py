"""A 层结构校验器测试 — 6 项检查正负 fixture。

全 fixture LLM 响应，不打真 API。
"""

from __future__ import annotations

import pytest

from src.akb2.quality.checks_structural import (
    Defect,
    check_a1_sections,
    check_a2_frontmatter,
    check_a3_raw_anchors,
    check_a4_graph_edges,
    check_a5_wikilinks,
    check_a6_maintenance_records,
    run_structural_checks,
    TEN_SECTION_TITLES,
)


# ---------------------------------------------------------------------------
# 正样本: 完整的黄金样张风格候选页
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_candidate_page() -> str:
    """完整的、通过全部 A 层检查的候选页。"""
    return """---
status: candidate
gate_decision: null
source_refs:
  - raw/addy-osmani-loop-engineering--raw--2026-06-11.md
gate_reason: 待评审
future_questions:
  - Loop 工程在实时系统中如何适配延迟约束？
  - 多 agent 协作场景下 loop 的故障隔离如何设计？
related_concepts:
  - harness-engineering
  - agent-collaboration
artifact_type: wiki
pipeline_version: v1
---

## TLDR

Loop Engineering 是一种设计有界、收敛、可观测 AI Agent 循环的工程实践 [raw §A]。

## 关键发现

1. 每个 loop 必须有显式停止条件 [raw §A]
2. 定点修复优于整体重跑 [raw §C]

## 入选理由

本文提供了完整的 loop 设计模板，填补了 AKB 在 agent 工程方法论的空白。

## 证据地图

| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| Loop 五组件 | §B | strong | 实时系统场景 |

## 核心论点

1. 有界循环设计 [raw §A]: 每个 loop 必须有显式停止条件
2. 五组件模板 [raw §B]: trigger, work unit selector, action executor, quality gate, ledger writer
3. 收敛优先 [raw §C]: 定点修复成本远低于整体重跑

## 论证与解释

Loop engineering 的核心洞察是: 先写账本再执行，崩溃恢复只需读最后一条账本记录 [raw §B]。

## 适用边界

适用: Agent pipeline、内容质量自动化
不适用: 简单 CRUD 脚本、一次性数据迁移

## 图谱关系

| target | relation | strength | evidence |
|--------|----------|----------|----------|
| harness-engineering | 上游概念 | strong | 原文明示 "楼上一层" |
| agent-collaboration | 相关领域 | medium | 场景应用 |

## Future Questions

1. Loop 工程在实时系统中如何适配延迟约束？
2. 多 agent 协作场景下 loop 的故障隔离如何设计？

## 维护记录

- 2026-06-13: 初始创建（待评审）
"""


@pytest.fixture
def valid_raw_content() -> str:
    """与 valid_candidate_page 对应的 raw 文件内容。"""
    return """---
artifact_type: raw
source_slug: addy-osmani-loop-engineering
source_url: https://example.com/loop-engineering
source_title: Loop Engineering
source_authors: Addy Osmani
source_publisher: Addy Osmani's Blog
source_published_at: 2026-06-07
fetched_at: 2026-06-13T00:00:00Z
fetched_via: defuddle
---

# §A Loop Engineering

Loop engineering is the practice of designing AI agent loops that are bounded,
convergent, and observable.

# §B How: A Complete Loop Shape

A well-designed loop has five components: trigger, work unit selector,
action executor, quality gate, ledger writer.

# §C Convergence Over Restart

When a step produces defects, fix the specific defects rather than restarting
the entire pipeline.

# §D Budget Constraints

Every loop must have an explicit budget.

# §E Maker/Checker Separation

The model that produces content must not be the model that judges it.

# §F Golden Sample Anchoring

Quality standards must be anchored to concrete golden samples.

# §G Measured Value Verdict

Don't predict value — measure it.

# §H Agent Boundary Rules

Not every node needs an agent.

# §I Skill Packs

Loops need externalized knowledge.

# §J Failure Signatures

Every failure must leave a signature.
"""


# ---------------------------------------------------------------------------
# A1: 模板 10 节齐 — 正负用例
# ---------------------------------------------------------------------------


class TestA1Sections:
    def test_all_sections_present(self, valid_candidate_page):
        """正: 10 节全齐 → 零缺陷。"""
        defects = check_a1_sections(valid_candidate_page)
        assert len(defects) == 0, f"Expected 0 defects, got: {defects}"

    def test_missing_section(self):
        """负: 缺少 TLDR 节 → 1 缺陷。"""
        text = """---
status: candidate
gate_decision: null
---

## 关键发现
some content

## 入选理由
some content

## 证据地图
some content

## 核心论点
some content

## 论证与解释
some content

## 适用边界
some content

## 图谱关系
some content

## Future Questions
some content

## 维护记录
some content
"""
        defects = check_a1_sections(text)
        assert len(defects) == 1
        assert defects[0].dimension == "A1-模板节齐"
        assert "TLDR" in defects[0].defect

    def test_multiple_missing_sections(self):
        """负: 缺少多个节 → 多个缺陷。"""
        text = """---
status: candidate
gate_decision: null
---

## TLDR
some content

## 维护记录
some content
"""
        defects = check_a1_sections(text)
        assert len(defects) == 8  # 10 - 2

    def test_empty_body(self):
        """负: 正文完全为空 → 10 缺陷。"""
        text = """---
status: candidate
gate_decision: null
---

"""
        defects = check_a1_sections(text)
        assert len(defects) == 10


# ---------------------------------------------------------------------------
# A2: frontmatter 硬字段 — 正负用例
# ---------------------------------------------------------------------------


class TestA2Frontmatter:
    def test_all_fields_valid(self, valid_candidate_page):
        """正: 全部硬字段符合约束 → 零缺陷。"""
        defects = check_a2_frontmatter(valid_candidate_page)
        assert len(defects) == 0, f"Expected 0 defects, got: {defects}"

    def test_missing_source_refs(self):
        """负: source_refs 为空 → 缺陷。"""
        text = """---
status: candidate
gate_decision: null
source_refs: []
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
        defects = check_a2_frontmatter(text)
        source_defects = [d for d in defects if "source_refs" in d.location]
        assert len(source_defects) >= 1

    def test_gate_decision_not_null(self):
        """负: gate_decision 非 null → 缺陷。"""
        text = """---
status: candidate
gate_decision: active
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
        defects = check_a2_frontmatter(text)
        gd_defects = [d for d in defects if "gate_decision" in d.location]
        assert len(gd_defects) >= 1

    def test_gate_reason_too_long(self):
        """负: gate_reason 超过 150 字符 → 缺陷。"""
        text = f"""---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
gate_reason: {"x" * 151}
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
        defects = check_a2_frontmatter(text)
        gr_defects = [d for d in defects if "gate_reason" in d.location]
        assert len(gr_defects) >= 1

    def test_future_questions_too_few(self):
        """负: future_questions 只有 1 条 → 缺陷。"""
        text = """---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
gate_reason: ok
future_questions:
  - only one
related_concepts:
  - c1
  - c2
---

## TLDR
test
"""
        defects = check_a2_frontmatter(text)
        fq_defects = [d for d in defects if "future_questions" in d.location]
        assert len(fq_defects) >= 1

    def test_future_questions_too_many(self):
        """负: future_questions 4 条 → 缺陷。"""
        text = """---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
gate_reason: ok
future_questions:
  - q1
  - q2
  - q3
  - q4
related_concepts:
  - c1
  - c2
---

## TLDR
test
"""
        defects = check_a2_frontmatter(text)
        fq_defects = [d for d in defects if "future_questions" in d.location]
        assert len(fq_defects) >= 1

    def test_related_concepts_too_few(self):
        """负: related_concepts 只有 1 条 → 缺陷。"""
        text = """---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
gate_reason: ok
future_questions:
  - q1
  - q2
related_concepts:
  - only one
---

## TLDR
test
"""
        defects = check_a2_frontmatter(text)
        rc_defects = [d for d in defects if "related_concepts" in d.location]
        assert len(rc_defects) >= 1

    def test_missing_frontmatter(self):
        """负: 无 frontmatter → 多缺陷。"""
        text = """# No frontmatter

Some content.
"""
        defects = check_a2_frontmatter(text)
        assert len(defects) >= 3  # source_refs, future_questions, related_concepts

    def test_gate_reason_exactly_150_chars(self):
        """正: gate_reason 恰好 150 字符 → 零缺陷。"""
        reason = "x" * 150
        text = f"""---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
gate_reason: {reason}
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
        defects = check_a2_frontmatter(text)
        gr_defects = [d for d in defects if "gate_reason" in d.location]
        assert len(gr_defects) == 0


# ---------------------------------------------------------------------------
# A3: [raw §X] 锚点真实存在 — 正负用例
# ---------------------------------------------------------------------------


class TestA3RawAnchors:
    def test_all_anchors_exist(self, valid_candidate_page, valid_raw_content):
        """正: 所有锚点在 raw 文件中存在 → 零缺陷。"""
        defects = check_a3_raw_anchors(valid_candidate_page, valid_raw_content)
        assert len(defects) == 0

    def test_nonexistent_anchor(self, valid_raw_content):
        """负: 引用不存在的锚点 → 缺陷。"""
        text = """---
status: candidate
gate_decision: null
---

## 核心论点
引用不存在的锚点 [raw §Z].
"""
        defects = check_a3_raw_anchors(text, valid_raw_content)
        assert len(defects) >= 1
        assert "§Z" in defects[0].defect

    def test_no_raw_content_skips_check(self, valid_candidate_page):
        """raw_content=None → 跳过检查，零缺陷。"""
        defects = check_a3_raw_anchors(valid_candidate_page, None)
        assert len(defects) == 0


# ---------------------------------------------------------------------------
# A4: 图谱边四列齐 — 正负用例
# ---------------------------------------------------------------------------


class TestA4GraphEdges:
    def test_all_edges_valid(self, valid_candidate_page):
        """正: 图谱边四列齐 → 零缺陷。"""
        defects = check_a4_graph_edges(valid_candidate_page)
        assert len(defects) == 0

    def test_missing_column(self):
        """负: 图谱边缺少列 → 缺陷。"""
        text = """---
status: candidate
---

## 图谱关系

| target | relation | strength |
|--------|----------|----------|
| concept-a | related | strong |
"""
        defects = check_a4_graph_edges(text)
        assert len(defects) >= 1
        assert "4 列" in defects[0].defect or "evidence" in defects[0].defect.lower() or "evidence" in defects[0].location.lower()

    def test_empty_cell(self):
        """负: evidence 列为空 → 缺陷。"""
        text = """---
status: candidate
---

## 图谱关系

| target | relation | strength | evidence |
|--------|----------|----------|----------|
| concept-a | related | strong | |
"""
        defects = check_a4_graph_edges(text)
        assert len(defects) >= 1

    def test_no_graph_section(self):
        """图谱关系节不存在 → 零缺陷（由 A1 报告）。"""
        text = """---
status: candidate
---

## TLDR
test
"""
        defects = check_a4_graph_edges(text)
        assert len(defects) == 0


# ---------------------------------------------------------------------------
# A5: wikilink 死链检测 — 正负用例
# ---------------------------------------------------------------------------


class TestA5Wikilinks:
    def test_all_links_known(self):
        """正: 所有 wikilink 在已知概念中 → 零缺陷。"""
        text = """---
status: candidate
---

## TLDR
参考 [[harness-engineering]] 和 [[agent-collaboration]]。
"""
        defects = check_a5_wikilinks(
            text,
            pending_queue=["harness-engineering"],
            known_concepts=["agent-collaboration"],
        )
        assert len(defects) == 0

    def test_dead_link(self):
        """负: wikilink 目标不在已知列表中 → 缺陷。"""
        text = """---
status: candidate
---

## TLDR
参考 [[unknown-concept]]。
"""
        defects = check_a5_wikilinks(
            text,
            pending_queue=[],
            known_concepts=["known-concept"],
        )
        assert len(defects) >= 1
        assert "unknown-concept" in defects[0].defect

    def test_empty_allowed_lists_skips(self):
        """已知列表全空 → 跳过检查。"""
        text = """---
status: candidate
---

## TLDR
参考 [[any-concept]]。
"""
        defects = check_a5_wikilinks(text)
        assert len(defects) == 0

    def test_link_in_pending_queue(self):
        """wikilink 在 pending 队列中 → 零缺陷。"""
        text = """---
status: candidate
---

## TLDR
参考 [[pending-concept]]。
"""
        defects = check_a5_wikilinks(
            text, pending_queue=["pending-concept"]
        )
        assert len(defects) == 0

    def test_link_with_alias(self):
        """wikilink 带别名 [[target|display]] → 正确解析。"""
        text = """---
status: candidate
---

## TLDR
参考 [[known-concept|显示文本]]。
"""
        defects = check_a5_wikilinks(
            text, known_concepts=["known-concept"]
        )
        assert len(defects) == 0


# ---------------------------------------------------------------------------
# A6: 维护记录 ≤5 条 — 正负用例
# ---------------------------------------------------------------------------


class TestA6MaintenanceRecords:
    def test_within_limit(self, valid_candidate_page):
        """正: 维护记录 1 条 → 零缺陷。"""
        defects = check_a6_maintenance_records(valid_candidate_page)
        assert len(defects) == 0

    def test_exceeds_limit(self):
        """负: 维护记录 6 条 → 缺陷。"""
        text = """---
status: candidate
---

## 维护记录

- 2026-06-13: entry 1
- 2026-06-13: entry 2
- 2026-06-13: entry 3
- 2026-06-13: entry 4
- 2026-06-13: entry 5
- 2026-06-13: entry 6
"""
        defects = check_a6_maintenance_records(text)
        assert len(defects) >= 1
        assert "6" in defects[0].defect

    def test_exactly_five(self):
        """正: 维护记录恰好 5 条 → 零缺陷。"""
        text = """---
status: candidate
---

## 维护记录

- 2026-06-13: entry 1
- 2026-06-13: entry 2
- 2026-06-13: entry 3
- 2026-06-13: entry 4
- 2026-06-13: entry 5
"""
        defects = check_a6_maintenance_records(text)
        assert len(defects) == 0

    def test_no_maintenance_section(self):
        """维护记录节不存在 → 零缺陷（由 A1 报告）。"""
        text = """---
status: candidate
---

## TLDR
test
"""
        defects = check_a6_maintenance_records(text)
        assert len(defects) == 0

    def test_date_format_variants(self):
        """日期格式变体: 无列表前缀。"""
        text = """---
status: candidate
---

## 维护记录

2026-06-13: direct date line
2026-06-14: another direct date
"""
        defects = check_a6_maintenance_records(text)
        assert len(defects) == 0


# ---------------------------------------------------------------------------
# 聚合入口测试
# ---------------------------------------------------------------------------


class TestRunStructuralChecks:
    def test_valid_page_passes_all(self, valid_candidate_page, valid_raw_content):
        """正: 完整有效候选页 → 全部通过。"""
        defects = run_structural_checks(
            valid_candidate_page,
            raw_content=valid_raw_content,
            pending_queue=["new-concept"],
            known_concepts=["harness-engineering", "agent-collaboration"],
        )
        assert len(defects) == 0, f"Expected 0 defects, got: {defects}"

    def test_broken_page_catches_all(self):
        """负: 完全破损的候选页 → 多个缺陷。"""
        text = """---
status: candidate
gate_decision: active
---

# Just a title
no sections at all
"""
        defects = run_structural_checks(text)
        assert len(defects) >= 10  # A1: 10 missing + A2: gate_decision


# ---------------------------------------------------------------------------
# Defect 输出与 B 同构验证
# ---------------------------------------------------------------------------


class TestDefectStruct:
    def test_defect_has_required_fields(self):
        """Defect 必须有 dimension/location/defect/suggestion。"""
        d = Defect(
            dimension="A1-模板节齐",
            location="## TLDR",
            defect="缺少 TLDR 节",
            suggestion="添加 TLDR 节",
        )
        assert d.dimension == "A1-模板节齐"
        assert d.location == "## TLDR"
        assert d.defect == "缺少 TLDR 节"
        assert d.suggestion == "添加 TLDR 节"

    def test_all_check_defects_have_location(self):
        """所有检查产出的缺陷必须有非空 location。"""
        text = """---
status: candidate
gate_decision: active
source_refs: []
---

"""
        defects = run_structural_checks(text)
        for d in defects:
            assert d.location, f"Defect missing location: {d}"
            assert d.dimension, f"Defect missing dimension: {d}"