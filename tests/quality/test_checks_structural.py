"""A 层结构校验器测试 — 6 项检查正负 fixture。

全 fixture LLM 响应，不打真 API。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.akb2.quality.checks_structural import (
    Defect,
    check_a1_sections,
    check_a2_frontmatter,
    check_a3_raw_anchors,
    check_a4_graph_edges,
    check_a5_wikilinks,
    check_a6_maintenance_records,
    run_candidate_self_gate,
    run_structural_checks,
)
from src.akb2.quality.source_ops_scanners import scan_all as scan_source_evidence

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
key_findings:
  - "[raw §A] (source_grounded) 每个 loop 必须有显式停止条件"
  - "[raw §B] (source_grounded) loop 需要 trigger、selector、executor、quality gate、ledger writer"
  - "[raw §C] (source_grounded) 定点修复优于整体重跑"
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

- [raw §A] (source_grounded) 来源事实：原文把任务从 prompt 上移为 loop/system 设计。
- [raw §B] (source_grounded) 来源事实：原文列出 trigger、selector、executor、quality gate、ledger writer。
- [raw §A] (akb_interpretation) 适用场景：Agent pipeline、内容质量自动化。
- [raw §C] (akb_interpretation) 不适用场景：简单 CRUD 脚本、一次性数据迁移。
- [raw §D] (akb_interpretation) 约束条件：需要显式预算、质量门和人工复核入口。

## 图谱关系

| target | relation | strength | evidence |
|--------|----------|----------|----------|
| harness-engineering | upstream | strong | [raw §B] (source_grounded) 原文明示 "楼上一层" |
| agent-collaboration | related | weak | [raw §E] (akb_interpretation) 多 agent 协作可复用 maker/checker separation，但原文未定义协作协议。 |

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

    def test_invalid_yaml_frontmatter_produces_a2_defect(self):
        """负: PyYAML 解析失败时不得假绿，必须返回 A2 frontmatter defect。"""
        text = """---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
key_findings:
  - [raw §A] (source_grounded) unsafe: unquoted
future_questions:
  - q1
  - q2
related_concepts:
  - c1
  - c2
---

## TLDR
test [raw §A]
"""
        defects = check_a2_frontmatter(text)
        assert len(defects) == 1
        assert defects[0].dimension == "A2-frontmatter"
        assert defects[0].location == "frontmatter"
        assert "YAML" in defects[0].defect

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

    def test_key_findings_require_provenance_when_anchored(self):
        """负: frontmatter key_findings 带 raw 锚点时也必须标 provenance。"""
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
  - c1
  - c2
key_findings:
  - "[raw §A] a source claim without provenance"
---

## TLDR
test
"""
        defects = check_a2_frontmatter(text)
        key_defects = [d for d in defects if "key_findings" in d.location]
        assert len(key_defects) >= 1

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

    def test_double_hash_dotted_anchor_headings_exist(self):
        """正: 真实 raw 的 `## §A. 标题` 章节格式应被识别为锚点声明。"""
        candidate = """---
status: candidate
gate_decision: null
---

## TLDR

Loop 工程需要五个组件 [raw §B]，也需要保留人工验证边界 [raw §I]。
"""
        raw = """---
artifact_type: raw
---

## 锚点目录

| 锚点 | 内容 |
|------|------|
| §B | loop 五要素 |
| §I | loop 不替你做的三件事 |

---

## §B. The five pieces, and then notes

A loop needs five things.

## §I. What the loop still does not do for you

Verification is still on you.
"""
        defects = check_a3_raw_anchors(candidate, raw)
        assert defects == []

    def test_bracketed_raw_anchor_lines_exist(self):
        """正: smoke/raw 常用的 `[raw §A] ...` 段落行应被识别为锚点声明。"""
        candidate = """---
status: candidate
gate_decision: null
---

## TLDR

Loop 工程需要显式停止条件 [raw §A]，也需要失败可观测 [raw §I]。
"""
        raw = """---
artifact_type: raw
---

[raw §A] Loop engineering is bounded, convergent, and observable.

[raw §I] A loop is deployable only when failure is observable.
"""
        assert check_a3_raw_anchors(candidate, raw) == []

    def test_numeric_anchor_headings_exist(self):
        """正: fresh raw 的 `§1/§2` 数字锚点必须被识别。"""
        candidate = """---
status: candidate
gate_decision: null
---

## TLDR

MCP 初始化和工具调用都来自原文数字锚点 [raw §1] [raw §2]。
"""
        raw = """---
artifact_type: raw
---

# §1 Initialize

Clients send initialize first.

# §2 Tools

Clients call tools/list before tools/call.
"""
        assert check_a3_raw_anchors(candidate, raw) == []

    def test_inline_section_mentions_do_not_create_anchor(self):
        """负: 正文里偶然提到 §Z 不能被当作合法 raw 锚点声明。"""
        candidate = """---
status: candidate
gate_decision: null
---

## TLDR

引用不存在的锚点 [raw §Z].
"""
        raw = """---
artifact_type: raw
---

## §A. 开篇

这段文字只是说不要引用 §Z，因为它不存在。
"""
        defects = check_a3_raw_anchors(candidate, raw)
        assert len(defects) == 1
        assert "§Z" in defects[0].defect

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


