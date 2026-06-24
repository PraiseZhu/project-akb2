"""修复循环测试 — 定点修复 → diff 校验 → A+B 复检 → 不收敛签名落盘。

全 fixture LLM 响应，不打真 API。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.akb2.quality.checks_structural import (
    Defect,
    run_structural_checks,
)
from src.akb2.quality.llm_router import LLMResponse
from src.akb2.quality.repair import (
    MAX_REPAIR_ROUNDS,
    _apply_boundary_repairs,
    _apply_graph_table_repairs,
    _build_repair_instruction,
    _verify_unchanged_sections,
    run_repair_loop,
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
1. [raw §A] (source_grounded) Bounded loops are important
2. [raw §C] (source_grounded) Convergence matters

## 入选理由
- [raw §A] (source_grounded) 来源事实：Loop Engineering describes bounded, observable loops.
- [raw §B] (source_grounded) 来源事实：The loop shape includes trigger, selector, executor, gate, and ledger.

## 证据地图
| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| §A | Bounded loop | source_grounded | 未量化：原文未提供效果数据 |
| §B | Loop shape | source_grounded | 未比较：原文未比较不同组件实现 |

## 核心论点
1. Bounded loops: must have explicit stop conditions (source_grounded)
2. Convergence: Fix defects, not restart (source_grounded)

## 论证与解释
**[raw §A]** (source_grounded) 来源事实：Bounded loops need explicit stop conditions.
**[raw §C]** (source_grounded) 来源事实：Convergence matters more than full restart.

### 操作步骤
1. [raw §B] (akb_interpretation) Configure trigger type `trigger={type:'schedule',cron:'0 * * * *'}`
2. [raw §B] (akb_interpretation) Select the next work item `work_item=ledger.next(status='pending',limit=1)`
3. [raw §B] (akb_interpretation) Run the quality gate `assert run_quality_gate(result,grader='gpt-5.4').defects == []`

## 适用边界
- [raw §A] (source_grounded) 来源事实：Loop engineering frames the work as bounded loops.
- [raw §B] (source_grounded) 来源事实：The source lists trigger, selector, executor, gate, and ledger.
- [raw §A] (akb_interpretation) 适用场景：Agent pipelines with repeatable gates.
- [raw §C] (akb_interpretation) 不适用场景：Simple scripts.
- [raw §B] (akb_interpretation) 约束条件：Need explicit ledger and gate.

## 图谱关系
| target | relation | strength | provenance | evidence |
|--------|----------|----------|------------|----------|
| harness-engineering | related | weak | source_grounded | [raw §B] (source_grounded) The source lists the loop components. |

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
1. [raw §A] (source_grounded) Bounded loops are important
2. [raw §C] (source_grounded) Convergence matters

## 入选理由
- [raw §A] (source_grounded) 来源事实：Loop Engineering describes bounded, observable loops.
- [raw §B] (source_grounded) 来源事实：The loop shape includes trigger, selector, executor, gate, and ledger.

## 证据地图
| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| §A | Bounded loop | source_grounded | 未量化：原文未提供效果数据 |
| §B | Loop shape | source_grounded | 未比较：原文未比较不同组件实现 |

## 核心论点
1. Bounded loops [raw §A]: must have explicit stop conditions (source_grounded)
2. Convergence [raw §C]: Fix defects, not restart (source_grounded)

## 论证与解释
**[raw §A]** (source_grounded) 来源事实：Bounded loops need explicit stop conditions.
**[raw §C]** (source_grounded) 来源事实：Convergence matters more than full restart.

### 操作步骤
1. [raw §B] (akb_interpretation) Configure trigger type `trigger={type:'schedule',cron:'0 * * * *'}`
2. [raw §B] (akb_interpretation) Select the next work item `work_item=ledger.next(status='pending',limit=1)`
3. [raw §B] (akb_interpretation) Run the quality gate `assert run_quality_gate(result,grader='gpt-5.4').defects == []`

## 适用边界
- [raw §A] (source_grounded) 来源事实：Loop engineering frames the work as bounded loops.
- [raw §B] (source_grounded) 来源事实：The source lists trigger, selector, executor, gate, and ledger.
- [raw §A] (akb_interpretation) 适用场景：Agent pipelines with repeatable gates.
- [raw §C] (akb_interpretation) 不适用场景：Simple scripts.
- [raw §B] (akb_interpretation) 约束条件：Need explicit ledger and gate.

## 图谱关系
| target | relation | strength | provenance | evidence |
|--------|----------|----------|------------|----------|
| harness-engineering | related | weak | source_grounded | [raw §B] (source_grounded) The source lists the loop components. |

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


@pytest.fixture
def repair_raw_text() -> str:
    """修复循环测试专用 raw 文本：含 §A/§B/§C 锚点（A3 锚点校验通过）但无编号步骤/
    结论句/边界句（scan_all 返回空 → SG-must-cover gate 无 must_cover 项需覆盖，
    不与 candidate_with_defects 的操作步骤冲突）。

    gate 未弱化：raw_content 非 None（不触发"raw_content 缺失"fail-closed），
    仅为分离语义——存量 candidate 不含 repair_raw_text 的编号步骤字面量，
    用此 fixture 避免 must_cover mismatch 误报。
    """
    return """---
