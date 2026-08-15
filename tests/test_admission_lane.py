"""test_admission_lane.py — 准入车道分类 + 单次结构化判定测试（P3 重构契约）。

覆盖（sc-p3-lane-enum / sc-p3-single-llm-call / sc-p3-lane-scoped-gates）:
    - 车道枚举：concept / tutorial / pattern / design 四通过态 + reject 一票拒；
      procedural 兼容别名归一为 tutorial；未知车道 fail-closed
    - 单次调用：单篇 run_admission 对 route_llm 恰好 1 次调用
      （novelty exact 重复路径为 0 次）
    - 车道作用域门：mechanism_signal 仅 concept 产出；how_tendency 仅 tutorial 拒收
    - 单次判定 LLM 不可用 → fail-closed（拒收+落账），不静默归车道

测试不打真 API：mock route_llm 返回单次结构化判定响应（unified schema）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.akb2.quality.admission import (
    LANE_CONCEPT,
    LANE_DESIGN,
    LANE_PATTERN,
    LANE_REJECT,
    LANE_TUTORIAL,
    AdmissionResult,
    run_admission,
)
from src.akb2.quality.llm_router import LLMResponse, RouterConfigError

# ---------------------------------------------------------------------------
# 测试内容素材
# ---------------------------------------------------------------------------

# 纯概念内容：0 可执行命令、机制阐述清晰（应判 concept；how_tendency 天然 fail）
_CONCEPT_BODY = """# The Four-Layer Loop Model

### 1. Trigger Layer
The trigger layer defines when work begins.

### 2. Orchestration Layer
The orchestration layer decides what to do.

### 3. Execution Layer
The execution layer performs the work.
"""

# 操作型内容：含可执行命令（确定性判 tutorial，不依赖 LLM 车道）
_TUTORIAL_BODY = """# How to build a bounded loop

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

# 混合内容：既含机制阐述、又含少量命令 → 交 LLM 主判（不短路）
_MIXED_BODY = """# Loop Engineering: Concepts and Commands

Loop engineering reframes agent work as bounded loops. The conceptual model
separates orchestration from execution. To put it into practice:

```bash
python -m akb2.quality.runner --once
make pipeline
```

The mechanism explains why convergence beats restart, and the commands show how.
"""

# pattern 内容：可复用模式集（无机制组件、无命令）
_PATTERN_BODY = """# Agent Design Patterns

A collection of reusable patterns for agent systems. Pattern one: bounded loop
with explicit stop conditions. Pattern two: maker-checker separation between
producer and judge. Pattern three: ledger-driven state machine for idempotent
progress. Each pattern is a named, reusable solution to a recurring problem.
"""

# design 内容：设计呈现（无机制组件、无命令）
_DESIGN_BODY = """# Dashboard Visual Design

This design presents a monitoring dashboard layout. The visual hierarchy puts
status first, trends second, details third. Color tokens map severity to a
three-step scale. Spacing follows an eight-point grid for rhythm.
"""


# ---------------------------------------------------------------------------
# mock 工具
# ---------------------------------------------------------------------------


def _unified_llm(
    lane: str,
    topic_pass: bool = True,
    how_pass: bool = True,
    mechanism_pass: bool = True,
    non_knowledge: bool = False,
):
    """构造 mock route_llm：单次结构化判定返回指定车道与子判定。"""
    calls = {"n": 0}

    def _mock(model_family, messages, response_schema=None, **kwargs):
        calls["n"] += 1
        return LLMResponse(
            content={
                "topic_pass": topic_pass,
                "lane": lane,
                "non_knowledge": non_knowledge,
                "mechanism_pass": mechanism_pass,
                "how_pass": how_pass,
                "reason": f"mock unified lane={lane}",
            },
            model_used="deepseek-v4-pro",
        )

    _mock.calls = calls  # type: ignore[attr-defined]
    return _mock


# ---------------------------------------------------------------------------
# sc-p3-lane-enum: 车道枚举与通过态闭合
# ---------------------------------------------------------------------------


