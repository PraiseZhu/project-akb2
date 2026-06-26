"""test_concept_judgment_e2e.py — step-3 SC-3g E2E + 对抗 + 零回归验收。

注入式 fixture LLM 跑 concept 判定 judgment chain（integrity_floor → grader entailment
→ weighted_score → runner early-return），不依赖真网关/抓取（spec Assumptions）。

  - concept_positive：正样本（mechanism≥3+causal边、全溯源、entailment=true、高分）→ pass。
  - negatives：机制糊/无据读后感/缺锚/无未覆盖列/无causal_edges/poisoned-trace → 各被对应判定拦。
  - adversarial：F4 fail + 辅助维满分 → 地板 early-return 拒、加权分不算（不可补偿）。

测试函数命名含 concept_positive/negatives/adversarial 子串（锚定 master -k 选择器）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.akb2.quality.grader import (
    RUBRIC_DIMENSIONS_BY_LANE,
    run_grader_with_fixture_detailed,
)
from src.akb2.quality.integrity_floor import evaluate_integrity_floor
from src.akb2.quality.weighted_score import compute_weighted_score


# ----------------------------- helpers ----------------------------- #


def _grounded(text: str, anchor: str = "§A", gap: str = "未提供 具体数据") -> dict:
    return {"text": text, "anchor": anchor, "provenance": "source_grounded", "evidence_gap": gap}


def _interp(text: str, traces_to: list[str], gap: str = "未覆盖 反压场景") -> dict:
    return {"text": text, "anchor": "§A", "provenance": "akb_interpretation",
            "traces_to": traces_to, "evidence_gap": gap}


def _concept_positive_extract() -> dict:
    """concept 正样本：3 命名组件 + 2 因果边 + 全溯源 + 全证据盲区。"""
    return {
        "claims": [
            _grounded("组件A 做输入", "§A"),
            _grounded("组件B 做处理", "§B"),
            _interp("A→B 是因果链路", ["§A", "§B"]),
        ],
        "argumentation": [],
        "mechanism_components": [
            {"name": "组件A", "role": "input"},
            {"name": "组件B", "role": "process"},
            {"name": "组件C", "role": "output"},
        ],
        "causal_edges": [
            {"from": "组件A", "to": "组件B", "relation": "feeds"},
            {"from": "组件B", "to": "组件C", "relation": "produces"},
        ],
        "how_steps": [],
    }


def _make_response(content: dict, model_used: str = "gpt-5.4"):
    resp = MagicMock()
    resp.content = content
    resp.model_used = model_used
    return resp


def _mock_route(primary_content: dict):
    def _route(model_family="", messages=None, response_schema=None, **kw):
        return _make_response(primary_content, "gpt-5.4")
    return _route


def _concept_grader_response(
    all_pass: bool = True,
    entailment: bool = True,
    score: float = 1.0,
) -> dict:
    """concept 7 维 grader 响应（全 pass + entailment + 逐维 score）。"""
    dims = RUBRIC_DIMENSIONS_BY_LANE["concept"]
    return {
        "dimensions": [
            {"dimension": d, "pass": all_pass, "defects": [], "score": score}
            for d in dims
        ],
        "interpretation_entailment": entailment,
    }


def _run_judgment(extract: dict, grader_content: dict) -> tuple[bool, str]:
    """跑 concept judgment chain：floor → grader → weighted_score。

    Returns (overall_pass, reject_reason)。reject_reason 标明在哪一层拒（floor/grader/score）。
    """
    # 1. 完整性地板（任一成员 fail → 拒，加权分不算）
    floor = evaluate_integrity_floor(extract, "concept")
    if not floor.passed:
        return False, f"floor:{floor.failed_floors}"

    # 2. grader（defects 非空 或 entailment=false → 拒）
    grader = run_grader_with_fixture_detailed(
        "candidate", _mock_route(grader_content), content_lane="concept"
    )
    if grader.defects:
        return False, f"grader:defects({len(grader.defects)})"
    if grader.interpretation_entailment is False:
        return False, "grader:poisoned-trace(entailment=false)"

    # 3. 加权分（<6.5 → 拒）
    score = compute_weighted_score(grader.dimension_scores, "concept")
    if not score.passed:
        return False, f"score:{score.score}<{score.threshold}"

    return True, "pass"


# ----------------------------- concept_positive ----------------------------- #


class TestConceptPositive:
    def test_concept_positive_passes(self):
        """concept 正样本：floor 过 + grader 全 pass + entailment=true + 高分 → pass。"""
        extract = _concept_positive_extract()
        grader = _concept_grader_response(all_pass=True, entailment=True, score=1.0)
        passed, reason = _run_judgment(extract, grader)
        assert passed, f"正样本应 pass，实际拒于 {reason}"


# ----------------------------- negatives ----------------------------- #


class TestNegatives:
    def test_negatives_mechanism_mush_rejected(self):
        """机制糊清单：3 组件但 0 causal_edges → F4 地板拒。"""
        extract = _concept_positive_extract()
        extract["causal_edges"] = []
        passed, reason = _run_judgment(extract, _concept_grader_response())
        assert not passed
        assert "floor" in reason
        assert "core_value_f4" in reason

    def test_negatives_ungrounded_reflection_rejected(self):
        """无据读后感：akb_interpretation 缺 traces_to → F3 层① 地板拒。"""
        extract = _concept_positive_extract()
        extract["claims"][2]["traces_to"] = []
        passed, reason = _run_judgment(extract, _concept_grader_response())
        assert not passed
        assert "interpretation_traceability" in reason

    def test_negatives_missing_anchor_rejected(self):
        """缺锚：source_grounded claim 无 anchor → claim_anchor_rate 地板拒。"""
        extract = _concept_positive_extract()
        extract["claims"][0]["anchor"] = ""
        passed, reason = _run_judgment(extract, _concept_grader_response())
        assert not passed
        assert "claim_anchor_rate" in reason

    def test_negatives_no_uncovered_col_rejected(self):
        """无未覆盖列：claim evidence_gap 无盲区词 → evidence_honesty 地板拒。"""
        extract = _concept_positive_extract()
        for c in extract["claims"]:
            c["evidence_gap"] = "原文如此"  # 无盲区词
        passed, reason = _run_judgment(extract, _concept_grader_response())
        assert not passed
        assert "evidence_honesty" in reason

    def test_negatives_no_causal_edges_rejected(self):
        """无 causal_edges：F4 结构字段缺 → 地板拒。"""
        extract = _concept_positive_extract()
        extract["causal_edges"] = []
        extract["mechanism_components"] = extract["mechanism_components"][:3]  # 3 组件但无边
        passed, reason = _run_judgment(extract, _concept_grader_response())
        assert not passed
        assert "core_value_f4" in reason

    def test_negatives_poisoned_trace_rejected(self):
        """poisoned-trace：floor 过但 grader entailment=false → grader 拒（层② 异族硬判）。"""
        extract = _concept_positive_extract()  # floor 全过
        grader = _concept_grader_response(all_pass=True, entailment=False)
        passed, reason = _run_judgment(extract, grader)
        assert not passed
        assert "poisoned-trace" in reason


# ----------------------------- adversarial ----------------------------- #


class TestAdversarial:
    def test_adversarial_floor_fail_with_auxiliary_max_rejected(self):
        """对抗样本：F4 fail + 辅助维满分 → 地板 early-return 拒、加权分不算（不可补偿）。

        F4 fail（mechanism<3）时 floor.passed=False，judgment chain 在 floor 层就拒，
        不进 grader/weighted_score——辅助维满分顶不过地板（宪法 8(a) 不可补偿）。
        """
        extract = _concept_positive_extract()
        extract["mechanism_components"] = []  # F4 fail
        # grader 给辅助维满分 + entailment=true（试图靠辅助分过）
        grader = _concept_grader_response(all_pass=True, entailment=True, score=1.0)
        passed, reason = _run_judgment(extract, grader)
        assert not passed
        assert "floor" in reason
        assert "core_value_f4" in reason
        # 不可补偿：地板 fail 时根本没到 score 层
        assert "score" not in reason

    def test_adversarial_low_h3_depth_below_threshold(self):
        """对抗：H3 深度低（score=0.3）+ 其它维满分 → 总分边界但 H3 独立判定。

        H3 weight=3.5, score=0.3 → 贡献 1.05；其它 6 维满分(1.0)权重和=6.5 → 总分 7.55 ≥6.5。
        但 H3 深度 0.3 <1.0（最小合规）→ 实际由 grader F4/地板 H3 维拒。
        本测试验加权分计算：H3 低分仍可能总分过阈，需地板/grader 独立把关（H3 独立于地板）。
        """
        from src.akb2.quality.weighted_score import _lane_weight_map
        from src.akb2.quality.grader import _load_rubric_spec

        weights = _lane_weight_map(_load_rubric_spec(), "concept")
        # H3 score=0.3, 其它满分
        scores = {name: (0.3 if "H3" in name else 1.0) for name in weights}
        r = compute_weighted_score(scores, "concept")
        h3_w = weights["概念机制清晰度(H3)"]
        expected = 0.3 * h3_w + (10.0 - h3_w)  # H3低分 + 其它满分
        assert abs(r.score - expected) < 1e-6
        # 总分可能过阈（H3 低分被辅助分补偿到 ≥6.5）——这正是为何 H3 深度要独立于加权分
        # 由 grader B 层 H3 维度 pass/fail + 地板 F4 把关，不靠加权分
