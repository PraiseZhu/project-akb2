"""tests/quality/test_distill_mechanism_narrative.py — F27 机制叙述编织 + anchor 归一 + TLDR 清洗测试。

工单 F27:三刀——(1) 机制叙述编织(eligible 边 + 带证据组件织成因果叙述)
(2) anchor 缩写归一(§D → §D_ARGUMENT)(3) TLDR 表格碎片清洗。

测试覆盖(工单要求:eligible 边 ≥1 时叙述含完整因果句+锚 / 组件无边时叙述含职责+证据 /
anchor 全称一致 / TLDR 无噪音模式):
  - _render_mechanism_narrative: eligible 边因果句 / llm_evidence 也织 / 组件无边渲染职责+证据 /
    无 eligible 无带证据组件 → 空(fail-closed) / anchor 不在声明集跳过(不造假)
  - _build_mechanism_evidence_section: anchor 归一到全称(bug A)
  - _clean_tldr_noise: 分号数字对清洗(bug B)

只测确定性函数,不打真 LLM;tmp_path 不写 vault。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))

from src.akb2.quality.distill import (  # noqa: E402
    _build_mechanism_evidence_section,
    _clean_tldr_noise,
    _normalize_anchors_in_extract_data,
    _render_mechanism_narrative,
)

# raw 含声明锚点 §A / §D_ARGUMENT / §E_CLAIMS / §J_DISCUSSION(全称),LLM 可能产缩写 §D/§E/§J
_RAW = """§A
intro

§D_ARGUMENT
The Brain is the model. The Hands are sandboxes. The Session is an event log.

§E_CLAIMS
The Brain calls Hands via dispatch.