class TestLaneEnum:
    def test_result_exposes_lane(self, tmp_path: Path) -> None:
        """AdmissionResult 暴露 content_lane 字段，tutorial 内容通过时为 tutorial。"""
        ledger = str(tmp_path / "ledger.jsonl")
        mock = _unified_llm(LANE_TUTORIAL)
        result = run_admission(
            raw_path="raw/tutorial--raw--2026-08-15.md",
            raw_content=_TUTORIAL_BODY,
            ledger_path=ledger,
            route_llm_fn=mock,
        )
        assert "content_lane" in AdmissionResult.__dataclass_fields__
        assert result.passed is True
        assert result.content_lane == LANE_TUTORIAL

    def test_passable_lanes_closed_enum(self, tmp_path: Path) -> None:
        """通过时 content_lane 只取 concept/tutorial/pattern/design（无 unknown/None）。"""
        from src.akb2.quality.admission import LANE_PASSABLE

        assert LANE_PASSABLE == (LANE_CONCEPT, LANE_TUTORIAL, LANE_PATTERN, LANE_DESIGN)
        ledger = str(tmp_path / "ledger.jsonl")
        for body, lane in (
            (_CONCEPT_BODY, LANE_CONCEPT),
            (_TUTORIAL_BODY, LANE_TUTORIAL),
            (_PATTERN_BODY, LANE_PATTERN),
            (_DESIGN_BODY, LANE_DESIGN),
        ):
            result = run_admission(
                raw_path=f"raw/{lane}--raw--2026-08-15.md",
                raw_content=body,
                ledger_path=ledger,
                route_llm_fn=_unified_llm(lane),
            )
            assert result.passed is True, f"{lane} 应通过: {result.reason}"
            assert result.content_lane == lane
            assert result.content_lane not in ("", "unknown", None, "procedural")

    def test_lane_constants_defined(self) -> None:
        """车道常量：tutorial/pattern/design 新枚举 + procedural 别名 + reject。"""
        import src.akb2.quality.admission as adm

        assert adm.LANE_TUTORIAL == "tutorial"
        assert adm.LANE_PATTERN == "pattern"
        assert adm.LANE_DESIGN == "design"
        assert adm.LANE_REJECT == "reject"
        assert adm.LANE_PROCEDURAL == "procedural"  # 兼容别名保留

    def test_procedural_alias_normalized_to_tutorial(self, tmp_path: Path) -> None:
        """LLM 回旧值 procedural → 输出侧归一为 tutorial（不再产生 procedural 终值）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/alias--raw--2026-08-15.md",
            raw_content="# Guide\n\nSome mixed guide content without strong signals.",
            ledger_path=ledger,
            route_llm_fn=_unified_llm("procedural"),
        )
        assert result.content_lane == LANE_TUTORIAL, (
            "procedural 别名应归一为 tutorial 终值"
        )

    def test_unknown_lane_fail_closed(self, tmp_path: Path) -> None:
        """未知车道值 → fail-closed 拒收 + 落账，不静默归车道。"""
        ledger_path = tmp_path / "ledger.jsonl"

        def _mock(model_family, messages, response_schema=None, **kwargs):
            return LLMResponse(
                content={"topic_pass": True, "lane": "mystery", "non_knowledge": False,
                         "reason": "unknown lane"},
                model_used="m",
            )

        result = run_admission(
            raw_path="raw/unknown--raw--2026-08-15.md",
            raw_content=_CONCEPT_BODY,
            ledger_path=str(ledger_path),
            route_llm_fn=_mock,
        )
        assert result.passed is False, "未知车道应 fail-closed 拒收"
        assert result.content_lane == ""
        assert "content_lane" in result.reason
        # 落账可见
        lines = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert lines and "content_lane" in lines[0]["details"]["failed_checks"]


# ---------------------------------------------------------------------------
# sc-p3-single-llm-call: 单篇恰好 1 次调用
# ---------------------------------------------------------------------------


class TestSingleCallBudget:
    def test_concept_path_exactly_one_llm_call(self, tmp_path: Path) -> None:
        """concept 全判定路径（topic+lane+non_knowledge+mechanism+how）恰好 1 次 LLM。"""
        ledger = str(tmp_path / "ledger.jsonl")
        mock = _unified_llm(LANE_CONCEPT)
        result = run_admission(
            raw_path="raw/concept--raw--2026-08-15.md",
            raw_content=_CONCEPT_BODY,
            ledger_path=ledger,
            route_llm_fn=mock,
        )
        assert result.passed is True
        assert mock.calls["n"] == 1, (
            f"单篇 run_admission 应恰好 1 次 LLM 调用，实际 {mock.calls['n']}"
        )

    def test_pattern_design_path_exactly_one_llm_call(self, tmp_path: Path) -> None:
        """pattern/design 路径同样恰好 1 次（无独立 mechanism/lane 调用）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        for body, lane in ((_PATTERN_BODY, LANE_PATTERN), (_DESIGN_BODY, LANE_DESIGN)):
            mock = _unified_llm(lane, mechanism_pass=False)
            result = run_admission(
                raw_path=f"raw/{lane}--raw--2026-08-15.md",
                raw_content=body,
                ledger_path=ledger,
                route_llm_fn=mock,
            )
            assert result.passed is True, f"{lane}: {result.reason}"
            assert mock.calls["n"] == 1

    def test_strong_procedural_short_circuit_still_one_call(self, tmp_path: Path) -> None:
        """强 procedural 信号短路车道（tutorial）后仍恰好 1 次调用（判 topic 等）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        mock = _unified_llm(LANE_CONCEPT)  # LLM 想判 concept，确定性 tutorial 盖过
        result = run_admission(
            raw_path="raw/tutorial--raw--2026-08-15.md",
            raw_content=_TUTORIAL_BODY,
            ledger_path=ledger,
            route_llm_fn=mock,
        )
        assert result.content_lane == LANE_TUTORIAL, "确定性短路应盖过 LLM 车道"
        assert mock.calls["n"] == 1

    def test_novelty_exact_duplicate_zero_llm_calls(self, tmp_path: Path) -> None:
        """novelty exact 重复（同 slug/url）→ 确定性拒收，0 次 LLM 调用。"""
        ledger = str(tmp_path / "ledger.jsonl")
        mock = _unified_llm(LANE_CONCEPT)
        dup_raw = (
            "---\nsource_slug: captain\nsource_url: https://x/c\n---\n\n"
            "# Captain\nsame claim content"
        )
        result = run_admission(
            raw_path="raw/dup--raw--2026-08-15.md",
            raw_content=dup_raw,
            ledger_path=ledger,
            wiki_references=[
                {"slug": "captain", "title": "Captain", "tldr": "captain arch",
                 "path": "p", "source_url": "https://x/c"},
            ],
            route_llm_fn=mock,
        )
        assert result.passed is False
        assert "novelty" in result.reason
        assert mock.calls["n"] == 0, "exact 重复应 0 次 LLM 调用（确定性拒收）"

    def test_unified_model_family_is_deepseek(self, tmp_path: Path) -> None:
        """单次判定收到的 model_family 为 ADMISSION_MODEL（deepseek-v4-pro，P7 异族）。"""
        from src.akb2.quality.admission import ADMISSION_MODEL

        seen: list[str] = []

        def _mock(model_family, messages, response_schema=None, **kwargs):
            seen.append(model_family)
            return LLMResponse(
                content={"topic_pass": True, "lane": LANE_CONCEPT, "non_knowledge": False,
                         "mechanism_pass": True, "how_pass": True, "reason": "ok"},
                model_used="deepseek-v4-pro",
            )

        run_admission(
            raw_path="raw/model--raw--2026-08-15.md",
            raw_content=_CONCEPT_BODY,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_mock,
        )
        assert seen == [ADMISSION_MODEL] == ["deepseek-v4-pro"]


# ---------------------------------------------------------------------------
# sc-p3-lane-scoped-gates: 车道作用域门
# ---------------------------------------------------------------------------


class TestLaneScopedGates:
    def test_concept_how_fail_not_rejected(self, tmp_path: Path) -> None:
        """concept 车道 how_tendency fail 不拒收（路由信号，非 concept 拒收门）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/concept-how-fail--raw--2026-08-15.md",
            raw_content=_CONCEPT_BODY,
            ledger_path=ledger,
            route_llm_fn=_unified_llm(LANE_CONCEPT, how_pass=False),
        )
        how_check = next(c for c in result.checks if c.name == "how_tendency")
        assert how_check.passed is False, "how_tendency 判定本身应为 fail"
        assert result.content_lane == LANE_CONCEPT
        assert result.passed is True, "concept 车道 how fail 不应拒收"

    def test_tutorial_how_fail_rejected(self, tmp_path: Path) -> None:
        """tutorial 车道 how_tendency fail → 拒收（唯一把 how 当拒收门的车道）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/tutorial-how-fail--raw--2026-08-15.md",
            raw_content="# Guide\n\nMixed guide prose without strong signals.",
            ledger_path=ledger,
            route_llm_fn=_unified_llm(LANE_TUTORIAL, how_pass=False),
        )
        assert result.passed is False, "tutorial 车道 how fail 应拒收"
        assert "how_tendency" in result.reason

    def test_pattern_design_how_fail_not_rejected(self, tmp_path: Path) -> None:
        """pattern/design 车道 how fail 不拒收（非 tutorial 缺步不得 abort）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        for body, lane in ((_PATTERN_BODY, LANE_PATTERN), (_DESIGN_BODY, LANE_DESIGN)):
            result = run_admission(
                raw_path=f"raw/{lane}-how-fail--raw--2026-08-15.md",
                raw_content=body,
                ledger_path=ledger,
                route_llm_fn=_unified_llm(lane, how_pass=False),
            )
            assert result.passed is True, f"{lane} 缺 how 不应 abort: {result.reason}"

    def test_mechanism_check_only_produced_for_concept(self, tmp_path: Path) -> None:
        """mechanism_signal check 仅 concept 车道产出（其余车道 3 条判定）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        cases = [
            (_CONCEPT_BODY, LANE_CONCEPT, _unified_llm(LANE_CONCEPT)),
            (_TUTORIAL_BODY, LANE_TUTORIAL, _unified_llm(LANE_TUTORIAL)),
            (_PATTERN_BODY, LANE_PATTERN, _unified_llm(LANE_PATTERN)),
            (_DESIGN_BODY, LANE_DESIGN, _unified_llm(LANE_DESIGN)),
        ]
        for body, lane, mock in cases:
            result = run_admission(
                raw_path=f"raw/{lane}-mech--raw--2026-08-15.md",
                raw_content=body,
                ledger_path=ledger,
                route_llm_fn=mock,
            )
            names = [c.name for c in result.checks]
            if lane == LANE_CONCEPT:
                assert "mechanism_signal" in names, "concept 应产出 mechanism_signal"
                assert len(names) == 4
            else:
                assert "mechanism_signal" not in names, (
                    f"{lane} 车道不应产出 mechanism_signal: {names}"
                )
                assert len(names) == 3

    def test_pattern_design_missing_mechanism_not_aborted(self, tmp_path: Path) -> None:
        """pattern/design 缺机制组件（mechanism_pass=false）不得 abort（非 concept）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        for body, lane in ((_PATTERN_BODY, LANE_PATTERN), (_DESIGN_BODY, LANE_DESIGN)):
            result = run_admission(
                raw_path=f"raw/{lane}-nomech--raw--2026-08-15.md",
                raw_content=body,
                ledger_path=ledger,
                route_llm_fn=_unified_llm(lane, mechanism_pass=False),
            )
            assert result.passed is True, (
                f"{lane} 缺机制组件不应 abort（mechanism 门仅 concept）: {result.reason}"
            )

    def test_concept_missing_mechanism_rejected(self, tmp_path: Path) -> None:
        """concept 车道 mechanism_pass=false → 拒收（机制门在 concept 生效）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/concept-nomech--raw--2026-08-15.md",
            raw_content=_CONCEPT_BODY,
            ledger_path=ledger,
            route_llm_fn=_unified_llm(LANE_CONCEPT, mechanism_pass=False),
        )
        assert result.passed is False
        assert "no_mechanism_signal" in result.reason


# ---------------------------------------------------------------------------
# 分类器判例（单次判定车道裁决）
# ---------------------------------------------------------------------------


class TestClassifierCases:
    def test_classifier_concept(self, tmp_path: Path) -> None:
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/concept--raw--2026-08-15.md",
            raw_content=_CONCEPT_BODY,
            ledger_path=ledger,
            route_llm_fn=_unified_llm(LANE_CONCEPT),
        )
        assert result.content_lane == LANE_CONCEPT

    def test_classifier_tutorial_short_circuit(self, tmp_path: Path) -> None:
        """强 procedural 形态（≥3 shell 命令）确定性短路 tutorial，盖过 LLM。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/tutorial--raw--2026-08-15.md",
            raw_content=_TUTORIAL_BODY,
            ledger_path=ledger,
            route_llm_fn=_unified_llm(LANE_CONCEPT),  # LLM 想判 concept
        )
        assert result.content_lane == LANE_TUTORIAL

    def test_mixed_goes_to_llm_not_short_circuited(self, tmp_path: Path) -> None:
        """混合内容（机制+2 命令）不短路，交单次判定主判 intent（LLM 判 concept）。"""
        from src.akb2.quality.admission import _has_strong_procedural_signal

        assert not _has_strong_procedural_signal(_MIXED_BODY)
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/mixed--raw--2026-08-15.md",
            raw_content=_MIXED_BODY,
            ledger_path=ledger,
            route_llm_fn=_unified_llm(LANE_CONCEPT),
        )
        assert result.content_lane == LANE_CONCEPT, "混合内容应交 LLM 主判"

    def test_reject_lane_one_vote(self, tmp_path: Path) -> None:
        """LLM 判 reject → 一票拒收（non_knowledge），content_lane 不下传。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/news--raw--2026-08-15.md",
            raw_content="# Product Launch\n\nWe are excited to announce our new product.",
            ledger_path=ledger,
            route_llm_fn=_unified_llm(LANE_REJECT),
        )
        assert result.passed is False
        assert "non_knowledge" in result.reason
        assert result.content_lane == ""

    def test_non_knowledge_flag_veto_even_with_passable_lane(self, tmp_path: Path) -> None:
        """non_knowledge=true 即便 lane=concept 也一票拒（一票出语义）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/recap--raw--2026-08-15.md",
            raw_content="# Event Recap\n\nKeynote recap without mechanism.",
            ledger_path=ledger,
            route_llm_fn=_unified_llm(LANE_CONCEPT, non_knowledge=True),
        )
        assert result.passed is False
        assert "non_knowledge" in result.reason
        assert result.content_lane == ""

    def test_topic_fail_rejects_all_lanes(self, tmp_path: Path) -> None:
        """topic_pass=false 全车道拒收（领域硬门，车道不豁免）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        for body, lane in (
            (_CONCEPT_BODY, LANE_CONCEPT),
            (_PATTERN_BODY, LANE_PATTERN),
        ):
            result = run_admission(
                raw_path=f"raw/{lane}-offtopic--raw--2026-08-15.md",
                raw_content=body,
                ledger_path=ledger,
                route_llm_fn=_unified_llm(lane, topic_pass=False),
            )
            assert result.passed is False, f"{lane} topic fail 应拒收"
            assert "topic_relevance" in result.reason


# ---------------------------------------------------------------------------
# 单次判定 LLM 不可用 → fail-closed
# ---------------------------------------------------------------------------


class TestUnifiedLLMUnavailableFailClosed:
    def test_unified_llm_unavailable_fail_closed(self, tmp_path: Path) -> None:
        """单次判定调用抛 RouterConfigError → fail-closed 拒收 + 落账。"""
        ledger_path = tmp_path / "ledger.jsonl"

        def _mock(model_family, messages, response_schema=None, **kwargs):
            raise RouterConfigError("unified admission model unavailable")

        result = run_admission(
            raw_path="raw/concept--raw--2026-08-15.md",
            raw_content=_CONCEPT_BODY,
            ledger_path=str(ledger_path),
            route_llm_fn=_mock,
        )
        assert result.passed is False, "单次判定不可用应 fail-closed 拒收"
        assert result.content_lane == ""
        assert "content_lane" in result.reason

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
# 概念文夹示例代码不短路（SC-1 判例保留）
# ---------------------------------------------------------------------------

_CONCEPT_WITH_ONE_BLOCK = """# The Four-Layer Loop Model

