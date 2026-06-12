"""修复循环测试 — 定点修复 → diff 校验 → A+B 复检 → 不收敛签名落盘。

全 fixture LLM 响应，不打真 API。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.akb2.quality.llm_router import LLMResponse
from src.akb2.quality.checks_structural import (
    Defect,
    run_structural_checks,
    _extract_body_text,
    _get_section_text,
)
from src.akb2.quality.repair import (
    MAX_REPAIR_ROUNDS,
    run_repair_loop,
    _verify_unchanged_sections,
    _build_repair_instruction,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def candidate_with_defects() -> str:
    """有已知缺陷的候选页（A 层: 缺 gate_reason 超长; B 层: TLDR 无锚点）。"""
    return """---
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
Loop Engineering is important for AI agents. It helps with bounded loops.

## 关键发现
1. Bounded loops are important
2. Convergence matters

## 入选理由
This fills a gap in AKB methodology.

## 证据地图
| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| test | N/A | strong | none |

## 核心论点
1. Bounded loops: must have explicit stop conditions (source_grounded)
2. Convergence: Fix defects, not restart (source_grounded)

## 论证与解释
Loops need explicit budgets and observable failure modes.

## 适用边界
适用: Agent pipelines
不适用: Simple scripts

## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| harness-engineering | upstream | strong | text says "one level up" |

## Future Questions
1. Loop engineering in real-time?
2. Multi-agent fault isolation?

## 维护记录
- 2026-06-13: created
"""


@pytest.fixture
def repaired_candidate() -> str:
    """修复后的候选页（fixture 响应: 修复了 TLDR 锚点 + 核心论点锚点）。"""
    return """---
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
Convergence over restart reduces cost significantly [raw §C].

## 关键发现
1. Bounded loops are important
2. Convergence matters

## 入选理由
This fills a gap in AKB methodology.

## 证据地图
| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| test | N/A | strong | none |

## 核心论点
1. Bounded loops [raw §A]: must have explicit stop conditions (source_grounded)
2. Convergence [raw §C]: Fix defects, not restart (source_grounded)

## 论证与解释
Loops need explicit budgets and observable failure modes.

## 适用边界
适用: Agent pipelines
不适用: Simple scripts

## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| harness-engineering | upstream | strong | text says "one level up" |

## Future Questions
1. Loop engineering in real-time?
2. Multi-agent fault isolation?

## 维护记录
- 2026-06-13: created
"""


@pytest.fixture
def repair_fixture_response(repaired_candidate) -> dict:
    """DeepSeek 修复 fixture 响应: 返回修复后的候选页。"""
    return {
        "deepseek-v4-pro": LLMResponse(
            content=repaired_candidate,
            model_used="deepseek-v4-pro",
            calls_used=1,
        ),
    }


@pytest.fixture
def grader_pass_response() -> dict:
    """B 层评分全 pass 的 fixture 响应。"""
    return {
        "gpt-5.4": LLMResponse(
            content={
                "dimensions": [
                    {"dimension": dim, "pass": True, "defects": []}
                    for dim in [
                        "论断锚定率", "How 密度", "How 可执行性", "证据诚实",
                        "解读分离", "TLDR 密度", "新颖增量", "边强度匹配",
                        "Future Questions 质量", "适用边界",
                    ]
                ]
            },
            model_used="gpt-5.4",
            calls_used=1,
        ),
    }


@pytest.fixture
def temp_failures_path():
    """临时 failures.jsonl 路径。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    ) as f:
        f.write("")
    path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 修复指令模板
# ---------------------------------------------------------------------------


class TestRepairInstruction:
    def test_builds_from_defects(self):
        """缺陷清单 → 模板化修复指令。"""
        defects = [
            Defect(
                dimension="A1-模板节齐",
                location="## TLDR",
                defect="缺少 TLDR 节",
                suggestion="添加 TLDR 节",
            ),
            Defect(
                dimension="B-论断锚定率",
                location="核心论点",
                defect="无锚点引用",
                suggestion="添加 [raw §X] 锚点",
            ),
        ]
        instruction = _build_repair_instruction(defects)
        assert "缺少 TLDR 节" in instruction
        assert "无锚点引用" in instruction
        assert "添加 TLDR 节" in instruction
        assert "添加 [raw §X] 锚点" in instruction
        assert "**[" in instruction  # 格式正确


