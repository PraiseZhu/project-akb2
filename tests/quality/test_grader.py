"""test_grader.py — 完整性地板（integrity floor）行为锁（intake-rebuild p4）。

本文件是 sc-p4-floor-uncompensated / sc-p4-high-score-low-floor-mutation 的验收套件，
全部测试围绕地板语义命名（含 "floor"）——`pytest -k floor` 须匹配本文件全部用例
（零 deselected，verify 纪律：skip/xfail/deselected 不当通过）。

锁定不变式：
  1. 地板四成员对全车道（concept / procedural / tutorial 别名 / 未来 pattern-design）生效，
     任一成员 fail → FloorResult.passed=False 且 failed_floors 恰含该成员（精确归因）。
  2. F4「≥3 组件+因果边」仅 concept 车道；非 concept 车道（procedural/tutorial/pattern）
     用 H1≥1 步骤判据——pattern/design 不得误用 concept F4 阈值。
  3. 不可补偿（宪法 8(a)）：地板任一成员 fail 时 runner 评分段 early-return——
     grader 不被调用、加权分不算，即使加权分潜在满分（mutation：删短路则红）。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.akb2.quality.grader import RUBRIC_DIMENSIONS_BY_LANE
from src.akb2.quality.integrity_floor import (
    FLOOR_CLAIM_ANCHOR_RATE,
    FLOOR_CORE_VALUE_F4,
    FLOOR_EVIDENCE_HONESTY,
    FLOOR_INTERPRETATION_TRACEABILITY,
    FloorResult,
    evaluate_integrity_floor,
)

# ---------------------------------------------------------------------------
# Fixtures：全成员 pass 的 baseline extract + 单成员 fail 的最小变形
# ---------------------------------------------------------------------------

_RAW_ANCHORS = {"§A", "§B"}

_GROUNDED_CLAIMS = [
    {
        "provenance": "source_grounded",
        "claim": "claim A",
        "anchor": "§A",
        "evidence_gap": "未提供 X 的量化对比",
    },
    {
        "provenance": "source_grounded",
        "claim": "claim B",
        "anchor": "§B",
        "evidence_gap": "未覆盖 Y 场景",
    },
]

_CONCEPT_COMPONENTS = [
    {"name": "组件A", "role": "r"},
    {"name": "组件B", "role": "r"},
    {"name": "组件C", "role": "r"},
]

_CONCEPT_ELIGIBLE_EDGE = {
    "from": "组件A",
    "to": "组件B",
    "edge_kind": "source_causal",
    "evidence": "raw 明示因果",
    "evidence_anchor": "§A",
}


def _concept_extract_all_floor_pass() -> dict:
    """concept 地板四成员全 pass 的 extract。"""
    return {
        "claims": [dict(c) for c in _GROUNDED_CLAIMS],
        "argumentation": [],
        "mechanism_components": [dict(c) for c in _CONCEPT_COMPONENTS],
        "causal_edges": [dict(_CONCEPT_ELIGIBLE_EDGE)],
        "how_steps": [],
    }


def _procedural_extract_all_floor_pass() -> dict:
    """procedural 地板四成员全 pass 的 extract（H1 步骤 + 组件/边可缺）。"""
    return {
        "claims": [dict(c) for c in _GROUNDED_CLAIMS],
        "argumentation": [],
        "mechanism_components": [],  # 非概念车道不需要机制组件
        "causal_edges": [],
        "how_steps": [
            {"step": "python3 -m pytest tests/quality -q", "how_level": "H1"},
            {"step": "先读量规再评", "how_level": "H2"},
        ],
    }


def _fail_member_extract(lane: str, member: str) -> dict:
    """构造「仅指定地板成员 fail」的最小变形 extract（其余成员保持 pass）。

    member ∈ {claim_anchor_rate, evidence_honesty, interpretation_traceability, core_value_f4}
    """
    extract = (
        _concept_extract_all_floor_pass()
        if lane == "concept"
        else _procedural_extract_all_floor_pass()
    )
    if member == "claim_anchor_rate":
        # ①：grounded claim 缺 § 锚点
        extract["claims"][0]["anchor"] = "no-anchor"
    elif member == "evidence_honesty":
        # ②：证据盲区全为「已覆盖」——无任何真实盲区标注
        for c in extract["claims"]:
            c["evidence_gap"] = "原文已完整覆盖"
    elif member == "interpretation_traceability":
        # ③：akb_interpretation 缺 traces_to（grounded anchors 存在 → 不可满足豁免不触发）
        extract["argumentation"] = [
            {
                "provenance": "akb_interpretation",
                "point": "解读飘浮",
                "traces_to": [],
            }
        ]
    elif member == "core_value_f4":
        if lane == "concept":
            # ④ concept：组件 3→2（跌破 ≥3 命名组件）
            extract["mechanism_components"] = _CONCEPT_COMPONENTS[:2]
        else:
            # ④ procedural/tutorial：抽掉 H1 步骤（只剩 H2）
            extract["how_steps"] = [
                {"step": "先读量规再评", "how_level": "H2"},
            ]
    else:  # pragma: no cover - 防御
        raise ValueError(f"unknown floor member: {member}")
    return extract


_FLOOR_MEMBERS = [
    ("claim_anchor_rate", FLOOR_CLAIM_ANCHOR_RATE),
    ("evidence_honesty", FLOOR_EVIDENCE_HONESTY),
    ("interpretation_traceability", FLOOR_INTERPRETATION_TRACEABILITY),
    ("core_value_f4", FLOOR_CORE_VALUE_F4),
]


# ---------------------------------------------------------------------------
# SC2 锚点 1：地板四成员全车道生效 + 单成员 fail 精确归因
# ---------------------------------------------------------------------------


class TestFloorAllLanesAllMembers:
    """地板 4 项对全车道生效：任一成员单 fail → passed=False 且 failed_floors 恰含该成员。"""

    @pytest.mark.parametrize("member_key,floor_key", _FLOOR_MEMBERS)
    def test_floor_single_member_fail_concept(self, member_key, floor_key):
        """concept：单成员 fail → 整体 fail，failed_floors 恰含该成员（红集=该成员）。"""
        extract = _fail_member_extract("concept", member_key)
        r = evaluate_integrity_floor(extract, "concept", raw_anchors=set(_RAW_ANCHORS))
        assert r.passed is False, f"地板成员 {floor_key} fail 必须使整体 fail"
        assert r.failed_floors == [floor_key], (
            f"红集必须恰为 [{floor_key}]（精确归因），实际 {r.failed_floors}"
        )
        assert r.reason.strip() != ""

    @pytest.mark.parametrize("member_key,floor_key", _FLOOR_MEMBERS)
    def test_floor_single_member_fail_procedural(self, member_key, floor_key):
        """procedural：单成员 fail → 整体 fail，failed_floors 恰含该成员。"""
        extract = _fail_member_extract("procedural", member_key)
        r = evaluate_integrity_floor(extract, "procedural", raw_anchors=set(_RAW_ANCHORS))
        assert r.passed is False
        assert r.failed_floors == [floor_key], (
            f"红集必须恰为 [{floor_key}]，实际 {r.failed_floors}"
        )

    @pytest.mark.parametrize("member_key,floor_key", _FLOOR_MEMBERS)
    def test_floor_single_member_fail_tutorial_alias(self, member_key, floor_key):
        """tutorial（p5 别名，车道字符串直达地板分支）：同 procedural 判据。"""
        extract = _fail_member_extract("procedural", member_key)
        r = evaluate_integrity_floor(extract, "tutorial", raw_anchors=set(_RAW_ANCHORS))
        assert r.passed is False
        assert r.failed_floors == [floor_key]

    def test_floor_all_members_pass_concept(self):
        """baseline：四成员全 pass → passed=True（防夹具本身全红造成的假阳性）。"""
        r = evaluate_integrity_floor(
            _concept_extract_all_floor_pass(), "concept", raw_anchors=set(_RAW_ANCHORS)
        )
        assert r.passed is True, f"baseline 必须 pass，实际 fail: {r.failed_floors}"

    def test_floor_all_members_pass_procedural_without_concept_f4(self):
        """procedural baseline：0 机制组件 + 0 因果边仍 pass——concept F4 阈值不外溢。"""
        r = evaluate_integrity_floor(
            _procedural_extract_all_floor_pass(), "procedural", raw_anchors=set(_RAW_ANCHORS)
        )
        assert r.passed is True, (
            f"procedural 不要求机制组件/因果边，实际 fail: {r.failed_floors}"
        )


# ---------------------------------------------------------------------------
# SC2 锚点 2：F4「≥3 组件+因果边」仅 concept；tutorial/procedural 用 H1
# ---------------------------------------------------------------------------


class TestFloorF4ConceptOnly:
    """F4 车道分支：concept=≥3 组件+≥1 eligible 边；非 concept=H1≥1。"""

    def test_floor_f4_concept_two_components_fail(self):
        """concept 组件 2 < 3 → F4 fail（红集恰为 core_value_f4）。"""
        extract = _concept_extract_all_floor_pass()
        extract["mechanism_components"] = _CONCEPT_COMPONENTS[:2]
        r = evaluate_integrity_floor(extract, "concept", raw_anchors=set(_RAW_ANCHORS))
        assert r.failed_floors == [FLOOR_CORE_VALUE_F4]

    def test_floor_f4_concept_zero_eligible_edge_fail(self):
        """concept 组件齐但 0 eligible 边（只剩 inferred 弱边）→ F4 fail。"""
        extract = _concept_extract_all_floor_pass()
        extract["causal_edges"] = [
            {
                "from": "组件A",
                "to": "组件B",
                "edge_kind": "inferred_from_structure",
                "evidence": "",
                "evidence_anchor": "",
            }
        ]
        r = evaluate_integrity_floor(extract, "concept", raw_anchors=set(_RAW_ANCHORS))
        assert r.failed_floors == [FLOOR_CORE_VALUE_F4]

    def test_floor_f4_procedural_sparse_components_pass(self):
        """procedural 仅 1 组件 0 边 + H1 在 → F4 pass（不套 concept 阈值）。"""
        extract = _procedural_extract_all_floor_pass()
        extract["mechanism_components"] = [{"name": "唯一组件", "role": "r"}]
        r = evaluate_integrity_floor(extract, "procedural", raw_anchors=set(_RAW_ANCHORS))
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors
        assert r.passed is True

    def test_floor_f4_procedural_no_h1_fail(self):
        """procedural 无 H1 命令级步骤 → F4 fail（tutorial 用地板步骤/H1 判据）。"""
        extract = _procedural_extract_all_floor_pass()
        extract["how_steps"] = [{"step": "概念性指导", "how_level": "H2"}]
        r = evaluate_integrity_floor(extract, "procedural", raw_anchors=set(_RAW_ANCHORS))
        assert r.failed_floors == [FLOOR_CORE_VALUE_F4]

    def test_floor_f4_tutorial_alias_uses_h1_not_components(self):
        """tutorial 别名：H1 在即过；不按 ≥3 组件判（p5 前置语义锁）。"""
        extract = _procedural_extract_all_floor_pass()
        extract["mechanism_components"] = []  # 无组件也行
        r = evaluate_integrity_floor(extract, "tutorial", raw_anchors=set(_RAW_ANCHORS))
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors

    def test_floor_f4_future_lane_not_concept_branch(self):
        """未来车道（pattern）不得误用 concept F4 阈值：concept 型结构（2 组件 0 H1）
        在 pattern 车道按 H1 分支判 → F4 fail 于 H1 缺失而非组件数。"""
        extract = _concept_extract_all_floor_pass()
        extract["how_steps"] = []  # 无任何步骤
        r = evaluate_integrity_floor(extract, "pattern", raw_anchors=set(_RAW_ANCHORS))
        assert r.failed_floors == [FLOOR_CORE_VALUE_F4]
        assert "H1" in r.reason, (
            f"非 concept 车道 F4 须按 H1 判据报因（防 pattern/design 误用组件阈值）: {r.reason}"
        )


# ---------------------------------------------------------------------------
# SC2/SC3 锚点 3：地板不可补偿——高加权分救不了地板 fail（runner 评分段短路）
# ---------------------------------------------------------------------------


def _max_dimension_scores(lane: str) -> dict[str, float]:
    """按车道构造全维满分的 dimension_scores（加权分 = 权重和 = 10 ≥ 阈值）。"""
    return {d: 1.0 for d in RUBRIC_DIMENSIONS_BY_LANE[lane]}


class TestFloorUncompensatedHighScore:
    """加权分很高但地板 fail → 整篇 reject（不可补偿；mutation：删短路则红）。"""

    def test_floor_high_score_low_honesty_rejected(self):
        """SC3 主断言：evidence_honesty fail + 全维满分 grader → 整篇 reject。

        grader mock 返回全维满分（若被调用即会通过），断言 grader 根本没被调——
        拒绝发生在地板层，加权分未参与（宪法 8(a)）。
        """
        from src.akb2.quality.grader import GraderResult
        from src.akb2.quality.runner import _run_quality_segments

        extract = _fail_member_extract("concept", "evidence_honesty")
        floor_fail = evaluate_integrity_floor(
            extract, "concept", raw_anchors=set(_RAW_ANCHORS)
        )
        assert floor_fail.failed_floors == [FLOOR_EVIDENCE_HONESTY]

        max_grader = GraderResult(
            defects=[],
            model_used="gpt-5.4",
            suspended=False,
            content_lane="concept",
            dimension_scores=_max_dimension_scores("concept"),
            interpretation_entailment=True,
        )
        with patch(
            "src.akb2.quality.checks_structural.run_candidate_self_gate"
        ) as mock_sg, patch(
            "src.akb2.quality.grader.run_grader_detailed", return_value=max_grader
        ) as mock_grader, patch(
            "src.akb2.quality.repair.run_repair_loop"
        ) as mock_repair:
            converged, error_class, _detail, meta = _run_quality_segments(
                candidate_text="candidate",
                candidate_path="/tmp/x.md",
                raw_content="raw",
                config=None,
                content_lane="concept",
                floor_result=floor_fail,
            )

        # 整篇 reject：地板层拦下，grader/A 层/repair 全未触达
        assert converged is False, "地板 fail 必须整篇 reject"
        assert error_class == "integrity_floor_failed"
        assert meta["floor_failed"] == [FLOOR_EVIDENCE_HONESTY]
        assert meta["grader_not_called"] is True
        assert meta["weighted_score_not_computed"] is True
        assert mock_grader.call_count == 0, (
            "地板 fail 时 grader 不得被调用（满分 grader 不得有机会补偿）"
        )
        assert mock_sg.call_count == 0
        assert mock_repair.call_count == 0

    def test_floor_high_score_counterfactual_weighted_would_pass(self):
        """反事实锚点：同构满分 dimension_scores 的加权分确实过阈——
        证明上一用例拒绝的唯一原因就是地板（不存在『加权分也不过』的混淆解释）。"""
        from src.akb2.quality.weighted_score import compute_weighted_score

        r = compute_weighted_score(_max_dimension_scores("concept"), "concept")
        assert r.score >= r.threshold and r.passed is True, (
            f"满分加权分必须过阈（否则 reject 无法归因于地板）: {r.score} < {r.threshold}"
        )

    def test_floor_pass_same_high_score_accepted_mutation_contrast(self):
        """mutation 对照分支：同 extract 修好 honesty（地板 pass）+ 同满分 grader
        → converged=True。与 reject 分支构成判别对——若有人删掉 runner 的地板
        early-return，reject 分支将走到 grader（grader_not_called 断言变红）。"""
        from src.akb2.quality.grader import GraderResult
        from src.akb2.quality.runner import _run_quality_segments

        floor_pass = evaluate_integrity_floor(
            _concept_extract_all_floor_pass(), "concept", raw_anchors=set(_RAW_ANCHORS)
        )
        assert floor_pass.passed is True

        max_grader = GraderResult(
            defects=[],
            model_used="gpt-5.4",
            suspended=False,
            content_lane="concept",
            dimension_scores=_max_dimension_scores("concept"),
            interpretation_entailment=True,
        )
        with patch(
            "src.akb2.quality.checks_structural.run_candidate_self_gate", return_value=[]
        ), patch(
            "src.akb2.quality.grader.run_grader_detailed", return_value=max_grader
        ) as mock_grader, patch(
            "src.akb2.quality.repair.run_repair_loop"
        ) as mock_repair:
            converged, error_class, _detail, _meta = _run_quality_segments(
                candidate_text="candidate",
                candidate_path="/tmp/x.md",
                raw_content="raw",
                config=None,
                content_lane="concept",
                floor_result=floor_pass,
            )

        assert converged is True, f"地板 pass + 满分 grader 必须通过: {error_class}"
        assert mock_grader.call_count == 1
        assert mock_repair.call_count == 0

    def test_floor_procedural_member_fail_also_rejects_before_grader(self):
        """procedural 车道同样不可补偿：F4(H1 缺失) fail → grader 不调用。"""
        from src.akb2.quality.grader import GraderResult
        from src.akb2.quality.runner import _run_quality_segments

        extract = _fail_member_extract("procedural", "core_value_f4")
        floor_fail = evaluate_integrity_floor(
            extract, "procedural", raw_anchors=set(_RAW_ANCHORS)
        )
        assert floor_fail.failed_floors == [FLOOR_CORE_VALUE_F4]

        max_grader = GraderResult(
            defects=[],
            model_used="gpt-5.4",
            suspended=False,
            content_lane="procedural",
            dimension_scores=_max_dimension_scores("procedural"),
        )
        with patch(
            "src.akb2.quality.checks_structural.run_candidate_self_gate"
        ) as mock_sg, patch(
            "src.akb2.quality.grader.run_grader_detailed", return_value=max_grader
        ) as mock_grader, patch(
            "src.akb2.quality.repair.run_repair_loop"
        ) as mock_repair:
            converged, error_class, _detail, meta = _run_quality_segments(
                candidate_text="candidate",
                candidate_path="/tmp/x.md",
                raw_content="raw",
                config=None,
                content_lane="procedural",
                floor_result=floor_fail,
            )

        assert converged is False
        assert error_class == "integrity_floor_failed"
        assert meta["grader_not_called"] is True
        assert meta["weighted_score_not_computed"] is True
        assert mock_grader.call_count == 0
        assert mock_sg.call_count == 0
        assert mock_repair.call_count == 0

    @pytest.mark.parametrize(
        "member_key,floor_key",
        _FLOOR_MEMBERS,
    )
    def test_floor_any_member_rejects_without_weighted_score(self, member_key, floor_key):
        """SC2 主断言（全成员 × concept）：任一地板成员 fail 即 reject，
        grader/加权分不参与——不可补偿对四个成员逐一成立。"""
        from src.akb2.quality.grader import GraderResult
        from src.akb2.quality.runner import _run_quality_segments

        extract = _fail_member_extract("concept", member_key)
        floor_fail = evaluate_integrity_floor(
            extract, "concept", raw_anchors=set(_RAW_ANCHORS)
        )
        assert floor_fail.failed_floors == [floor_key]

        max_grader = GraderResult(
            defects=[],
            model_used="gpt-5.4",
            suspended=False,
            content_lane="concept",
            dimension_scores=_max_dimension_scores("concept"),
            interpretation_entailment=True,
        )
        with patch(
            "src.akb2.quality.checks_structural.run_candidate_self_gate"
        ) as mock_sg, patch(
            "src.akb2.quality.grader.run_grader_detailed", return_value=max_grader
        ) as mock_grader, patch(
            "src.akb2.quality.repair.run_repair_loop"
        ):
            converged, error_class, _detail, meta = _run_quality_segments(
                candidate_text="candidate",
                candidate_path="/tmp/x.md",
                raw_content="raw",
                config=None,
                content_lane="concept",
                floor_result=floor_fail,
            )

        assert converged is False
        assert error_class == "integrity_floor_failed"
        assert meta["floor_failed"] == [floor_key]
        assert meta["grader_not_called"] is True
        assert meta["weighted_score_not_computed"] is True
        assert mock_grader.call_count == 0
        assert mock_sg.call_count == 0


# ---------------------------------------------------------------------------
# 地板结果对象语义（failed_floors 机读 key 与 rubric.yaml 地板 4 项对齐）
# ---------------------------------------------------------------------------


class TestFloorResultContract:
    def test_floor_failed_floors_keys_are_machine_names(self):
        """failed_floors 用机读 key（snake_case），不混入 rubric 中文名。"""
        extract = _fail_member_extract("concept", "evidence_honesty")
        r = evaluate_integrity_floor(extract, "concept", raw_anchors=set(_RAW_ANCHORS))
        assert r.failed_floors == ["evidence_honesty"]
        assert r.content_lane == "concept"
        assert isinstance(r, FloorResult)

    def test_floor_reason_visible_on_fail(self):
        """失败原因非空（fail-visible，宪法 P4）。"""
        extract = _fail_member_extract("procedural", "claim_anchor_rate")
        r = evaluate_integrity_floor(extract, "procedural", raw_anchors=set(_RAW_ANCHORS))
        assert r.passed is False
        assert r.reason.strip() != ""