Loop engineering describes a conceptual architecture for AI agents. The model
separates orchestration from execution. Convergence emerges from bounded
stopping conditions.

```bash
python -m akb2.quality.runner --once
```

The runner invocation above is merely illustrative of the execution layer.
"""


class TestConceptWithIncidentalCode:
    def test_one_code_block_defers_to_llm_and_lands_concept(self, tmp_path: Path) -> None:
        """单个示例代码块不短路 procedural，交单次判定 → concept。"""
        from src.akb2.quality.admission import _scan_procedural_evidence_count

        assert _scan_procedural_evidence_count(_CONCEPT_WITH_ONE_BLOCK) == 1
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/concept-one-block--raw--2026-08-15.md",
            raw_content=_CONCEPT_WITH_ONE_BLOCK,
            ledger_path=ledger,
            route_llm_fn=_unified_llm(LANE_CONCEPT),
        )
        assert result.content_lane == LANE_CONCEPT

    def test_one_code_block_llm_tutorial_still_tutorial(self, tmp_path: Path) -> None:
        """count=1 交 LLM，LLM 判 tutorial 时仍 tutorial（LLM 裁决被尊重）。"""
        ledger = str(tmp_path / "ledger.jsonl")
        result = run_admission(
            raw_path="raw/concept-one-block--raw--2026-08-15.md",
            raw_content=_CONCEPT_WITH_ONE_BLOCK,
            ledger_path=ledger,
            route_llm_fn=_unified_llm(LANE_TUTORIAL),
        )
        assert result.content_lane == LANE_TUTORIAL


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
