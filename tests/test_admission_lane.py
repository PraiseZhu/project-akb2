"""test_admission_lane.py — step-1 车道分类（content_lane）测试。

覆盖 SC-1..4:
    - SC-1: AdmissionResult 暴露 content_lane，取值仅 concept/procedural（无 unknown/None 终值）
    - SC-2: lane 分类器判对 concept/procedural/mixed（mixed→procedural）
    - SC-3: how_tendency 不再因缺命令拒收纯概念内容（改路由信号）
    - SC-4: lane 分类器在 LLM 不可用时 fail-closed（拒收+落账），不静默归车道

测试不打真 API：有命令内容走确定性 procedural（不触发 LLM）；无命令内容用 mock
route_llm 注入 lane 响应或抛异常。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.akb2.quality.admission import (
    LANE_CONCEPT,
    LANE_PROCEDURAL,
    AdmissionResult,
    run_admission,
)

# ---------------------------------------------------------------------------
# 测试内容素材
# ---------------------------------------------------------------------------

# 纯概念内容：0 可执行命令、机制阐述清晰（应判 concept；how_tendency 天然 fail）
_CONCEPT_BODY = """# The Four-Layer Loop Model

Loop engineering describes a conceptual architecture for AI agents. The model has four
conceptual layers: the trigger layer defines when work begins, the orchestration layer
decides what to do, the execution layer performs the work, and the observability layer
records outcomes. This model explains why agent reliability depends on loop design
rather than prompt quality. The fundamental principle is that convergence emerges from
explicit bounded stopping conditions, not from raw model intelligence. Understanding
this distinction reframes the engineering problem from prompt-authoring to loop-design.
"""

# 操作型内容：含可执行命令/代码（确定性判 procedural，不触发 LLM）
_PROCEDURAL_BODY = """# How to build a bounded loop

Follow these steps to implement a convergent agent loop:
1. Define the trigger type
2. Run the test harness

```bash
npm test
make deploy
python -m akb2.quality.runner --once
```

These commands wire the loop's execution path end to end.
"""

# 混合内容：既含机制阐述、又含可执行命令（spec FR-002 应归 procedural）
_MIXED_BODY = """# Loop Engineering: Concepts and Commands

Loop engineering reframes agent work as bounded, convergent, observable loops. The
conceptual model separates orchestration from execution. To put it into practice:

```bash
python -m akb2.quality.runner --once
make pipeline
```