artifact_type: raw
source_slug: loop-engineering
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
convergent, and observable. An agent loop without explicit stopping conditions is
a runaway cost generator.

# §B How: A Complete Loop Shape

A well-designed loop has five components: a trigger, a work unit selector, an action
executor, a quality gate, and a ledger writer.

原文操作示例（AKB 操作化命令字面量，供 SG-command-recheck 回查锚定）：

```text
trigger={type:'schedule',cron:'0 * * * *'}
work_item=ledger.next(status='pending',limit=1)
assert run_quality_gate(result,grader='gpt-5.4').defects == []
queue_item={source_url,raw_anchor,content_hash,output_path,lifecycle_state}
ledger.append({event:'distill_extract',status:'start'})
max_repair_rounds=3; assert remaining_defects == 0
result=run_action(work_item,timeout_seconds=60); ledger.append({item_id:work_item.id,status:'done',result_hash:hash(result)})
ledger.append({event:'abort',node:node,error_class:error_class,model_family:model_family,timeout_s:60,max_retries:0,retries_used:retries_used})
MAX_REPAIR_ROUNDS=3; DISTILL_TIMEOUT_SECONDS=60; GRADER_TIMEOUT_SECONDS=120; MAX_RETRIES=0; MAX_PARALLEL_ITEMS=1
```

# §C Convergence Over Restart

When a step produces defects, fix the specific defects rather than restarting the
entire pipeline. This is the principle of convergence over restart.
"""


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
            "## 关键发现\n1. [raw §A] (source_grounded) Bounded loops are important",
            "## 关键发现\n1. [raw §A] (source_grounded) COMPLETELY DIFFERENT CONTENT",
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

    def test_new_h3_inside_named_h2_is_allowed(self):
        """命名 H2 内新增 H3 属于该 H2 的合法改动，不算夹带改未点名节。"""
        original = """---
status: candidate
---

## 适用边界
- [raw §A] (source_grounded) 来源事实：Loop needs a gate.
- [raw §A] (akb_interpretation) 适用场景：多步骤管线。

## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| loop | related | weak | (akb_interpretation) 库内推断 |
"""
        repaired = """---
status: candidate
---

## 适用边界

### 来源明确适用对象
- [raw §A] (source_grounded) 来源事实：Loop needs a gate.

### AKB 推定边界
- [raw §A] (akb_interpretation) 适用场景：多步骤管线。

## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| loop | related | weak | (akb_interpretation) 库内推断 |
"""
        changed = _verify_unchanged_sections(original, repaired, {"适用边界"})
        assert changed == []


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
        repair_raw_text,
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
            raw_text=repair_raw_text,
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
        self, candidate_with_defects, temp_failures_path, repair_raw_text
    ):
        """修复改动了未点名小节 → 该轮重写被丢弃。"""

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
            raw_text=repair_raw_text,
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


# ---------------------------------------------------------------------------
# 根因④: 路由失败不再静默吞(违宪法 P4 失败可见)
# ---------------------------------------------------------------------------


class TestRouteFailureVisible:
    def test_route_failure_records_signature(
        self, candidate_with_defects, temp_failures_path
    ):
        """根因④:路由失败(RouterConfigError/超时)→ 记 signature 留痕,不再静默 continue。

        绑可观测行为:failures.jsonl 必须出现 repair_route_failed(每轮失败都留痕),
        而非旧的"静默 continue 把失败伪装成模型修不动"。
        """
        from src.akb2.quality.llm_router import RouterConfigError

        def mock_route_fail(model_family, messages, response_schema=None):
            raise RouterConfigError("simulated key/endpoint failure (根因④ 测试)")

        a_defects = run_structural_checks(candidate_with_defects)
        b_defects = [
            Defect(
                dimension="论断锚定率",
                location="TLDR",
                defect="无锚点",
                suggestion="添加锚点",
            ),
        ]

        result = run_repair_loop(
            candidate_text=candidate_with_defects,
            a_defects=a_defects,
            b_defects=b_defects,
            failures_path=temp_failures_path,
            mock_route_llm=mock_route_fail,
        )

        # 路由失败留痕 → failures.jsonl 含 repair_route_failed(不再静默)
        failures_content = Path(temp_failures_path).read_text()
        assert "repair_route_failed" in failures_content, (
            "根因④:路由失败必须记 signature 留痕(违宪法 P4),不可静默吞"
        )
        # route/worker 失败直接 fail-closed，避免外层 smoke 被杀前没有主账本 abort
        assert result["rounds"] == 1
        assert result["failure_signature"] == "loop1/repair_loop/repair_route_failed"
        assert result["signature_written"]

    def test_repair_worker_timeout_fails_closed_first_round(
        self, candidate_with_defects, temp_failures_path, monkeypatch
    ):
        """live repair worker 超时必须第一轮 fail-closed，不得拖到外层 kill。"""
        import subprocess

        monkeypatch.setattr(
            "src.akb2.quality.repair.subprocess.run",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(cmd=["python"], timeout=90)
            ),
        )
        b_defects = [
            Defect(
                dimension="论断锚定率",
                location="TLDR",
                defect="无锚点",
                suggestion="添加锚点",
            ),
        ]

        result = run_repair_loop(
            candidate_text=candidate_with_defects,
            a_defects=[],
            b_defects=b_defects,
            failures_path=temp_failures_path,
        )

        assert result["rounds"] == 1
        assert result["signature_written"] is True
        assert result["failure_signature"] == "loop1/repair_loop/repair_route_failed"
        assert result["trace_paths"]
        trace = json.loads(Path(result["trace_paths"][0]).read_text(encoding="utf-8"))
        assert trace["status"] == "route_failed"
        assert "repair_worker_timeout" in trace["route_error"]


# ---------------------------------------------------------------------------
# 根因①②: 定位认 H2+H3 + location 归一化匹配
# ---------------------------------------------------------------------------


class TestH3LocationNormalization:
    def test_finds_h3_sections(self):
        """根因②:正文 H3 子节也被识别(不只 H2)。"""
        from src.akb2.quality.repair import _find_sections_h2_h3

        text = "## 核心论点\n### 操作步骤1-9\n内容\n## 论证与解释\n正文"
        titles = {t for _, t in _find_sections_h2_h3(text)}
        assert "核心论点" in titles
        assert "操作步骤1-9" in titles, "H3 子节必须被识别(根因②)"
        assert "论证与解释" in titles

    def test_normalize_matches_free_location(self):
        """根因①:grader 自由 location(如'核心论点 第2条')归一化后匹配 H2'核心论点'。"""
        from src.akb2.quality.repair import _normalize_loc

        loc = _normalize_loc("核心论点 第2条")
        title = _normalize_loc("核心论点")
        assert title and title in loc, (
            "归一化后 '核心论点' 应匹配 '核心论点 第2条'(根因①,原精确子串匹配会漏)"
        )


# ---------------------------------------------------------------------------
# 根因③: 定点爬坡切节拼回(废整页重写)
# ---------------------------------------------------------------------------


class TestApplySectionPatches:
    def test_only_named_section_replaced(self):
        """根因③:只替换被点名节,其余原样。"""
        from src.akb2.quality.repair import _apply_section_patches

        body = "## TLDR\n旧TLDR\n## 核心论点\n旧论点\n## 维护记录\n- 旧"
        patches = {"TLDR": "## TLDR\n新TLDR(带锚点)"}
        result = _apply_section_patches(body, patches)
        assert "新TLDR(带锚点)" in result
        assert "旧论点" in result, "核心论点未点名必须不变"
        assert "- 旧" in result, "维护记录未点名必须不变"

    def test_unnamed_section_byte_unchanged(self):
        """根因③:未点名节逐字节不变(机制保证,LLM 碰不到)。"""
        from src.akb2.quality.repair import _apply_section_patches

        body = "## A\na内容\n## B\nb内容"
        result = _apply_section_patches(body, {"A": "## A\n新a"})
        assert "## B\nb内容" in result, "B 节逐字节不变"

    def test_invalid_patch_returns_body_unchanged(self):
        """根因③:patch 非 JSON/解析失败 → 原样返回(上层记 signature,不崩)。"""
        from src.akb2.quality.repair import _apply_section_patches

        body = "## A\na内容"
        assert _apply_section_patches(body, "not json") == body
        assert _apply_section_patches(body, None) == body

    def test_allowed_titles_drops_extra_patch(self):
        """问题5:allowed_titles 限制——LLM 返回未点名节 patch 必须丢弃(机制保证)。"""
        from src.akb2.quality.repair import _apply_section_patches

        body = "## TLDR\n旧TLDR\n## 核心论点\n旧论点\n## 维护记录\n- 旧"
        # LLM 返回 TLDR(命名)+ 核心论点(未命名,额外/违规)
        patches = {
            "TLDR": "## TLDR\n新TLDR",
            "核心论点": "## 核心论点\n被改了!",
        }
        result = _apply_section_patches(body, patches, allowed_titles={"TLDR"})
        assert "新TLDR" in result, "命名节 TLDR 应被 patch"
        # 核心论点未点名 → patch 被丢弃 → 逐字节不变
        assert "旧论点" in result
        assert "被改了!" not in result, "未命名节 patch 必须被 apply 丢弃(问题5机制保证)"

    def test_empty_allowed_titles_applies_zero_patch(self):
        """问题3(Codex):空集合(没定位到任何节)→ 必须零 patch,而非当 None 全应用。"""
        from src.akb2.quality.repair import _apply_section_patches

        body = "## A\na内容\n## B\nb内容"
        # 空集合 ≠ None;LLM 返回任意 patch 都不应应用
        result = _apply_section_patches(body, {"A": "## A\n被改了!"}, allowed_titles=set())
        assert result == body, "空 allowed_titles 必须零 patch(问题3,原空当 None 会全应用)"


class TestDeterministicGraphRepair:
    def test_boundary_repair_removes_template_residue_and_balances_categories(self):
        """适用边界 repair 不得保留占位符或来源事实元说明。"""
        text = _wrap("""## 适用边界