class TestCandidateSelfGate:
    def test_rejects_excessive_akb_interpretation(self, valid_candidate_page, valid_raw_content):
        text = valid_candidate_page.replace(
            "## 关键发现\n\n"
            "1. 每个 loop 必须有显式停止条件 [raw §A]\n"
            "2. 定点修复优于整体重跑 [raw §C]\n\n"
            "## 入选理由\n\n"
            "本文提供了完整的 loop 设计模板，填补了 AKB 在 agent 工程方法论的空白。",
            "## 关键发现\n\n"
            "1. [raw §A] (akb_interpretation) 每个 loop 必须有显式停止条件。\n"
            "2. [raw §C] (akb_interpretation) 定点修复优于整体重跑。\n\n"
            "## 入选理由\n\n"
            "- [raw §B] (akb_interpretation) 本文提供了完整的 loop 设计模板。\n"
            "- [raw §D] (akb_interpretation) 该方法论可以泛化到多种 agent 管线。",
        )
        defects = run_candidate_self_gate(text, valid_raw_content)
        assert any(d.dimension == "SG-provenance-ratio" for d in defects)

    def test_rejects_core_section_akb_interpretation_ratio(
        self, valid_candidate_page, valid_raw_content
    ):
        text = valid_candidate_page.replace(
            "## 核心论点\n\n"
            "1. 有界循环设计 [raw §A]: 每个 loop 必须有显式停止条件\n"
            "2. 五组件模板 [raw §B]: trigger, work unit selector, action executor, quality gate, ledger writer\n"
            "3. 收敛优先 [raw §C]: 定点修复成本远低于整体重跑",
            "## 核心论点\n\n"
            "1. [raw §A] (akb_interpretation) 有界循环设计可以推导出治理面。\n"
            "2. [raw §B] (akb_interpretation) 五组件模板适合所有 agent 系统。\n"
            "3. [raw §C] (akb_interpretation) 定点修复必然优于整体重跑。",
        )
        defects = run_candidate_self_gate(text, valid_raw_content)
        assert any(d.dimension == "SG-provenance-ratio" for d in defects)

    def test_allows_interpretation_markers_in_operational_sections(
        self, valid_candidate_page, valid_raw_content
    ):
        text = valid_candidate_page.replace(
            "## 适用边界\n\n"
            "- [raw §A] (source_grounded) 来源事实：原文把任务从 prompt 上移为 loop/system 设计。\n"
            "- [raw §B] (source_grounded) 来源事实：原文列出 trigger、selector、executor、quality gate、ledger writer。\n"
            "- [raw §A] (akb_interpretation) 适用场景：Agent pipeline、内容质量自动化。\n"
            "- [raw §C] (akb_interpretation) 不适用场景：简单 CRUD 脚本、一次性数据迁移。\n"
            "- [raw §D] (akb_interpretation) 约束条件：需要显式预算、质量门和人工复核入口。",
            "## 适用边界\n\n"
            "- [raw §A] (akb_interpretation) 适用场景：Agent pipeline、内容质量自动化。\n"
            "- [raw §B] (akb_interpretation) 适用场景：需要 trigger、selector、executor、quality gate、ledger writer 的多步骤系统。\n"
            "- [raw §C] (akb_interpretation) 不适用场景：简单 CRUD 脚本、一次性数据迁移。\n"
            "- [raw §D] (akb_interpretation) 约束条件：需要显式预算、质量门和人工复核入口。",
        ).replace(
            "| harness-engineering | upstream | strong | [raw §B] (source_grounded) 原文明示 \"楼上一层\" |\n"
            "| agent-collaboration | related | weak | [raw §E] (akb_interpretation) 多 agent 协作可复用 maker/checker separation，但原文未定义协作协议。 |",
            "| harness-engineering | related | weak | [raw §B] (akb_interpretation) 与 harness-engineering 保守相关，等待图谱校准。 |\n"
            "| agent-collaboration | related | weak | [raw §E] (akb_interpretation) 多 agent 协作可复用 maker/checker separation，但原文未定义协作协议。 |",
        )
        defects = run_candidate_self_gate(text, valid_raw_content)
        assert not any(d.dimension == "SG-provenance-ratio" for d in defects)

    def test_how_interpretation_steps_do_not_count_against_core_ratio(
        self, valid_candidate_page, valid_raw_content
    ):
        text = valid_candidate_page.replace(
            "## Future Questions",
            "### 操作步骤\n\n"
            "1. [raw §A] (akb_interpretation) Record failure before stopping `ledger.append({event:'abort',node:node})`\n"
            "2. [raw §B] (akb_interpretation) Assert loop budget before executing `assert max_repair_rounds <= 3 and timeout_s == 60`\n"
            "3. [raw §C] (akb_interpretation) Dispatch section repair by defect `repair_router.dispatch(section=defect.section)`\n\n"
            "## Future Questions",
        )
        defects = run_candidate_self_gate(text, valid_raw_content)
        assert not any(d.dimension == "SG-provenance-ratio" for d in defects)

    def test_rejects_generic_akb_command(self, valid_candidate_page, valid_raw_content):
        text = valid_candidate_page.replace(
            "## Future Questions",
            "### 操作步骤\n\n"
            "- [raw §A] (akb_interpretation) `assert evidence_map.contains('[raw §A]')`\n\n"
            "## Future Questions",
        )
        defects = run_candidate_self_gate(text, valid_raw_content)
        assert any(d.dimension == "SG-generic-command" for d in defects)

    def test_rejects_hardcoded_novelty_baseline(self, valid_candidate_page, valid_raw_content):
        text = valid_candidate_page.replace(
            "## Future Questions",
            "## 新颖增量\n\n"
            "wiki/sources/addy-osmani-loop-engineering.md\n\n"
            "## Future Questions",
        )
        defects = run_candidate_self_gate(text, valid_raw_content)
        assert any(d.dimension == "SG-novelty-hardcoded" for d in defects)

    def test_concept_lane_zero_commands_no_how_density_defect(
        self, valid_candidate_page, valid_raw_content
    ):
        """SC-3：concept 车道 + 操作步骤节存在但 0 可执行命令 → 不判 SG-how-density fail。

        满仓观测：Context Engineering 概念样本挂 `SG-how-density 0<3`。代码查证表明
        how-density 门已 lane-aware（how_density_min=0 if concept），透传链完整；该失败
        是样本被误判 procedural（SC-1 同根）所致，非 how-density 门缺 lane-aware。
        本测试锁死 concept 车道豁免行为，防未来回归把 SC-1 下游保障打掉。
        """
        # 在「论证与解释」节内插入一个无反引号命令的 ### 操作步骤 子节
        text = valid_candidate_page.replace(
            "崩溃恢复只需读最后一条账本记录 [raw §B]。\n",
            "崩溃恢复只需读最后一条账本记录 [raw §B]。\n\n"
            "### 操作步骤\n\n"
            "概念型内容无可执行命令，本节阐述操作要点而不提供代码。\n",
        )
        # 守卫：操作步骤节存在且 0 可执行命令（否则测试本身失效）
        from src.akb2.quality.checks_structural import (
            _how_executable_commands,
            _how_section_text,
        )

        assert _how_section_text(text) != "", "测试素材须有 ### 操作步骤 节"
        assert _how_executable_commands(text) == [], "测试素材须 0 可执行命令"

        # concept 车道：how_density_min=0 → 不判 SG-how-density
        defects_concept = run_candidate_self_gate(
            text, valid_raw_content, content_lane="concept"
        )
        assert not any(d.dimension == "SG-how-density" for d in defects_concept), (
            "concept 车道 + 0 命令不应判 SG-how-density（SC-3）"
        )
        # 对比：procedural 车道同样 0 命令 → 仍判 fail（锁死 lane-aware 不退化）
        defects_proc = run_candidate_self_gate(
            text, valid_raw_content, content_lane=""
        )
        assert any(d.dimension == "SG-how-density" for d in defects_proc), (
            "procedural 车道 0 命令应判 SG-how-density（零回归）"
        )


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

    def test_evidence_requires_raw_anchor_or_interpretation(self):
        """负: 图谱 evidence 无 raw anchor 且无 provenance → 本地契约直接抓出。"""
        text = """---
status: candidate
---

## 图谱关系

| target | relation | strength | evidence |
|--------|----------|----------|----------|
| agent-collaboration | related | medium | multi-agent loops require coordination primitives |
"""
        defects = check_a4_graph_edges(text)
        assert any(d.dimension == "A7-图谱证据" for d in defects)
        assert any(d.dimension == "A7-边强度匹配" for d in defects)

    def test_weak_interpretation_edge_without_raw_anchor_is_allowed(self):
        """正: 无 raw anchor 的 AKB 推断边必须降 weak 且标 provenance。"""
        text = """---
status: candidate
---

## 图谱关系

| target | relation | strength | evidence |
|--------|----------|----------|----------|
| agent-collaboration | related | weak | (akb_interpretation) 多 agent 协作可复用账本模式，但原文未定义协作协议。 |
"""
        defects = check_a4_graph_edges(text)
        assert defects == []

    def test_five_column_graph_edge_with_provenance_is_allowed(self):
        """正: 新图谱契约含 provenance 列，evidence 同步 marker。"""
        text = """---
status: candidate
---

## 图谱关系

| target | relation | strength | provenance | evidence |
|--------|----------|----------|------------|----------|
| agent-collaboration | related | weak | akb_interpretation | (akb_interpretation) 多 agent 协作可复用账本模式，但原文未定义协作协议。 |
"""
        defects = check_a4_graph_edges(text)
        assert defects == []

    def test_five_column_graph_edge_requires_matching_marker(self):
        """负: provenance 列与 evidence marker 必须一致，防止边界只写在表头里。"""
        text = """---
status: candidate
---

## 图谱关系

| target | relation | strength | provenance | evidence |
|--------|----------|----------|------------|----------|
| agent-collaboration | related | weak | akb_interpretation | 多 agent 协作可复用账本模式。 |
"""
        defects = check_a4_graph_edges(text)
        assert any("marker" in d.defect for d in defects)


