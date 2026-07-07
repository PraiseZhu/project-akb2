"""test_f29_repair_reach_evidence.py — F29 四刀行为测试(三路盲查会审后 lead 亲改)。

三路盲查(F28-A 量规对质/B repair 效能/C maker A/B)会师结论:不换 maker,修四个流程缺口:
  刀1 repair 回滚护栏只计 blocking(advisory 撑死分母+grader 换措辞制造伪新缺陷→永远回滚)
  刀2 抽取收全问题/结果侧因果证据 + relation 须被证据句直接推出
  刀3 机制叙述提为独立 H3 节(repair 整节 patch 够得着,F28-B: 四轮 changed_sections=[])
  刀4 grader H3 按成文量规判,不得要求量规外的"闭环/三元组"文体
标准零点不动:rubric/threshold/五闸/admission 判定零改动(刀4 是对齐成文判据,
F28-A 已证剔除"闭环"措辞后原 fail 判决不变)。
"""

from __future__ import annotations

from src.akb2.quality.checks_structural import Defect
from src.akb2.quality.distill import (
    _build_extract_segment_messages,
    _render_mechanism_narrative,
)
from src.akb2.quality.grader import _concept_h3_fail_note
from src.akb2.quality.repair import _blocking_defects, _defect_identity


def _d(dim: str, text: str = "d") -> Defect:
    return Defect(dimension=dim, location="x", defect=text, suggestion="s")


# ---------------------------------------------------------------------------
# 刀1: 回滚护栏 blocking 同口径(与收敛判据一致)
# ---------------------------------------------------------------------------


class TestMonotonicGuardBlockingOnly:
    """护栏现用 _defect_identity(_blocking_defects(...)) 组合——锁该组合语义。"""

    def test_advisory_not_counted_in_identity(self) -> None:
        """advisory 维(图谱可调用性/新颖增量)不进护栏 identity/count。"""
        mixed = [_d("概念机制清晰度(H3)"), _d("图谱可调用性"), _d("新颖增量")]
        blocking = _blocking_defects(mixed)
        assert len(blocking) == 1
        ids = _defect_identity(blocking)
        assert len(ids) == 1
        assert all("图谱" not in i and "新颖" not in i for i in ids)

    def test_reworded_advisory_creates_no_new_identity(self) -> None:
        """[LR 四轮 6→6 根因回归] grader 换措辞的 advisory 缺陷不再制造伪新 identity。"""
        prev = [_d("概念机制清晰度(H3)", "叙述罗列"), _d("图谱可调用性", "边为空")]
        curr = [_d("概念机制清晰度(H3)", "叙述罗列"),
                _d("图谱可调用性", "图谱边仅弱边且换了个说法")]  # advisory 措辞变了
        new_ids = (_defect_identity(_blocking_defects(curr))
                   - _defect_identity(_blocking_defects(prev)))
        assert new_ids == set(), "advisory 措辞变化不得触发回滚"

    def test_blocking_progress_visible_despite_advisory_residual(self) -> None:
        """修掉 1 条 blocking、2 条 advisory 原样残留 → count 严格下降(不回滚)。"""
        prev = [_d("概念机制清晰度(H3)", "a"), _d("TLDR密度", "b"),
                _d("图谱可调用性", "c"), _d("图谱可调用性", "c2")]
        curr = [_d("TLDR密度", "b"), _d("图谱可调用性", "c"), _d("图谱可调用性", "c2")]
        assert len(_blocking_defects(curr)) < len(_blocking_defects(prev))

    def test_guard_source_uses_blocking_composition(self) -> None:
        """源码断言:B 路护栏确实用 _blocking_defects 组合(防回退)。"""
        import inspect

        from src.akb2.quality import repair
        src = inspect.getsource(repair.run_repair_loop)
        assert "_new_blocking = _blocking_defects(current_defects)" in src
        assert "_prev_blocking = _blocking_defects(prev_defects)" in src


# ---------------------------------------------------------------------------
# 刀2: 抽取指令收全问题/结果侧证据 + relation 蕴含
# ---------------------------------------------------------------------------


class TestExtractEvidenceCoverage:
    def _prompt(self) -> str:
        msgs = _build_extract_segment_messages("how_params", "原文")
        return next(m["content"] for m in msgs if m["role"] == "system")

    def test_relation_entailment_required(self) -> None:
        sp = self._prompt()
        assert "直接读出" in sp
        assert "spawns" in sp  # SW-Lifecycle 实锤反例进 prompt
        assert "不得语义跳跃" in sp

    def test_problem_and_outcome_edges_required(self) -> None:
        sp = self._prompt()
        assert "问题陈述" in sp and "量化结果" in sp
        assert "问题→机制" in sp and "机制→结果" in sp

    def test_anti_fabrication_retained(self) -> None:
        """宁少勿强原句仍在(收证据 ≠ 放开编造)。"""
        sp = self._prompt()
        assert "宁少勿强" in sp and "不编造" in sp


# ---------------------------------------------------------------------------
# 刀3: 机制叙述独立 H3 节
# ---------------------------------------------------------------------------


class TestNarrativeAsH3Section:
    def test_narrative_renders_as_h3(self) -> None:
        data = {
            "mechanism_components": [
                {"name": "Brain", "role": "调度", "evidence_anchor": "§A",
                 "evidence_sentence": "The Brain is the model."},
            ],
            "causal_edges": [
                {"from": "Brain", "to": "Hands", "relation": "calls",
                 "edge_kind": "source_causal", "evidence": "Brain calls Hands.",
                 "evidence_anchor": "§A"},
            ],
        }
        raw = "## 锚点目录\n\n| §A | x |\n\n## §A\n\nBrain calls Hands."
        out = _render_mechanism_narrative(data, raw)
        assert out.strip().startswith("### 机制叙述"), (
            "F29: 须为 H3 节,repair 整节 patch 才够得着(F28-B 根因)"
        )

    def test_empty_material_still_failclosed_empty(self) -> None:
        out = _render_mechanism_narrative({"mechanism_components": [],
                                           "causal_edges": []}, "raw")
        assert out == ""


# ---------------------------------------------------------------------------
# 刀4: grader H3 判据边界(对齐成文量规,非放宽)
# ---------------------------------------------------------------------------


class TestGraderRubricBoundary:
    def test_boundary_clause_present(self) -> None:
        note = _concept_h3_fail_note("concept")
        assert "闭环" in note and "不构成 defect" in note
        assert "relation 可从 evidence 句直接推出" in note

    def test_old_fail_conditions_retained(self) -> None:
        """严格度不降:原 fail 条件逐字仍在。"""
        note = _concept_h3_fail_note("concept")
        assert "inferred_from_structure 弱边" in note
        assert "无 evidence 的边不作机制清晰度证据" in note
        assert "evidence + evidence_anchor" in note

    def test_non_concept_lane_empty(self) -> None:
        assert _concept_h3_fail_note("procedural") == ""
