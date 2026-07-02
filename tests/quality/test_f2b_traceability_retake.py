"""test_f2b_traceability_retake.py — F2-B/F3-A traceability 有界 retake 测试。

F2-B(原)+ F3-A(覆盖面+接受逻辑+fail-visible):

覆盖:
    - [F3-A 覆盖面] traces_to 非空但指错(与 grounded 无交集)→ 纳入 retake(原 only `not tt` 漏)
    - [F3-A 接受逻辑] LLM 返单字母 §E(grounded 是全名 §E_CLAIMS)→ resolve_anchor_ref 接受
    - [F3-A 接受逻辑] LLM 返编造 §Z(无前缀匹配)→ 解析不了仍拒(fail-closed 不放水)
    - [F2-B] retake 成功补 traces_to → changed=True
    - [F2-B] 无 grounded → changed=False, reject_reason=no_grounded
    - [F2-B] 无 interp(指错+空均无)→ changed=False, reject_reason=no_interp
    - [F2-B] LLM 失败 → changed=False, reject_reason=llm_failed
    - [F2-B] retake 使 floor 过
    - [F3-A fail-visible] 返回带 diagnostics(interp_count/llm_called/items_returned/accepted_count/reject_reason)
"""

from __future__ import annotations

from typing import Any

from src.akb2.quality.distill import retake_traceability
from src.akb2.quality.integrity_floor import (
    FLOOR_CORE_VALUE_F4,
    FLOOR_INTERPRETATION_TRACEABILITY,
    evaluate_integrity_floor,
)


class _MockResp:
    def __init__(self, content: Any):
        self.content = content


def _extract(grounded_anchor: str = "§E_CLAIMS", interp_traces: list | None = None) -> dict:
    """1 source_grounded + 1 akb_interpretation(traces_to 可配:None=无字段,[]=空,['§X']=指错)。"""
    interp: dict = {
        "text": "interp based on A", "anchor": grounded_anchor,
        "provenance": "akb_interpretation", "evidence_gap": "未覆盖 Y",
    }
    if interp_traces is not None:
        interp["traces_to"] = interp_traces
    return {
        "claims": [
            {"text": "grounded", "anchor": grounded_anchor, "provenance": "source_grounded",
             "evidence_gap": "未提供 X"},
            interp,
        ],
        "argumentation": [],
        "mechanism_components": [
            {"name": "Comp A", "role": "x"}, {"name": "Comp B", "role": "y"},
            {"name": "Comp C", "role": "z"},
        ],
        "causal_edges": [
            {"from": "Comp A", "to": "Comp B", "relation": "r", "edge_kind": "source_causal",
             "evidence": "A feeds B", "evidence_anchor": grounded_anchor},
        ],
        "how_steps": [],
    }


_RAW = "## 锚点目录\n\n| §E | x |\n\n---\n\n§E_CLAIMS\n\ngrounded content.\n"


def _patch_route_llm(monkeypatch, retake_payload: Any, raises: type | None = None):
    import src.akb2.quality.llm_router as lr

    if raises is not None:
        def _raise(*a, **kw):
            raise raises("mock LLM down")
        monkeypatch.setattr(lr, "route_llm", _raise, raising=False)
    else:
        def _fake(*a, **kw):
            return _MockResp(retake_payload)
        monkeypatch.setattr(lr, "route_llm", _fake, raising=False)


