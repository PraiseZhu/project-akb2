"""SC-5 观测验收 — P0-5：admission lane_reason 透出 + repair 逐轮 final_defects 可读。

承载 master-plan SC-5 中"admission pass/abort 两路 lane_reason 可见"与
"repair 逐轮 final_defects 落可读位置"的观测增强（P0-5）。

P0-5 已落地：
    - AdmissionResult 透出 lane_reason（pass 路径也可见，不止 reject）；
    - runner 准入 pass/abort 两处 _log_event details 补 lane_reason；
    - repair 逐轮 trace_payload 经 _write_repair_trace 注入 final_defects 快照，
      REPAIR_TRACE_HAS_ROUND_FINAL_DEFECTS=True。
判定行为零改动（仅加观测字段）。

本文件归 p0-5 task packet owned（与 p0-4 的 tests/test_admission_lane.py 拆分，
避免共享测试路径）。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest


def _has_field(cls: object, name: str) -> bool:
    if dataclasses.is_dataclass(cls):
        return name in {f.name for f in dataclasses.fields(cls)}
    return name in getattr(cls, "__annotations__", {}) or hasattr(cls, name)


# 有缺陷的候选页（与 test_repair_loop.candidate_with_defects 同源，self_gate 可跑）。
# 用于触发 repair 写逐轮 trace——无需收敛，route_failed 分支即写 round-N.json。
_CANDIDATE_WITH_DEFECTS = """---
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


def test_admission_pass_path_exposes_lane_reason(tmp_path: Path) -> None:
    """P0-5：AdmissionResult pass 路径透出 lane_reason（非空，不止 reject 可见）。"""
    admission = pytest.importorskip("akb2.quality.admission")
    ar = getattr(admission, "AdmissionResult", None)
    if ar is None or not _has_field(ar, "lane_reason"):
        pytest.skip("P0-5 未实现：AdmissionResult 尚未透出 lane_reason")
    from src.akb2.quality.admission import LANE_CONCEPT, run_admission
    from src.akb2.quality.llm_router import LLMResponse

    def _mock(model_family, messages, response_schema=None, **kwargs):
        sys_prompt = messages[0]["content"] if messages else ""
        if "主要意图" in sys_prompt or "概念型" in sys_prompt or "操作型" in sys_prompt:
            return LLMResponse(
                content={"content_lane": LANE_CONCEPT, "reason": "mock concept 车道"},
                model_used="deepseek-v4-pro",
            )
        return LLMResponse(
            content={"pass": True, "reason": "mock pass"},
            model_used="deepseek-v4-pro",
        )

    ledger = str(tmp_path / "ledger.jsonl")
    result = run_admission(
        raw_path="raw/concept--raw--2026-06-26.md",
        # WP-2: concept 候选须有机制信号过 mechanism_signal 门（3 命名组件 → 确定性 pass）
        raw_content=(
            "# Loop Engineering\n\n"
            "### 1. Trigger\nThe loop starts on a schedule or event.\n\n"
            "### 2. Work Selector\nPicks the next work item from the ledger.\n\n"
            "### 3. Executor\nRuns the work and produces a result.\n"
        ),
        ledger_path=ledger,
        route_llm_fn=_mock,
    )
    assert result.passed is True, f"pass 场景应通过准入: {result.reason}"
    assert result.lane_reason, "pass 路径 lane_reason 应非空（不止 reject 可见）"


def test_admission_abort_path_exposes_lane_reason(tmp_path: Path) -> None:
    """P0-5：AdmissionResult abort(reject)路径也透出 lane_reason（每篇可读）。"""
    admission = pytest.importorskip("akb2.quality.admission")
    ar = getattr(admission, "AdmissionResult", None)
    if ar is None or not _has_field(ar, "lane_reason"):
        pytest.skip("P0-5 未实现：AdmissionResult 尚未透出 lane_reason")
    from src.akb2.quality.admission import LANE_REJECT, run_admission
    from src.akb2.quality.llm_router import LLMResponse

    def _mock(model_family, messages, response_schema=None, **kwargs):
        sys_prompt = messages[0]["content"] if messages else ""
        if "主要意图" in sys_prompt or "概念型" in sys_prompt or "操作型" in sys_prompt:
            return LLMResponse(
                content={"content_lane": LANE_REJECT, "reason": "mock 非知识 reject"},
                model_used="deepseek-v4-pro",
            )
        return LLMResponse(
            content={"pass": True, "reason": "mock pass"},
            model_used="deepseek-v4-pro",
        )

    ledger = str(tmp_path / "ledger.jsonl")
    result = run_admission(
        raw_path="raw/news--raw--2026-06-26.md",
        raw_content="# Product Launch\n\nWe are excited to announce our new product.",
        ledger_path=ledger,
        route_llm_fn=_mock,
    )
    assert result.passed is False, "reject 场景应拒收"
    assert result.lane_reason, "abort 路径 lane_reason 应非空（每篇可读）"


def test_repair_trace_exposes_per_round_final_defects(tmp_path: Path) -> None:
    """P0-5：repair 逐轮 trace 含 final_defects 快照（观测增强，不改收敛条件）。"""
    repair = pytest.importorskip("akb2.quality.repair")
    marker = getattr(repair, "REPAIR_TRACE_HAS_ROUND_FINAL_DEFECTS", None)
    if marker is not True:
        pytest.skip("P0-5 未实现：repair 逐轮 final_defects 可读汇总待补")
    from src.akb2.quality.checks_structural import Defect
    from src.akb2.quality.llm_router import RouterConfigError
    from src.akb2.quality.repair import run_repair_loop

    b_defects = [
        Defect(
            dimension="论断锚定率",
            location="TLDR",
            defect="TLDR 无 [raw §X] 锚点",
            suggestion="为 TLDR 每句添加锚点引用",
        ),
    ]

    def _mock(model_family, messages, response_schema=None, **kwargs):
        # repair route 抛 → route_failed 分支写 round-N trace（含 final_defects）；
        # 不触发 grader（route 在 grader 之前），故无需 mock grader 响应。
        raise RouterConfigError("mock repair route unavailable")

    failures = str(tmp_path / "failures.jsonl")
    result = run_repair_loop(
        candidate_text=_CANDIDATE_WITH_DEFECTS,
        a_defects=[],
        b_defects=b_defects,
        failures_path=failures,
        mock_route_llm=_mock,
        raw_text=_CANDIDATE_WITH_DEFECTS,
    )
    assert result["trace_paths"], "route_failed 应写至少一轮 trace"
    trace = json.loads(Path(result["trace_paths"][0]).read_text(encoding="utf-8"))
    assert "final_defects" in trace, "逐轮 trace 应含 final_defects 快照"
    assert isinstance(trace["final_defects"], list)
