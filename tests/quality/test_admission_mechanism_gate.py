"""test_admission_mechanism_gate.py — 机制信号准入门测试（P3 单次判定契约改写）。

覆盖（sc-p3-lane-scoped-gates / sc-p3-single-llm-call）:
    - 无机制散文（openai 政策/新闻/纯观点）fail 于 mechanism_signal
      (no_mechanism_signal)——确定性强反信号，单次判定 mechanism_pass 不可绕过
    - 有机制源过 mechanism_signal（单次判定 mechanism_pass 裁决）
    - tutorial/pattern/design 候选不受 mechanism_signal 门（F4 H1 判 + 新车道）
    - novelty 确定性化：exact 重复拒、lexical top-k pass + reason 记近邻、
      无近邻 pass 低置信（不再打 LLM）
    - reject 一票拒（D11 降级已删：reject 直接拒，不降级 concept）
    - F8 长正文无信号走单次判定；短正文 total==0 仍确定性 fail

mock 边界：route_llm_fn（单次结构化判定），不发真网络。断言绑可观测行为
（mechanism_signal check passed / error_class / checks 数 / LLM 调用计数）。
"""

from __future__ import annotations

from pathlib import Path

from src.akb2.quality.admission import (
    LANE_CONCEPT,
    LANE_DESIGN,
    LANE_PATTERN,
    LANE_REJECT,
    LANE_TUTORIAL,
    run_admission,
)
from src.akb2.quality.llm_router import LLMResponse

# ---------------------------------------------------------------------------
# mock route_llm
# ---------------------------------------------------------------------------


def _make_mock(
    lane: str = LANE_CONCEPT,
    pass_response: bool = True,
    how_pass: bool | None = None,
    mechanism_only_fail: bool = False,
):
    """mock route_llm: 单次结构化判定返回指定车道与子判定。

    mechanism_pass / how_pass / topic_pass 均由 pass_response 驱动；
    how_pass 显式传值时覆盖。mechanism_only_fail=True 时仅 mechanism_pass
    为 False（topic/how 保持 pass，隔离"缺机制"单一拒因）。确定性强反信号
    （全信号 0 且正文短）不调 LLM。
    """
    calls = {"n": 0}

    def _mock(model_family, messages, response_schema=None, **kwargs):
        calls["n"] += 1
        return LLMResponse(
            content={
                "topic_pass": pass_response,
                "lane": lane,
                "non_knowledge": False,
                "mechanism_pass": not mechanism_only_fail and pass_response,
                "how_pass": how_pass if how_pass is not None else pass_response,
                "reason": "mock pass" if pass_response else "mock fail",
            },
            model_used="deepseek-v4-pro",
        )

    _mock.calls = calls  # type: ignore[attr-defined]
    return _mock


# ---------------------------------------------------------------------------
# 测试 raw 内容
# ---------------------------------------------------------------------------

# 无机制散文（openai 安全治理政策型）：纯散文，无命名组件/编号步/code/因果/source_op
_NO_MECHANISM_PROSE = """---
artifact_type: raw
source_slug: openai-safety
source_url: https://example.com/safety
---

# Appia Foundation Safety Standards

The Appia Foundation published shared standards for AI safety. International bodies
agree on common principles for responsible deployment. CAISI provides a framework
for trust between organizations. The community supports these shared standards.
Widespread adoption promotes accountability across the industry.
"""

# 有机制源（ag2 captain 风格）：4 命名 H3 组件 → 单次判定 mechanism_pass 裁决
_MECHANISM_SOURCE = """---
artifact_type: raw
source_slug: ag2-captain
source_url: https://example.com/captain
---

# Captain Agent Architecture

### 1. Captain (Coordinator)
The Captain holds the system goal and dispatches sub-tasks to executors.

### 2. Executor Pool
Executors register capability manifests and receive scoped context.

### 3. Task Graph and Checkpointing
The task graph is a DAG of sub-tasks with dependencies and status tracking.

### 4. Sub-Agent Lifecycle
Executors spawn on demand and terminate after producing a typed result.
"""