# ---------------------------------------------------------------------------
# diff 校验
# ---------------------------------------------------------------------------


class TestDiffVerification:
    def test_unchanged_sections_pass(self, candidate_with_defects, repaired_candidate):
        """修复只改了被点名小节 → 未点名小节全部不变。"""
        named = {"TLDR", "核心论点"}
        changed = _verify_unchanged_sections(
            candidate_with_defects, repaired_candidate, named
        )
        # 关键发现/入选理由/证据地图/论证与解释/适用边界/图谱关系/Future Questions/维护记录 应该不变
        assert len(changed) == 0, (
            f"Unchanged sections modified: {changed}"
        )

    def test_changed_section_detected(self, candidate_with_defects):
        """如果修改了未点名小节 → 被检测到。"""
        named = {"TLDR"}  # 只允许改 TLDR

        # 构造一个改了关键发现的版本
        modified = candidate_with_defects.replace(
            "## 关键发现\n1. Bounded loops are important",
            "## 关键发现\n1. COMPLETELY DIFFERENT CONTENT",
        )
        changed = _verify_unchanged_sections(candidate_with_defects, modified, named)
        assert "关键发现" in changed, f"Should detect modified section: {changed}"

    def test_named_sections_allowed_to_change(
        self, candidate_with_defects, repaired_candidate
    ):
        """被点名小节允许修改。"""
        named = {"TLDR", "核心论点"}
        changed = _verify_unchanged_sections(
            candidate_with_defects, repaired_candidate, named
        )
        # TLDR 和核心论点被改了，但它们在 named 中，所以不应出现在 changed
        for section in changed:
            assert section not in named, (
                f"Named section {section} should be allowed to change"
            )


# ---------------------------------------------------------------------------
# 修复循环: 一轮收敛
# ---------------------------------------------------------------------------


class TestRepairLoopConverge:
    def test_single_round_convergence(
        self,
        candidate_with_defects,
        repaired_candidate,
        repair_fixture_response,
        grader_pass_response,
        temp_failures_path,
    ):
        """一轮修复后 A+B 全过 → 收敛。"""
        from tests.quality.conftest import make_mock_route_llm

        # 合并 repair LLM + grader LLM 响应
        all_responses = {
            **repair_fixture_response,
            **grader_pass_response,
        }
        mock_llm = make_mock_route_llm(all_responses)

        # A 层缺陷: candidate_with_defects 的 TLDR 无锚点
        a_defects = run_structural_checks(candidate_with_defects)
        # B 层缺陷: 模拟
        b_defects = [
            Defect(
                dimension="论断锚定率",
                location="TLDR",
                defect="TLDR 无 [raw §X] 锚点",
                suggestion="为 TLDR 每句添加锚点引用",
            ),
            Defect(
                dimension="论断锚定率",
                location="核心论点",
                defect="核心论点缺少锚点",
                suggestion="为每个论点添加 [raw §X] 锚点",
            ),
        ]

        result = run_repair_loop(
            candidate_text=candidate_with_defects,
            a_defects=a_defects,
            b_defects=b_defects,
            failures_path=temp_failures_path,
            mock_route_llm=mock_llm,
        )

        assert result["converged"], (
            f"Should converge in 1 round. Defects remaining: {result['final_defects']}"
        )
        assert result["rounds"] <= MAX_REPAIR_ROUNDS
        assert result["final_text"] is not None
        assert len(result["final_defects"]) == 0
        assert not result["signature_written"]

    def test_no_defects_skips_repair(self, candidate_with_defects, temp_failures_path):
        """零缺陷输入 → 直接返回收敛，零轮修复。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_llm = make_mock_route_llm({})

        result = run_repair_loop(
            candidate_text=candidate_with_defects,
            a_defects=[],
            b_defects=[],
            failures_path=temp_failures_path,
            mock_route_llm=mock_llm,
        )

        assert result["converged"]
        assert result["rounds"] == 0
        assert result["final_text"] == candidate_with_defects


# ---------------------------------------------------------------------------
# 修复循环: 不收敛
# ---------------------------------------------------------------------------


class TestRepairLoopNoConverge:
    def test_max_rounds_exhausted_fail_closed(
        self, candidate_with_defects, temp_failures_path
    ):
        """3 轮修复后仍有缺陷 → fail-closed, 签名落盘。"""
        from tests.quality.conftest import make_mock_route_llm

        # 每轮修复都返回同样的有缺陷的文本（模拟始终修不好）
        def mock_always_broken(model_family, messages, response_schema=None):
            if model_family == "deepseek-v4-pro":
                return LLMResponse(
                    content=candidate_with_defects,  # 返回原样，未修复
                    model_used="deepseek-v4-pro",
                    calls_used=1,
                )
            elif model_family == "gpt-5.4":
                # B 层始终报告缺陷
                return LLMResponse(
                    content={
                        "dimensions": [
                            {
                                "dimension": "论断锚定率",
                                "pass": False,
                                "defects": [
                                    {
                                        "location": "TLDR",
                                        "defect": "TLDR 无锚点",
                                        "suggestion": "添加锚点",
                                    }
                                ],
                            },
                        ]
                        + [
                            {"dimension": dim, "pass": True, "defects": []}
                            for dim in [
                                "How 密度", "How 可执行性", "证据诚实",
                                "解读分离", "TLDR 密度", "新颖增量",
                                "边强度匹配", "Future Questions 质量",
                                "适用边界",
                            ]
                        ]
                    },
                    model_used="gpt-5.4",
                    calls_used=1,
                )
            return LLMResponse(content={}, model_used=model_family)

        a_defects = run_structural_checks(candidate_with_defects)
        b_defects = [
            Defect(
                dimension="论断锚定率",
                location="TLDR",
                defect="TLDR 无锚点",
                suggestion="添加锚点",
            ),
        ]

        result = run_repair_loop(
            candidate_text=candidate_with_defects,
            a_defects=a_defects,
            b_defects=b_defects,
            failures_path=temp_failures_path,
            mock_route_llm=mock_always_broken,
        )

        assert not result["converged"], "Should fail-closed after max rounds"
        assert result["rounds"] == MAX_REPAIR_ROUNDS
        assert result["signature_written"]
        assert result["failure_signature"] is not None
        assert "distill_no_converge" in result["failure_signature"]

        # 验证签名文件已写入
        failures_path = Path(temp_failures_path)
        assert failures_path.exists(), "Failures file should exist"
        content = failures_path.read_text()
        assert "distill_no_converge" in content

    def test_signature_follows_schema(self, temp_failures_path):
        """不收敛签名符合 failure-signature.schema.json。"""
        from tests.quality.conftest import make_mock_route_llm

        def mock_always_broken(model_family, messages, response_schema=None):
            if model_family == "deepseek-v4-pro":
                return LLMResponse(
                    content="""---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
