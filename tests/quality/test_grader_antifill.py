"""反填表用例测试 — 结构 10 节全齐但内容空洞，A 层全过、B 层必须打回。

黄金样张质量锚定（P6）的核心验收：表单填满不代表质量合格。
"""

from __future__ import annotations

import pytest

from src.akb2.quality.llm_router import LLMResponse
from src.akb2.quality.checks_structural import run_structural_checks
from src.akb2.quality.grader import (
    run_grader_with_fixture,
    RUBRIC_DIMENSIONS,
)


@pytest.fixture
def hollow_candidate_page() -> str:
    """反填表候选页: 10 节全齐但内容极度空洞。

    - 所有节标题都存在（A1 全过）
    - frontmatter 字段合规（A2 全过）
    - 但每节只有 1-2 句泛泛而谈的内容
    - TLDR 没有锚点，核心论点无实质内容，证据地图为空表
    """
    return """---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
gate_reason: 待评审
future_questions:
  - 还有哪些应用？
  - 未来会怎样？
related_concepts:
  - ai
  - ml
artifact_type: wiki
pipeline_version: v1
---

## TLDR

这是一篇关于 AI 的文章。AI 很重要。未来 AI 会更好。

## 关键发现

1. AI 在进步
2. AI 很有用

## 入选理由

这篇文章讨论了 AI，与知识库主题相关。

## 证据地图

| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|

## 核心论点

1. AI 很重要。
2. AI 有很多应用。
3. AI 需要继续研究。

## 论证与解释

AI 技术发展迅速，在各个领域都有应用。

## 适用边界

适用于所有场景。

## 图谱关系

| target | relation | strength | evidence |
|--------|----------|----------|----------|
| ai | related | strong | 普遍认知 |

## Future Questions

1. 还有哪些应用？
2. 未来会怎样？

## 维护记录

- 2026-06-13: 初始创建
"""


@pytest.fixture
def anti_fill_grader_response() -> dict:
    """反填表打回 fixture 响应: B 层必须产出缺陷。

    空洞内容的典型缺陷:
    - 论断锚定率: 无 [raw §X] 锚点
    - How 密度: 无可执行步骤
    - TLDR 密度: 泛泛而谈
    - 证据诚实: 证据地图为空
    - 解读分离: 无 provenance 标注
    - Future Questions 质量: 问题泛泛
    - 适用边界: "适用于所有场景"无边界意识
    """
    return {
        "gpt-5.4": LLMResponse(
            content={
                "dimensions": [
                    {
                        "dimension": "论断锚定率",
                        "pass": False,
                        "defects": [
                            {
                                "location": "核心论点",
                                "defect": "所有论点均无 [raw §X] 锚点引用",
                                "suggestion": "为每个论点和 TLDR 句添加 raw 锚点引用",
                            },
                            {
                                "location": "TLDR",
                                "defect": "TLDR 无锚点引用",
                                "suggestion": "每句 TLDR 对应一个核心论点并带锚点",
                            },
                        ],
                    },
                    {
                        "dimension": "How 密度",
                        "pass": False,
                        "defects": [
                            {
                                "location": "核心论点",
                                "defect": "全文无可执行步骤或代码示例",
                                "suggestion": "添加具体操作步骤、命令或配置参数",
                            },
                        ],
                    },
                    {
                        "dimension": "How 可执行性",
                        "pass": False,
                        "defects": [
                            {
                                "location": "论证与解释",
                                "defect": "无任何可直接执行的指令",
                                "suggestion": "添加可复制执行的命令或代码片段",
                            },
                        ],
                    },
                    {
                        "dimension": "证据诚实",
                        "pass": False,
                        "defects": [
                            {
                                "location": "证据地图",
                                "defect": "证据地图完全为空，无任何证据记录",
                                "suggestion": "填写证据地图，每行含未覆盖列",
                            },
                        ],
                    },
                    {
                        "dimension": "解读分离",
                        "pass": False,
                        "defects": [
                            {
                                "location": "核心论点",
                                "defect": "所有论点未标注 provenance（source_grounded/akb_interpretation）",
                                "suggestion": "为每个论点标注 provenance",
                            },
                        ],
                    },
                    {
                        "dimension": "TLDR 密度",
                        "pass": False,
                        "defects": [
                            {
                                "location": "TLDR",
                                "defect": "TLDR 为泛泛而谈，未覆盖全文核心信息",
                                "suggestion": "用 3 句话覆盖核心论点，每句带锚点",
                            },
                        ],
                    },
                    {"dimension": "新颖增量", "pass": True, "defects": []},
                    {"dimension": "边强度匹配", "pass": True, "defects": []},
                    {
                        "dimension": "Future Questions 质量",
                        "pass": False,
                        "defects": [
                            {
                                "location": "Future Questions",
                                "defect": '问题全是泛泛而谈（"还有哪些应用""未来会怎样"）',
                                "suggestion": "替换为具体的研究问题，指向明确方向",
                            },
                        ],
                    },
                    {
                        "dimension": "适用边界",
                        "pass": False,
                        "defects": [
                            {
                                "location": "适用边界",
                                "defect": '声称"适用于所有场景"，无边界意识',
                                "suggestion": "明确列出 ≥2 适用场景 + ≥1 不适用场景",
                            },
                        ],
                    },
                ]
            },
            model_used="gpt-5.4",
            calls_used=1,
        ),
    }