The mechanism explains why convergence beats restart, and the commands show how.
"""


def _llm_returns_lane(lane: str):
    """构造 mock route_llm：topic/how 判定返回默认 pass，lane 分类返回指定 lane。

    用 model_family 路由：gemini-flash 被 topic/how/novelty/lane 共用，按调用序返回。
    这里统一让前几次（topic/how）pass，最后一次（lane，无命令时才触发）返回 lane。
    """
    from src.akb2.quality.llm_router import LLMResponse

    calls = {"n": 0}

    def _mock(model_family, messages, response_schema=None, **kwargs):
        calls["n"] += 1
        # lane 分类的 prompt 含「概念型」「操作型」关键词，据此区分 lane 调用
        sys_prompt = messages[0]["content"] if messages else ""
        if "概念型" in sys_prompt or "操作型" in sys_prompt:
            return LLMResponse(
                content={"content_lane": lane, "reason": "mock lane"},
                model_used="gemini-2.5-flash",
            )
        # topic / how / novelty 一律 pass（不阻断 concept 路由测试）
        return LLMResponse(
            content={"pass": True, "reason": "mock pass"},
            model_used="gemini-2.5-flash",
        )

    _mock.calls = calls  # type: ignore[attr-defined]
    return _mock


def _llm_pass_then_lane_raises():
    """构造 mock：topic/how/novelty pass，但 lane 分类调用抛 RouterConfigError。"""
    from src.akb2.quality.llm_router import LLMResponse, RouterConfigError

    def _mock(model_family, messages, response_schema=None, **kwargs):
        sys_prompt = messages[0]["content"] if messages else ""
        if "概念型" in sys_prompt or "操作型" in sys_prompt:
            raise RouterConfigError("lane classifier model unavailable")
        return LLMResponse(
            content={"pass": True, "reason": "mock pass"},
            model_used="gemini-2.5-flash",
        )

    return _mock


# ---------------------------------------------------------------------------
# SC-1: AdmissionResult 暴露 content_lane，枚举闭合
# ---------------------------------------------------------------------------


class TestLaneFieldExposed:
    def test_result_exposes_lane(self, tmp_path: Path) -> None:
        """AdmissionResult 暴露 content_lane 字段，通过时为合法车道值。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/procedural--raw--2026-06-26.md",
            raw_content=_PROCEDURAL_BODY,
            ledger_path=ledger,
            route_llm_fn=_llm_returns_lane(LANE_PROCEDURAL),
        )
        assert "content_lane" in AdmissionResult.__dataclass_fields__
        assert result.passed is True
        assert result.content_lane in (LANE_CONCEPT, LANE_PROCEDURAL)

    def test_lane_enum_closed(self, tmp_path: Path) -> None:
        """通过时 content_lane 只取 concept/procedural（无 unknown/None 合法终值）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        # concept 内容（无命令）+ mock 返回 concept
        result_c = run_admission(
            raw_path="raw/concept--raw--2026-06-26.md",
            raw_content=_CONCEPT_BODY,
            ledger_path=ledger,
            route_llm_fn=_llm_returns_lane(LANE_CONCEPT),
        )
        # procedural 内容（确定性）
        result_p = run_admission(
            raw_path="raw/proc--raw--2026-06-26.md",
            raw_content=_PROCEDURAL_BODY,
            ledger_path=ledger,
            route_llm_fn=_llm_returns_lane(LANE_CONCEPT),  # 有命令，确定性盖过 LLM
        )
        for r in (result_c, result_p):
            if r.passed:
                assert r.content_lane in (LANE_CONCEPT, LANE_PROCEDURAL)
                assert r.content_lane not in ("", "unknown", None)


# ---------------------------------------------------------------------------
# SC-2: 分类器判对 concept/procedural/mixed
# ---------------------------------------------------------------------------


class TestClassifierCases:
    def test_classifier_cases_concept(self, tmp_path: Path) -> None:
        """纯概念内容（无命令）经 LLM 判 concept。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/concept--raw--2026-06-26.md",
            raw_content=_CONCEPT_BODY,
            ledger_path=ledger,
            route_llm_fn=_llm_returns_lane(LANE_CONCEPT),
        )
        assert result.content_lane == LANE_CONCEPT

    def test_classifier_cases_procedural(self, tmp_path: Path) -> None:
        """操作型内容（含命令）确定性判 procedural。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/proc--raw--2026-06-26.md",
            raw_content=_PROCEDURAL_BODY,
            ledger_path=ledger,
            # 即便 LLM 想判 concept，确定性命令证据盖过 → procedural
            route_llm_fn=_llm_returns_lane(LANE_CONCEPT),
        )
        assert result.content_lane == LANE_PROCEDURAL

    def test_mixed_goes_to_llm_not_short_circuited(self, tmp_path: Path) -> None:
        """SC-1（worker round-4）：混合内容（机制+2 命令）不再 deterministic 短路 procedural。

        旧 spec FR-002「混合从严归 procedural」被反转——2 个 illustrative 命令不足以确定性
        判 procedural（需 ≥3 shell 命令 / how-to 标题 / 含命令编号步才短路）。混合交 LLM
        主判 intent。满仓 12/18 失败的根因就是旧短路把概念博客误判 procedural。
        """
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/mixed--raw--2026-06-27.md",
            raw_content=_MIXED_BODY,
            ledger_path=ledger,
            route_llm_fn=_llm_returns_lane(LANE_CONCEPT),  # LLM 判 concept（主旨是机制）
        )
        # 不再短路 procedural；LLM 被调用并判 concept
        assert result.content_lane == LANE_CONCEPT, (
            "混合内容（2 命令）应交 LLM 主判，不短路 procedural（SC-1）"
        )


# ---------------------------------------------------------------------------
# SC-3: how_tendency 不再拒收纯概念内容（路由信号）
# ---------------------------------------------------------------------------


class TestConceptNotRejectedByHow:
    def test_concept_not_rejected_by_how(self, tmp_path: Path) -> None:
        """纯概念内容 how_tendency=fail 但 passed=True（concept 车道 how 路由化）。"""
        from src.akb2.quality.llm_router import LLMResponse

        ledger = str(tmp_path / "ledger.jsonl")

        def _mock(model_family, messages, response_schema=None, **kwargs):
            sys_prompt = messages[0]["content"] if messages else ""
            if "概念型" in sys_prompt or "操作型" in sys_prompt:
                return LLMResponse(
                    content={"content_lane": LANE_CONCEPT, "reason": "概念型"},
                    model_used="gemini-2.5-flash",
                )
            if "可执行知识" in sys_prompt or "how-to" in sys_prompt:
                # how_tendency 明确 fail（纯概念无可执行步骤）
                return LLMResponse(
                    content={"pass": False, "reason": "无可执行步骤，纯概念"},
                    model_used="gemini-2.5-flash",
                )
            # topic 等 pass
            return LLMResponse(
                content={"pass": True, "reason": "命中领域"},
                model_used="gemini-2.5-flash",
            )

        result = run_admission(
            raw_path="raw/concept--raw--2026-06-26.md",
            raw_content=_CONCEPT_BODY,
            ledger_path=ledger,
            route_llm_fn=_mock,
        )
        # how_tendency 判定本身 fail，但 concept 车道下不使 passed=False
        how_check = next(c for c in result.checks if c.name == "how_tendency")
        assert how_check.passed is False, "how_tendency 判定本身应为 fail（纯概念无命令）"
        assert result.content_lane == LANE_CONCEPT
        assert result.passed is True, "concept 车道下 how_tendency fail 不应拒收"


# ---------------------------------------------------------------------------
# SC-4: lane 分类器 LLM 不可用时 fail-closed
# ---------------------------------------------------------------------------


class TestLaneLLMUnavailableFailClosed:
    def test_lane_llm_unavailable_fail_closed(self, tmp_path: Path) -> None:
        """lane 分类器 LLM 不可用（无命令证据）→ fail-closed 拒收 + 落账，不静默归车道。"""
        ledger_path = tmp_path / "ledger.jsonl"
        result = run_admission(
            raw_path="raw/concept--raw--2026-06-26.md",
            raw_content=_CONCEPT_BODY,  # 无命令 → 必须走 LLM → LLM 抛错
            ledger_path=str(ledger_path),
            route_llm_fn=_llm_pass_then_lane_raises(),
        )
        assert result.passed is False, "lane 分类器不可用应 fail-closed 拒收"
        assert result.content_lane == "", "fail-closed 时不静默归车道（content_lane 为空）"
        assert "content_lane" in result.reason

        # 落账可见：failed_checks 含 content_lane
        assert ledger_path.exists()
        lines = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert lines, "fail-closed 应落账"
        rejection = lines[0]
        assert rejection["details"]["admission_result"] == "rejected"
        assert "content_lane" in rejection["details"]["failed_checks"]


# ---------------------------------------------------------------------------
# SC-1（bugfix）：概念文章夹单个示例代码块不应被确定性判 procedural
# ---------------------------------------------------------------------------

# 概念阐述 + 单个示例代码块：SC-1 前 count=1 ≥ 旧阈值 1 → 确定性 procedural
# （how_tendency 随之正当拒收概念内容，实测 7/18 满仓正样本卡此）；SC-1 后阈值=2，
# count=1 < 2 → 不短路，交 LLM 轻判。
_CONCEPT_WITH_ONE_BLOCK = """# The Four-Layer Loop Model