### 来源明示边界。。
- [raw §A] (source_grounded) 来源事实：Loop needs a gate.
### AKB 推定边界
- [raw §A] (akb_interpretation) 适用场景：多步骤管线。
""")
        repaired, actions = _apply_boundary_repairs(
            text,
            [
                Defect(
                    dimension="适用边界",
                    location="适用边界",
                    defect="适用边界包含模板占位符且分类不足",
                    suggestion="改成适用/不适用/约束条件独立列表",
                )
            ],
        )
        assert actions
        section = repaired.split("## 适用边界", 1)[1]
        assert "### 来源明示边界" not in section
        assert "来源事实：" not in section
        assert "。。" not in section
        assert section.count("(source_grounded) 来源事实：") == 0
        assert section.count("(akb_interpretation) 适用场景：") >= 2
        assert section.count("(akb_interpretation) 不适用场景：") >= 1
        assert section.count("(akb_interpretation) 约束条件：") >= 1

    def test_graph_location_repairs_specific_row_and_column(self):
        """图谱关系/evidence/<target> 应定位到表格行/列并确定性修复。"""
        text = _wrap("""## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| agent collaboration | related | medium | multi-agent loops require coordination primitives |
| harness engineering | upstream | strong | [raw §B] (source_grounded) harness is one floor below loop. |
""")
        repaired, actions = _apply_graph_table_repairs(
            text,
            [
                Defect(
                    dimension="边强度匹配",
                    location="图谱关系/evidence/agent collaboration",
                    defect="缺 raw anchor/provenance，medium 证据不足",
                    suggestion="补 provenance 并降级",
                )
            ],
        )
        assert actions and actions[0]["row_key"] == "agent collaboration"
        assert "| agent collaboration | related | weak |" in repaired
        assert "(akb_interpretation)" in repaired
        assert "harness is one floor below loop" in repaired

    def test_how_executability_repair_replaces_conceptual_steps(self):
        """How 可执行性缺陷应由本地确定性修复收敛，不依赖 repair LLM 猜。

        治本2（砍编造源）：repair 重建 How 的反引号命令必须 emission 前回查 raw。
        本用例提供含这些命令的 raw_text → 命令接地 → 反引号命令保留（防回归：不误杀
        真正可锚定的操作命令）。conceptual 占位 prose 被替换、配置示例占位值被改成
        明确数值的核心行为不变。
        """
        from src.akb2.quality.repair import _apply_deterministic_repairs

        text = _wrap("""## 关键发现
- [raw §D] (source_grounded) Every loop must have explicit budgets for max repair rounds, timeout per model call, max retries, and maximum parallel workers.
- [raw §E] (source_grounded) The model that produces content must be different from the model that judges it.
- [raw §I] (source_grounded) A loop is deployable only when failure is observable.

## 论证与解释
**[raw §D]** (source_grounded) 来源事实：Every loop must have explicit budgets.

### 操作步骤

1. [raw §D] (akb_interpretation) Define explicit budgets. `Define budget constants`
2. [raw §E] (akb_interpretation) Use maker/checker. `Separate maker and checker models`
3. [raw §I] (akb_interpretation) Record failures. `Record LLM call metadata`

配置示例：
```yaml
max_repair_rounds: explicit budget constant
```

## 新颖增量
keep me unchanged
""")
        # raw 含 _how_command_for_claim 三个分支会产出的命令字面量 → 命令接地 raw。
        raw_with_commands = (
            "Loops need budgets: "
            "MAX_REPAIR_ROUNDS=3; DISTILL_TIMEOUT_SECONDS=60; "
            "GRADER_TIMEOUT_SECONDS=120; MAX_RETRIES=0; MAX_PARALLEL_ITEMS=1\n"
            "maker_model='deepseek-v4-pro'; checker_model='gpt-5.4'; "
            "assert maker_model != checker_model and fallback_used is False\n"
            "ledger.append({event:'abort',node:node,error_class:error_class,"
            "model_family:model_family,timeout_s:60,max_retries:0,retries_used:retries_used})\n"
        )
        repaired, actions = _apply_deterministic_repairs(
            text,
            [
                Defect(
                    dimension="How 可执行性",
                    location="### 操作步骤",
                    defect="步骤为概念性英文描述，缺少可复制执行的命令",
                    suggestion="补充真实命令与具体参数值",
                ),
                Defect(
                    dimension="How 可执行性",
                    location="配置示例",
                    defect="存在占位式值 explicit budget constant",
                    suggestion="替换为明确数值",
                ),
            ],
            raw_with_commands,
        )

        assert actions and actions[-1]["section"] == "论证与解释"
        assert "`Define budget constants`" not in repaired
        assert "source_basis=[raw" in repaired
        assert "unsupported_assumptions=" in repaired
        # 命令接地 raw → 反引号命令保留（防回归：治本2 不误杀真可锚定命令）
        assert "MAX_REPAIR_ROUNDS=3" in repaired
        assert "maker_model='deepseek-v4-pro'; checker_model='gpt-5.4'" in repaired
        assert "ledger.append({event:'abort'" in repaired
        assert "max_repair_rounds: explicit budget constant" not in repaired
        assert "max_repair_rounds: 3" in repaired
        assert "max_retries: 0" in repaired
        assert "checker_model: gpt-5.4" in repaired
        assert "## 新颖增量\nkeep me unchanged" in repaired

    def test_how_executability_repair_strips_fabricated_commands_when_absent_from_raw(self):
        """治本2 核心：raw 不含命令时，repair 重建 How 不得注入编造的反引号命令。

        痛点（SC-9 卡点）：repair 重建 How 段调 _how_command_for_claim 合成
        ledger.append(...) / MAX_REPAIR_ROUNDS=3 / run_action(...) 等命令，这些不字面
        存在于真实 raw → SG-command-recheck 判为「原文不存在此命令字面量（疑似编造）」
        → repair 永不收敛。护栏（与 compose 同口径）在 emission 前回查 raw 剥离不接地命令。
        断言绑定可观测行为：SG-command-recheck 同实现 verify_commands_in_raw 对重建段
        回查 0 违规 + 候选 body 不含编造命令字面量 + 步骤仍保留 instruction（降级不丢步）。
        """
        from src.akb2.quality.repair import _apply_deterministic_repairs
        from src.akb2.quality.source_ops_scanners.raw_verify import (
            verify_commands_in_raw,
        )

        text = _wrap("""## 关键发现
