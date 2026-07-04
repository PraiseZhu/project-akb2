"""test_f6b_floating_interp_drop.py — F6-B 飘浮 interpretation 有界丢弃测试。

覆盖:
    - 无据 interp(确定性+LLM 都救不回)被移除 + floor 过 + diag 计数
    - 有据 interp(有 traces_to)不动
    - 全移空后 claims 非空(有 source_grounded)→ floor 行为不回归
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


_RAW = """\
## 锚点目录

| §A | x |

---

§A_CONTENT_START

grounded claim here.
"""


def _extract(grounded: bool = True, interp_count: int = 1) -> dict:
    """1 source_grounded(§A) + N interp(无 traces_to,无 raw 重合——LLM 也救不回)。"""
    claims = []
    if grounded:
        claims.append({"text": "grounded A", "anchor": "§A", "provenance": "source_grounded",
                       "evidence_gap": "未提供 X"})
    for i in range(interp_count):
        claims.append({"text": f"completely unrelated interp {i} xyz unique", "anchor": "§A",
                       "provenance": "akb_interpretation", "evidence_gap": "未覆盖 Y"})
    return {
        "claims": claims,
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


class TestF6BFloatingInterpDrop:
    def test_floating_interp_dropped_floor_passes(self, monkeypatch):
        """无据 interp(确定性+LLM 都救不回)被移除 + floor traceability 过。"""
        data = _extract(grounded=True, interp_count=1)
        # LLM 返空(救不回)
        _patch_route_llm(monkeypatch, {"retake": [{"claim_index": 1, "traces_to": [], "provenance": "akb_interpretation"}]})
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is True
        assert result["diagnostics"]["dropped_floating_interpretations"] == 1
        # interp 被移除,只剩 source_grounded
        assert len(data["claims"]) == 1
        assert data["claims"][0]["provenance"] == "source_grounded"
        # floor traceability 过(无 interp 需检查)
        floor = evaluate_integrity_floor(data, "concept", raw_anchors={"§A"})
        assert FLOOR_INTERPRETATION_TRACEABILITY not in floor.failed_floors

    def test_grounded_interp_not_dropped(self, monkeypatch):
        """有 traces_to 的 interp(被 LLM 救回)→ 不移除。"""
        data = _extract(grounded=True, interp_count=1)
        # LLM 救回(补 traces_to=['§A'])
        _patch_route_llm(monkeypatch, {"retake": [{"claim_index": 1, "traces_to": ["§A"], "provenance": "akb_interpretation"}]})
        result = retake_traceability(data, _RAW, "concept")
        assert result["diagnostics"]["dropped_floating_interpretations"] == 0
        # interp 保留(有 traces_to)
        assert len(data["claims"]) == 2
        assert data["claims"][1].get("traces_to") == ["§A"]

    def test_all_interp_dropped_claims_nonempty(self, monkeypatch):
        """全 interp 移除 → claims 还有 source_grounded(不归空)。"""
        data = _extract(grounded=True, interp_count=3)
        _patch_route_llm(monkeypatch, {"retake": []})
        result = retake_traceability(data, _RAW, "concept")
        assert result["diagnostics"]["dropped_floating_interpretations"] == 3
        # claims 还有 source_grounded
        assert len(data["claims"]) == 1
        assert data["claims"][0]["provenance"] == "source_grounded"

    def test_f7a_mispointed_traces_to_dropped(self, monkeypatch):
        """F7-A: traces_to 非空但指向非 grounded 锚(§J_DISCUSSION)→ 救济链走完仍无据 → 丢弃。
        probe-p6 vercel 死因:§J_DISCUSSION 指向真实 raw 锚但该处无 grounded claim,
        LLM 返空 traces_to 也没改 → 原 F6-B 条件 `if not tt_set` 漏了(非空但指错)。"""
        data = {
            "claims": [
                {"text": "grounded A", "anchor": "§A", "provenance": "source_grounded",
                 "evidence_gap": "未提供 X"},
                {"text": "interp about §J", "anchor": "§J_DISCUSSION",
                 "provenance": "akb_interpretation", "evidence_gap": "未覆盖 Y",
                 "traces_to": ["§J_DISCUSSION"]},  # 指向真实 raw 锚但非 grounded
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
        # LLM 返空(救不回)
        _patch_route_llm(monkeypatch, {"retake": [{"claim_index": 1, "traces_to": [], "provenance": "akb_interpretation"}]})
        result = retake_traceability(data, _RAW, "concept")
        # F7-A:traces_to 非空但指向非 grounded → 丢弃(原 F6-B 漏了,只丢空 traces_to)
        assert result["diagnostics"]["dropped_floating_interpretations"] == 1
        assert result["changed"] is True
        assert len(data["claims"]) == 1  # interp 被移除
        assert data["claims"][0]["provenance"] == "source_grounded"
        # floor traceability 过(无 interp 需检查)
        floor = evaluate_integrity_floor(data, "concept", raw_anchors={"§A"})
        assert FLOOR_INTERPRETATION_TRACEABILITY not in floor.failed_floors
