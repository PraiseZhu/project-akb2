"""test_f12a_backfill_strong_coverage.py — F12-A poisoned trace 溯源+修测试。

probe-p9b v2 poisoned_trace_entailment ×2(idx0 + idx1 ag2):F3 层② 异族 grader 判
解读未从 cited anchor 推出。溯源主因:F5-B 确定性 backfill 旧阈值"≥2 长 token 重合"
过弱——token 重合≠语义蕴含(interp 是 paraphrase 时 raw 句不 entail 它)→ grader 判
poisoned。修:backfill 接受阈值提到 B1 强匹配级(0.60 verbatim 覆盖),弱重合→返 None
→走 LLM retake / F6-B 丢弃(fail-closed:弱 trace 变少,要么真 trace 要么丢弃,不绕门)。
"""

from __future__ import annotations

from src.akb2.quality.distill import _deterministic_backfill_grounded_claim


class TestF12ABackfillStrongCoverage:
    def test_weak_token_overlap_no_backfill(self):
        """弱重合(token 命中但 <60% verbatim)→ 返 None(走丢弃,不造弱 trace)。

        旧版:≥2 长 token 重合即接受 → 造弱 trace → grader 判 poisoned。
        F12-A:覆盖率 <0.60 → 返 None。
        """
        # interp 是 paraphrase(主题词重合但非 verbatim)
        interp = "The Captain coordinates the Executor Pool for task scheduling."
        block = (
            "The Captain agent holds the system-level goal and maintains a task graph. "
            "The Executor Pool dispatches work to available agents."
        )
        anchor_blocks = {"§E_CLAIMS": block}
        result = _deterministic_backfill_grounded_claim(interp, "§E_CLAIMS", anchor_blocks, block)
        # 主题词(captain/executor/pool/task)重合但非 verbatim → <60% → None
        assert result is None, (
            f"弱重合不应 backfill(造 poisoned trace): {result}"
        )

    def test_strong_verbatim_backfill_picked(self):
        """强 verbatim(≥60% 覆盖)→ backfill 摘句(真 trace,grader 判 entailed)。"""
        interp = "The model that produces content must be different from the model that judges it."
        block = (
            "Some intro. "
            "The model that produces content must be different from the model that judges it. "
            "Other text."
        )
        anchor_blocks = {"§E_CLAIMS": block}
        result = _deterministic_backfill_grounded_claim(interp, "§E_CLAIMS", anchor_blocks, block)
        assert result is not None
        assert "The model that produces content" in result["text"]
        assert result["anchor"] == "§E_CLAIMS"

    def test_partial_verbatim_below_threshold_no_backfill(self):
        """部分 verbatim(<60%)→ None(如 interp 扩展了 raw 句内容)。"""
        raw_sent = "The Captain agent holds the system-level goal."
        interp = "The Captain agent holds the system-level goal and dispatches tasks to executors."
        block = f"Intro. {raw_sent} Other."
        anchor_blocks = {"§A": block}
        # interp 比 raw 句长很多(加了 dispatches...)→ 覆盖率 <60% → None
        result = _deterministic_backfill_grounded_claim(interp, "§A", anchor_blocks, block)
        assert result is None, f"部分 verbatim(<60%)不应 backfill: {result}"

    def test_no_overlap_no_backfill(self):
        """无重合 → None(既有行为不变)。"""
        interp = "Completely unrelated interpretation xyz unique."
        block = "The system runs tasks with agents and tools."
        anchor_blocks = {"§A": block}
        assert _deterministic_backfill_grounded_claim(interp, "§A", anchor_blocks, block) is None

    def test_empty_target_searches_all_blocks_strong(self):
        """target_anchor 空 → 在所有 block 找最高覆盖(强 verbatim 才接受)。"""
        interp = "The model that produces content must be different from the model that judges it."
        anchor_blocks = {
            "§A": "unrelated content here.",
            "§E_CLAIMS": "The model that produces content must be different from the model that judges it.",
        }
        result = _deterministic_backfill_grounded_claim(interp, "", anchor_blocks, interp)
        assert result is not None
        assert result["anchor"] == "§E_CLAIMS"
