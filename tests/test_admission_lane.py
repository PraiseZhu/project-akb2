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

    def test_classifier_cases_mixed_goes_procedural(self, tmp_path: Path) -> None:
        """混合内容（机制+命令）归 procedural（spec FR-002 从严）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/mixed--raw--2026-06-26.md",
            raw_content=_MIXED_BODY,
            ledger_path=ledger,
            route_llm_fn=_llm_returns_lane(LANE_CONCEPT),
        )
        assert result.content_lane == LANE_PROCEDURAL


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