---

## TLDR
test

## 关键发现
test

## 入选理由
test

## 证据地图
test

## 核心论点
test

## 论证与解释
test

## 适用边界
test

## 图谱关系
test

## Future Questions
test

## 维护记录
test
""",
                    model_used="deepseek-v4-pro",
                    calls_used=1,
                )
            elif model_family == "gpt-5.4":
                return LLMResponse(
                    content={
                        "dimensions": [
                            {
                                "dimension": "论断锚定率",
                                "pass": False,
                                "defects": [
                                    {
                                        "location": "TLDR",
                                        "defect": "missing anchor",
                                        "suggestion": "add anchor",
                                    }
                                ],
                            },
                        ]
                        + [
                            {"dimension": d, "pass": True, "defects": []}
                            for d in [
                                "How 密度", "How 可执行性", "证据诚实",
                                "解读分离", "TLDR 密度", "新颖增量",
                                "边强度匹配", "Future Questions 质量",
                                "适用边界",
                            ]
                        ]
                    },
                    model_used="gpt-5.4",
                    calls_used=1,
                )
            return LLMResponse(content={}, model_used=model_family)

        run_repair_loop(
            candidate_text="""---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
---

## TLDR
test
## 关键发现
test
## 入选理由
test
## 证据地图
test
## 核心论点
test
## 论证与解释
test
## 适用边界
test
## 图谱关系
test
## Future Questions
test
## 维护记录
test
""",
            a_defects=[],
            b_defects=[
                Defect(
                    dimension="论断锚定率",
                    location="TLDR",
                    defect="missing anchor",
                    suggestion="add anchor",
                ),
            ],
            failures_path=temp_failures_path,
            mock_route_llm=mock_always_broken,
        )

        # 验证 JSONL 每行可解析
        failures_path = Path(temp_failures_path)
        assert failures_path.exists()
        lines = failures_path.read_text().strip().split("\n")
        for line in lines:
            if line.strip():
                record = json.loads(line)
                assert "loop" in record
                assert "node" in record
                assert "error_class" in record
                assert "count" in record
                assert "ts" in record


# ---------------------------------------------------------------------------
# diff 校验: 未点名小节被改动 → fail
# ---------------------------------------------------------------------------


class TestRepairDiffRejection:
    def test_modified_unnamed_section_rejected(
        self, candidate_with_defects, temp_failures_path
    ):
        """修复改动了未点名小节 → 该轮重写被丢弃。"""
        from tests.quality.conftest import make_mock_route_llm

        # 修复返回的文本改了"关键发现"（未点名小节）
        tampered_text = candidate_with_defects.replace(
            "## 关键发现\n1. Bounded loops are important",
            "## 关键发现\n1. COMPLETELY DIFFERENT AND WRONG",
        )

        call_count = [0]

        def mock_tampered_then_fix(model_family, messages, response_schema=None):
            call_count[0] += 1
            if model_family == "deepseek-v4-pro":
                if call_count[0] == 1:
                    # 第一轮: 改了未点名小节（应被拒绝）
                    return LLMResponse(
                        content=tampered_text,
                        model_used="deepseek-v4-pro",
                        calls_used=1,
                    )
                else:
                    # 后续轮: 正确修复（只改 TLDR 和核心论点）
                    return LLMResponse(
                        content="""---
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