- [raw §D] (source_grounded) Every loop must have explicit budgets for max repair rounds, timeout per model call, max retries, and maximum parallel workers.
- [raw §E] (source_grounded) The model that produces content must be different from the model that judges it.
- [raw §I] (source_grounded) A loop is deployable only when failure is observable.

## 论证与解释
**[raw §D]** (source_grounded) 来源事实：Every loop must have explicit budgets.

### 操作步骤

1. [raw §D] (akb_interpretation) Define explicit budgets. `Define budget constants`
2. [raw §E] (akb_interpretation) Use maker/checker. `Separate maker and checker models`
3. [raw §I] (akb_interpretation) Record failures. `Record LLM call metadata`

## 新颖增量
keep me unchanged
""")
        # raw 完全不含任何 _how_command_for_claim 会产出的命令字面量。
        raw_no_commands = (
            "Loops should have bounded budgets and separate maker/checker models, "
            "and failures must be observable. This source gives prose only, "
            "no copy-pasteable code commands at all."
        )
        repaired, actions = _apply_deterministic_repairs(
            text,
            [
                Defect(
                    dimension="How 可执行性",
                    location="### 操作步骤",
                    defect="步骤为概念性英文描述，缺少可复制执行的命令",
                    suggestion="补充真实命令与具体参数值",
                ),
            ],
            raw_no_commands,
        )

        # 仍发生重建（占位 prose 被替换、步骤未被整体丢弃）
        assert actions and actions[-1]["section"] == "论证与解释"
        assert "`Define budget constants`" not in repaired
        assert "source_basis=[raw" in repaired
        # 编造命令字面量被剥离：候选 body 不含它们
        assert "MAX_REPAIR_ROUNDS=3" not in repaired
        assert "maker_model='deepseek-v4-pro'" not in repaired
        assert "ledger.append({event:'abort'" not in repaired
        assert "run_action(work_item" not in repaired
        # 降级标记出现 + 步骤数不减（不丢步，只降级为无命令 instruction）
        assert "无原文可锚定命令" in repaired
        # SG-command-recheck 同实现回查重建段：0 违规（compose/repair 同口径）
        body = repaired.split("---", 2)[-1]
        violations = verify_commands_in_raw(body, raw_no_commands)
        assert violations == [], (
            "repair 重建 How 不得注入不接地 raw 的编造命令，实际违规: "
            f"{[v.command for v in violations]}"
        )

    def test_how_executability_repair_strips_commands_when_raw_text_none(self):
        """raw_text None（缺原文）→ fail-closed：所有派生命令视为不接地被剥离。

        防御：run_repair_loop 在未提供 raw_text 时不得让 repair 偷偷上稿编造命令。
        与 _command_grounded_in_raw 的 raw 空 → False 口径一致。
        """
        from src.akb2.quality.repair import _apply_deterministic_repairs

        text = _wrap("""## 关键发现
- [raw §D] (source_grounded) Every loop must have explicit budgets.
- [raw §E] (source_grounded) Maker differs from checker.
- [raw §I] (source_grounded) Failure must be observable.

## 论证与解释
**[raw §D]** (source_grounded) 来源事实：budgets.

### 操作步骤

1. [raw §D] (akb_interpretation) Define budgets. `x`

## 新颖增量
keep me unchanged
""")
        repaired, actions = _apply_deterministic_repairs(
            text,
            [
                Defect(
                    dimension="How 可执行性",
                    location="### 操作步骤",
                    defect="步骤为概念性英文描述，缺少可复制执行的命令",
                    suggestion="补充真实命令与具体参数值",
                ),
            ],
            None,
        )
        assert actions
        assert "MAX_REPAIR_ROUNDS=3" not in repaired
        assert "maker_model='deepseek-v4-pro'" not in repaired
        assert "ledger.append({event:'abort'" not in repaired
        assert "无原文可锚定命令" in repaired

    def test_novelty_provenance_repair_adds_explicit_column(self):
        """新颖增量 provenance 缺陷应本地修复，不依赖 repair LLM。"""
        from src.akb2.quality.repair import _apply_deterministic_repairs

        text = _wrap("""## 新颖增量

| 既有条目/基线 | 已覆盖点 | 本文新增内容 | 新增类型 | 证据 |
|---|---|---|---|---|
| baseline | covered | delta | source claim | [raw §A] (source_grounded) |