class TestAntiFill:
    """反填表用例: 结构完整但内容空洞 → A 全过, B 必须打回。"""

    def test_a_layer_passes_hollow_page(self, hollow_candidate_page):
        """A 层: 10 节全齐 + frontmatter 合规 → 零缺陷（反填表通过结构检查）。"""
        defects = run_structural_checks(hollow_candidate_page)
        assert len(defects) == 0, (
            f"Anti-fill page should pass structural checks, but got defects: {defects}"
        )

    def test_b_layer_rejects_hollow_page(
        self, hollow_candidate_page, anti_fill_grader_response
    ):
        """B 层: 内容空洞 → 必须打回（产出缺陷）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_llm = make_mock_route_llm(anti_fill_grader_response)
        defects, model_used, suspended = run_grader_with_fixture(
            hollow_candidate_page, mock_llm
        )

        assert not suspended
        assert len(defects) > 0, (
            "Anti-fill page MUST be rejected by B layer. Got 0 defects."
        )
        assert model_used == "gpt-5.4"

        # 关键验证: 至少覆盖以下维度
        failed_dimensions = {d.dimension for d in defects}
        required_fails = {"论断锚定率", "TLDR 密度", "证据诚实", "Future Questions 质量"}
        missing = required_fails - failed_dimensions
        assert not missing, (
            f"Anti-fill grader should fail on: {missing}. "
            f"Actually failed on: {failed_dimensions}"
        )

    def test_b_layer_defects_are_localized(
        self, hollow_candidate_page, anti_fill_grader_response
    ):
        """B 层打回的缺陷必须全部可定位。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_llm = make_mock_route_llm(anti_fill_grader_response)
        defects, _, _ = run_grader_with_fixture(hollow_candidate_page, mock_llm)

        for d in defects:
            assert d.location, f"Anti-fill defect missing location: {d.dimension}"
            assert d.defect, f"Anti-fill defect missing description: {d.dimension}"
            assert d.suggestion, f"Anti-fill defect missing suggestion: {d.dimension}"

    def test_real_page_passes_both(self):
        """对照: 真实质量页应通过 A 和 B 两层。"""
        from tests.quality.conftest import make_mock_route_llm

        # 使用完整的候选页
        real_page = """---
status: candidate
gate_decision: null
source_refs:
  - raw/loop-engineering.md
gate_reason: 待评审
future_questions:
  - Loop 在实时系统如何适配？
  - 多 agent 故障隔离设计？
related_concepts:
  - harness-engineering
  - agent-collaboration
---

## TLDR
Loop Engineering: bounded, convergent, observable agent loops [raw §A].
Five-component template for loop design [raw §B].
Convergence over restart reduces cost [raw §C].

## 关键发现
1. Bounded loops prevent runaway costs [raw §A]
2. Five components: trigger, selector, executor, gate, ledger [raw §B]
3. Fix defects, don't restart [raw §C]

## 入选理由
Complete loop design template filling a methodology gap in AKB.

## 证据地图
| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| Loop 五组件 | §B | strong | 实时场景 |

## 核心论点
1. Bounded loops [raw §A]: explicit stop (source_grounded)
2. Five components [raw §B]: trigger/selector/executor/gate/ledger (source_grounded)
3. Convergence [raw §C]: fix defects, not restart (source_grounded)

## 论证与解释
Write ledger before executing — crash recovery reads last entry [raw §B].

## 适用边界
适用: Agent pipeline, content QA
不适用: Simple CRUD, one-off migrations

## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| harness-engineering | upstream | strong | text says "one level up" |

## Future Questions
1. Loop engineering in real-time systems?
2. Fault isolation in multi-agent loops?

## 维护记录
- 2026-06-13: created
"""

        all_pass = {
            "gpt-5.4": LLMResponse(
                content={
                    "dimensions": [
                        {"dimension": dim, "pass": True, "defects": []}
                        for dim in RUBRIC_DIMENSIONS
                    ]
                },
                model_used="gpt-5.4",
                calls_used=1,
            ),
        }

        # A 通过
        a_defects = run_structural_checks(real_page)
        assert len(a_defects) == 0

        # B 通过
        mock_llm = make_mock_route_llm(all_pass)
        b_defects, _, suspended = run_grader_with_fixture(real_page, mock_llm)
        assert not suspended
        assert len(b_defects) == 0