class TestA8ApplicabilityBoundary:
    def test_boundary_bullets_require_anchor_and_provenance(self):
        """负: 自由文本边界会被本地契约拒绝。"""
        text = """---
status: candidate
---

## 适用边界

适用: Agent pipeline
不适用: one-shot
"""
        defects = run_structural_checks(text)
        assert any(d.dimension == "A8-适用边界" for d in defects)

    def test_boundary_rejects_mixed_categories_in_one_bullet(self):
        """负: 同一 bullet 混写适用/不适用/约束。"""
        text = """---
status: candidate
---

## 适用边界

- [raw §A] (akb_interpretation) 适用场景：Agent pipeline。不适用场景：one-shot。约束条件：需要账本。
"""
        from src.akb2.quality.checks_structural import check_a8_applicability_boundary

        defects = check_a8_applicability_boundary(text)
        assert any("混写" in d.defect for d in defects)


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


# ---------------------------------------------------------------------------
# SG-must-cover: 确定性证据覆盖（fail-closed）— step-3
# ---------------------------------------------------------------------------


# 与 5 处生产调用点对应的源文件路径（provenance 静态审计用）。
# 锚定到 repo root（__file__ = tests/quality/，parents[2] = repo 根），不依赖进程
# CWD——conftest 的 sample_raw_path autouse fixture 已把 CWD 切到 per-test tmp 子目录
# 以消除共享 raw/ 竞态，这里若用 CWD 相对路径会读不到 src/。改绝对锚定，纯路径解析
# 机制变更，断言内容不变。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CALLSITE_SOURCE_PATHS = {
    "distill": str(_REPO_ROOT / "src/akb2/quality/distill.py"),
    "repair": str(_REPO_ROOT / "src/akb2/quality/repair.py"),
    "runner": str(_REPO_ROOT / "src/akb2/quality/runner.py"),
    "smoke_validator": str(_REPO_ROOT / "src/akb2/quality/smoke_validator.py"),
}

