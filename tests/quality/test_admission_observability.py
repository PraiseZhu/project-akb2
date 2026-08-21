"""SC-5 观测验收 + sc-p3-second-call-mutation 单次判定守卫测试。

P3 重构改写（单次结构化判定契约）：
    - admission pass/abort 两路 lane_reason 可见（P0-5 语义在 unified 判定下保留）
    - repair 逐轮 trace 含 final_defects 快照（P0-5，与本次重构无关，原样保留）
    - sc-p3-second-call-mutation：同篇第 2 次 admission LLM 调用被
      ``_SingleCallGuard`` 源码级守卫拦住——守卫直测 + run_admission 计数锁
      双保险：若有人把第二次调用加回（无论是否绕过守卫），测试必红。

本文件归 g6 task packet owned（与 tests/test_admission_lane.py 的车道判定测试拆分）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _has_field(cls: object, name: str) -> bool:
    import dataclasses

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


# ---------------------------------------------------------------------------
# P0-5：lane_reason 透出（unified 判定契约）
# ---------------------------------------------------------------------------


def _unified_mock(lane: str, reason: str):
    from src.akb2.quality.llm_router import LLMResponse

    def _mock(model_family, messages, response_schema=None, **kwargs):
        return LLMResponse(
            content={
                "topic_pass": True,
                "lane": lane,
                "non_knowledge": lane == "reject",
                "mechanism_pass": True,
                "how_pass": True,
                "reason": reason,
            },
            model_used="deepseek-v4-pro",
        )

    return _mock


def test_admission_pass_path_exposes_lane_reason(tmp_path: Path) -> None:
    """P0-5：AdmissionResult pass 路径透出 lane_reason（非空，不止 reject 可见）。"""
    from src.akb2.quality.admission import LANE_CONCEPT, AdmissionResult, run_admission

    assert _has_field(AdmissionResult, "lane_reason"), "AdmissionResult 须有 lane_reason"
    ledger = str(tmp_path / "ledger.jsonl")
    result = run_admission(
        raw_path="raw/concept--raw--2026-08-15.md",
        raw_content=(
            "# Loop Engineering\n\n"
            "### 1. Trigger\nThe loop starts on a schedule or event.\n\n"
            "### 2. Work Selector\nPicks the next work item from the ledger.\n\n"
            "### 3. Executor\nRuns the work and produces a result.\n"
        ),
        ledger_path=ledger,
        route_llm_fn=_unified_mock(LANE_CONCEPT, "mock concept 车道"),
    )
    assert result.passed is True, f"pass 场景应通过准入: {result.reason}"
    assert result.lane_reason, "pass 路径 lane_reason 应非空（不止 reject 可见）"
    assert "mock concept 车道" in result.lane_reason
    assert result.topic_borderline is False


def test_admission_abort_path_exposes_lane_reason(tmp_path: Path) -> None:
    """P0-5：AdmissionResult abort(reject)路径也透出 lane_reason（每篇可读）。"""
    from src.akb2.quality.admission import LANE_REJECT, AdmissionResult, run_admission

    assert _has_field(AdmissionResult, "lane_reason"), "AdmissionResult 须有 lane_reason"
    ledger = str(tmp_path / "ledger.jsonl")
    result = run_admission(
        raw_path="raw/news--raw--2026-08-15.md",
        raw_content="# Product Launch\n\nWe are excited to announce our new product.",
        ledger_path=ledger,
        route_llm_fn=_unified_mock(LANE_REJECT, "mock 非知识 reject"),
    )
    assert result.passed is False, "reject 场景应拒收"
    assert result.lane_reason, "abort 路径 lane_reason 应非空（每篇可读）"


def test_admission_result_exposes_topic_borderline_bool() -> None:
    """AdmissionResult.topic_borderline 是二值观测字段。"""
    from src.akb2.quality.admission import AdmissionResult

    assert _has_field(AdmissionResult, "topic_borderline")
    assert AdmissionResult(passed=True).topic_borderline is False


# ---------------------------------------------------------------------------
# sc-p3-second-call-mutation：单次判定守卫
# ---------------------------------------------------------------------------


class TestSingleCallGuard:
    """同篇第 2 次 admission LLM 调用被守卫拦住（源码级，不依赖外部计数）。"""

    def test_guard_blocks_second_call(self) -> None:
        """守卫直测：第 1 次放行，第 2 次抛 SecondAdmissionCallError。"""
        from src.akb2.quality.admission import (
            SecondAdmissionCallError,
            _make_single_call_guard,
        )
        from src.akb2.quality.llm_router import LLMResponse

        guard = _make_single_call_guard(
            lambda *a, **kw: LLMResponse(content={}, model_used="m")
        )
        guard("deepseek-v4-pro", [{"role": "user", "content": "x"}])  # 第 1 次放行
        with pytest.raises(SecondAdmissionCallError) as excinfo:
            guard("deepseek-v4-pro", [{"role": "user", "content": "x"}])  # 第 2 次
        assert "单次判定守卫" in str(excinfo.value), (
            f"守卫错误应可诊断: {excinfo.value}"
        )

    def test_guard_is_runtime_error_for_fail_closed(self) -> None:
        """守卫错误继承 RuntimeError：统一落进 fail-closed 拒收分支。"""
        from src.akb2.quality.admission import SecondAdmissionCallError

        assert issubclass(SecondAdmissionCallError, RuntimeError)

    def test_run_admission_exactly_one_llm_call_all_lanes(self, tmp_path: Path) -> None:
        """计数锁：concept / pattern / design / reject 各路径 route_llm 恰好 1 次。

        若有人把第二次 admission LLM 调用加回且未经过守卫，本计数断言直接红；
        若经过守卫，守卫抛错使任何期待 pass 的测试红——两种 mutation 均被抓。
        """
        from src.akb2.quality.admission import (
            LANE_CONCEPT,
            LANE_DESIGN,
            LANE_PATTERN,
            LANE_REJECT,
            run_admission,
        )

        concept_body = (
            "# Loop Engineering\n\n"
            "### 1. Trigger\nThe loop starts.\n\n"
            "### 2. Selector\nPicks work.\n\n"
            "### 3. Executor\nRuns work.\n"
        )
        pattern_body = "# Patterns\n\nNamed reusable patterns for agents, one two three."
        design_body = "# Design\n\nVisual design presentation with hierarchy and tokens."
        cases = (
            (concept_body, LANE_CONCEPT),
            (pattern_body, LANE_PATTERN),
            (design_body, LANE_DESIGN),
            ("# News\nProduct launch today.", LANE_REJECT),
        )

        def _run_count_case(body: str, lane: str) -> int:
            calls = {"n": 0}

            def _spy(model_family, messages, response_schema=None, **kwargs):
                calls["n"] += 1
                from src.akb2.quality.llm_router import LLMResponse

                return LLMResponse(
                    content={
                        "topic_pass": True, "lane": lane, "non_knowledge": False,
                        "mechanism_pass": True, "how_pass": True,
                        "reason": "mock",
                    },
                    model_used="deepseek-v4-pro",
                )

            run_admission(
                raw_path=f"raw/{lane}-guard--raw--2026-08-15.md",
                raw_content=body,
                ledger_path=str(tmp_path / "ledger.jsonl"),
                route_llm_fn=_spy,
            )
            return calls["n"]

        for body, lane in cases:
            assert _run_count_case(body, lane) == 1, (
                f"{lane} 路径应恰好 1 次 LLM 调用"
            )

    def test_run_admission_exact_dup_zero_calls(self, tmp_path: Path) -> None:
        """exact 重复路径 0 次调用（确定性拒收先于 LLM）。"""
        from src.akb2.quality.admission import run_admission

        calls = {"n": 0}

        def _spy(model_family, messages, response_schema=None, **kwargs):
            calls["n"] += 1
            from src.akb2.quality.llm_router import LLMResponse

            return LLMResponse(
                content={"topic_pass": True, "lane": "concept", "non_knowledge": False,
                         "reason": "mock"},
                model_used="m",
            )

        dup_raw = (
            "---\nsource_slug: captain\nsource_url: https://x/c\n---\n\n"
            "# Captain\nsame claim"
        )
        run_admission(
            raw_path="raw/dup-guard--raw--2026-08-15.md",
            raw_content=dup_raw,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            wiki_references=[
                {"slug": "captain", "title": "Captain", "tldr": "arch",
                 "path": "p", "source_url": "https://x/c"},
            ],
            route_llm_fn=_spy,
        )
        assert calls["n"] == 0, "exact 重复应 0 次 LLM 调用"

    def test_guard_exception_fails_closed_rejection(self, tmp_path: Path) -> None:
        """守卫触发（模拟 mutation：有人加回第 2 次调用）→ fail-closed 拒收 + 落账。

        夹具：route_llm_fn 在第 1 次被调用后，通过守卫包装模拟第 2 次调用发生
        （守卫视角的同篇重入），断言守卫拦截 + admission 结果为拒收路径可见。
        """
        from src.akb2.quality.admission import (
            LANE_CONCEPT,
            SecondAdmissionCallError,
            _make_single_call_guard,
            run_admission,
        )
        from src.akb2.quality.llm_router import LLMResponse

        ledger_path = tmp_path / "ledger.jsonl"

        def _mock(model_family, messages, response_schema=None, **kwargs):
            return LLMResponse(
                content={"topic_pass": True, "lane": LANE_CONCEPT, "non_knowledge": False,
                         "mechanism_pass": True, "how_pass": True, "reason": "ok"},
                model_used="deepseek-v4-pro",
            )

        # 模拟 mutation：有人把第二次调用加回（在 run_admission 之外用同一守卫
        # 复现调用点行为），守卫必须拦住——若守卫失效（第二次不抛），本测试红。
        guarded = _make_single_call_guard(_mock)
        guarded("deepseek-v4-pro", [{"role": "user", "content": "first"}])
        with pytest.raises(SecondAdmissionCallError):
            guarded("deepseek-v4-pro", [{"role": "user", "content": "second"}])

        # 真实路径回归：正常单次调用下 admission 可通过（守卫不误伤第一次）。
        result = run_admission(
            raw_path="raw/concept-guard--raw--2026-08-15.md",
            raw_content=(
                "# Loop Engineering\n\n"
                "### 1. Trigger\nThe loop starts.\n\n"
                "### 2. Selector\nPicks work.\n\n"
                "### 3. Executor\nRuns work.\n"
            ),
            ledger_path=str(ledger_path),
            route_llm_fn=_mock,
        )
        assert result.passed is True, f"正常单次调用不应被守卫误伤: {result.reason}"


# ---------------------------------------------------------------------------
# P0-5：repair 逐轮 final_defects（原样保留）
# ---------------------------------------------------------------------------


def test_repair_trace_exposes_per_round_final_defects(tmp_path: Path) -> None:
    """P0-5：repair 逐轮 trace 含 final_defects 快照（观测增强，不改收敛条件）。"""
    from src.akb2.quality.checks_structural import Defect
    from src.akb2.quality.llm_router import RouterConfigError
    from src.akb2.quality.repair import REPAIR_TRACE_HAS_ROUND_FINAL_DEFECTS, run_repair_loop

    assert REPAIR_TRACE_HAS_ROUND_FINAL_DEFECTS is True

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