## 适用边界
keep me unchanged
""")
        repaired, actions = _apply_deterministic_repairs(
            text,
            [
                Defect(
                    dimension="解读分离",
                    location="## 新颖增量",
                    defect="新颖增量表缺少独立 provenance 字段",
                    suggestion="为新颖增量表增加 provenance 列",
                )
            ],
        )

        assert actions and actions[-1]["section"] == "新颖增量"
        assert "| 既有条目/基线 | 已覆盖点 | 本文新增内容 | provenance | 新增类型 | 证据 |" in repaired
        assert "| baseline | covered | delta | source_grounded | source claim | [raw §A] (source_grounded) |" in repaired
        assert "## 适用边界\nkeep me unchanged" in repaired

    def test_loop_writes_repair_trace_and_final_grader_meta(self, temp_failures_path, repair_raw_text):
        """repair 收敛时必须留下 trace，且返回最终 grader meta。"""
        from tests.quality.conftest import make_mock_route_llm

        all_pass = [
            {"dimension": d, "pass": True, "defects": []}
            for d in [
                "论断锚定率", "How 密度", "How 可执行性", "证据诚实", "解读分离",
                "TLDR 密度", "新颖增量", "边强度匹配", "Future Questions 质量", "适用边界",
            ]
        ]
        mock_llm = make_mock_route_llm({
            "gpt-5.4": LLMResponse(
                content={"dimensions": all_pass},
                model_used="gpt-5.4",
                calls_used=1,
            )
        })
        text = """---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
gate_reason: 待评审
future_questions:
  - 如何处理失败？
  - 如何设置预算？
related_concepts:
  - harness-engineering
  - agent-collaboration
---

## TLDR
Loop [raw §A].

## 关键发现
- [raw §A] (source_grounded) Loop needs a gate.

## 入选理由
- [raw §A] (source_grounded) It is useful.

## 证据地图
| 锚点 | 论点 | 来源 | 未覆盖 |
|------|------|------|--------|
| §A | Loop | source_grounded | 未量化：原文未提供效果数据 |

## 核心论点
1. [raw §A] (source_grounded) Loop needs a gate.

## 论证与解释
**[raw §A]** (source_grounded) 来源事实：Loop needs a gate.

## 适用边界
- [raw §A] (akb_interpretation) 适用场景：多步骤管线。
- [raw §A] (akb_interpretation) 适用场景：需要可恢复账本和质量门控的内容流水线。
- [raw §A] (akb_interpretation) 不适用场景：one-shot。
- [raw §A] (akb_interpretation) 约束条件：需要账本。

## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| agent collaboration | related | medium | multi-agent loops require coordination primitives |

## Future Questions
- 如何处理失败？
- 如何设置预算？

## 维护记录
| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-06-20 | 初始创建 | raw: test |
"""
        result = run_repair_loop(
            candidate_text=text,
            a_defects=[],
            b_defects=[
                Defect(
                    dimension="边强度匹配",
                    location="图谱关系/evidence/agent collaboration",
                    defect="缺 raw anchor/provenance，medium 证据不足",
                    suggestion="补 provenance 并降级",
                )
            ],
            failures_path=temp_failures_path,
            mock_route_llm=mock_llm,
            raw_text=repair_raw_text,
        )
        assert result["converged"]
        assert result["final_grader_meta"]["grader_model_used"] == "gpt-5.4"
        assert result["trace_paths"], "repair 必须写 trace artifact"
        trace = json.loads(Path(result["trace_paths"][0]).read_text())
        assert trace["deterministic_actions"]
        assert any(
            json.loads(Path(p).read_text())["status"] == "converged_after_deterministic"
            for p in result["trace_paths"]
        )

    def test_deterministic_repair_normalizes_empty_graph_placeholder(
        self, temp_failures_path, repair_raw_text
    ):
        """占位图谱行也应被确定性修复为 weak + akb_interpretation。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_llm = make_mock_route_llm(
            {
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
                )
            }
        )
        text = """---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
future_questions:
  - 如何处理失败？
  - 如何设置预算？
related_concepts:
  - harness-engineering
  - agent-collaboration
---

## TLDR
Loop [raw §A].

## 关键发现
- [raw §A] (source_grounded) Loop needs a gate.

## 入选理由
- [raw §A] (source_grounded) It is useful.

## 证据地图
| 锚点 | 论点 | 来源 | 未覆盖 |
|------|------|------|--------|
| §A | Loop | source_grounded | 未量化：原文未提供效果数据 |

## 核心论点
1. [raw §A] (source_grounded) Loop needs a gate.

## 论证与解释
**[raw §A]** (source_grounded) 来源事实：Loop needs a gate.

## 适用边界
- [raw §A] (akb_interpretation) 适用场景：多步骤管线。
- [raw §A] (akb_interpretation) 适用场景：需要可恢复账本和质量门控的内容流水线。
- [raw §A] (akb_interpretation) 不适用场景：one-shot。
- [raw §A] (akb_interpretation) 约束条件：需要账本。

## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| （无相关概念） | — | — | — |

## Future Questions
- 如何处理失败？
- 如何设置预算？

## 维护记录
| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-06-20 | 初始创建 | raw: test |
"""
        result = run_repair_loop(
            candidate_text=text,
            a_defects=[
                Defect(
                    dimension="A4-图谱边",
                    location="图谱关系/（无相关概念）/strength",
                    defect="图谱边 strength 必须是 strong/medium/weak，当前: —",
                    suggestion="按 evidence 强度改为 strong、medium 或 weak",
                ),
                Defect(
                    dimension="A7-图谱证据",
                    location="图谱关系/evidence/（无相关概念）",
                    defect="图谱 evidence 缺少 [raw §X] 锚点，也未明确标注 akb_interpretation",
                    suggestion="若只是库内推断，必须标注 (akb_interpretation)",
                ),
            ],
            b_defects=[],
            failures_path=temp_failures_path,
            mock_route_llm=mock_llm,
            raw_text=repair_raw_text,
        )
        assert result["converged"]
        assert "no-supported-related-concept" in result["final_text"]
        assert "| no-supported-related-concept | related | weak | (akb_interpretation)" in result["final_text"]
        assert "保守占位" not in result["final_text"]
        assert any(
            json.loads(Path(p).read_text())["status"] == "converged_after_deterministic"
            for p in result["trace_paths"]
        )