# raw 含 operation/boundary/conclusion 三类 must_cover，scan_all 命中 4 项。
_MUST_COVER_RAW = """\
# §A Setup

1. Run npm install
2. Run npm test

仅限生产环境使用。

因此该方案只适合生产部署。
"""

# 操作/边界/结论的字面量，用于断言 scan_all 非空且命中预期 must_cover。
_EXPECTED_MUST_COVER_LITERALS = (
    "1. Run npm install",
    "2. Run npm test",
    "仅限生产环境使用",
    "因此该方案只适合生产部署",
)


def _candidate_with_how_steps(steps_text: str) -> str:
    """构造一个含 ### 操作步骤 / ## 适用边界 / TLDR 的候选页骨架。"""
    return f"""---
status: candidate
gate_decision: null
source_refs:
  - raw/test-must-cover--raw.md
gate_reason: 待评审
future_questions:
  - Q1?
  - Q2?
related_concepts:
  - concept-a
key_findings:
  - "[raw §A] (source_grounded) finding"
artifact_type: wiki
pipeline_version: v1
---

## TLDR

因此该方案只适合生产部署 [raw §A]。

## 关键发现

1. finding [raw §A]

## 入选理由

理由。

## 证据地图

| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| A | §A | strong | - |

## 核心论点

1. 因此该方案只适合生产部署 [raw §A]

## 论证与解释

解释。

### 操作步骤

{steps_text}

## 适用边界

- [raw §A] (source_grounded) 仅限生产环境使用。

## 图谱关系

| target | relation | strength | evidence |
|--------|----------|----------|----------|
| concept-a | related | weak | [raw §A] (source_grounded) x |

## Future Questions

1. Q1?
2. Q2?

## 维护记录

- 2026-06-23: 初始创建（待评审）
"""


