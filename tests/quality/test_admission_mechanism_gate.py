"""test_admission_mechanism_gate.py — WP-2 admission 机制信号准入门测试 (SC-2)。

覆盖:
    - 无机制散文（openai 政策/新闻/纯观点）fail 于 mechanism_signal (no_mechanism_signal)
    - 有机制源（≥3 命名组件）过 mechanism_signal
    - procedural 候选不受 mechanism_signal 门（F4 H1 判），how_tendency 不误拒
    - novelty 不误拒真新内容（有 wiki_references 时 LLM 判 pass）
    - LLM 不得绕过确定性强反信号（无信号散文即便 mock pass 仍 fail）
    - 信号识别不可用 → fail-closed 拒收

mock 边界：route_llm_fn（lane classify + topic/how/novelty/mechanism LLM 仲裁），
不发真网络。断言绑可观测行为（mechanism_signal check passed / error_class / checks 数）。
"""

from __future__ import annotations

from pathlib import Path

from src.akb2.quality.admission import LANE_CONCEPT, LANE_PROCEDURAL, run_admission
from src.akb2.quality.llm_router import LLMResponse, RouterConfigError

# ---------------------------------------------------------------------------
# mock route_llm
# ---------------------------------------------------------------------------


def _make_mock(lane: str = LANE_CONCEPT, pass_response: bool = True):
    """mock route_llm: lane classify 返回 `lane`；其余判定返回 pass=pass_response。

    mechanism_signal 的确定性短路（强信号 PASS / 强反信号 FAIL）不调 LLM，
    仅模糊态走 LLM 仲裁分支。
    """

    def _mock(model_family, messages, response_schema=None, **kwargs):
        sys_prompt = messages[0]["content"] if messages else ""
        # lane classify prompt 含「主要意图 / 概念型 / 操作型」
        if "主要意图" in sys_prompt or "概念型" in sys_prompt or "操作型" in sys_prompt:
            return LLMResponse(
                content={"content_lane": lane, "reason": f"mock {lane} 车道"},
                model_used="deepseek-v4-pro",
            )
        return LLMResponse(
            content={"pass": pass_response, "reason": "mock pass" if pass_response else "mock fail"},
            model_used="deepseek-v4-pro",
        )

    return _mock


def _make_mock_raise_on_mechanism():
    """mock route_llm: mechanism_signal 仲裁分支抛 RouterConfigError（测 fail-closed）。

    mechanism_signal LLM prompt 含「≥3 命名组件 + 因果关系」。
    """

    def _mock(model_family, messages, response_schema=None, **kwargs):
        sys_prompt = messages[0]["content"] if messages else ""
        if "主要意图" in sys_prompt or "概念型" in sys_prompt:
            return LLMResponse(
                content={"content_lane": LANE_CONCEPT, "reason": "mock concept"},
                model_used="deepseek-v4-pro",
            )
        if "命名组件" in sys_prompt and "因果关系" in sys_prompt:
            raise RouterConfigError("mock mechanism LLM 不可用")
        return LLMResponse(
            content={"pass": True, "reason": "mock pass"},
            model_used="deepseek-v4-pro",
        )

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

# 有机制源（ag2 captain 风格）：4 命名 H3 组件 → 确定性 PASS
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