# tutorial 源（how-to + shell 命令）：强 procedural 信号短路车道，不走 mechanism_signal
_TUTORIAL_SOURCE = """---
artifact_type: raw
source_slug: how-to-agent
source_url: https://example.com/how-to
---

# How to Set Up an Agent Loop

Follow these steps to configure and run your agent loop.

```bash
npm install agent-cli
agent init my-loop
agent run --max-steps 10
```
"""

# pattern 源：可复用模式集（无机制组件命名、无命令）
_PATTERN_SOURCE = """---
artifact_type: raw
source_slug: agent-patterns
source_url: https://example.com/patterns
---

# Agent Design Patterns

Reusable patterns for agent systems. Pattern one: bounded loop with explicit
stop conditions. Pattern two: maker-checker separation. Pattern three:
ledger-driven idempotent progress. Each pattern is a named reusable solution.
"""

# design 源：设计呈现（无机制组件命名、无命令）
_DESIGN_SOURCE = """---
artifact_type: raw
source_slug: dashboard-design
source_url: https://example.com/design
---

# Dashboard Visual Design

A monitoring dashboard layout presentation. Visual hierarchy puts status first,
trends second, details third. Color tokens map severity to a three-step scale.
Spacing follows an eight-point grid for rhythm.
"""

# 模糊态：1 命名组件 → total>0 → mechanism 由单次判定裁决
_AMBIGUOUS_MECHANISM = """---
artifact_type: raw
source_slug: vague-concept
source_url: https://example.com/vague
---

# Some Concept

### 1. Only Component
A single named component with no causal edges or further structure.
"""


# ---------------------------------------------------------------------------
# 机制门（sc-p3-lane-scoped-gates：仅 concept）
# ---------------------------------------------------------------------------