class TestSGMustCoverFailClosed:
    """SC-3.1 / SC-3.2: must_cover 漏即挡 + raw 缺失 fail-closed。"""

    def test_scan_all_hits_expected_must_cover(self):
        """scan_all(_MUST_COVER_RAW) 非空且命中 4 个预期字面量。"""
        items = scan_source_evidence(_MUST_COVER_RAW)
        literals = {i.literal for i in items}
        assert len(items) == 4
        for expected in _EXPECTED_MUST_COVER_LITERALS:
            assert expected in literals, f"scan_all 漏掉 must_cover: {expected!r}"

    def test_missing_operation_is_defect(self):
        """SC-3.1 漏即挡：原文有编号步骤但 ### 操作步骤 没渲染 → SG-must-cover Defect。"""
        # How 步骤只渲染了第二条，漏了第一条 "1. Run npm install"。
        steps = "- [raw §A] (source_grounded) 2. Run npm test `npm test`"
        text = _candidate_with_how_steps(steps)
        defects = run_candidate_self_gate(text, raw_content=_MUST_COVER_RAW)
        must_cover = [d for d in defects if d.dimension == "SG-must-cover"]
        assert must_cover, "漏渲染的 must_cover 未被挡下"
        assert any("1. Run npm install" in d.defect for d in must_cover)
        assert all(d.location == "### 操作步骤" for d in must_cover if "Run npm" in d.defect)

    def test_rendered_operations_no_must_cover_defect(self):
        """SC-3.1 不漏→空：How 步骤逐字渲染了所有 operation → 无 SG-must-cover Defect。"""
        steps = (
            "- [raw §A] (source_grounded) 1. Run npm install `npm install`\n"
            "- [raw §A] (source_grounded) 2. Run npm test `npm test`"
        )
        text = _candidate_with_how_steps(steps)
        defects = run_candidate_self_gate(text, raw_content=_MUST_COVER_RAW)
        must_cover = [d for d in defects if d.dimension == "SG-must-cover"]
        assert must_cover == [], f"已逐字渲染却报 must_cover 缺陷: {must_cover}"

    def test_must_cover_match_tolerates_markdown_reformatting(self):
        """SC-4：raw 字面量带 **bold**，候选渲染去 ** → 不应误判缺陷（归一化匹配）。

        满仓 simonwillison "1. **A computation graph**..." 挂此：compose 渲染同源内容
        时格式微调（去 **/插 [raw §X] 锚点），逐字匹配 `literal in section_text` 失败。
        归一化后比实质内容，仍 fail-closed 真缺失（内容根本没渲染时不匹配）。
        """
        raw = "1. **Run npm install** to fetch deps.\n2. Run npm test."
        # 候选渲染时去掉了 **（compose 重格式化，插了 [raw §X] 锚点）
        steps = (
            "- [raw §A] (source_grounded) 1. Run npm install to fetch deps.\n"
            "- [raw §B] (source_grounded) 2. Run npm test."
        )
        text = _candidate_with_how_steps(steps)
        defects = run_candidate_self_gate(text, raw_content=raw)
        must_cover = [d for d in defects if d.dimension == "SG-must-cover"]
        assert must_cover == [], (
            f"markdown 重格式化不应误判 must_cover 缺陷（SC-4）: {must_cover}"
        )

    def test_missing_boundary_is_defect(self):
        """SC-3.1 边界漏渲染：适用边界节不含原文边界字面量 → SG-must-cover Defect。"""
        # 用一个不含 "仅限生产环境使用" 的候选页（边界节改成别的文字）。
        text = _candidate_with_how_steps(
            "- [raw §A] (source_grounded) 1. Run npm install `npm install`\n"
            "- [raw §A] (source_grounded) 2. Run npm test `npm test`"
        ).replace("仅限生产环境使用。", "适用所有环境。")
        defects = run_candidate_self_gate(text, raw_content=_MUST_COVER_RAW)
        must_cover = [d for d in defects if d.dimension == "SG-must-cover"]
        assert any("仅限生产环境使用" in d.defect for d in must_cover)
        assert any(d.location == "## 适用边界" for d in must_cover)

    def test_missing_conclusion_is_defect(self):
        """SC-3.1 结论漏渲染：TLDR/关键发现/核心论点 不含原文结论字面量 → SG-must-cover Defect。"""
        text = _candidate_with_how_steps(
            "- [raw §A] (source_grounded) 1. Run npm install `npm install`\n"
            "- [raw §A] (source_grounded) 2. Run npm test `npm test`"
        ).replace("因此该方案只适合生产部署", "该方案通用")
        defects = run_candidate_self_gate(text, raw_content=_MUST_COVER_RAW)
        must_cover = [d for d in defects if d.dimension == "SG-must-cover"]
        assert any("因此该方案只适合生产部署" in d.defect for d in must_cover)
        assert any(
            d.location == "TLDR/关键发现/核心论点" for d in must_cover
        )

    def test_raw_none_fail_closed(self):
        """SC-3.2 raw 缺失 fail-closed：raw_content=None → 必产 SG-must-cover Defect，不静默跳过。"""
        text = _candidate_with_how_steps(
            "- [raw §A] (source_grounded) 1. Run npm install `npm install`"
        )
        defects = run_candidate_self_gate(text, raw_content=None)
        must_cover = [d for d in defects if d.dimension == "SG-must-cover"]
        assert len(must_cover) == 1
        assert "raw_content 缺失" in must_cover[0].defect
        assert must_cover[0].location == "raw_content"

    def test_raw_none_not_silently_skipped_on_clean_page(self):
        """SC-3.2 即使候选页本身结构完整，raw=None 仍必须报 fail-closed 缺陷。"""
        # 一个能通过其他 SG 检查的候选页：raw=None 时唯一新缺陷就是 SG-must-cover。
        text = _candidate_with_how_steps(
            "- [raw §A] (source_grounded) 1. Run npm install `npm install`\n"
            "- [raw §A] (source_grounded) 2. Run npm test `npm test`\n"
            "- [raw §A] (source_grounded) 3. Run npm build `npm build`"
        )
        defects_none = run_candidate_self_gate(text, raw_content=None)
        defects_raw = run_candidate_self_gate(text, raw_content=_MUST_COVER_RAW)
        none_mc = [d for d in defects_none if d.dimension == "SG-must-cover"]
        raw_mc = [d for d in defects_raw if d.dimension == "SG-must-cover"]
        assert len(none_mc) == 1 and "raw_content 缺失" in none_mc[0].defect
        # 有真实 raw 时，已逐字渲染 → 无 must_cover 缺陷（语义分离）。
        assert raw_mc == []