# procedural 源（how-to + shell 命令）：强 procedural 信号短路，不走 mechanism_signal
_PROCEDURAL_SOURCE = """---
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

# 模糊态：1 命名组件，无因果/编号/code → 不达强信号，total>0 → 走 LLM 仲裁
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
# SC-2 测试
# ---------------------------------------------------------------------------


class TestMechanismSignalGate:
    """concept 候选机制信号准入门（WP-2/D2）。"""

    def test_no_mechanism_prose_fails_at_mechanism_signal(self, tmp_path: Path):
        """无机制散文 → mechanism_signal fail (no_mechanism_signal)，不下传 distill。"""
        result = run_admission(
            raw_path="raw/openai-safety--raw--2026-06-29.md",
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
        """确定性强反信号（无机制）即便 LLM mock 一律 pass 也仍 fail（不绕过）。"""
        # mock 一律 pass（含 mechanism LLM 仲裁分支），但确定性 total==0 先 fail，不调 LLM
        result = run_admission(
            raw_path="raw/openai-safety--raw--2026-06-29.md",
            raw_content=_NO_MECHANISM_PROSE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        assert result.passed is False
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.passed is False
        assert mech.reason.startswith("no_mechanism_signal")

    def test_mechanism_source_passes(self, tmp_path: Path):
        """有机制源（4 命名组件）→ mechanism_signal 确定性 pass，准入通过。"""
        result = run_admission(
            raw_path="raw/ag2-captain--raw--2026-06-29.md",
            raw_content=_MECHANISM_SOURCE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        assert result.passed is True, f"有机制源应通过准入: {result.reason}"
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.passed is True
        assert "确定性机制信号" in mech.reason

    def test_procedural_not_gated_by_mechanism_signal(self, tmp_path: Path):
        """procedural 候选不受 mechanism_signal 门（checks=3 无 mech），how_tendency 不误拒。"""
        result = run_admission(
            raw_path="raw/how-to-agent--raw--2026-06-29.md",
            raw_content=_PROCEDURAL_SOURCE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_PROCEDURAL, pass_response=True),
        )
        # procedural 不跑 mechanism_signal（强 procedural 信号短路车道）
        assert result.content_lane == LANE_PROCEDURAL
        assert not any(c.name == "mechanism_signal" for c in result.checks), (
            "procedural 候选不应跑 mechanism_signal 门"
        )
        assert len(result.checks) == 3, "procedural 候选 3 条判定（无 mechanism_signal）"
        # how_tendency 在 procedural 车道是拒收门，但不误拒真 procedural（mock pass）
        how = next(c for c in result.checks if c.name == "how_tendency")
        assert how.passed is True
        assert result.passed is True, f"真 procedural 不应被 how_tendency 误拒: {result.reason}"

    def test_novelty_not_false_reject_true_new_content(self, tmp_path: Path):
        """novelty 不误拒真新内容（有 wiki_references 时 LLM 判 pass）。"""
        result = run_admission(
            raw_path="raw/ag2-captain--raw--2026-06-29.md",
            raw_content=_MECHANISM_SOURCE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            wiki_references=["既有 wiki 摘要：旧的 agent loop 五组件介绍"],
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        assert result.passed is True, f"真新内容不应被 novelty 误拒: {result.reason}"
        novelty = next(c for c in result.checks if c.name == "novelty")
        assert novelty.passed is True

    def test_mechanism_signal_llm_unavailable_fail_closed(self, tmp_path: Path):
        """模糊态（部分信号）走 LLM 仲裁；LLM 不可用 → fail-closed 拒收。"""
        result = run_admission(
            raw_path="raw/vague-concept--raw--2026-06-29.md",
            raw_content=_AMBIGUOUS_MECHANISM,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock_raise_on_mechanism(),
        )
        assert result.passed is False, "LLM 不可用时模糊态应 fail-closed"
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.passed is False
        assert "no_mechanism_signal" in mech.reason
        assert "fail-closed" in mech.reason


# ---------------------------------------------------------------------------
# 多拒因校准（WP-2.2）—— 各拒因独立 fixture，不互相掩盖
# ---------------------------------------------------------------------------


class TestMultiRejectCalibration:
    """多拒因各列 baseline + 目标（novelty/topic/non_knowledge/procedural how_tendency）。"""

    def test_negative_no_mechanism_rejected_by_mechanism_signal(self, tmp_path: Path):
        """负样本（无机制 concept 散文）→ mechanism_signal 拒（no_mechanism_signal）。"""
        result = run_admission(
            raw_path="raw/openai-safety--raw--2026-06-29.md",
            raw_content=_NO_MECHANISM_PROSE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        assert result.passed is False
        assert "no_mechanism_signal" in result.reason

    def test_negative_plagiarism_rejected_by_novelty(self, tmp_path: Path):
        """负样本（洗稿）→ novelty 拒（mechanism_signal 过，因有机制内容）。"""
        # 用有机制内容 + wiki_refs + mock novelty fail → novelty 拒（mech 过）
        result = run_admission(
            raw_path="raw/ag2-captain--raw--2026-06-29.md",
            raw_content=_MECHANISM_SOURCE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            wiki_references=["既有 wiki：Captain Agent 完全相同架构介绍"],
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=False),
        )
        assert result.passed is False
        # mech 过（确定性），novelty 拒
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.passed is True
        novelty = next(c for c in result.checks if c.name == "novelty")
        assert novelty.passed is False
        assert "novelty" in result.reason


# ---------------------------------------------------------------------------
# §0.2/G2/C4：普通 H2/H3 weak 信号分级（不触发 deterministic pass，只救误拒）
# ---------------------------------------------------------------------------


class TestG2C4WeakSignalGrading:
    """§0.2/G2/C4：普通（非编号）H2/H3 标题 = weak 机制信号。

    - weak 不触发 deterministic pass（C4：3 泛技术标题观点稿不绕过 LLM 仲裁）
    - weak 计入 total，避免普通 H2/H3 机制文被 no_mechanism_signal 误拒（idx0/idx8）
    """

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

        idx0/idx8 修复：普通 H2/H3 机制文 → total>0（weak_components）→ 走 LLM 窄仲裁。
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
            raw_path="raw/plain-mechanism--raw--2026-06-29.md",
            raw_content=plain_mechanism,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        # 不被 no_mechanism_signal deterministic fail（走 LLM 仲裁，mock pass → pass）
        assert not mech.reason.startswith("no_mechanism_signal"), (
            f"普通 H2/H3 机制文不应 deterministic fail: {mech.reason}"
        )

    def test_three_plain_headings_not_deterministic_pass(self, tmp_path: Path):
        """C4：3 个普通标题（weak）不触发 deterministic pass → 走 LLM 仲裁。

        防 3 泛技术标题观点/新闻稿绕过 LLM 仲裁混进。mock LLM fail → fail。
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
        # mock LLM fail（观点稿应被 LLM 仲裁拒）
        result = run_admission(
            raw_path="raw/opinion--raw--2026-06-29.md",
            raw_content=three_plain,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=False),
        )
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        # weak 不触发 deterministic pass → 走 LLM → mock fail → fail
        assert mech.passed is False, "3 普通标题（weak）不应 deterministic pass"
        assert not mech.reason.startswith("确定性机制信号"), (
            f"weak 不应 deterministic pass: {mech.reason}"
        )

    def test_pure_prose_no_headings_still_deterministic_fail(self, tmp_path: Path):
        """纯散文（无任何标题）→ total==0 → no_mechanism_signal deterministic fail（不变）。"""
        result = run_admission(
            raw_path="raw/pure-prose--raw--2026-06-29.md",
            raw_content=_NO_MECHANISM_PROSE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            route_llm_fn=_make_mock(lane=LANE_CONCEPT, pass_response=True),
        )
        mech = next(c for c in result.checks if c.name == "mechanism_signal")
        assert mech.reason.startswith("no_mechanism_signal"), (
            "纯散文（无标题）仍应 deterministic fail"
        )
