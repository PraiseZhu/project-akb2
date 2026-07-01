"""Step.1 SC-3a: 三级 how（H1/H2/H3）+ 结构化机制字段 + lane-aware extract 校验。

测的是 lane-aware extract 校验**真生效、不被绕过**：

1. concept 车道（content_lane="concept"）：
   - extract_claims(raw, content_lane="concept", route_llm_fn=<fake 返 H1=0/mechanism≥3>)
     → success（in-process LLM 路径，注入绕开真网关）
   - build_deterministic_extract_result(raw, raw_path, content_lane="concept")
     → success（in-process deterministic 路径）
   - 两条路径都不卡 executable≥3、允许 H1==0、改判 mechanism_components≥3。
2. procedural 车道（默认/兼容）：
   - executable≥3 仍 success（零回归）
   - executable<3 仍 fail（零回归）
3. 结构字段：how_level 三级标注被 normalize 注入；mechanism_components/causal_edges
   随 merged 输出携出。

痛点：step-1 把 mechanism_components/causal_edges/how_level 加进了 schema properties
但既没进 required（strict 不变式测试 fail）也没在 merge/normalize 流转——concept 抽取
拿不到结构字段，F4 无真相源可读。本 step 让字段真流转 + lane-aware 改判。
"""

from __future__ import annotations

import pytest

from src.akb2.quality.distill import (
    CONCEPT_MIN_MECHANISM_COMPONENTS,
    build_deterministic_extract_result,
    extract_claims,
)
from src.akb2.quality.llm_router import LLMResponse


def _claims_segment() -> dict:
    """概念型 claims 段（≤40% akb_interpretation，每条带 anchor + evidence_gap）。"""
    return {
        "claims": [
            {
                "text": "Loop engineering 把 agent 工作收束为有停止条件的反馈循环",
                "anchor": "§A",
                "provenance": "source_grounded",
                "evidence_gap": "未量化：原文未提供该机制的成功率或成本数据",
            },
            {
                "text": "工作队列携带 source URL、raw anchor、content hash 与生命周期状态",
                "anchor": "§B",
                "provenance": "source_grounded",
                "evidence_gap": "未覆盖：原文未展开多源并发下队列字段冲突处理",
            },
            {
                "text": "修复路径固定轮数、只在剩余缺陷清零时接受候选",
                "anchor": "§C",
                "provenance": "source_grounded",
                "evidence_gap": "未比较：原文未比较不同修复策略效果",
            },
        ],
        "tldr": "Loop engineering 用命名机制组件与因果边把 agent 主链路讲清为可观测反馈循环。",
    }


def _concept_how_params_segment() -> dict:
    """概念型 how_params 段：H1==0（无命令级步骤）+ ≥3 命名机制组件 + 因果边。

    关键：how_steps 全部为说明级（无可执行命令），LLM 标 how_level=H2/H3；
    mechanism_components ≥3，causal_edges 指向命名组件。这是 concept 的 F4 真相源。
    """
    return {
        "how_steps": [
            {
                "instruction": "trigger 组件通过调度/事件/手动入口启动 loop",
                "command": "",
                "executable": False,
                "how_level": "H2",
            },
            {
                "instruction": "work_selector 读账本选下一个待处理工作单元，"
                "再交给 quality_gate 校验",
                "command": "",
                "executable": False,
                "how_level": "H3",
            },
            {
                "instruction": "quality_gate 不达标时把缺陷送进有界修复闭环",
                "command": "",
                "executable": False,
                "how_level": "H3",
            },
        ],
        "mechanism_components": [
            {"name": "trigger", "role": "启动 loop 的调度/事件/手动入口"},
            {"name": "work_selector", "role": "读账本选下一个待处理工作单元"},
            {"name": "quality_gate", "role": "校验输出达标，不达标进修复闭环"},
            {"name": "ledger", "role": "记录每段生命周期事件，支撑崩溃恢复"},
        ],
        "causal_edges": [
            {"from": "trigger", "to": "work_selector", "relation": "activates"},
            {"from": "work_selector", "to": "quality_gate", "relation": "feeds"},
            {"from": "quality_gate", "to": "ledger", "relation": "writes_result_to"},
        ],
        "param_entries": [{"key": "max_repair_rounds", "value": "3"}],
    }


def _procedural_how_params_segment(executable_count: int) -> dict:
    """操作型 how_params 段：executable_count 条可执行命令步骤（无机制组件）。"""
    steps = []
    for i in range(executable_count):
        steps.append(
            {
                "instruction": f"操作步骤 {i}",
                "command": f"akb_op_{i}=run(input=x)",
                "executable": True,
                "how_level": "H1",
            }
        )
    # 补一条非可执行说明，保证 how_steps 非空但不算可执行
    if executable_count == 0:
        steps.append(
            {
                "instruction": "纯说明步骤",
                "command": "",
                "executable": False,
                "how_level": "H2",
            }
        )
    return {
        "how_steps": steps,
        "mechanism_components": [],
        "causal_edges": [],
        "param_entries": [{"key": "max_rounds", "value": "3"}],
    }


