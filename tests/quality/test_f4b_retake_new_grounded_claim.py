"""test_f4b_retake_new_grounded_claim.py — F4-B retake 补 grounded claim 语义测试。

F4-B:retake 死结(指回真实 raw 锚但该处无 grounded claim)→ 允许 LLM 返回
new_grounded_claim={text,anchor} verbatim 引文,B1 强校验后作新 source_grounded claim 追加。

覆盖:
    - verbatim 引文被收 + traceability 过(interpretation.traces_to 指向新 grounded claim)
    - 改写引文(非 verbatim,覆盖 <60%)被拒(fail-closed 不造假)
    - 假锚(不在 raw 声明集)被拒
    - 原重指路径(traces_to 指向已有 grounded)仍工作(F3-A 不回归)
    - 诊断字段:accepted_new_claim/rejected_new_claim/llm_return_summary
"""

from __future__ import annotations

from typing import Any

from src.akb2.quality.distill import retake_traceability
from src.akb2.quality.integrity_floor import (
    FLOOR_INTERPRETATION_TRACEABILITY,
    evaluate_integrity_floor,
)


class _MockResp:
    def __init__(self, content: Any):
        self.content = content


# raw:§E_CLAIMS 是真实锚但该处无 grounded claim(只有 §A 有 grounded)
_RAW = """\
## 锚点目录

| §A | x |

---

§A_CONTENT_START

grounded claim here.

§E_CLAIMS

The model that produces content must be different from the model that judges it.
"""


def _extract_with_interp_pointing_wrong_anchor() -> dict:
    """1 source_grounded(§A) + 1 interp traces_to=['§E_CLAIMS'](指错:§E 无 grounded claim)。"""
    return {
        "claims": [
            {"text": "grounded A", "anchor": "§A", "provenance": "source_grounded",
             "evidence_gap": "未提供 X"},
            {"text": "interp about §E", "anchor": "§E_CLAIMS", "provenance": "akb_interpretation",
             "evidence_gap": "未覆盖 Y", "traces_to": ["§E_CLAIMS"]},
        ],
        "argumentation": [],
        "mechanism_components": [
            {"name": "Comp A", "role": "x"}, {"name": "Comp B", "role": "y"},
            {"name": "Comp C", "role": "z"},
        ],
        "causal_edges": [
            {"from": "Comp A", "to": "Comp B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B", "evidence_anchor": "§A"},
        ],
        "how_steps": [],
    }


def _patch_route_llm(monkeypatch, retake_payload: Any):
    import src.akb2.quality.llm_router as lr

    def _fake(*a, **kw):
        return _MockResp(retake_payload)
    monkeypatch.setattr(lr, "route_llm", _fake, raising=False)


class TestF4BNewGroundedClaim:
    def test_verbatim_quote_accepted_traceability_passes(self, monkeypatch):
        """verbatim 引文(≥60% 覆盖 raw §E_CLAIMS block)→ 收为新 grounded claim + traces_to 指向它。"""
        data = _extract_with_interp_pointing_wrong_anchor()
        # LLM 返回 new_grounded_claim:text 是 §E_CLAIMS block 的逐字引文
        _patch_route_llm(
            monkeypatch,
            {"retake": [{
                "claim_index": 1, "traces_to": [], "provenance": "akb_interpretation",
                "new_grounded_claim": {
                    "text": "The model that produces content must be different from the model that judges it.",
                    "anchor": "§E_CLAIMS",
                },
            }]},
        )
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is True
        assert result["diagnostics"]["accepted_new_claim"] == 1
        # 新 grounded claim 追加
        assert any(
            c.get("provenance") == "source_grounded" and c.get("anchor") == "§E_CLAIMS"
            for c in data["claims"]
        )
        # interp traces_to 指向新 grounded claim 的锚
        assert data["claims"][1]["traces_to"] == ["§E_CLAIMS"]
        # floor traceability 过
        floor = evaluate_integrity_floor(data, "concept", raw_anchors={"§A", "§E_CLAIMS"})
        assert FLOOR_INTERPRETATION_TRACEABILITY not in floor.failed_floors

    def test_paraphrased_quote_rejected(self, monkeypatch):
        """改写引文(非 verbatim,覆盖 <60%)→ 拒(fail-closed 不造假)。"""
        data = _extract_with_interp_pointing_wrong_anchor()
        _patch_route_llm(
            monkeypatch,
            {"retake": [{
                "claim_index": 1, "traces_to": [], "provenance": "akb_interpretation",
                "new_grounded_claim": {
                    "text": "The maker and checker should use different model families for fairness.",
                    "anchor": "§E_CLAIMS",
                },
            }]},
        )
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is False  # 改写,拒
        assert result["diagnostics"]["rejected_new_claim"] == 1
        # 无新 grounded claim 追加
        assert not any(
            c.get("provenance") == "source_grounded" and c.get("anchor") == "§E_CLAIMS"
            for c in data["claims"]
        )

    def test_fake_anchor_rejected(self, monkeypatch):
        """假锚(不在 raw 声明集)→ 拒。"""
        data = _extract_with_interp_pointing_wrong_anchor()
        _patch_route_llm(
            monkeypatch,
            {"retake": [{
                "claim_index": 1, "traces_to": [], "provenance": "akb_interpretation",
                "new_grounded_claim": {
                    "text": "The model that produces content must be different.",
                    "anchor": "§Z_FAKE",
                },
            }]},
        )
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is False
        assert result["diagnostics"]["rejected_new_claim"] == 1

    def test_single_letter_anchor_resolved_for_new_claim(self, monkeypatch):
        """new_grounded_claim 的 anchor 单字母 §E → resolve 为全名 §E_CLAIMS(同 F3-A 逻辑)。"""
        data = _extract_with_interp_pointing_wrong_anchor()
        _patch_route_llm(
            monkeypatch,
            {"retake": [{
                "claim_index": 1, "traces_to": [], "provenance": "akb_interpretation",
                "new_grounded_claim": {
                    "text": "The model that produces content must be different from the model that judges it.",
                    "anchor": "§E",  # 单字母
                },
            }]},
        )
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is True
        assert result["diagnostics"]["accepted_new_claim"] == 1
        # 新 claim 的 anchor 是全名 §E_CLAIMS(resolve 后)
        assert any(
            c.get("anchor") == "§E_CLAIMS" and c.get("provenance") == "source_grounded"
            for c in data["claims"]
        )


class TestF3ARetakePathStillWorks:
    """F4-B 不破坏 F3-A 原重指路径(traces_to 指向已有 grounded anchor)。"""

    def test_traces_to_repoint_still_works(self, monkeypatch):
        """interp traces_to 指错 → LLM 返 traces_to=['§A'](已有 grounded)→ F3-A 重指仍工作。"""
        data = _extract_with_interp_pointing_wrong_anchor()
        _patch_route_llm(
            monkeypatch,
            {"retake": [{
                "claim_index": 1, "traces_to": ["§A"], "provenance": "akb_interpretation",
            }]},
        )
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is True
        assert result["diagnostics"]["accepted_traces_to"] == 1
        assert result["diagnostics"]["accepted_new_claim"] == 0  # 走的是重指路径
        assert data["claims"][1]["traces_to"] == ["§A"]