Loop engineering describes a conceptual architecture for AI agents. The model has four
conceptual layers: the trigger layer defines when work begins, the orchestration layer
decides what to do, the execution layer performs the work, and the observability layer
records outcomes. This model explains why agent reliability depends on loop design
rather than prompt quality. The fundamental principle is that convergence emerges from
explicit bounded stopping conditions. Understanding this distinction reframes the
engineering problem from prompt-authoring to loop-design.

```bash
python -m akb2.quality.runner --once
```

As shown above, the runner invocation is merely illustrative of the execution layer.
"""


class TestConceptWithIncidentalCode:
    """SC-1：概念文章夹单个示例代码块（count=1）应交 LLM 轻判，不短路 procedural。"""

    def test_one_code_block_defers_to_llm_and_lands_concept(self, tmp_path: Path) -> None:
        """count=1 < 阈值 2 → 不短路 procedural，交 LLM 轻判 → concept。"""
        from src.akb2.quality.admission import _scan_procedural_evidence_count

        # 守卫：测试素材确实只有 1 个可执行操作（否则测试本身失效）
        assert _scan_procedural_evidence_count(_CONCEPT_WITH_ONE_BLOCK) == 1

        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/concept-one-block--raw--2026-06-27.md",
            raw_content=_CONCEPT_WITH_ONE_BLOCK,
            ledger_path=ledger,
            route_llm_fn=_llm_returns_lane(LANE_CONCEPT),
        )
        assert result.content_lane == LANE_CONCEPT, (
            "单个示例代码块不应确定性判 procedural；应交 LLM 轻判 concept（SC-1）"
        )

    def test_one_code_block_llm_procedural_still_procedural(self, tmp_path: Path) -> None:
        """count=1 交 LLM，LLM 判 procedural 时仍 procedural（LLM 裁决被尊重）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/concept-one-block--raw--2026-06-27.md",
            raw_content=_CONCEPT_WITH_ONE_BLOCK,
            ledger_path=ledger,
            route_llm_fn=_llm_returns_lane(LANE_PROCEDURAL),
        )
        assert result.content_lane == LANE_PROCEDURAL