class TestSGMustCoverCallSiteProvenance:
    """SC-3.3: 5 处 run_candidate_self_gate 调用点 raw provenance 静态审计。"""

    @staticmethod
    def _read(rel_path: str) -> str:
        from pathlib import Path

        return Path(rel_path).read_text(encoding="utf-8")

    def test_distill_callsite_reads_real_raw_from_disk(self):
        """distill.py: raw_content = Path(raw_path).read_text()，同源于原始 raw 文件。

        step-3 SC-3e/A3-8：compose 第一道 self-gate 改为多参调用（真传 known_concepts/
        pending_queue/content_lane 做图谱 resolve），故断言由「整行字面」放宽为「call +
        raw_content=raw_content 实参同行存在」——审计意图（raw_content 是权威源、fail-closed）
        不变，仍验 raw_content 来自磁盘读 + OSError 时置 None。
        """
        src = self._read(_CALLSITE_SOURCE_PATHS["distill"])
        assert "run_candidate_self_gate(" in src
        assert "raw_content=raw_content," in src
        # step-3：第一闸真传 known/pending/lane（SC-3e），不再传 None 被 fixture 掩盖。
        assert "known_concepts=known_concepts," in src
        assert "pending_queue=pending_queue," in src
        assert "content_lane=content_lane," in src
        assert "Path(raw_path).read_text(encoding=\"utf-8\")" in src
        # OSError 时 raw_content=None → fail-closed（不静默跳过）。
        assert "raw_content = None" in src

    def test_repair_callsites_use_raw_text_param(self):
        """repair.py 两处 self-gate 调用：raw_text 同源 + 透传 content_lane + B1 透传
        known_concepts/pending_queue（w6/w9 + B1）。"""
        src = self._read(_CALLSITE_SOURCE_PATHS["repair"])
        # B1：两处 self-gate 都透传 known_concepts/pending_queue/content_lane，使
        # repair 轮图谱 resolve 与主链首过 self-gate 用同一上下文（不再用 None 复验）。
        assert src.count("known_concepts=known_concepts,") == 2
        assert src.count("pending_queue=pending_queue,") == 2
        assert src.count("content_lane=content_lane,") == 2
        # raw_text / known_concepts / pending_queue 是 run_repair_loop 的参数（非局部拼接）。
        assert "raw_text: str | None = None," in src
        assert "known_concepts: list[str] | None = None," in src
        assert "pending_queue: list[str] | None = None," in src

    def test_runner_callsite_uses_non_none_raw_content(self):
        """runner.py: raw_content 是 _run_quality_segments 的非可选 str 参数，源于磁盘读。

        step-3 SC-3e/A3-8：runner 第二道 self-gate 改为多参调用（真传 known_concepts/
        pending_queue/content_lane），断言由「整行字面」放宽为「call + candidate_text +
        raw_content 同段存在」——审计意图（raw_content 非可选、源于磁盘读）不变。
        """
        src = self._read(_CALLSITE_SOURCE_PATHS["runner"])
        # 第二道 self-gate 调用点用 candidate_text + raw_content（非 None）。
        assert "run_candidate_self_gate(" in src
        assert "candidate_text,\n        raw_content," in src
        # step-3：第二闸真传 known/pending/lane（SC-3e），与 compose 第一闸同语义复核。
        assert "known_concepts=known_concepts," in src
        assert "pending_queue=pending_queue," in src
        assert "content_lane=content_lane," in src
        # _run_quality_segments 接收非可选 raw_content: str（非 str | None）。
        assert "raw_content: str," in src
        # raw_content 源头是磁盘读真实 raw 文件。
        assert "raw_content = raw_file.read_text(encoding=\"utf-8\")" in src
        # run_repair_loop 接收的 raw_text 同源 raw_content。
        assert "raw_text=raw_content," in src

    def test_smoke_validator_callsite_reads_raw_from_disk_or_none(self):
        """smoke_validator.py: raw_content 来自 _load_candidate_raw 读盘；缺失显式 None。"""
        src = self._read(_CALLSITE_SOURCE_PATHS["smoke_validator"])
        assert "run_candidate_self_gate(candidate_text, raw_content=raw_content)" in src
        # _load_candidate_raw 读盘，raw_path 不存在时返回 None（fail-closed）。
        assert "raw_path.read_text(encoding=\"utf-8\")" in src
        assert "return None" in src

    def test_scan_all_on_real_raw_fixture_is_nonempty(self):
        """SC-3.3 fixture：scan_all(真实 raw) 非空且命中预期 must_cover。"""
        items = scan_source_evidence(_MUST_COVER_RAW)
        assert items, "scan_all(真实 raw) 不应为空"
        kinds = {i.kind for i in items}
        assert "source_operation" in kinds
        assert "source_boundary" in kinds
        assert "source_conclusion" in kinds
        literals = {i.literal for i in items}
        for expected in _EXPECTED_MUST_COVER_LITERALS:
            assert expected in literals

    def test_callsite_raw_not_truncated_or_alien(self):
        """SC-3.3: 5 处调用点 raw 变量均追溯到 read_text 或非可选 raw 参数，无截断/异源。"""
        # distill / smoke_validator: read_text 直读
        distill_src = self._read(_CALLSITE_SOURCE_PATHS["distill"])
        smoke_src = self._read(_CALLSITE_SOURCE_PATHS["smoke_validator"])
        assert "read_text" in distill_src
        assert "read_text" in smoke_src
        # runner: read_text + 非可选 str 参数透传
        runner_src = self._read(_CALLSITE_SOURCE_PATHS["runner"])
        assert "read_text" in runner_src
        # repair: raw_text 来自 runner 的 raw_content（非截断）
        repair_src = self._read(_CALLSITE_SOURCE_PATHS["repair"])
        assert "raw_text" in repair_src
        # 无任何调用点传截断/异源 raw（无 [:N] / split 截断模式指向 raw_text/raw_content）。
        assert "raw_text[: " not in repair_src
        assert "raw_content[: " not in runner_src