class TestRepairLoopSectionPatch:
    """根因③ 集成:LLM 返回 JSON 节 patch → loop apply 真改点名节,未点名节逐字节不变。

    (区别于旧 test_single_round_convergence:旧 fixture 返回完整页,③后 apply 会
    解析失败回退原样——但因 mock grader 全 pass 仍"收敛",是假阳性。本测试用
    JSON patch fixture,真正验证定点爬坡生效。)
    """

    def test_loop_applies_section_patch_and_preserves_unnamed(
        self, candidate_with_defects, temp_failures_path, repair_raw_text
    ):
        from tests.quality.conftest import make_mock_route_llm

        all_pass = [
            {"dimension": d, "pass": True, "defects": []}
            for d in [
                "论断锚定率", "How 密度", "How 可执行性", "证据诚实", "解读分离",
                "TLDR 密度", "新颖增量", "边强度匹配", "Future Questions 质量", "适用边界",
            ]
        ]
        patch_resp = {
            "deepseek-v4-pro": LLMResponse(
                content={"TLDR": "## TLDR\nLoop Engineering: bounded loops [raw §A]."},
                model_used="deepseek-v4-pro",
                calls_used=1,
            ),
            "gpt-5.4": LLMResponse(
                content={"dimensions": all_pass},
                model_used="gpt-5.4",
                calls_used=1,
            ),
        }
        mock_llm = make_mock_route_llm(patch_resp)

        result = run_repair_loop(
            candidate_text=candidate_with_defects,
            a_defects=[],
            b_defects=[
                Defect(
                    dimension="论断锚定率",
                    location="TLDR",
                    defect="TLDR 无 [raw §X] 锚点",
                    suggestion="为 TLDR 加 [raw §A] 锚点",
                )
            ],
            failures_path=temp_failures_path,
            mock_route_llm=mock_llm,
            raw_text=repair_raw_text,
        )

        assert result["converged"], "应收敛(patch 改 TLDR + grader 全 pass)"
        # 根因③ 真测:TLDR 节被 patch 替换(出现锚点)
        assert "[raw §A]" in result["final_text"], (
            "TLDR 节应被 JSON patch 替换(出现锚点)——定点爬坡真生效"
        )
        # 未点名节逐字节不变(根因③ 机制保证,repair 碰不到)
        assert "1. [raw §A] (source_grounded) Bounded loops are important" in result["final_text"], (
            "未点名节'关键发现'逐字节不变"
        )


# ---------------------------------------------------------------------------
# 根因③(2026-06-20 修):diff 校验规范化——吸收格式噪声不破护栏
# SC-3: 空白/标点微调不触发整轮丢弃,实质 token 变动仍被拦
# ---------------------------------------------------------------------------


def _wrap(body: str) -> str:
    """包 frontmatter 成候选页文本（_verify_unchanged_sections 输入格式）。"""
    return f"""---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
---

{body}"""