## 关键发现
1. Bounded loops are important
2. Convergence matters

## 入选理由
This fills a gap in AKB methodology.

## 证据地图
| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| test | N/A | strong | none |

## 核心论点
1. Bounded loops [raw §A]: must have explicit stop conditions (source_grounded)
2. Convergence [raw §C]: Fix defects, not restart (source_grounded)

## 论证与解释
Loops need explicit budgets and observable failure modes.

## 适用边界
适用: Agent pipelines
不适用: Simple scripts

## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| harness-engineering | upstream | strong | text says "one level up" |

## Future Questions
1. Loop engineering in real-time?
2. Multi-agent fault isolation?

## 维护记录
- 2026-06-13: created
""",
                        model_used="deepseek-v4-pro",
                        calls_used=1,
                    )
            elif model_family == "gpt-5.4":
                return LLMResponse(
                    content={
                        "dimensions": [
                            {"dimension": d, "pass": True, "defects": []}
                            for d in [
                                "论断锚定率", "How 密度", "How 可执行性", "证据诚实",
                                "解读分离", "TLDR 密度", "新颖增量", "边强度匹配",
                                "Future Questions 质量", "适用边界",
                            ]
                        ]
                    },
                    model_used="gpt-5.4",
                    calls_used=1,
                )
            return LLMResponse(content={}, model_used=model_family)

        a_defects = run_structural_checks(candidate_with_defects)
        b_defects = [
            Defect(
                dimension="论断锚定率",
                location="TLDR",
                defect="TLDR 无锚点",
                suggestion="添加锚点",
            ),
            Defect(
                dimension="论断锚定率",
                location="核心论点",
                defect="核心论点缺锚点",
                suggestion="添加锚点",
            ),
        ]

        result = run_repair_loop(
            candidate_text=candidate_with_defects,
            a_defects=a_defects,
            b_defects=b_defects,
            failures_path=temp_failures_path,
            mock_route_llm=mock_tampered_then_fix,
        )

        # 第一轮被拒绝，后续轮修复成功 → 最终收敛
        assert result["converged"], (
            "Should converge after rejecting tampered round"
        )


# ---------------------------------------------------------------------------
# MAX_REPAIR_ROUNDS 具名常量
# ---------------------------------------------------------------------------


class TestMaxRepairRounds:
    def test_constant_is_named(self):
        """MAX_REPAIR_ROUNDS 是模块级具名常量。"""
        assert MAX_REPAIR_ROUNDS == 3
        assert isinstance(MAX_REPAIR_ROUNDS, int)

    def test_constant_used_in_loop(self):
        """run_repair_loop 使用 MAX_REPAIR_ROUNDS 而非内联魔数。"""
        import inspect
        source = inspect.getsource(run_repair_loop)
        assert "MAX_REPAIR_ROUNDS" in source, (
            "run_repair_loop must use MAX_REPAIR_ROUNDS constant"
        )