class TestSGCommandRecheckFailClosed:
    """SC-4.2: 正文含"原文不存在命令" → run_candidate_self_gate 产 SG-command-recheck Defect。

    SG-must-cover 治"漏"（raw 有 → 候选没渲染），SG-command-recheck 治"编"
    （候选有 → raw 没有）。两者语义分离，互不替代。
    """

    def test_fabricated_command_produces_recheck_defect(self):
        """正文 How 步骤含 raw 不存在的命令 → SG-command-recheck Defect。"""
        # 所有 must_cover 字面量都已逐字渲染（npm install / npm test / 边界 / 结论），
        # 额外注入一条 raw 不存在的命令 `rm -rf /`。
        steps = (
            "- [raw §A] (source_grounded) 1. Run npm install `npm install`\n"
            "- [raw §A] (source_grounded) 2. Run npm test `npm test`\n"
            "- [raw §A] (source_grounded) 3. 清理环境 `rm -rf /`"
        )
        text = _candidate_with_how_steps(steps)
        defects = run_candidate_self_gate(text, raw_content=_MUST_COVER_RAW)
        recheck = [d for d in defects if d.dimension == "SG-command-recheck"]
        assert recheck, "正文含原文不存在的命令未被 SG-command-recheck 挡下"
        assert any("rm -rf /" in d.defect for d in recheck)
        assert all(d.location == "### 操作步骤" for d in recheck)

    def test_real_commands_no_recheck_defect(self):
        """正文命令都在 raw 中字面量存在 → 无 SG-command-recheck Defect。"""
        steps = (
            "- [raw §A] (source_grounded) 1. Run npm install `npm install`\n"
            "- [raw §A] (source_grounded) 2. Run npm test `npm test`"
        )
        text = _candidate_with_how_steps(steps)
        defects = run_candidate_self_gate(text, raw_content=_MUST_COVER_RAW)
        recheck = [d for d in defects if d.dimension == "SG-command-recheck"]
        assert recheck == [], f"命令都来自 raw 却报 SG-command-recheck: {recheck}"

    def test_recheck_does_not_duplicate_must_cover(self):
        """SG-command-recheck 与 SG-must-cover 语义分离：漏渲染不触发 recheck。"""
        # 漏渲染第一条 npm install（must_cover 会报），但不注入编造命令 → recheck 不报。
        steps = "- [raw §A] (source_grounded) 2. Run npm test `npm test`"
        text = _candidate_with_how_steps(steps)
        defects = run_candidate_self_gate(text, raw_content=_MUST_COVER_RAW)
        must_cover = [d for d in defects if d.dimension == "SG-must-cover"]
        recheck = [d for d in defects if d.dimension == "SG-command-recheck"]
        assert must_cover, "漏渲染应触发 SG-must-cover"
        # npm test 在 raw 中 → 不应触发 recheck。
        assert all("npm test" not in d.defect for d in recheck)

    def test_raw_none_does_not_duplicate_recheck(self):
        """raw_content=None 已被 SG-must-cover 块 fail-closed，recheck 不重复报 raw 缺失。"""
        steps = (
            "- [raw §A] (source_grounded) 1. Run npm install `npm install`\n"
            "- [raw §A] (source_grounded) 2. Run npm test `npm test`"
        )
        text = _candidate_with_how_steps(steps)
        defects = run_candidate_self_gate(text, raw_content=None)
        recheck = [d for d in defects if d.dimension == "SG-command-recheck"]
        # raw=None 时 recheck 块不执行（raw_content falsy），不产 recheck Defect。
        assert recheck == []

    def test_empty_raw_still_runs_command_recheck(self):
        """raw_content="" 是存在但为空的 raw，必须由 raw_verify 产 SG-command-recheck。"""
        steps = "- [raw §A] (source_grounded) 1. Run npm install `npm install`"
        text = _candidate_with_how_steps(steps)
        defects = run_candidate_self_gate(text, raw_content="")
        recheck = [d for d in defects if d.dimension == "SG-command-recheck"]
        assert recheck, "空 raw 不得跳过 SG-command-recheck"
        assert any("npm install" in d.defect for d in recheck)