def _make_segment_route(claims_seg: dict, how_seg: dict):
    """构造 fixture route_llm：按 segment 顺序（claims → how_params）返回预设响应。

    extract_claims 内部按 _DISTILL_SEGMENT_SCHEMAS 顺序逐段调 route_llm_fn；
    本 fake 用 prompt 内容判 segment（claims/how_params），返回对应 LLMResponse。
    """

    def route(model_family, messages, response_schema=None, **kwargs):
        joined = " ".join(m.get("content", "") for m in messages)
        if "mechanism_components" in joined or "how_steps" in joined:
            return LLMResponse(content=how_seg, model_used=model_family)
        return LLMResponse(content=claims_seg, model_used=model_family)

    return route


# 关闭语义 retake 隔离本测试（retake 默认关；本测试聚焦 lane-aware 校验，显式 setenv 0 防御）
@pytest.fixture(autouse=True)
def _disable_semantic_retake(monkeypatch):
    monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "0")


class TestConceptLaneExtractLLMPath:
    """concept 车道 in-process LLM 抽取路径（与 worker 子进程同调用 extract_claims）。"""

    def test_concept_h1_zero_mechanism_three_extract_succeeds(self, sample_raw_content):
        """concept(H1==0, mechanism_components≥3) → extract success（不卡 executable）。"""
        route = _make_segment_route(_claims_segment(), _concept_how_params_segment())
        result = extract_claims(
            sample_raw_content, route_llm_fn=route, content_lane="concept"
        )
        assert result.success is True, result.error
        # H1==0：没有命令级步骤
        h1_steps = [s for s in result.data["how_steps"] if s.get("how_level") == "H1"]
        assert h1_steps == [], "concept fixture 不应有 H1 命令级步骤"
        # mechanism_components ≥3（F4 结构真相源真流转到 merged）
        assert (
            len(result.data["mechanism_components"]) >= CONCEPT_MIN_MECHANISM_COMPONENTS
        )
        # causal_edges 携出
        assert len(result.data["causal_edges"]) >= 1
        # how_level 三级标注被注入
        levels = {s.get("how_level") for s in result.data["how_steps"]}
        assert levels <= {"H1", "H2", "H3"}
        assert "H2" in levels or "H3" in levels

    def test_concept_too_few_mechanism_components_fails(self, sample_raw_content):
        """concept 但 mechanism_components<3 → extract fail（改判门生效，不被绕过）。"""
        thin_how = _concept_how_params_segment()
        thin_how["mechanism_components"] = thin_how["mechanism_components"][:2]
        route = _make_segment_route(_claims_segment(), thin_how)
        result = extract_claims(
            sample_raw_content, route_llm_fn=route, content_lane="concept"
        )
        assert result.success is False
        assert "mechanism_components" in (result.error or "")


class TestConceptLaneDeterministicPath:
    """concept 车道 deterministic fallback 路径（maker LLM 不可用时）。"""

    def test_concept_deterministic_extract_fails_on_implicit_causal(self, sample_raw_content):
        """P1-A: 黄金样张组件是编号列表(实体名 trigger/selector/...)但无显式因果语言
        → fallback 不造假边 → F4 连通性 fail → fail-closed(保守; LLM extract 在生产
        处理黄金样张, fallback 仅给 LLM 失败的显式机制内容)。"""
        result = build_deterministic_extract_result(
            sample_raw_content, "raw/concept-sample.md", content_lane="concept"
        )
        assert result.success is False, (
            "黄金样张无显式因果语言, fallback 应 fail-closed 不造假边"
        )
        assert "F4" in (result.error or "") or "因果边" in (result.error or ""), (
            f"应 F4 连通性 fail: {result.error}"
        )

    def test_procedural_deterministic_still_succeeds(self, sample_raw_content):
        """procedural deterministic（默认 lane）仍 success（零回归），无机制组件。"""
        result = build_deterministic_extract_result(
            sample_raw_content, "raw/proc-sample.md", content_lane="procedural"
        )
        assert result.success is True, result.error
        assert result.data["mechanism_components"] == []
        assert result.data["causal_edges"] == []


class TestProceduralLaneRegression:
    """操作型车道零回归：executable≥3 过 / executable<3 拒。"""

    def test_procedural_three_executable_succeeds(self, sample_raw_content):
        """procedural executable≥3 → extract success（零回归）。"""
        route = _make_segment_route(
            _claims_segment(), _procedural_how_params_segment(3)
        )
        result = extract_claims(
            sample_raw_content, route_llm_fn=route, content_lane="procedural"
        )
        assert result.success is True, result.error

    def test_procedural_one_executable_fails(self, sample_raw_content):
        """procedural executable<3 → extract fail（零回归，门未被 lane 改动破坏）。"""
        route = _make_segment_route(
            _claims_segment(), _procedural_how_params_segment(1)
        )
        result = extract_claims(
            sample_raw_content, route_llm_fn=route, content_lane="procedural"
        )
        assert result.success is False
        assert "可执行步骤" in (result.error or "")

    def test_default_lane_keeps_executable_gate(self, sample_raw_content):
        """空 content_lane（默认）= procedural 行为：executable<3 拒（兼容旧调用）。"""
        route = _make_segment_route(
            _claims_segment(), _procedural_how_params_segment(0)
        )
        result = extract_claims(sample_raw_content, route_llm_fn=route)
        assert result.success is False
        assert "可执行步骤" in (result.error or "")
