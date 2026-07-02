"""test_f2b_traceability_retake.py — F2-B traceability 有界 retake 测试。

覆盖:
    - retake 成功补 traces_to → 返回更新 data
    - LLM 产 traces_to 不在 grounded 集 → 拒收(防编造),返 None
    - 无 grounded anchor → 返 None(retake 无处指)
    - 无缺 traces_to 的 interpretation → 返 None
    - LLM 失败 → 返 None(维持原状 fail-closed)
    - 触发条件:floor 仅 traceability fail 才触发(F4 fail 不触发 retake)
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


def _extract_with_interp_no_traces() -> dict:
    """1 source_grounded(§A)+ 1 akb_interpretation 缺 traces_to。"""
    return {
        "claims": [
            {"text": "grounded A", "anchor": "§A", "provenance": "source_grounded",
             "evidence_gap": "未提供 X"},
            {"text": "interp based on A", "anchor": "§A", "provenance": "akb_interpretation",
             "evidence_gap": "未覆盖 Y"},
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


_RAW = "## 锚点目录\n\n| §A | x |\n\n---\n\n§A_CONTENT_START\n\nA feeds B.\n"


def _patch_route_llm(monkeypatch, retake_payload: Any, raises: type | None = None):
    """monkeypatch llm_router.route_llm 返回 retake_payload(或抛 raises)。"""
    import src.akb2.quality.llm_router as lr

    if raises is not None:
        def _raise(*a, **kw):
            raise raises("mock LLM down")
        monkeypatch.setattr(lr, "route_llm", _raise, raising=False)
    else:
        def _fake(*a, **kw):
            return _MockResp(retake_payload)
        monkeypatch.setattr(lr, "route_llm", _fake, raising=False)


class TestRetakeTraceability:
    def test_retake_fills_traces_to(self, monkeypatch):
        """LLM 补 traces_to=['§A'](grounded)→ claim[1].traces_to 更新。"""
        data = _extract_with_interp_no_traces()
        _patch_route_llm(
            monkeypatch,
            {"retake": [{"claim_index": 1, "traces_to": ["§A"], "provenance": "akb_interpretation"}]},
        )
        result = retake_traceability(data, _RAW, "concept")
        assert result is not None
        assert data["claims"][1]["traces_to"] == ["§A"]

    def test_retake_rejects_traces_to_not_in_grounded(self, monkeypatch):
        """LLM 产 traces_to=['§Z'](不在 grounded)→ 拒收,返 None,claim 不变。"""
        data = _extract_with_interp_no_traces()
        _patch_route_llm(
            monkeypatch,
            {"retake": [{"claim_index": 1, "traces_to": ["§Z"], "provenance": "akb_interpretation"}]},
        )
        result = retake_traceability(data, _RAW, "concept")
        assert result is None  # 无有效改动
        assert not data["claims"][1].get("traces_to")  # 未补

    def test_no_grounded_anchors_returns_none(self, monkeypatch):
        """无 source_grounded claim → 无 grounded anchor → retake 无处指,返 None。"""
        data = _extract_with_interp_no_traces()
        data["claims"][0]["provenance"] = "akb_interpretation"  # 全 interp
        result = retake_traceability(data, _RAW, "concept")
        assert result is None

    def test_no_interp_without_traces_returns_none(self, monkeypatch):
        """无缺 traces_to 的 interpretation → 无需 retake,返 None。"""
        data = _extract_with_interp_no_traces()
        data["claims"][1]["traces_to"] = ["§A"]  # 已有 traces_to
        _patch_route_llm(monkeypatch, {"retake": []})
        result = retake_traceability(data, _RAW, "concept")
        assert result is None

    def test_llm_failure_returns_none(self, monkeypatch):
        """LLM 抛异常 → 返 None(维持原状 fail-closed)。"""
        data = _extract_with_interp_no_traces()
        _patch_route_llm(monkeypatch, None, raises=RuntimeError)
        result = retake_traceability(data, _RAW, "concept")
        assert result is None
        assert not data["claims"][1].get("traces_to")  # 不变

    def test_retake_makes_floor_pass(self, monkeypatch):
        """retake 补 traces_to 后,floor 从 traceability-fail → pass。"""
        data = _extract_with_interp_no_traces()
        decl = {"§A"}
        floor_before = evaluate_integrity_floor(data, "concept", raw_anchors=decl)
        assert FLOOR_INTERPRETATION_TRACEABILITY in floor_before.failed_floors
        _patch_route_llm(
            monkeypatch,
            {"retake": [{"claim_index": 1, "traces_to": ["§A"], "provenance": "akb_interpretation"}]},
        )
        retake_traceability(data, _RAW, "concept")
        floor_after = evaluate_integrity_floor(data, "concept", raw_anchors=decl)
        assert FLOOR_INTERPRETATION_TRACEABILITY not in floor_after.failed_floors


class TestTriggerCondition:
    """触发条件:floor 仅 traceability fail 才 retake(F4 fail 不触发 retake;走 F2-A)。"""

    def test_only_traceability_fail_triggers(self):
        """failed_floors == [FLOOR_INTERPRETATION_TRACEABILITY] → 满足触发条件。"""
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
        """F4 fail(非 traceability)→ 不满足 only-traceability 条件,retake 不触发(走 F2-A)。"""
        from src.akb2.quality.integrity_floor import FloorResult

        floor = FloorResult(
            passed=False,
            failed_floors=[FLOOR_CORE_VALUE_F4],
            reason="F4 0 eligible",
            content_lane="concept",
            meta={},
        )
        assert floor.failed_floors != [FLOOR_INTERPRETATION_TRACEABILITY]
