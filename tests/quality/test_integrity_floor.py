"""test_integrity_floor.py — step-3 SC-3b/SC-3h 完整性地板四成员验收。

覆盖：
  - SC-3b: F4 地板二值硬卡 + 不可补偿（concept: mechanism≥3 + 每组件有 causal 边；
           procedural: H1≥1）。对抗样本（F4 fail + 辅助满分）→ 整体 reject。
  - SC-3h: claim_anchor_rate（每条 source_grounded claim 带真实 anchor，<100% fail）
           + evidence_honesty（证据地图含未覆盖列且 ≥1 行标真实盲区）。
  - runner early-return：任一地板成员 fail → grader NOT called + weighted_score_not_computed
           + floor_failed 日志（宪法原则 8(a) 不可补偿）。

设计：直接构造 extract_data fixture 喂 evaluate_integrity_floor，四成员各自 pass/fail；
runner 级用 mock 验 floor fail 时 grader 不被调。
"""

from __future__ import annotations

from unittest.mock import patch

from src.akb2.quality.integrity_floor import (
    CONCEPT_MIN_MECHANISM_COMPONENTS,
    FLOOR_CLAIM_ANCHOR_RATE,
    FLOOR_CORE_VALUE_F4,
    FLOOR_EVIDENCE_HONESTY,
    FLOOR_INTERPRETATION_TRACEABILITY,
    FloorResult,
    evaluate_integrity_floor,
)

# ----------------------------- fixtures ----------------------------- #


def _grounded_claim(text: str, anchor: str = "§A", gap: str = "未提供 X") -> dict:
    return {
        "provenance": "source_grounded",
        "claim": text,
        "anchor": anchor,
        "evidence_gap": gap,
    }


def _interpretation(text: str, traces_to: list[str]) -> dict:
    return {
        "provenance": "akb_interpretation",
        "claim": text,
        "traces_to": traces_to,
        "evidence_gap": "未覆盖 Y",
    }