§J_DISCUSSION
Brain calls Hands via dispatch.
"""


def _comp(name: str, anchor: str, sentence: str, role: str = "") -> dict:
    return {
        "name": name,
        "role": role,
        "evidence_anchor": anchor,
        "evidence_sentence": sentence,
    }


def _edge(frm: str, to: str, kind: str, anchor: str, evidence: str, rel: str = "dispatches") -> dict:
    return {
        "from": frm,
        "to": to,
        "relation": rel,
        "edge_kind": kind,
        "evidence": evidence,
        "evidence_anchor": anchor,
    }


# ---------------------------------------------------------------------------
# 主刀:机制叙述编织
# ---------------------------------------------------------------------------


class TestMechanismNarrativeEdges:
    def test_source_causal_edge_renders_causal_sentence(self) -> None:
        """eligible source_causal 边 → 叙述含 'from rel to（证据：[raw anchor] ev）'。"""
        data = {
            "mechanism_components": [_comp("Brain", "§D_ARGUMENT", "The Brain is the model.")],
            "causal_edges": [_edge("Brain", "Hands", "source_causal", "§J_DISCUSSION", "Brain calls Hands via dispatch.")],
        }
        out = _render_mechanism_narrative(data, _RAW)
        assert "Brain dispatches Hands" in out, f"应含因果句,实际: {out}"
        assert "证据：[raw §J_DISCUSSION]" in out, "应含 anchor 证据"
        assert "Brain calls Hands" in out, "应含 evidence 原句"

    def test_llm_evidence_edge_also_woven(self) -> None:
        """F27: llm_evidence 边也织进叙述(不只 source_causal)。"""
        data = {
            "mechanism_components": [_comp("Brain", "§D_ARGUMENT", "The Brain is the model.")],
            "causal_edges": [_edge("Brain", "Hands", "llm_evidence", "§J_DISCUSSION", "Brain drives Hands.")],
        }
        out = _render_mechanism_narrative(data, _RAW)
        assert "Brain" in out and "Hands" in out, "llm_evidence 边应织进叙述"

    def test_inferred_from_structure_edge_not_woven(self) -> None:
        """inferred_from_structure 弱边(无 evidence)不织进叙述(不造假)。"""
        data = {
            "mechanism_components": [],
            "causal_edges": [_edge("A", "B", "inferred_from_structure", "§J_DISCUSSION", "", "rel")],
        }
        out = _render_mechanism_narrative(data, _RAW)
        assert out == "", "inferred 弱边不该织进叙述(无证据)"


class TestMechanismNarrativeComponents:
    def test_component_without_edge_renders_role_evidence(self) -> None:
        """F27 主刀:带证据组件无边时渲染'组件(职责,证据)'句。"""
        data = {
            "mechanism_components": [
                _comp("Brain", "§D_ARGUMENT", "The Brain is the model.", role="推理核心"),
                _comp("Hands", "§D_ARGUMENT", "The Hands are sandboxes.", role="执行环境"),
            ],
            "causal_edges": [],
        }
        out = _render_mechanism_narrative(data, _RAW)
        assert "Brain" in out and "Hands" in out, "组件应织进叙述"
        assert "推理核心" in out, "应含职责"
        assert "证据：[raw §D_ARGUMENT]" in out, "应含证据 anchor"
        assert "The Brain is the model." in out, "应含 evidence_sentence"

    def test_component_in_edge_not_duplicated(self) -> None:
        """组件已在边叙述里(from/to)→ 不重复渲染组件句。"""
        data = {
            "mechanism_components": [_comp("Brain", "§D_ARGUMENT", "The Brain is the model.")],
            "causal_edges": [_edge("Brain", "Hands", "source_causal", "§J_DISCUSSION", "Brain calls Hands.")],
        }
        out = _render_mechanism_narrative(data, _RAW)
        # Brain 在边里,不重复组件句
        assert out.count("Brain") <= 2, f"Brain 不该重复渲染,实际 count={out.count('Brain')}: {out}"

    def test_no_eligible_no_evidenced_comps_returns_empty(self) -> None:
        """无 eligible 边 + 无带证据组件 → 空(fail-closed,不硬凑)。"""
        data = {"mechanism_components": [], "causal_edges": []}
        assert _render_mechanism_narrative(data, _RAW) == ""
        # 组件无 evidence_sentence 也不织
        data2 = {"mechanism_components": [{"name": "X", "evidence_anchor": "§D_ARGUMENT"}], "causal_edges": []}
        assert _render_mechanism_narrative(data2, _RAW) == ""

    def test_anchor_not_in_decl_skipped(self) -> None:
        """anchor 不在 raw 声明集 → 跳过(不造假 anchor)。"""
        data = {
            "mechanism_components": [_comp("X", "§Z_FAKE", "fake sentence.")],
            "causal_edges": [],
        }
        out = _render_mechanism_narrative(data, _RAW)
        assert "§Z_FAKE" not in out, "不在声明集的 anchor 不该出现(不造假)"
        assert out == "", "无有效 anchor 的组件该跳过"


# ---------------------------------------------------------------------------
# bug A:anchor 缩写归一
# ---------------------------------------------------------------------------


class TestAnchorNormalization:
    def test_component_anchor_abbrev_normalized_to_full(self) -> None:
        """组件 evidence_anchor §D 归一到 §D_ARGUMENT(bug A)。"""
        data = {
            "mechanism_components": [_comp("Brain", "§D", "The Brain is the model.")],
            "causal_edges": [],
        }
        out = _build_mechanism_evidence_section(data, _RAW)
        assert "§D_ARGUMENT" in out, f"§D 应归一到 §D_ARGUMENT,实际: {out}"
        # 缩写 §D 单独出现(不在 §D_ARGUMENT 里)不该有
        # 找 evidence_anchor 列(第 4 列),应是 §D_ARGUMENT 不是 §D
        assert "| §D |" not in out, "anchor 列不该是缩写 §D"

    def test_edge_anchor_normalized(self) -> None:
        """边 evidence_anchor §J 归一到 §J_DISCUSSION。"""
        data = {
            "mechanism_components": [_comp("Brain", "§D_ARGUMENT", "Brain.")],
            "causal_edges": [_edge("Brain", "Hands", "source_causal", "§J", "calls.")],
        }
        out = _build_mechanism_evidence_section(data, _RAW)
        assert "§J_DISCUSSION" in out, "§J 应归一到 §J_DISCUSSION"

    def test_no_raw_content_keeps_original_anchor(self) -> None:
        """raw_content=None 时退原值(兼容旧调用,不造假)。"""
        data = {
            "mechanism_components": [_comp("Brain", "§D", "Brain.")],
            "causal_edges": [],
        }
        out = _build_mechanism_evidence_section(data, None)
        assert "§D" in out, "无 raw_content 时退原值"


# ---------------------------------------------------------------------------
# bug B:TLDR 表格碎片清洗
# ---------------------------------------------------------------------------


class TestTLDRClean:
    def test_clean_semicolon_digit_pairs(self) -> None:
        """分号数字对(表格碎片)清洗:'Terminal Bench 2;0' → 'Terminal Bench'。"""
        tldr = "Terminal Bench 2;0 is a benchmark."
        out = _clean_tldr_noise(tldr)
        assert "2;0" not in out, f"分号数字对该清洗,实际: {out}"
        assert "Terminal Bench" in out, "正常词不该删"

    def test_clean_multiple_semicolon_digits(self) -> None:
        """多个分号数字对都清洗(如 '13;7')。"""
        tldr = "Scores 13;7 and 2;0 are noise."
        out = _clean_tldr_noise(tldr)
        assert "13;7" not in out and "2;0" not in out
        assert "Scores" in out and "are noise" in out

    def test_normal_tldr_not_broken(self) -> None:
        """正常 TLDR(无分号数字对)不被破坏。"""
        tldr = "Long-running agents need persistence. The Brain is the model. Recovery is key."
        out = _clean_tldr_noise(tldr)
        assert out == tldr, f"正常 TLDR 不该变,实际: {out}"

    def test_collapsed_whitespace(self) -> None:
        """多余空白压成单空格。"""
        tldr = "Too   much   space"
        out = _clean_tldr_noise(tldr)
        assert out == "Too much space"


# ---------------------------------------------------------------------------
# bug A 延伸:extract_data 全量 anchor 归一(消 compose A3-锚点真实 fail)
# ---------------------------------------------------------------------------


class TestNormalizeAnchorsInExtractData:
    def test_claims_anchor_normalized(self) -> None:
        """claims.anchor §E → §E_CLAIMS(in-place)。"""
        data = {"claims": [{"text": "x", "anchor": "§E", "provenance": "source_grounded"}]}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert data["claims"][0]["anchor"] == "§E_CLAIMS", f"§E 应归一,实际 {data['claims'][0]['anchor']}"

    def test_argumentation_claim_anchor_normalized(self) -> None:
        """argumentation.claim_anchor §J → §J_DISCUSSION。"""
        data = {"argumentation": [{"claim_anchor": "§J", "explanation": "x", "provenance": "source_grounded"}]}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert data["argumentation"][0]["claim_anchor"] == "§J_DISCUSSION"

    def test_mechanism_components_anchor_normalized(self) -> None:
        """mechanism_components.evidence_anchor §D → §D_ARGUMENT。"""
        data = {"mechanism_components": [{"name": "Brain", "evidence_anchor": "§D", "evidence_sentence": "x"}]}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert data["mechanism_components"][0]["evidence_anchor"] == "§D_ARGUMENT"

    def test_causal_edges_anchor_normalized(self) -> None:
        """causal_edges.evidence_anchor §J → §J_DISCUSSION。"""
        data = {"causal_edges": [{"from": "A", "to": "B", "edge_kind": "source_causal", "evidence_anchor": "§J", "evidence": "x"}]}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert data["causal_edges"][0]["evidence_anchor"] == "§J_DISCUSSION"

    def test_full_anchor_unchanged(self) -> None:
        """已是全称的 anchor 不变(§D_ARGUMENT 保持)。"""
        data = {"claims": [{"anchor": "§D_ARGUMENT"}]}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert data["claims"][0]["anchor"] == "§D_ARGUMENT"

    def test_single_letter_anchor_A_kept(self) -> None:
        """§A 是 raw 声明全名(addy raw 声明 §A)→ 保持 §A。"""
        data = {"claims": [{"anchor": "§A"}]}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert data["claims"][0]["anchor"] == "§A"

    def test_no_raw_content_noop(self) -> None:
        """raw_content=None → 不改(兼容)。"""
        data = {"claims": [{"anchor": "§E"}]}
        _normalize_anchors_in_extract_data(data, None)
        assert data["claims"][0]["anchor"] == "§E"

    def test_fake_anchor_not_normalized(self) -> None:
        """不在声明集的 anchor 保持原值(不造假,§Z_FAKE 不归一)。"""
        data = {"claims": [{"anchor": "§Z_FAKE"}]}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert data["claims"][0]["anchor"] == "§Z_FAKE"


class TestNormTextInFields:
    """F27 bug A 延伸:evidence_sentence/explanation/claim text 里的 [raw §X] 归一。"""

    def test_claim_text_anchor_normalized(self) -> None:
        """claim text 里 [raw §E] 归一到 [raw §E_CLAIMS](消 A3 fail)。"""
        data = {"claims": [{"text": "[raw §E] The Brain is the model.", "anchor": "§E"}]}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert "[raw §E_CLAIMS]" in data["claims"][0]["text"]
        # 缩写 [raw §E] 不该残留(会被 A3 抓)
        assert "[raw §E]" not in data["claims"][0]["text"].replace("[raw §E_CLAIMS]", "")

    def test_evidence_sentence_anchor_normalized(self) -> None:
        """mechanism_components.evidence_sentence 里 [raw §D] → [raw §D_ARGUMENT]。"""
        data = {"mechanism_components": [{"name": "Brain", "evidence_anchor": "§D", "evidence_sentence": "[raw §D] Brain is model."}]}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert "[raw §D_ARGUMENT]" in data["mechanism_components"][0]["evidence_sentence"]

    def test_argumentation_explanation_anchor_normalized(self) -> None:
        """argumentation.explanation 里 [raw §J] → [raw §J_DISCUSSION]。"""
        data = {"argumentation": [{"claim_anchor": "§J", "explanation": "[raw §J] Brain calls Hands.", "provenance": "source_grounded"}]}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert "[raw §J_DISCUSSION]" in data["argumentation"][0]["explanation"]

    def test_edge_evidence_anchor_normalized(self) -> None:
        """causal_edges.evidence 里 [raw §E] → [raw §E_CLAIMS]。"""
        data = {"causal_edges": [{"from": "A", "to": "B", "edge_kind": "source_causal", "evidence_anchor": "§E", "evidence": "[raw §E] A feeds B."}]}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert "[raw §E_CLAIMS]" in data["causal_edges"][0]["evidence"]

    def test_tldr_anchor_normalized(self) -> None:
        """tldr 里 [raw §D] → [raw §D_ARGUMENT]。"""
        data = {"tldr": "[raw §D] The Brain is the model. Recovery is key."}
        _normalize_anchors_in_extract_data(data, _RAW)
        assert "[raw §D_ARGUMENT]" in data["tldr"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