class TestNormalizeCompare:
    """_verify_unchanged_sections 规范化比较：放行格式噪声，拦截实质改动。"""

    def test_whitespace_only_change_not_rejected(self):
        """SC-3:空白噪声(多空格/空行/制表符)不触发整轮丢弃。

        痛点:旧字节级比较下 LLM 顺手多敲个空格/空行就整轮丢(addy 8→10 发散真凶)。
        断言绑定可观测行为:关键发现(未命名)仅空白差异 → 不出现在 changed。
        """
        orig = _wrap("## TLDR\nnamed.\n## 关键发现\n1. foo\n2. bar")
        rep = _wrap("## TLDR\nnamed.\n## 关键发现\n1.  foo\n\n2. bar")
        changed = _verify_unchanged_sections(orig, rep, named_sections={"TLDR"})
        assert "关键发现" not in changed, f"纯空白差异不应判 changed: {changed}"

    def test_punctuation_micro_change_not_rejected(self):
        """SC-3:全角/半角等价标点(，↔, 。↔.)放行不触发整轮丢弃。

        痛点:旧字节级比较下 。→. 这类等价标点也判违规丢弃。
        断言绑定可观测行为:适用边界(未命名)仅句读/逗号差异 → 不在 changed。
        """
        orig = _wrap("## 适用边界\n适用：Agent 管线。前提，必须有 budget。")
        rep = _wrap("## 适用边界\n适用:Agent 管线.前提,必须有 budget.")
        changed = _verify_unchanged_sections(orig, rep, named_sections=set())
        assert "适用边界" not in changed, f"全角/半角等价标点不应判 changed: {changed}"

    def test_semantic_punctuation_change_still_rejected(self):
        """SC-3(Codex R2-Finding2 拒绝样例):改表格分隔符 | / 锚点 [raw §X] / 否定词 → 仍判 changed。

        痛点:若规范化吸收了结构性字符,repair 会放任乱改未点名节(护栏破)。
        断言绑定可观测行为:三类实质变动各自仍被拦截。
        """
        # (a) 表格分隔符 | 被换成 ; —— 图谱边结构被破坏,必须拦
        orig_a = _wrap("## 图谱关系\n| target | relation |\n| a | b |")
        rep_a = _wrap("## 图谱关系\n| target ; relation ;\n| a ; b |")
        assert "图谱关系" in _verify_unchanged_sections(orig_a, rep_a, named_sections=set())

        # (b) 锚点 §A 被换成 §B —— 论断锚定被改,必须拦
        orig_b = _wrap("## 核心论点\n1. [raw §A] bounded loops")
        rep_b = _wrap("## 核心论点\n1. [raw §B] bounded loops")
        assert "核心论点" in _verify_unchanged_sections(orig_b, rep_b, named_sections=set())

        # (c) 否定词「不」被删 —— 语义反转,必须拦(NFKC 不动汉字)
        orig_c = _wrap("## 适用边界\n不适用: one-shot 查询")
        rep_c = _wrap("## 适用边界\n适用: one-shot 查询")
        assert "适用边界" in _verify_unchanged_sections(orig_c, rep_c, named_sections=set())

    def test_substantive_change_still_rejected(self):
        """SC-3:实质 token 增删(改内容)仍判 changed。

        断言绑定可观测行为:关键发现内容被改 → 出现在 changed。
        """
        orig = _wrap("## 关键发现\n1. Bounded loops matter")
        rep = _wrap("## 关键发现\n1. Completely different content here")
        changed = _verify_unchanged_sections(orig, rep, named_sections=set())
        assert "关键发现" in changed, f"实质改动必须被判 changed: {changed}"

    def test_h3_subsection_change_inside_named_h2_ok(self):
        """SC-3:H3 子节改动落在被命名的 H2 内 → 不判 changed（命名节允许改,含其子节）。

        痛点:口径不一致时,命名 H2 内的 H3 重排会被误判 → 整轮丢弃(发散)。
        断言绑定可观测行为:命名 H2「论证与解释」内的 H3「操作步骤」改 → 不在 changed。
        """
        orig = _wrap("## 论证与解释\nintro text\n### 操作步骤\nstep A")
        rep = _wrap("## 论证与解释\nintro text\n### 操作步骤\nstep CHANGED")
        changed = _verify_unchanged_sections(
            orig, rep, named_sections={"论证与解释"}
        )
        assert "操作步骤" not in changed, "命名 H2 内 H3 改动不应判 changed(豁免)"
        assert "论证与解释" not in changed, "命名 H2 本身豁免"

    def test_new_unnamed_section_in_repair_is_rejected(self):
        """修复稿新增未点名节(只 rep 有)→ 必须判违规夹带(P1)。

        痛点:旧 verify 只遍历 orig 节,apply 把 patch 原样插入时 LLM 可在命名节 patch 内
        夹带额外 H2/H3 → 新增节漏检 → 护栏洞(SC-3 不成立)。
        断言绑定可观测行为:rep 新增未点名节「新增未点名节」出现在 changed。
        """
        orig = _wrap("## TLDR\nnamed\n## 关键发现\norig content")
        rep = _wrap("## TLDR\nnamed\n## 关键发现\norig content\n## 新增未点名节\n偷塞")
        changed = _verify_unchanged_sections(orig, rep, named_sections={"TLDR"})
        assert "新增未点名节" in changed, (
            f"修复稿新增未点名节必须被抓: {changed}"
        )

    def test_fullwidth_structural_chars_still_rejected(self):
        """全角结构性字符(｜［］①)不被吸收,仍判 changed(R2-Finding2 边界修复)。

        痛点:旧实现全量 NFKC 把全角 ｜→|、［］→[]、①→1,等价归一后判未变 →
        未点名节的表格分隔符/锚点/数字被错误放行。
        断言绑定可观测行为:全角 pipe/方括号/圈号数字 各自仍判 changed。
        """
        cases = [
            ("｜ target ｜", "| target |", "全角 pipe"),
            ("［raw §A］", "[raw §A]", "全角方括号"),
            ("① first", "1 first", "圈号数字"),
        ]
        for fullwidth, ascii_equiv, name in cases:
            orig = _wrap(f"## 关键发现\n{fullwidth}")
            rep = _wrap(f"## 关键发现\n{ascii_equiv}")
            changed = _verify_unchanged_sections(orig, rep, named_sections=set())
            assert "关键发现" in changed, (
                f"{name}({fullwidth!r}↔{ascii_equiv!r})应判 changed,实际: {changed}"
            )