# ---------------------------------------------------------------------------
# SC-1（worker round-4）：concept intent + 示例代码/API/编号机制点不得短路 procedural
# ---------------------------------------------------------------------------

# 概念阐述 + 2 个 fenced Python illustrative call（非 bash，非 how-to）→ 应交 LLM 判 concept
_CONCEPT_WITH_2_FENCED_CALLS = """# The Four-Layer Loop Model

Loop engineering describes a conceptual architecture for AI agents. The model
separates orchestration from execution. Understanding this distinction reframes
the engineering problem from prompt-authoring to loop-design.

```python
result = agent.run(task)
queue.dispatch(job)
```

The two calls above illustrate the execution layer, not a how-to guide.
"""

# 概念阐述 + 3 个编号机制点（无命令）→ 应交 LLM 判 concept
_CONCEPT_WITH_3_NUMBERED_MECHANISM = """# The Four-Layer Loop Model

The model has four conceptual layers:

1. The trigger layer defines when work begins
2. The orchestration layer decides what to do
3. The execution layer performs the work
4. The observability layer records outcomes

These are conceptual components of the architecture, not procedural steps.
"""

# 概念阐述 + prose 里 API 名（agent.plan()/execute()/ledger.append()）→ 应交 LLM 判 concept
_CONCEPT_WITH_API_NAMES_IN_PROSE = """# The Four-Layer Loop Model

Loop engineering describes a conceptual architecture. The orchestration layer
calls agent.plan() and agent.execute() to decide and perform work. The
observability layer uses ledger.append() to record outcomes. This model
explains why convergence emerges from bounded stopping conditions rather than
raw model intelligence. The distinction reframes the engineering problem.
"""


class TestConceptIntentNotShortCircuited:
    """SC-1（worker round-4）：concept intent + 示例代码/API/编号机制点不得被 deterministic 短路成 procedural。

    满仓 12/18 失败根因：旧 `count>=2` 短路把这些概念样本判成 procedural。新逻辑只对
    强 procedural 形态（≥3 shell 命令 / how-to 标题 / 含命令编号步）短路，其余交 LLM。
    """

    def test_concept_with_2_fenced_calls_judged_concept(self, tmp_path: Path) -> None:
        """2 个 Python illustrative call 不触发强 procedural 短路 → LLM 判 concept。"""
        from src.akb2.quality.admission import _has_strong_procedural_signal

        assert not _has_strong_procedural_signal(_CONCEPT_WITH_2_FENCED_CALLS), (
            "2 个 Python call 不构成强 procedural 信号（SC-1）"
        )
        result = run_admission(
            raw_path="raw/concept-2calls--raw--2026-06-27.md",
            raw_content=_CONCEPT_WITH_2_FENCED_CALLS,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_llm_returns_lane(LANE_CONCEPT),
        )
        assert result.content_lane == LANE_CONCEPT

    def test_concept_with_3_numbered_mechanism_judged_concept(self, tmp_path: Path) -> None:
        """3 个编号机制点（无命令）不触发强 procedural 短路 → LLM 判 concept。"""
        from src.akb2.quality.admission import _has_strong_procedural_signal

        assert not _has_strong_procedural_signal(_CONCEPT_WITH_3_NUMBERED_MECHANISM)
        result = run_admission(
            raw_path="raw/concept-3num--raw--2026-06-27.md",
            raw_content=_CONCEPT_WITH_3_NUMBERED_MECHANISM,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_llm_returns_lane(LANE_CONCEPT),
        )
        assert result.content_lane == LANE_CONCEPT

    def test_concept_with_api_names_in_prose_judged_concept(self, tmp_path: Path) -> None:
        """prose 里 API 名（agent.plan() 等）不触发强 procedural 短路 → LLM 判 concept。"""
        from src.akb2.quality.admission import _has_strong_procedural_signal

        assert not _has_strong_procedural_signal(_CONCEPT_WITH_API_NAMES_IN_PROSE)
        result = run_admission(
            raw_path="raw/concept-api--raw--2026-06-27.md",
            raw_content=_CONCEPT_WITH_API_NAMES_IN_PROSE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_llm_returns_lane(LANE_CONCEPT),
        )
        assert result.content_lane == LANE_CONCEPT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