def _concept_pass_extract() -> dict:
    """全四成员 pass 的 concept extract。"""
    return {
        "claims": [
            _grounded_claim("组件 A 做事", anchor="§A"),
            _grounded_claim("组件 B 做事", anchor="§B"),
            _interpretation("A→B 是因果", traces_to=["§A", "§B"]),
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


# ----------------------------- ① claim_anchor_rate ----------------------------- #


class TestClaimAnchorRate:
    def test_pass_all_grounded_have_anchor(self):
        r = evaluate_integrity_floor(_concept_pass_extract(), "concept")
        assert FLOOR_CLAIM_ANCHOR_RATE not in r.failed_floors

    def test_fail_missing_anchor(self):
        d = _concept_pass_extract()
        d["claims"][0]["anchor"] = ""  # 一条 grounded claim 缺 anchor
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_CLAIM_ANCHOR_RATE in r.failed_floors
        assert not r.passed

    def test_fail_no_grounded_claims(self):
        d = _concept_pass_extract()
        for c in d["claims"]:
            c["provenance"] = "akb_interpretation"
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_CLAIM_ANCHOR_RATE in r.failed_floors


# ----------------------------- ② evidence_honesty ----------------------------- #


class TestEvidenceHonesty:
    def test_pass_has_real_blind_spot(self):
        r = evaluate_integrity_floor(_concept_pass_extract(), "concept")
        assert FLOOR_EVIDENCE_HONESTY not in r.failed_floors

    def test_fail_no_gap(self):
        d = _concept_pass_extract()
        d["claims"][0]["evidence_gap"] = ""
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_EVIDENCE_HONESTY in r.failed_floors
        assert not r.passed

    def test_fail_gap_without_real_blind_spot_word(self):
        """裸来源断言（gap 文本无盲区词）→ 证据不诚实 fail。"""
        d = _concept_pass_extract()
        for c in d["claims"]:
            c["evidence_gap"] = "原文如此"
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_EVIDENCE_HONESTY in r.failed_floors


# ----------------------------- ③ interpretation_traceability (层①) ----------------------------- #


class TestInterpretationTraceability:
    def test_pass_traces_to_real_anchor(self):
        r = evaluate_integrity_floor(_concept_pass_extract(), "concept")
        assert FLOOR_INTERPRETATION_TRACEABILITY not in r.failed_floors

    def test_fail_no_traces_to(self):
        """无据飘浮读后感（akb_interpretation 缺 traces_to）→ fail。"""
        d = _concept_pass_extract()
        d["claims"][2]["traces_to"] = []
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_INTERPRETATION_TRACEABILITY in r.failed_floors
        assert not r.passed

    def test_fail_traces_to_nonexistent_anchor(self):
        """poisoned-trace 的层① 形态：traces_to 指向不存在的 anchor（anchor 真实但不在 claims）。
        注：层② entailment（anchor 真实但与 interpretation 语义无关）由 grader 异族硬判，
        本层① 只判「指向真实存在的 anchor」。这里 anchor 不存在于 grounded_anchors → 层① fail。"""
        d = _concept_pass_extract()
        d["claims"][2]["traces_to"] = ["§Z"]  # §Z 不在 grounded anchors {§A, §B}
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_INTERPRETATION_TRACEABILITY in r.failed_floors

    def test_argumentation_interpretation_also_checked(self):
        """argumentation 里的 akb_interpretation 也要 traces_to。"""
        d = _concept_pass_extract()
        d["argumentation"] = [
            {"provenance": "akb_interpretation", "claim": "飘浮解读", "traces_to": []}
        ]
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_INTERPRETATION_TRACEABILITY in r.failed_floors

    def test_pass_empty_grounded_skips_traceability(self):
        """SC-5：grounded_anchors 空时 traceability 豁免（vacuously unsatisfiable）。

        无源页 fixture（vercel-ai-sdk-nosource）无 §-anchor 结构，extraction 产不出
        grounded anchor；"≥1 traces_to 命中真实 grounded anchor"不可满足，floor ③ 不应
        误判 fail。缺 grounded 的真问题由 floor ① claim_anchor_rate 抓（不在此断言）。
        满仓观测：该 fixture 挂 `traces_to ['§J_DISCUSSION'] 未指向任何真实 source`。
        """
        d = _concept_pass_extract()
        # 全部 claim 改 akb_interpretation（无 grounded）+ 都给 traces_to
        for c in d["claims"]:
            c["provenance"] = "akb_interpretation"
            if "traces_to" not in c:
                c["traces_to"] = ["§A"]
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_INTERPRETATION_TRACEABILITY not in r.failed_floors, (
            "grounded_anchors 空时 traceability 应豁免（SC-5）"
        )


# ----------------------------- ④ F4 core_value ----------------------------- #


class TestCoreValueF4:
    def test_concept_pass_3_components_with_edges(self):
        r = evaluate_integrity_floor(_concept_pass_extract(), "concept")
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors

    def test_concept_fail_too_few_components(self):
        d = _concept_pass_extract()
        d["mechanism_components"] = d["mechanism_components"][:2]  # 只 2 个
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors
        assert not r.passed

    def test_concept_fail_no_causal_edges(self):
        """纯名词清单（3 组件但 0 causal_edges）→ F4 fail（机制未讲清）。"""
        d = _concept_pass_extract()
        d["causal_edges"] = []
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors

    def test_concept_fail_isolated_component(self):
        """某组件无出/入边（孤立）→ F4 fail。"""
        d = _concept_pass_extract()
        d["causal_edges"] = [
            {"from": "组件A", "to": "组件B", "relation": "x"}
        ]  # 组件C 无边
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors

    def test_concept_fail_duplicate_component_names(self):
        """codex worker：重复名 [A,A,B] 不能伪装 3 组件过 F4（去重后只 2 唯一）。"""
        d = _concept_pass_extract()
        d["mechanism_components"] = [
            {"name": "组件A", "role": "x"},
            {"name": "组件A", "role": "y"},  # 重复名
            {"name": "组件B", "role": "z"},
        ]
        d["causal_edges"] = [{"from": "组件A", "to": "组件B", "relation": "r"}]
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors
        assert not r.passed

    def test_procedural_pass_h1_ge_1(self):
        d = {
            "claims": [_grounded_claim("cmd")],
            "how_steps": [{"instruction": "run x", "how_level": "H1"}],
        }
        r = evaluate_integrity_floor(d, "procedural")
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors

    def test_procedural_fail_h1_zero(self):
        d = {
            "claims": [_grounded_claim("cmd")],
            "how_steps": [{"instruction": "note", "how_level": "H3"}],
        }
        r = evaluate_integrity_floor(d, "procedural")
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors


# ----------------------------- 整体 evaluate ----------------------------- #


class TestEvaluateIntegrityFloor:
    def test_all_pass(self):
        r = evaluate_integrity_floor(_concept_pass_extract(), "concept")
        assert r.passed is True
        assert r.failed_floors == []
        assert r.reason == ""
        assert r.content_lane == "concept"

    def test_one_fail_makes_not_passed(self):
        d = _concept_pass_extract()
        d["mechanism_components"] = []  # 触发 F4 fail
        r = evaluate_integrity_floor(d, "concept")
        assert r.passed is False
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors
        assert CONCEPT_MIN_MECHANISM_COMPONENTS == 3

    def test_adversarial_floor_fail_not_compensable(self):
        """对抗样本：F4 fail + 其它成员/辅助维都「满分」→ 仍 fail（不可补偿）。
        地板二值，不被其它成员 pass 或辅助分顶过。"""
        d = _concept_pass_extract()
        d["mechanism_components"] = []  # F4 fail
        # 其它三成员都 pass（claims/argumentation 完整）
        r = evaluate_integrity_floor(d, "concept")
        assert r.passed is False
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors
        # 不可补偿：即使 claim_anchor_rate/evidence_honesty/traceability 都 pass，
        # F4 fail 仍使整体 passed=False
        assert FLOOR_CLAIM_ANCHOR_RATE not in r.failed_floors
        assert FLOOR_EVIDENCE_HONESTY not in r.failed_floors


# ----------------------------- runner early-return (SC-3b/3h 集成) ----------------------------- #


class TestRunnerFloorEarlyReturn:
    """floor fail → grader NOT called + weighted_score_not_computed + floor_failed。"""

    def test_floor_fail_skips_grader_and_weighted_score(self):
        from src.akb2.quality.runner import _run_quality_segments

        floor_fail = FloorResult(
            passed=False,
            failed_floors=[FLOOR_CORE_VALUE_F4],
            reason="concept F4：命名机制组件 0 < 3",
            content_lane="concept",
        )

        with patch("src.akb2.quality.checks_structural.run_candidate_self_gate") as mock_sg, \
             patch("src.akb2.quality.grader.run_grader_detailed") as mock_grader, \
             patch("src.akb2.quality.repair.run_repair_loop") as mock_repair:
            converged, error_class, error_detail, meta = _run_quality_segments(
                candidate_text="some candidate",
                candidate_path="/tmp/x.md",
                raw_content="raw",
                config=None,
                content_lane="concept",
                floor_result=floor_fail,
            )

        # 地板 fail → 直接 reject，不进 A 层/grader/repair
        assert converged is False
        assert error_class == "integrity_floor_failed"
        assert mock_sg.call_count == 0
        assert mock_grader.call_count == 0
        assert mock_repair.call_count == 0
        # 日志硬证三 key（SC-3b/3h 断言）
        assert meta["floor_failed"] == [FLOOR_CORE_VALUE_F4]
        assert meta["weighted_score_not_computed"] is True
        assert meta["grader_not_called"] is True
        assert meta["content_lane"] == "concept"

    def test_floor_pass_proceeds_to_self_gate(self):
        """floor pass → 进入 A 层 self-gate（不 early-return）。"""
        from src.akb2.quality.runner import _run_quality_segments

        floor_pass = FloorResult(passed=True, failed_floors=[], reason="", content_lane="concept")

        with patch("src.akb2.quality.checks_structural.run_candidate_self_gate") as mock_sg, \
             patch("src.akb2.quality.grader.run_grader_detailed"), \
             patch("src.akb2.quality.repair.run_repair_loop"):
            # patch grader/repair 防真调用；本测试只验 floor pass 后 self-gate 被触达（不 early-return）
            mock_sg.return_value = []
            _run_quality_segments(
                candidate_text="c",
                candidate_path="/tmp/x.md",
                raw_content="r",
                config=None,
                content_lane="concept",
                floor_result=floor_pass,
            )

        # floor pass → self-gate 被调（没 early-return）
        assert mock_sg.call_count == 1