class TestMechanismSignalGate:
    """concept 候选机制信号准入门。"""

    def test_no_mechanism_prose_fails_at_mechanism_signal(self, tmp_path: Path):
        """无机制散文 → mechanism_signal fail (no_mechanism_signal)，不下传 distill。"""
        result = run_admission(
            raw_path="raw/openai-safety--raw--2026-08-15.md",
            raw_content=_NO_MECHANISM_PROSE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        assert result.passed is False, "无机制散文不应通过准入"
        # error_class 可观测：reason 含 no_mechanism_signal（runner 据此映射 error_class）
        assert "no_mechanism_signal" in result.reason, (
            f"reason 应含 no_mechanism_signal: {result.reason}"
        )
        # mechanism_signal check 在场且失败
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.passed is False
        assert mech.reason.startswith("no_mechanism_signal")

    def test_llm_cannot_override_deterministic_no_signal(self, tmp_path: Path):
        """确定性强反信号（无机制）即便单次判定 mechanism_pass=true 也仍 fail。"""
        result = run_admission(
            raw_path="raw/openai-safety--raw--2026-08-15.md",
            raw_content=_NO_MECHANISM_PROSE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        assert result.passed is False
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.passed is False
        assert mech.reason.startswith("no_mechanism_signal")

    def test_mechanism_source_passes(self, tmp_path: Path):
        """有机制源（4 命名组件，total>0）→ 单次判定 mechanism_pass → 准入通过。"""
        result = run_admission(
            raw_path="raw/ag2-captain--raw--2026-08-15.md",
            raw_content=_MECHANISM_SOURCE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        assert result.passed is True, f"有机制源应通过准入: {result.reason}"
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.passed is True
        assert not mech.reason.startswith("确定性机制信号")

    def test_mechanism_llm_fail_rejects_concept(self, tmp_path: Path):
        """有机制形态（total>0）但单次判定 mechanism_pass=false → concept 拒收。"""
        result = run_admission(
            raw_path="raw/ag2-captain--raw--2026-08-15.md",
            raw_content=_MECHANISM_SOURCE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=False),
        )
        assert result.passed is False
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.passed is False
        assert "no_mechanism_signal" in result.reason

    def test_tutorial_not_gated_by_mechanism_signal(self, tmp_path: Path):
        """tutorial 候选不受 mechanism_signal 门（checks=3 无 mech），how 不误拒。"""
        mock = _make_mock(lane=LANE_TUTORIAL, pass_response=True)
        result = run_admission(
            raw_path="raw/how-to-agent--raw--2026-08-15.md",
            raw_content=_TUTORIAL_SOURCE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=mock,
        )
        # tutorial（强 procedural 短路车道）不跑 mechanism_signal
        assert result.content_lane == LANE_TUTORIAL
        assert not any(c.name == "mechanism_signal" for c in result.checks), (
            "tutorial 候选不应跑 mechanism_signal 门"
        )
        assert len(result.checks) == 3, "tutorial 候选 3 条判定（无 mechanism_signal）"
        # how_tendency 在 tutorial 车道是拒收门，但不误拒真 tutorial（mock pass）
        how = next(c for c in result.checks if c.name == "how_tendency")
        assert how.passed is True
        assert result.passed is True, f"真 tutorial 不应被 how_tendency 误拒: {result.reason}"
        assert mock.calls["n"] == 1

    def test_pattern_not_gated_by_mechanism_signal(self, tmp_path: Path):
        """pattern 候选缺机制组件不 abort（mechanism 门仅 concept；p5 铺垫）。"""
        mock = _make_mock(lane=LANE_PATTERN, mechanism_only_fail=True)
        result = run_admission(
            raw_path="raw/agent-patterns--raw--2026-08-15.md",
            raw_content=_PATTERN_SOURCE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=mock,
        )
        assert result.content_lane == LANE_PATTERN
        assert not any(c.name == "mechanism_signal" for c in result.checks), (
            "pattern 候选不应产出 mechanism_signal 门"
        )
        assert result.passed is True, (
            f"pattern 缺机制组件不得 abort: {result.reason}"
        )

    def test_design_not_gated_by_mechanism_signal(self, tmp_path: Path):
        """design 候选缺机制组件不 abort（mechanism 门仅 concept；p5 铺垫）。"""
        result = run_admission(
            raw_path="raw/dashboard-design--raw--2026-08-15.md",
            raw_content=_DESIGN_SOURCE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_DESIGN, mechanism_only_fail=True),
        )
        assert result.content_lane == LANE_DESIGN
        assert not any(c.name == "mechanism_signal" for c in result.checks)
        assert result.passed is True, (
            f"design 缺机制组件不得 abort: {result.reason}"
        )


# ---------------------------------------------------------------------------
# novelty 确定性化（sc-p3-single-llm-call：不打 LLM）
# ---------------------------------------------------------------------------


class TestDeterministicNovelty:
    """novelty 纯确定性：exact 拒 / lexical pass / 无近邻 pass。"""

    def test_select_neighbors_exact_slug_duplicate(self):
        """raw slug == ref slug → duplicate_same_claim（exact 匹配）。"""
        from src.akb2.quality.admission import _select_novelty_neighbors

        raw = "---\nsource_slug: captain\nsource_url: https://x/c\n---\n\n# Captain\ncontent"
        refs = [
            {"slug": "other", "title": "Other", "tldr": "unrelated content about cats", "path": "p1", "source_url": ""},
            {"slug": "captain", "title": "Captain", "tldr": "captain architecture", "path": "p2", "source_url": "https://x/c"},
        ]
        neighbors, match_type = _select_novelty_neighbors(raw, refs)
        assert match_type == "duplicate_same_claim"
        assert len(neighbors) >= 1
        assert neighbors[0]["slug"] == "captain"

    def test_select_neighbors_no_relevant_baseline(self):
        """无相关近邻（token 无重叠）→ no_relevant_baseline。"""
        from src.akb2.quality.admission import _select_novelty_neighbors

        raw = "---\nsource_slug: unique\n---\n\n# Unique Topic\nzzz qqq xxx"
        refs = [
            {"slug": "a", "title": "Cats", "tldr": "all about cats and dogs", "path": "p1", "source_url": ""},
            {"slug": "b", "title": "Cooking", "tldr": "recipes for pasta", "path": "p2", "source_url": ""},
        ]
        neighbors, match_type = _select_novelty_neighbors(raw, refs)
        assert match_type == "no_relevant_baseline"
        assert neighbors == []

    def test_select_neighbors_lexical_unrelated_baseline(self):
        """token 重叠但非 exact slug → unrelated_baseline（top-k 按 overlap 排序）。"""
        from src.akb2.quality.admission import _select_novelty_neighbors

        raw = "---\nsource_slug: new\n---\n\n# Captain Architecture\nThe captain dispatches executors"
        refs = [
            {"slug": "old-captain", "title": "Captain Architecture Old", "tldr": "captain dispatches executors pool", "path": "p1", "source_url": ""},
        ]
        neighbors, match_type = _select_novelty_neighbors(raw, refs)
        assert match_type == "unrelated_baseline"
        assert len(neighbors) >= 1
        assert neighbors[0]["slug"] == "old-captain"

    def test_novelty_no_relevant_baseline_passes_low_confidence(self):
        """无相关近邻 → pass（no_relevant_baseline，低置信），不打 LLM。"""
        from src.akb2.quality.admission import _check_novelty

        raw = "---\nsource_slug: u\n---\n\n# Unique\nzzz qqq"
        refs = [{"slug": "a", "title": "Cats", "tldr": "cats dogs", "path": "p", "source_url": ""}]
        check = _check_novelty(
            raw, refs,
            route_llm_fn=lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("novelty 不应再调 LLM")
            ),
        )
        assert check.passed is True
        assert "no_relevant_baseline" in check.reason

    def test_novelty_exact_duplicate_rejected_without_llm(self):
        """duplicate_same_claim → 确定性拒收（reason 记 compare slug），不打 LLM。"""
        from src.akb2.quality.admission import _check_novelty

        raw = "---\nsource_slug: captain\nsource_url: https://x/c\n---\n\n# Captain\ncontent"
        refs = [
            {"slug": "captain", "title": "Captain", "tldr": "captain architecture",
             "path": "p", "source_url": "https://x/c"},
        ]
        check = _check_novelty(
            raw, refs,
            route_llm_fn=lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("novelty 不应再调 LLM")
            ),
        )
        assert check.passed is False, "exact 重复应确定性拒收"
        assert "duplicate_same_claim" in check.reason
        assert "captain" in check.reason

    def test_novelty_lexical_neighbor_passes_without_llm(self):
        """lexical top-k 近邻 → pass（相关≠重复），reason 记比较对象，不打 LLM。"""
        from src.akb2.quality.admission import _check_novelty

        raw = "---\nsource_slug: new\n---\n\n# Captain Architecture\nThe captain dispatches executors"
        refs = [
            {"slug": "old-captain", "title": "Captain Architecture Old",
             "tldr": "captain dispatches executors pool", "path": "p", "source_url": ""},
        ]
        check = _check_novelty(
            raw, refs,
            route_llm_fn=lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("novelty 不应再调 LLM")
            ),
        )
        assert check.passed is True
        assert "unrelated_baseline" in check.reason
        assert "old-captain" in check.reason

    def test_run_admission_exact_dup_zero_llm_and_rejected(self, tmp_path: Path):
        """端到端：exact 重复拒收 + 0 次 LLM 调用（sc-p3-single-llm-call）。"""
        mock = _make_mock(lane=LANE_CONCEPT, pass_response=True)
        dup_raw = (
            "---\nsource_slug: captain\nsource_url: https://x/c\n---\n\n"
            "# Captain\nsame claim"
        )
        result = run_admission(
            raw_path="raw/dup--raw--2026-08-15.md",
            raw_content=dup_raw,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            wiki_references=[
                {"slug": "captain", "title": "Captain", "tldr": "captain arch",
                 "path": "p", "source_url": "https://x/c"},
            ],
            route_llm_fn=mock,
        )
        assert result.passed is False
        assert "novelty" in result.reason
        assert mock.calls["n"] == 0, "exact 重复路径应为 0 次 LLM 调用"


