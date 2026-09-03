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
        # §0.2/D1：eligible edge = source_causal/llm_evidence + evidence + evidence_anchor
        "causal_edges": [
            {
                "from": "组件A",
                "to": "组件B",
                "relation": "feeds",
                "edge_kind": "source_causal",
                "evidence": "A feeds B",
                "evidence_anchor": "§A",
            },
            {
                "from": "组件B",
                "to": "组件C",
                "relation": "produces",
                "edge_kind": "source_causal",
                "evidence": "B produces C",
                "evidence_anchor": "§B",
            },
        ],
        "how_steps": [],
    }


# ----------------------------- ① claim_anchor_rate ----------------------------- #


class TestClaimAnchorRate:
    def test_pass_all_grounded_have_anchor(self):
        r = evaluate_integrity_floor(_concept_pass_extract(), "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CLAIM_ANCHOR_RATE not in r.failed_floors

    def test_fail_missing_anchor(self):
        d = _concept_pass_extract()
        d["claims"][0]["anchor"] = ""  # 一条 grounded claim 缺 anchor
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CLAIM_ANCHOR_RATE in r.failed_floors
        assert not r.passed

    def test_fail_no_grounded_claims(self):
        d = _concept_pass_extract()
        for c in d["claims"]:
            c["provenance"] = "akb_interpretation"
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CLAIM_ANCHOR_RATE in r.failed_floors


# ----------------------------- ② evidence_honesty ----------------------------- #


class TestEvidenceHonesty:
    def test_pass_has_real_blind_spot(self):
        r = evaluate_integrity_floor(_concept_pass_extract(), "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_EVIDENCE_HONESTY not in r.failed_floors

    def test_fail_no_gap(self):
        d = _concept_pass_extract()
        d["claims"][0]["evidence_gap"] = ""
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_EVIDENCE_HONESTY in r.failed_floors
        assert not r.passed

    def test_fail_gap_without_real_blind_spot_word(self):
        """裸来源断言（gap 文本无盲区词）→ 证据不诚实 fail。"""
        d = _concept_pass_extract()
        for c in d["claims"]:
            c["evidence_gap"] = "原文如此"
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_EVIDENCE_HONESTY in r.failed_floors

    def test_pass_gap_weimingque(self):
        """实跑句「原文未明确说明…」须被地板盲区词收下。"""
        d = _concept_pass_extract()
        for c in d["claims"]:
            c["evidence_gap"] = "原文未明确说明目标用户群"
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_EVIDENCE_HONESTY not in r.failed_floors

    def test_pass_gap_weiliechu(self):
        d = _concept_pass_extract()
        for c in d["claims"]:
            c["evidence_gap"] = "原文未列出对 Firefox 的支持计划"
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_EVIDENCE_HONESTY not in r.failed_floors


# ----------------------------- ③ interpretation_traceability (层①) ----------------------------- #


class TestInterpretationTraceability:
    def test_pass_traces_to_real_anchor(self):
        r = evaluate_integrity_floor(_concept_pass_extract(), "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_INTERPRETATION_TRACEABILITY not in r.failed_floors

    def test_fail_no_traces_to(self):
        """无据飘浮读后感（akb_interpretation 缺 traces_to）→ fail。"""
        d = _concept_pass_extract()
        d["claims"][2]["traces_to"] = []
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_INTERPRETATION_TRACEABILITY in r.failed_floors
        assert not r.passed

    def test_fail_traces_to_nonexistent_anchor(self):
        """poisoned-trace 的层① 形态：traces_to 指向不存在的 anchor（anchor 真实但不在 claims）。
        注：层② entailment（anchor 真实但与 interpretation 语义无关）由 grader 异族硬判，
        本层① 只判「指向真实存在的 anchor」。这里 anchor 不存在于 grounded_anchors → 层① fail。"""
        d = _concept_pass_extract()
        d["claims"][2]["traces_to"] = ["§Z"]  # §Z 不在 grounded anchors {§A, §B}
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_INTERPRETATION_TRACEABILITY in r.failed_floors

    def test_argumentation_interpretation_also_checked(self):
        """argumentation 里的 akb_interpretation 也要 traces_to。"""
        d = _concept_pass_extract()
        d["argumentation"] = [
            {"provenance": "akb_interpretation", "claim": "飘浮解读", "traces_to": []}
        ]
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
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
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_INTERPRETATION_TRACEABILITY not in r.failed_floors, (
            "grounded_anchors 空时 traceability 应豁免（SC-5）"
        )
        # SC-5：豁免时 meta 可见（治诊断变弱——失败归因转 claim_anchor_rate，需可见）
        assert r.meta.get("traceability_skipped_no_grounded_anchor") is True, (
            "grounded 空豁免时须写 traceability_skipped_no_grounded_anchor meta（SC-5）"
        )


# ----------------------------- ④ F4 core_value ----------------------------- #


class TestCoreValueF4:
    """§0.2/D1：F4 只认 eligible edge（source_causal/llm_evidence + evidence + anchor）。"""

    def test_concept_pass_3_components_with_eligible_edges(self):
        """≥3 组件 + ≥1 eligible edge（source_causal 带 evidence+anchor）→ F4 pass。"""
        r = evaluate_integrity_floor(_concept_pass_extract(), "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors

    def test_concept_fail_too_few_components(self):
        d = _concept_pass_extract()
        d["mechanism_components"] = d["mechanism_components"][:2]  # 只 2 个
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors
        assert not r.passed

    def test_concept_fail_no_causal_edges(self):
        """0 边 → 0 eligible → F4 fail（机制未讲清）。"""
        d = _concept_pass_extract()
        d["causal_edges"] = []
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors

    def test_concept_fail_only_inferred_weak_edges(self):
        """§0.2/D1(b)：只有 inferred_from_structure 弱边、无 eligible edge → 仍 F4 fail。"""
        d = _concept_pass_extract()
        d["causal_edges"] = [
            {
                "from": "组件A",
                "to": "组件B",
                "relation": "structural_sequence",
                "edge_kind": "inferred_from_structure",
                "evidence": "",
                "evidence_anchor": "",
            }
        ]
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, "inferred 弱边不计 F4 → 0 eligible 仍 fail"

    def test_concept_fail_edges_missing_evidence_anchor(self):
        """§0.2/D1：source_causal 边缺 evidence_anchor → 非 eligible → 0 eligible fail。"""
        d = _concept_pass_extract()
        d["causal_edges"] = [
            {
                "from": "组件A",
                "to": "组件B",
                "relation": "feeds",
                "edge_kind": "source_causal",
                "evidence": "A feeds B",
                "evidence_anchor": "",  # 缺 anchor → 非 eligible
            }
        ]
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, "缺 evidence_anchor → 非 eligible"

    def test_concept_fail_edges_missing_evidence(self):
        """§0.2/D1：source_causal 边缺 evidence → 非 eligible → 0 eligible fail。"""
        d = _concept_pass_extract()
        d["causal_edges"] = [
            {
                "from": "组件A",
                "to": "组件B",
                "relation": "feeds",
                "edge_kind": "source_causal",
                "evidence": "",
                "evidence_anchor": "§A",
            }
        ]
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors

    def test_concept_pass_llm_evidence_eligible_edge(self):
        """§0.2/D1(c)：llm_evidence 边带 evidence+anchor 也算 eligible → pass。"""
        d = _concept_pass_extract()
        d["causal_edges"] = [
            {
                "from": "组件A",
                "to": "组件B",
                "relation": "implies",
                "edge_kind": "llm_evidence",
                "evidence": "A implies B (interpretation)",
                "evidence_anchor": "§A",
            }
        ]
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors

    def test_concept_pass_isolated_component_with_eligible_edge(self):
        """§0.2/D1：不再要求每组件连边——组件C 孤立但 ≥1 eligible edge → pass。

        D1 推翻旧"每个组件都连边"口径（孤立点整篇拒）：≥1 eligible edge 连接两个命名
        组件即过 F4，孤立组件交 grader H3 审语义。
        """
        d = _concept_pass_extract()
        # 只留 1 条 eligible edge（A→B），组件C 无边（孤立）
        d["causal_edges"] = [
            {
                "from": "组件A",
                "to": "组件B",
                "relation": "feeds",
                "edge_kind": "source_causal",
                "evidence": "A feeds B",
                "evidence_anchor": "§A",
            }
        ]
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors, (
            "D1：≥1 eligible edge 即过 F4，组件C 孤立不再整篇拒"
        )

    def test_concept_fail_duplicate_component_names(self):
        """codex worker：重复名 [A,A,B] 不能伪装 3 组件过 F4（去重后只 2 唯一）。"""
        d = _concept_pass_extract()
        d["mechanism_components"] = [
            {"name": "组件A", "role": "x"},
            {"name": "组件A", "role": "y"},  # 重复名
            {"name": "组件B", "role": "z"},
        ]
        d["causal_edges"] = [
            {
                "from": "组件A",
                "to": "组件B",
                "relation": "r",
                "edge_kind": "source_causal",
                "evidence": "A to B",
                "evidence_anchor": "§A",
            }
        ]
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
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


# ---------------------------------------------------------------------------
# GPT 单审 P1：F4 按车道化——pattern/design 不再要求 H1 命令，改锚定 H3/H2 门
# （对齐 distill._LANE_HOW_PARAMS_CORE_INSTRUCTIONS：pattern/design 典型条目
#   command=""、executable=false；H3=模式间关系/设计系统组件、H2=数据流/可见效果；
#   H1 仅原文有命令才标。旧「一律 H1≥1」把 pattern/design 在评分前拦死。）
# ---------------------------------------------------------------------------


def _pattern_design_extract(lane: str, steps: list[dict]) -> dict:
    """构造 pattern/design extract：grounded §A claim + 给定 how_steps。"""
    return {
        "claims": [_grounded_claim("核心条目", anchor="§A")],
        "argumentation": [],
        "mechanism_components": [],
        "causal_edges": [],
        "how_steps": steps,
        "content_lane": lane,
    }


class TestF4LaneSplitPatternDesign:
    """F4 车道化：pattern/design 核心价值 = ≥1 条可锚定的 H3/H2 how_step。"""

    def test_pattern_h3_no_h1_passes_f4(self):
        """pattern：锚定 H3（模式间关系）+ 无 H1 → F4 pass（不再被 H1 门拦死）。"""
        d = _pattern_design_extract("pattern", [
            {"instruction": "模式 X 与模式 Y 组合使用", "command": "",
             "executable": False, "anchor": "§A", "provenance": "source_grounded",
             "how_level": "H3"},
        ])
        r = evaluate_integrity_floor(d, "pattern", raw_anchors={"§A"})
        assert r.passed is True
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors, (
            f"pattern 锚定 H3 无 H1 应过 F4（车道核心价值=可复用模式，非命令）: {r.reason}"
        )

    def test_design_h2_no_h1_passes_f4(self):
        """design：锚定 H2（决策的可见效果）+ 无 H1 → F4 pass。"""
        d = _pattern_design_extract("design", [
            {"instruction": "层级决策：副标题降一级灰度", "command": "",
             "executable": False, "anchor": "§A", "provenance": "source_grounded",
             "how_level": "H2"},
        ])
        r = evaluate_integrity_floor(d, "design", raw_anchors={"§A"})
        assert r.passed is True
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors, (
            f"design 锚定 H2 无 H1 应过 F4（车道核心价值=设计决策，非命令）: {r.reason}"
        )

    def test_pattern_h2_also_passes_f4(self):
        """pattern：H2（模式的数据流效果）同样计核心价值 → pass。"""
        d = _pattern_design_extract("pattern", [
            {"instruction": "模式 X 的数据流效果说明", "command": "",
             "executable": False, "anchor": "§A", "provenance": "source_grounded",
             "how_level": "H2"},
        ])
        r = evaluate_integrity_floor(d, "pattern", raw_anchors={"§A"})
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors

    def test_pattern_fails_when_no_anchorable_core_step(self):
        """pattern：只有 H1（不可锚定——伪 §Z）→ F4 fail（拒伪锚）。"""
        d = _pattern_design_extract("pattern", [
            {"instruction": "模式 X 描述", "command": "",
             "executable": False, "anchor": "§Z", "provenance": "source_grounded",
             "how_level": "H3"},
        ])
        r = evaluate_integrity_floor(d, "pattern", raw_anchors={"§A"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, (
            "伪 §Z 锚不可信 → 0 条可锚定核心条目 → F4 fail"
        )

    def test_pattern_fails_when_no_h2_h3(self):
        """pattern：只有 H1 命令级（车道语义上异常但结构性允许）→ 不满足 H3/H2 门 → fail。

        与 tutorial 的 H1 门互补：pattern 的核心价值门是「可锚定的模式条目」，
        纯 H1 命令不构成 pattern 车道的核心价值。
        """
        d = _pattern_design_extract("pattern", [
            {"instruction": "run x", "command": "run x", "executable": True,
             "anchor": "§A", "provenance": "source_grounded", "how_level": "H1"},
        ])
        r = evaluate_integrity_floor(d, "pattern", raw_anchors={"§A"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors

    def test_pattern_empty_anchor_fails(self):
        """pattern：H3 但 anchor 空（无据飘浮模式名）→ 不算可锚定 → fail。"""
        d = _pattern_design_extract("pattern", [
            {"instruction": "模式 X 描述", "command": "",
             "executable": False, "anchor": "", "provenance": "akb_interpretation",
             "how_level": "H3"},
        ])
        r = evaluate_integrity_floor(d, "pattern", raw_anchors={"§A"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors

    def test_design_fails_when_no_anchorable_core_step(self):
        """design：伪锚 H3 → F4 fail（与 pattern 同口径）。"""
        d = _pattern_design_extract("design", [
            {"instruction": "决策 D", "command": "", "executable": False,
             "anchor": "§Z", "provenance": "source_grounded", "how_level": "H3"},
        ])
        r = evaluate_integrity_floor(d, "design", raw_anchors={"§A"})
        fail_floor = FLOOR_CORE_VALUE_F4 in r.failed_floors
        assert fail_floor, "design 伪锚 → 0 可锚定核心条目 → F4 fail"

    def test_tutorial_h2_h3_only_still_fails(self):
        """tutorial（canonical）：只有 H2/H3、无 H1 → F4 仍 fail（H1 命令门不放松）。"""
        d = _pattern_design_extract("tutorial", [
            {"instruction": "数据流说明", "command": "", "executable": False,
             "anchor": "§A", "provenance": "source_grounded", "how_level": "H2"},
        ])
        r = evaluate_integrity_floor(d, "tutorial", raw_anchors={"§A"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, (
            "tutorial 保持 H1≥1 命令门（车道化不得放松操作型门）"
        )

    def test_procedural_alias_h2_h3_only_still_fails(self):
        """procedural（别名）：只有 H2/H3、无 H1 → F4 仍 fail（旧默认偏严保留）。"""
        d = _pattern_design_extract("procedural", [
            {"instruction": "数据流说明", "command": "", "executable": False,
             "anchor": "§A", "provenance": "source_grounded", "how_level": "H2"},
        ])
        r = evaluate_integrity_floor(d, "procedural", raw_anchors={"§A"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors

    def test_empty_lane_h2_h3_only_still_fails(self):
        """空车道：只有 H2/H3、无 H1 → F4 仍 fail（fail-closed 偏严默认）。"""
        d = _pattern_design_extract("", [
            {"instruction": "数据流说明", "command": "", "executable": False,
             "anchor": "§A", "provenance": "source_grounded", "how_level": "H2"},
        ])
        r = evaluate_integrity_floor(d, "", raw_anchors={"§A"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors

    def test_unknown_lane_h2_h3_only_still_fails(self):
        """未知车道：只有 H2/H3、无 H1 → F4 仍 fail（fail-closed 偏严默认）。"""
        d = _pattern_design_extract("mystery", [
            {"instruction": "data flow note", "command": "", "executable": False,
             "anchor": "§A", "provenance": "source_grounded", "how_level": "H2"},
        ])
        lanes = {"tutorial", "procedural", "concept", "pattern", "design"}
        assert "mystery" not in lanes  # sanity: mystery 确非已知车道
        r = evaluate_integrity_floor(d, "mystery", raw_anchors={"§A"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors

    def test_pattern_no_raw_anchors_fail_closed(self):
        """pattern：不传 raw_anchors（trusted=∅）→ 0 可锚定核心条目 → F4 fail（fail-closed）。"""
        d = _pattern_design_extract("pattern", [
            {"instruction": "模式 X 描述", "command": "",
             "executable": False, "anchor": "§A", "provenance": "source_grounded",
             "how_level": "H3"},
        ])
        r = evaluate_integrity_floor(d, "pattern")
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, (
            "无 raw_anchors → trusted 空 → 锚不可信 → F4 fail（不回落 claims）"
        )


# ----------------------------- 整体 evaluate ----------------------------- #


class TestEvaluateIntegrityFloor:
    def test_all_pass(self):
        r = evaluate_integrity_floor(_concept_pass_extract(), "concept", raw_anchors={"§A", "§B"})
        assert r.passed is True
        assert r.failed_floors == []
        assert r.reason == ""
        assert r.content_lane == "concept"

    def test_one_fail_makes_not_passed(self):
        d = _concept_pass_extract()
        d["mechanism_components"] = []  # 触发 F4 fail
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert r.passed is False
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors
        assert CONCEPT_MIN_MECHANISM_COMPONENTS == 3

    def test_adversarial_floor_fail_not_compensable(self):
        """对抗样本：F4 fail + 其它成员/辅助维都「满分」→ 仍 fail（不可补偿）。
        地板二值，不被其它成员 pass 或辅助分顶过。"""
        d = _concept_pass_extract()
        d["mechanism_components"] = []  # F4 fail
        # 其它三成员都 pass（claims/argumentation 完整）
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
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


class TestRunnerFloorLaneSplit:
    """GPT 单审 P1：pattern/design 的 floor 正向路径——floor 过 → 不以
    integrity_floor_failed / H1<1 早退，评分段（self-gate/grader）被触达。"""

    def _lane_floor_pass(self, lane: str) -> FloorResult:
        """构造 pattern/design 车道 F4 pass 的 FloorResult（锚定 H3/H2、无 H1）。"""
        from src.akb2.quality.integrity_floor import evaluate_integrity_floor

        d = {
            "claims": [_grounded_claim("核心条目", anchor="§A")],
            "argumentation": [],
            "mechanism_components": [],
            "causal_edges": [],
            "how_steps": [
                {"instruction": "模式/决策核心条目", "command": "",
                 "executable": False, "anchor": "§A",
                 "provenance": "source_grounded", "how_level": "H3"},
            ],
        }
        return evaluate_integrity_floor(d, lane, raw_anchors={"§A"})

    def test_pattern_floor_pass_reaches_grader_stage(self):
        """pattern：锚定 H3 无 H1 → floor pass → 不 early-return，grader 会被调用。"""
        from src.akb2.quality.runner import _run_quality_segments

        floor_pass = self._lane_floor_pass("pattern")
        assert floor_pass.passed, f"fixture 应 floor 全过: {floor_pass.reason}"

        with patch("src.akb2.quality.checks_structural.run_candidate_self_gate") as mock_sg, \
             patch("src.akb2.quality.grader.run_grader_detailed"), \
             patch("src.akb2.quality.repair.run_repair_loop"):
            mock_sg.return_value = []
            converged, error_class, error_detail, meta = _run_quality_segments(
                candidate_text="c",
                candidate_path="/tmp/x.md",
                raw_content="r",
                config=None,
                content_lane="pattern",
                floor_result=floor_pass,
            )

        assert error_class != "integrity_floor_failed", (
            f"pattern floor pass 不应被完整性地板早退: {error_class} / {error_detail}"
        )
        assert "H1" not in error_detail or error_class == "", (
            f"不应因 H1<1 早退: {error_detail}"
        )
        # 评分段被触达：self-gate 被调、grader mock 会被后续流程使用（不再 floor 拦死）
        assert mock_sg.call_count == 1
        assert meta.get("grader_not_called") is not True
        assert meta.get("weighted_score_not_computed") is not True

    def test_design_floor_pass_reaches_grader_stage(self):
        """design：锚定 H2 无 H1 → floor pass → 不 early-return，评分段被触达。"""
        from src.akb2.quality.runner import _run_quality_segments

        floor_pass = self._lane_floor_pass("design")
        assert floor_pass.passed, f"fixture 应 floor 全过: {floor_pass.reason}"

        with patch("src.akb2.quality.checks_structural.run_candidate_self_gate") as mock_sg, \
             patch("src.akb2.quality.grader.run_grader_detailed"), \
             patch("src.akb2.quality.repair.run_repair_loop"):
            mock_sg.return_value = []
            converged, error_class, error_detail, meta = _run_quality_segments(
                candidate_text="c",
                candidate_path="/tmp/x.md",
                raw_content="r",
                config=None,
                content_lane="design",
                floor_result=floor_pass,
            )

        assert error_class != "integrity_floor_failed", (
            f"design floor pass 不应被完整性地板早退: {error_class} / {error_detail}"
        )
        assert mock_sg.call_count == 1
        assert meta.get("grader_not_called") is not True
        assert meta.get("weighted_score_not_computed") is not True

    def test_pattern_floor_fail_still_early_returns(self):
        """pattern：floor fail（0 可锚定核心条目）→ 仍 integrity_floor_failed 早退。"""
        from src.akb2.quality.runner import _run_quality_segments

        d = {
            "claims": [_grounded_claim("核心条目", anchor="§A")],
            "argumentation": [],
            "mechanism_components": [],
            "causal_edges": [],
            "how_steps": [
                {"instruction": "模式 X 描述", "command": "",
                 "executable": False, "anchor": "§Z",
                 "provenance": "source_grounded", "how_level": "H3"},
            ],
        }
        floor_fail = evaluate_integrity_floor(d, "pattern", raw_anchors={"§A"})
        assert not floor_fail.passed

        with patch("src.akb2.quality.checks_structural.run_candidate_self_gate") as mock_sg, \
             patch("src.akb2.quality.grader.run_grader_detailed") as mock_grader, \
             patch("src.akb2.quality.repair.run_repair_loop"):
            converged, error_class, error_detail, meta = _run_quality_segments(
                candidate_text="c",
                candidate_path="/tmp/x.md",
                raw_content="r",
                config=None,
                content_lane="pattern",
                floor_result=floor_fail,
            )

        assert converged is False
        assert error_class == "integrity_floor_failed"
        assert mock_sg.call_count == 0
        assert mock_grader.call_count == 0
        assert meta["floor_failed"] == [FLOOR_CORE_VALUE_F4]


# ---------------------------------------------------------------------------
# §0.2/F1：eligible edge evidence_anchor 必须是真实 raw anchor（防伪 §Z/合成 §raw）
# ---------------------------------------------------------------------------


def _f1_extract(edges, claims_anchors=("§A", "§B")):
    """构造 F1 concept extract：claims 带 anchor，edges 给定。"""
    claims = [
        {
            "provenance": "source_grounded",
            "claim": f"claim {a}",
            "anchor": a,
            "evidence_gap": "未提供 X",
        }
        for a in claims_anchors
    ]
    return {
        "claims": claims,
        "argumentation": [],
        "mechanism_components": [
            {"name": "A", "role": "r"}, {"name": "B", "role": "r"}, {"name": "C", "role": "r"},
        ],
        "causal_edges": edges,
        "how_steps": [],
    }


class TestF1EligibleEdgeAnchorValidation:
    """§0.2/F1：eligible edge evidence_anchor 必须在可信 raw anchor 集（grounded claims）。

    伪 §Z / 合成 §raw 不计 eligible → F4 fail（防伪边破坏 traceability）。
    """

    def test_real_anchor_eligible_edge_passes_f4(self):
        """evidence_anchor=§A（在 claims anchors）→ eligible → F4 pass。"""
        d = _f1_extract([
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B", "evidence_anchor": "§A"},
        ])
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors, "真实 §A anchor 应 eligible"

    def test_fake_anchor_z_not_eligible(self):
        """evidence_anchor=§Z（不在 claims anchors）→ 非 eligible → F4 fail。"""
        d = _f1_extract([
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B", "evidence_anchor": "§Z"},  # 伪 §Z
        ])
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, "伪 §Z 不应计 eligible"

    def test_synthetic_raw_anchor_not_eligible(self):
        """evidence_anchor=§raw（合成，不在 claims anchors）→ 非 eligible → F4 fail。"""
        d = _f1_extract([
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B", "evidence_anchor": "§raw"},  # 合成 §raw
        ])
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, "合成 §raw 不应计 eligible"

    def test_zero_eligible_with_components_triggers_retake_signal(self):
        """§0.2/F1 太脆：0 eligible + 组件≥3 → needs_causal_edge_retake 信号（不静默早死）。"""
        d = _f1_extract([
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "", "evidence_anchor": ""},  # 缺字段 → 0 eligible
        ])
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, "0 eligible 仍 F4 fail"
        assert r.meta.get("needs_causal_edge_retake") is True, (
            "0 eligible + 组件≥3 应标 needs_causal_edge_retake（runner 补抽）"
        )

    def test_too_few_components_no_retake_signal(self):
        """<3 组件 → F4 fail 但不标 retake（retake 只救 0 eligible，不救 <3 组件）。"""
        d = _f1_extract([], claims_anchors=("§A",))
        d["mechanism_components"] = [{"name": "A", "role": "r"}]
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors
        assert not r.meta.get("needs_causal_edge_retake"), "<3 组件不应标 retake"


# ---------------------------------------------------------------------------
# §0.2/F1-followup：trusted anchor 集扩到 raw 真实 anchor 全集
# ---------------------------------------------------------------------------


class TestF1FollowupRawAnchorUniverse:
    """§0.2/F1-followup：eligible edge evidence_anchor 须 ∈ raw 真实 anchor 全集
    （不只 grounded claims）。防 needs_retake 补抽的真边 / LLM primary 真边被误杀。
    """

    def test_a_anchor_in_raw_not_in_claims_counts_eligible(self):
        """(a) anchor ∈ raw 真实但 ∉ grounded claims → eligible + F4 pass。"""
        d = _f1_extract([
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B", "evidence_anchor": "§D"},  # §D 不在 claims(§A,§B)
        ], claims_anchors=("§A", "§B"))
        # raw_anchors 含 §D（raw 真实 anchor，未被 claim 引用）
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B", "§D"})
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors, (
            "§D ∈ raw 真实 anchor 集 → 应 eligible（不只 grounded claims）"
        )

    def test_b_needs_retake_edge_from_raw_repasses_f4(self):
        """(b) needs_retake 补抽边（anchor 来自 raw）→ 重评 F4 pass。"""
        # 初始：0 eligible（LLM 边缺 anchor）→ needs_retake
        d = _f1_extract([
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B", "evidence_anchor": ""},  # 缺 anchor
        ])
        r1 = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert r1.meta.get("needs_causal_edge_retake") is True
        # retake 补抽边 anchor=§B（来自 raw，∈ raw_anchors）
        d["causal_edges"] = [
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B (retake)", "evidence_anchor": "§B"},
        ]
        r2 = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 not in r2.failed_floors, (
            "retake 补抽边（anchor §B ∈ raw）→ 重评应 F4 pass"
        )

    def test_c_fake_anchor_still_fail_with_raw_anchors(self):
        """(c) 伪 §Z 仍不计 eligible → F4 fail（防伪不破，即使用 raw_anchors）。"""
        d = _f1_extract([
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B", "evidence_anchor": "§Z"},  # 伪 §Z
        ])
        # raw_anchors 不含 §Z（§Z 不在 raw）
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, "伪 §Z 不在 raw 真实集 → 仍 fail"

    def test_c_synthetic_raw_anchor_still_fail(self):
        """(c) 合成 §raw 仍不计 eligible → F4 fail（§raw 不在 raw 真实 §-anchor 集）。"""
        d = _f1_extract([
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B", "evidence_anchor": "§raw"},
        ])
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, "合成 §raw 不在 raw 集红 → 仍 fail"

    def test_d_too_few_components_still_fail_with_raw_anchors(self):
        """(d) <3 组件 → F4 fail（不回归，raw_anchors 不救 <3 组件）。"""
        d = _f1_extract([], claims_anchors=("§A",))
        d["mechanism_components"] = [{"name": "A", "role": "r"}]
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors

    def test_d_inferred_weak_edges_still_not_counted(self):
        """(d) inferred_from_structure 弱边仍不计 eligible（不回归）。"""
        d = _f1_extract([
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "inferred_from_structure",
             "evidence": "", "evidence_anchor": ""},
        ])
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, "inferred 弱边不计 eligible → 0 eligible fail"

    def test_no_raw_anchors_fail_closed_no_claims_fallback(self):
        """§0.2/F1-regex 根治：无 raw_anchors → 不回落 claims → §A 即使在 claims 也不 trusted → F4 fail。

        codex 反方向放水缝：若 trusted 回落 claims，LLM 把 §Z 同时写进 claim+edge → §Z 进
        trusted → 伪边计 eligible 混过 F4。根治：trusted 只认 raw 真实声明全集，不回落 claims。
        """
        d = _f1_extract([
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B", "evidence_anchor": "§A"},  # §A ∈ claims 但 raw_anchors 未提供
        ])
        # 不传 raw_anchors → trusted=空集 → §A 不 trusted → 0 eligible → F4 fail（不回落 claims）
        r = evaluate_integrity_floor(d, "concept")
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, (
            "无 raw_anchors → 不回落 claims → §A 不 trusted → F4 fail（codex 根治放水缝）"
        )

    def test_forged_z_in_claim_and_edge_still_fail(self):
        """§0.2/F1-regex 根治硬证据：raw 无 §Z，但 grounded claim 和 edge 都写 §Z → F4 仍 fail。

        LLM 伪造 §Z 同时写进 source_grounded claim（过 anchor_rate 格式校验）和 edge 的
        evidence_anchor → 旧 claims 回落口径会让 §Z 进 trusted → 伪边 eligible 混过 F4。
        根治后：trusted=raw 全集，§Z 不在 raw → 不 trusted → 不计 eligible → F4 fail。
        """
        d = _f1_extract([
            {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B", "evidence_anchor": "§Z"},  # 伪造 §Z
        ], claims_anchors=("§Z",))  # claim 也写 §Z（过 anchor_rate 格式校验）
        # raw_anchors 不含 §Z（raw 无 §Z）
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors, (
            "raw 无 §Z → 即使 claim+edge 都写 §Z 也不 trusted → F4 fail（根治反方向放水缝）"
        )


class TestLoop2DryGoodsHowOverride:
    """loop2 合同层放宽：官方文档/设计手记不必长得像 H1 命令。loop1 默认不放。"""

    def test_tutorial_action_sentence_passes_only_when_override_on(self):
        d = _pattern_design_extract("tutorial", [
            {"instruction": "Start at high effort then test the other levels",
             "command": "", "executable": False, "anchor": "§A",
             "how_level": "H2"},
        ])
        loop1 = evaluate_integrity_floor(d, "tutorial", raw_anchors={"§A"})
        loop2 = evaluate_integrity_floor(
            d, "tutorial", raw_anchors={"§A"}, allow_dry_goods_how=True
        )
        assert FLOOR_CORE_VALUE_F4 in loop1.failed_floors
        assert FLOOR_CORE_VALUE_F4 not in loop2.failed_floors

    def test_design_unlabeled_anchored_decision_passes_only_when_override_on(self):
        d = _pattern_design_extract("design", [
            {"instruction": "I killed the outer container and made the entity key a section header",
             "command": "", "executable": False, "anchor": "§A",
             "how_level": "H1"},
        ])
        loop1 = evaluate_integrity_floor(d, "design", raw_anchors={"§A"})
        loop2 = evaluate_integrity_floor(
            d, "design", raw_anchors={"§A"}, allow_dry_goods_how=True
        )
        assert FLOOR_CORE_VALUE_F4 in loop1.failed_floors
        assert FLOOR_CORE_VALUE_F4 not in loop2.failed_floors

    def test_override_still_rejects_empty_how(self):
        d = _pattern_design_extract("tutorial", [])
        r = evaluate_integrity_floor(
            d, "tutorial", raw_anchors={"§A"}, allow_dry_goods_how=True
        )
        assert FLOOR_CORE_VALUE_F4 in r.failed_floors