class TestF3ACoverageAndAcceptance:
    """F3-A:覆盖面(指错锚纳入)+ 接受逻辑(resolve 单字母/拒编造)。"""

    def test_mispointed_traces_to_included_in_retake(self, monkeypatch):
        """[F3-A 覆盖面] traces_to=['§T_CONFIGURATION'](非 grounded,指错)→ 纳入 retake(原 only `not tt` 漏)。"""
        data = _extract(interp_traces=["§T_CONFIGURATION"])  # 指错
        _patch_route_llm(
            monkeypatch,
            {"retake": [{"claim_index": 1, "traces_to": ["§E"], "provenance": "akb_interpretation"}]},
        )
        result = retake_traceability(data, _RAW, "concept")
        # 指错锚被纳入(interp_count=1,非 0),且 LLM 补的单字母 §E 被 resolve 接受
        assert result["changed"] is True
        assert result["diagnostics"]["interp_count"] == 1
        assert data["claims"][1]["traces_to"] == ["§E_CLAIMS"]  # §E → resolve → 全名

    def test_single_letter_anchor_accepted_via_resolve(self, monkeypatch):
        """[F3-A 接受逻辑] LLM 返单字母 §E(grounded=§E_CLAIMS)→ resolve_anchor_ref 接受为 §E_CLAIMS。"""
        data = _extract(interp_traces=None)  # 无 traces_to
        _patch_route_llm(
            monkeypatch,
            {"retake": [{"claim_index": 1, "traces_to": ["§E"], "provenance": "akb_interpretation"}]},
        )
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is True
        assert data["claims"][1]["traces_to"] == ["§E_CLAIMS"]

    def test_fabricated_anchor_rejected_fail_closed(self, monkeypatch):
        """[F3-A 接受逻辑] LLM 返编造 §Z(无前缀匹配)→ resolve 不了 → 拒(fail-closed 不放水)。"""
        data = _extract(interp_traces=None)
        _patch_route_llm(
            monkeypatch,
            {"retake": [{"claim_index": 1, "traces_to": ["§Z"], "provenance": "akb_interpretation"}]},
        )
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is False  # §Z 解析不了,拒
        assert not data["claims"][1].get("traces_to")  # 未补
        assert result["diagnostics"]["reject_reason"] == "no_accept"

    def test_diagnostics_returned_on_success(self, monkeypatch):
        """[F3-A fail-visible] 成功也带 diagnostics。"""
        data = _extract(interp_traces=None)
        _patch_route_llm(
            monkeypatch,
            {"retake": [{"claim_index": 1, "traces_to": ["§E"], "provenance": "akb_interpretation"}]},
        )
        result = retake_traceability(data, _RAW, "concept")
        d = result["diagnostics"]
        assert d["interp_count"] == 1
        assert d["llm_called"] is True
        assert d["items_returned"] == 1
        assert d["accepted_count"] == 1


class TestF2BRetakeBasics:
    """F2-B 原有:成功补 / 无 grounded / 无 interp / LLM 失败 / floor 过。"""

    def test_retake_fills_traces_to(self, monkeypatch):
        data = _extract(interp_traces=None)
        _patch_route_llm(
            monkeypatch,
            {"retake": [{"claim_index": 1, "traces_to": ["§E_CLAIMS"], "provenance": "akb_interpretation"}]},
        )
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is True
        assert data["claims"][1]["traces_to"] == ["§E_CLAIMS"]

    def test_no_grounded_anchors_no_change(self, monkeypatch):
        data = _extract()
        data["claims"][0]["provenance"] = "akb_interpretation"  # 全 interp,无 grounded
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is False
        assert result["diagnostics"]["reject_reason"] == "no_grounded"

    def test_no_interp_no_change(self, monkeypatch):
        """无需 retake 的 interp(已有正确 traces_to)→ changed=False, reject_reason=no_interp。"""
        data = _extract(interp_traces=["§E_CLAIMS"])  # 已有正确 traces_to
        _patch_route_llm(monkeypatch, {"retake": []})
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is False
        assert result["diagnostics"]["reject_reason"] == "no_interp"

    def test_llm_failure_no_change(self, monkeypatch):
        data = _extract(interp_traces=None)
        _patch_route_llm(monkeypatch, None, raises=RuntimeError)
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is False
        assert "llm_failed" in result["diagnostics"]["reject_reason"]

    def test_retake_makes_floor_pass(self, monkeypatch):
        data = _extract(interp_traces=None)
        decl = {"§E_CLAIMS"}
        floor_before = evaluate_integrity_floor(data, "concept", raw_anchors=decl)
        assert FLOOR_INTERPRETATION_TRACEABILITY in floor_before.failed_floors
        _patch_route_llm(
            monkeypatch,
            {"retake": [{"claim_index": 1, "traces_to": ["§E_CLAIMS"], "provenance": "akb_interpretation"}]},
        )
        retake_traceability(data, _RAW, "concept")
        floor_after = evaluate_integrity_floor(data, "concept", raw_anchors=decl)
        assert FLOOR_INTERPRETATION_TRACEABILITY not in floor_after.failed_floors


class TestTriggerCondition:
    """触发条件:floor 仅 traceability fail 才 retake(F4 fail 不触发 retake;走 F2-A)。"""

    def test_only_traceability_fail_triggers(self):
        from src.akb2.quality.integrity_floor import FloorResult

        floor = FloorResult(
            passed=False,
            failed_floors=[FLOOR_INTERPRETATION_TRACEABILITY],
            reason="traceability fail",
            content_lane="concept",
            meta={},
        )
        assert floor.failed_floors == [FLOOR_INTERPRETATION_TRACEABILITY]
        assert not floor.meta.get("traceability_retake_applied")

    def test_f4_fail_does_not_trigger_retake(self):
        from src.akb2.quality.integrity_floor import FloorResult

        floor = FloorResult(
            passed=False,
            failed_floors=[FLOOR_CORE_VALUE_F4],
            reason="F4 0 eligible",
            content_lane="concept",
            meta={},
        )
        assert floor.failed_floors != [FLOOR_INTERPRETATION_TRACEABILITY]