# ---------------------------------------------------------------------------
# reject 一票拒（P3：D11 删除，直接拒）
# ---------------------------------------------------------------------------


class TestRejectOneVoteNoDowngrade:
    """reject 直接拒收——how PASS / 强机制信号都不再降级（D11 已删）。"""

    _STRONG_MECHANISM_RAW = """---
artifact_type: raw
source_slug: strong-mech
source_url: https://example.com/strong
---

# Strong Mechanism Article

### 1. Captain
The Captain dispatches to the Executor Pool.

### 2. Executor Pool
The Executor Pool feeds the Task Graph.

### 3. Task Graph
The Task Graph writes to the Lifecycle.
"""

    _WEAK_ONLY_RAW = """---
artifact_type: raw
source_slug: weak-opinion
source_url: https://example.com/weak
---

# Opinion Piece

## Introduction
Some opinion prose without mechanism.

## Analysis
More opinion prose.

## Conclusion
Final opinion.
"""

    @staticmethod
    def _mock_reject_how_pass():
        """mock: 单次判定 lane=reject + how/mechanism 全 pass（旧 D11 触发形态）。"""
        def _mock(model_family, messages, response_schema=None, **kwargs):
            return LLMResponse(
                content={"topic_pass": True, "lane": "reject", "non_knowledge": True,
                         "mechanism_pass": True, "how_pass": True, "reason": "mock 非知识"},
                model_used="deepseek-v4-pro",
            )
        return _mock

    def test_strong_mechanism_how_pass_lane_reject_still_rejected(self, tmp_path):
        """强机制 + how PASS + 判 reject → 直接拒（不降级 concept）。"""
        result = run_admission(
            raw_path="raw/strong-mech--raw--2026-08-15.md",
            raw_content=self._STRONG_MECHANISM_RAW,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=self._mock_reject_how_pass(),
        )
        assert result.passed is False, "holds: LANE_REJECT 仍直接拒收"
        assert result.lane_reject_recheck is False, "D11 已删除，无降级标记"
        assert result.content_lane == ""

    def test_weak_only_lane_reject_still_rejected(self, tmp_path):
        """weak-only（普通 H2/H3）+ 判 reject → 仍拒。"""
        result = run_admission(
            raw_path="raw/weak-opinion--raw--2026-08-15.md",
            raw_content=self._WEAK_ONLY_RAW,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=self._mock_reject_how_pass(),
        )
        assert result.lane_reject_recheck is False
        assert result.passed is False
        assert result.content_lane == ""

    def test_news_no_mechanism_lane_reject_still_rejected(self, tmp_path):
        """新闻负样本（无机制信号）+ 判 reject → 仍拒。"""
        news = """---
artifact_type: raw
source_slug: news
source_url: https://example.com/news
---

# Google I/O Recap
Google announced new products. The keynote included updates.
"""
        result = run_admission(
            raw_path="raw/news--raw--2026-08-15.md",
            raw_content=news,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=self._mock_reject_how_pass(),
        )
        assert result.passed is False, "新闻负样本应仍拒"


# ---------------------------------------------------------------------------
# F13-fix3 语义保留：弱信号分级 + 长短正文
# ---------------------------------------------------------------------------


class TestG2C4WeakSignalGrading:
    """普通（非编号）H2/H3 标题 = weak 机制信号；确定性只留强反信号 FAIL。"""

    def test_scan_mechanism_signals_distinguishes_strong_weak(self):
        """编号标题=strong components；普通标题=weak_components。"""
        from src.akb2.quality.admission import _scan_mechanism_signals

        body = (
            "### 1. Captain\nx.\n"  # strong (numbered)
            "### 2. Executor Pool\nx.\n"  # strong
            "## Captain Architecture\nx.\n"  # weak (plain)
            "### Results\nx.\n"  # weak (plain)
        )
        sig = _scan_mechanism_signals(body)
        assert sig["components"] == 2, "编号标题=strong"
        assert sig["weak_components"] == 2, "普通标题=weak"

    def test_plain_heading_mechanism_not_deterministic_fail(self, tmp_path: Path):
        """普通 H2/H3 机制文（无编号标题）→ 不被 no_mechanism_signal deterministic fail。

        total>0（weak_components）→ 由单次判定 mechanism_pass 裁决（mock pass → 过）。
        """
        plain_mechanism = """---
artifact_type: raw
source_slug: plain-mechanism
source_url: https://example.com/plain
---

# Plain Mechanism Article

## Captain
The Captain dispatches sub-tasks to executors.

## Executor Pool
Executors register capability manifests.

## Task Graph
The task graph tracks sub-task dependencies.
"""
        result = run_admission(
            raw_path="raw/plain-mechanism--raw--2026-08-15.md",
            raw_content=plain_mechanism,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert not mech.reason.startswith("no_mechanism_signal"), (
            f"普通 H2/H3 机制文不应 deterministic fail: {mech.reason}"
        )
        assert mech.passed is True

    def test_three_plain_headings_llm_fail_still_fail(self, tmp_path: Path):
        """3 个普通标题（weak）→ 单次判定 mechanism_pass=false → fail。

        防 3 泛技术标题观点/新闻稿混进（LLM 仍是唯一放行人）。
        """
        three_plain = """---
artifact_type: raw
source_slug: opinion
source_url: https://example.com/opinion
---

# Opinion Piece

## Introduction
Some opinion prose.

## Analysis
More opinion prose.

## Conclusion
Final opinion.
"""
        result = run_admission(
            raw_path="raw/opinion--raw--2026-08-15.md",
            raw_content=three_plain,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=False),
        )
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.passed is False, "weak 不触发确定性 pass，LLM fail → fail"
        assert not mech.reason.startswith("确定性机制信号"), (
            f"weak 不应 deterministic pass: {mech.reason}"
        )

    def test_pure_prose_no_headings_still_deterministic_fail(self, tmp_path: Path):
        """纯散文（无任何标题）→ total==0 → no_mechanism_signal deterministic fail。"""
        result = run_admission(
            raw_path="raw/pure-prose--raw--2026-08-15.md",
            raw_content=_NO_MECHANISM_PROSE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.reason.startswith("no_mechanism_signal"), (
            "纯散文（无标题）仍应 deterministic fail"
        )

    def test_f8_long_prose_no_signal_goes_to_llm(self, tmp_path: Path):
        """F8：长正文（≥500 字）无机制信号 → 单次判定裁决（不 deterministic fail）。"""
        long_prose = """---
artifact_type: raw
source_slug: long-agents
source_url: https://example.com/long
---

# Long Running Agents

Long running agents require careful design of the agent loop to handle
sustained operation over hours or days. The core challenge is managing
state persistence across failures, ensuring the agent can recover from
interruptions without losing progress. A well designed system separates
the trigger mechanism from the work execution, allowing the agent to
pick up where it left off. The work selector reads the ledger to find the
next pending item, while the executor processes it and writes results
back. A quality gate verifies the output meets standards before the
ledger records completion. This loop continues until all work is done
or the budget is exhausted. The maker checker separation ensures the
model producing content is not the same as the one judging it, preventing
systematic bias. Error handling must be explicit with failure signatures
recorded for escalation after repeated occurrences.
"""
        result = run_admission(
            raw_path="raw/long-agents--raw--2026-08-15.md",
            raw_content=long_prose,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert not mech.reason.startswith("no_mechanism_signal"), (
            f"长正文（≥500 字）应由单次判定裁决，不 deterministic fail: {mech.reason}"
        )
        assert mech.passed is True

    def test_f8_short_prose_no_signal_still_deterministic_fail(self, tmp_path: Path):
        """F8：短正文（<500 字）无机制信号 → 仍 deterministic fail（不放松负样本）。"""
        result = run_admission(
            raw_path="raw/short-prose--raw--2026-08-15.md",
            raw_content=_NO_MECHANISM_PROSE,  # 短 prose
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.reason.startswith("no_mechanism_signal"), (
            "短 prose 无信号仍应 deterministic fail"
        )


# ---------------------------------------------------------------------------
# 多拒因校准（各拒因独立，不互相掩盖）
# ---------------------------------------------------------------------------


class TestMultiRejectCalibration:
    def test_negative_no_mechanism_rejected_by_mechanism_signal(self, tmp_path: Path):
        """负样本（无机制 concept 散文）→ mechanism_signal 拒（no_mechanism_signal）。"""
        result = run_admission(
            raw_path="raw/openai-safety--raw--2026-08-15.md",
            raw_content=_NO_MECHANISM_PROSE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        assert result.passed is False
        assert "no_mechanism_signal" in result.reason

    def test_negative_topic_rejected_by_topic(self, tmp_path: Path):
        """负样本（领域外）→ topic_relevance 拒（机制过，全车道硬门）。"""
        result = run_admission(
            raw_path="raw/ag2-captain--raw--2026-08-15.md",
            raw_content=_MECHANISM_SOURCE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=False),
        )
        assert result.passed is False
        assert "topic_relevance" in result.reason

    def test_negative_news_rejected_by_non_knowledge(self, tmp_path: Path):
        """负样本（新闻）→ non_knowledge 一票拒（不依赖 how_tendency 造假绿）。"""
        result = run_admission(
            raw_path="raw/news--raw--2026-08-15.md",
            raw_content="# Product Launch\n\nExciting announcement today.",
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_REJECT, pass_response=True),
        )
        assert result.passed is False
        assert "non_knowledge" in result.reason


# ---------------------------------------------------------------------------
# f1-nonknowledge-narrow：发布稿含机制可放行；纯新闻/融资/recap/无机制营销仍拒
# ---------------------------------------------------------------------------


def _capture_unified_system_prompt(body: str = "# Loop\n\nNamed component A interacts with B.") -> str:
    """跑一次 unified 判定并返回 system prompt（不打真 API）。"""
    from src.akb2.quality.admission import _run_unified_admission

    captured: list[str] = []

    def _mock(model_family, messages, response_schema=None, **kwargs):
        captured.append(messages[0]["content"] if messages else "")
        return LLMResponse(
            content={
                "topic_pass": True,
                "lane": LANE_CONCEPT,
                "non_knowledge": False,
                "mechanism_pass": True,
                "how_pass": True,
                "reason": "mock capture",
            },
            model_used="deepseek-v4-pro",
        )

    _run_unified_admission(body, _mock)
    assert captured, "unified 判定应发出一次 LLM 调用"
    return captured[0]


class TestNonKnowledgeNarrowPrompt:
    """锁 unified system prompt：收窄 non_knowledge，且不破坏 conftest/f13 mock 识别词。"""

    def test_unified_prompt_keeps_conftest_mock_markers_for_non_knowledge(self):
        """改 prompt 后仍含 conftest lane 识别词「主要意图」与 f13 识别词「机制」。"""
        sp = _capture_unified_system_prompt()
        assert "主要意图" in sp, "conftest make_mock_route_llm 靠「主要意图」走 lane 分支"
        assert "机制" in sp, "test_f13_lead_fixes mock 靠「机制」走 mechanism 分支"

    def test_unified_prompt_release_with_mechanism_is_not_non_knowledge(self):
        """产品/模型发布稿若含 ≥3 可命名机制组件或可执行配置/代码 → concept/tutorial 放行。"""
        sp = _capture_unified_system_prompt()
        assert "产品/模型发布稿" in sp
        assert "≥3" in sp
        assert "可命名机制组件" in sp
        assert "可执行配置" in sp
        assert "concept" in sp and "tutorial" in sp
        # 旧口径把「产品发布公告」一律 reject / how_pass 不算；收窄后不得再无例外一票拒。
        assert "产品发布公告" not in sp

    def test_unified_prompt_pure_news_funding_recap_marketing_still_non_knowledge(self):
        """纯新闻/融资/会议 recap/无机制营销仍一票拒。"""
        sp = _capture_unified_system_prompt()
        assert "纯新闻" in sp
        assert "融资" in sp
        assert "会议 recap" in sp
        assert "无机制营销" in sp
        assert "true 仅当是纯新闻/融资/会议 recap/无机制营销/纯观点" in sp

    def test_conftest_lane_mock_still_rejects_pure_news_as_non_knowledge(
        self, tmp_path: Path
    ):
        """conftest mock 仍因「主要意图」命中 lane 分支，纯新闻走 reject 一票拒。"""
        from tests.quality.conftest import make_mock_route_llm

        mock = make_mock_route_llm(
            {
                "lane": LLMResponse(
                    content={
                        "content_lane": "reject",
                        "reason": "mock: 纯新闻播报，非知识",
                    },
                    model_used="gemini-2.5-flash",
                    calls_used=1,
                ),
            }
        )
        result = run_admission(
            raw_path="raw/google-io-recap--raw--2026-08-20.md",
            raw_content="# Conference Recap\n\nFunding news and a product launch recap.",
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=mock,
        )
        assert result.passed is False
        assert "non_knowledge" in result.reason
