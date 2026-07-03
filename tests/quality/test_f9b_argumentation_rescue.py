"""test_f9b_argumentation_rescue.py — F9-B 救济链覆盖 argumentation 测试。

probe-p7 memory/crewai 死因:floor "akb_interpretation[N] 缺 traces_to" 指的是
argumentation 条目(provenance=akb_interpretation 缺 traces_to),但旧 retake_traceability
只救 claims 侧,argumentation 飘浮无人救 → floor 持续 fail。

F9-B:retake_traceability 收集/摘句/LLM/丢弃四步全扩展到 argumentation。
floor interpretation 索引口径=claims interp + argumentation interp 合并列表。

覆盖:
    - argumentation 飘浮被确定性摘句救(arg.traces_to 指向新 grounded claim)
    - argumentation 救不回被丢(从 argumentation 移除,diag dropped_arg 计数)
    - floor 重评过(interpretation_traceability 不在 failed_floors)
    - claims 侧行为不回归(同 F5-B 数据,claims 侧仍工作)
    - LLM retake schema argumentation_retake 项被接受
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


# raw:§E_CLAIMS block 有 "model produces content" 句;§A 有 grounded claim
_RAW = """\
## 锚点目录

| §A | x |

---

§A_CONTENT_START

grounded claim here.

§E_CLAIMS

The model that produces content must be different from the model that judges it.
"""

_RAW_NO_OVERLAP = """\
## 锚点目录

| §A | x |

---

§A_CONTENT_START

grounded claim here.

§E_CLAIMS

Lorem ipsum dolor sit amet consectetur adipiscing elit.
"""


def _extract_with_interp_arg(traces_to: list | None = None) -> dict:
    """1 source_grounded(§A) + 1 interp argumentation(traces_to 可配,指错 §E)。

    argumentation 用 explanation 字段(非 text),claim_anchor=§E_CLAIMS。
    """
    arg: dict = {
        "explanation": (
            "The model that produces content must be different from the model "
            "that judges it."
        ),
        "claim_anchor": "§E_CLAIMS",
        "provenance": "akb_interpretation",
    }
    if traces_to is not None:
        arg["traces_to"] = traces_to
    return {
        "claims": [
            {"text": "grounded A", "anchor": "§A", "provenance": "source_grounded",
             "evidence_gap": "未提供 X"},
        ],
        "argumentation": [arg],
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


class TestF9BArgumentationDeterministicBackfill:
    def test_arg_overlap_backfilled_traceability_passes(self, monkeypatch):
        """argumentation 飘浮(指错 §E)→ 确定性摘句救回(arg.traces_to 指向新 grounded)。"""
        data = _extract_with_interp_arg(traces_to=["§E_CLAIMS"])  # 指错(§E 无 grounded)
        _patch_route_llm(monkeypatch, {"retake": [], "argumentation_retake": []})
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is True
        assert result["diagnostics"]["arg_deterministic_backfill_count"] == 1
        assert result["diagnostics"]["llm_called"] is False  # 确定性补了,不调 LLM
        # 新 grounded claim 追加到 claims
        assert any(
            c.get("provenance") == "source_grounded" and c.get("anchor") == "§E_CLAIMS"
            for c in data["claims"]
        )
        # argumentation traces_to 指向新 grounded claim
        assert data["argumentation"][0]["traces_to"] == ["§E_CLAIMS"]
        # floor traceability 过(claims+arg 合并列表,全有 traces_to 指向 grounded)
        floor = evaluate_integrity_floor(data, "concept", raw_anchors={"§A", "§E_CLAIMS"})
        assert FLOOR_INTERPRETATION_TRACEABILITY not in floor.failed_floors

    def test_arg_no_overlap_dropped(self, monkeypatch):
        """argumentation 无重合(§E block lorem ipsum)→ 确定性补不了 + LLM 救不回 → 丢。"""
        data = _extract_with_interp_arg(traces_to=["§E_CLAIMS"])
        # LLM 返空(救不回)
        _patch_route_llm(monkeypatch, {"retake": [], "argumentation_retake": []})
        result = retake_traceability(data, _RAW_NO_OVERLAP, "concept")
        assert result["changed"] is True
        assert result["diagnostics"]["dropped_floating_argumentation"] == 1
        # argumentation 被移除
        assert len(data["argumentation"]) == 0
        # floor 过(无 interp arg 需检查,claims grounded 还在)
        floor = evaluate_integrity_floor(data, "concept", raw_anchors={"§A"})
        assert FLOOR_INTERPRETATION_TRACEABILITY not in floor.failed_floors

    def test_arg_empty_traces_to_searches_all_blocks(self, monkeypatch):
        """argumentation traces_to 空 → 在全 raw 找重合 block 摘句(同 claims F5-B)。"""
        data = _extract_with_interp_arg(traces_to=None)
        _patch_route_llm(monkeypatch, {"retake": [], "argumentation_retake": []})
        result = retake_traceability(data, _RAW, "concept")
        assert result["diagnostics"]["arg_deterministic_backfill_count"] >= 1
        assert result["changed"] is True


class TestF9BArgumentationLLMRetake:
    def test_arg_llm_repoint_accepted(self, monkeypatch):
        """LLM argumentation_retake 重指 §A(已有 grounded)→ 接受。"""
        data = _extract_with_interp_arg(traces_to=["§E_CLAIMS"])
        _patch_route_llm(
            monkeypatch,
            {"retake": [], "argumentation_retake": [
                {"arg_index": 0, "traces_to": ["§A"], "provenance": "akb_interpretation"},
            ]},
        )
        result = retake_traceability(data, _RAW_NO_OVERLAP, "concept")
        # 确定性补不了(无重合)→ 走 LLM
        assert result["diagnostics"]["llm_called"] is True
        assert result["diagnostics"]["arg_accepted_count"] == 1
        assert data["argumentation"][0]["traces_to"] == ["§A"]
        # floor 过
        floor = evaluate_integrity_floor(data, "concept", raw_anchors={"§A"})
        assert FLOOR_INTERPRETATION_TRACEABILITY not in floor.failed_floors

    def test_arg_llm_new_grounded_claim_accepted(self, monkeypatch):
        """LLM 返 new_grounded_claim(verbatim 引文)→ 接受为新 grounded + arg.traces_to 指向。

        explanation 用改写(与 §E block 无 token 重合→确定性补不了走 LLM);LLM 返该锚
        位置的逐字原文(B1 verbatim 校验通过)。
        """
        data = _extract_with_interp_arg(traces_to=["§E_CLAIMS"])
        # explanation 改为改写(无重合)→ 确定性补不了
        data["argumentation"][0]["explanation"] = "Maker and checker separation is required for fairness"
        _patch_route_llm(
            monkeypatch,
            {"retake": [], "argumentation_retake": [
                {"arg_index": 0, "traces_to": [], "provenance": "akb_interpretation",
                 "new_grounded_claim": {
                     "text": "The model that produces content must be different from the model that judges it.",
                     "anchor": "§E_CLAIMS",
                 }},
            ]},
        )
        result = retake_traceability(data, _RAW, "concept")  # _RAW §E block 有该逐字句
        assert result["diagnostics"]["llm_called"] is True
        assert result["diagnostics"]["accepted_new_claim"] >= 1
        # 新 grounded claim 追加
        assert any(
            c.get("provenance") == "source_grounded" and c.get("anchor") == "§E_CLAIMS"
            for c in data["claims"]
        )
        assert data["argumentation"][0]["traces_to"] == ["§E_CLAIMS"]


class TestF9BClaimsSideNoRegression:
    """claims 侧救济链行为不回归(同 F5-B 数据,claims interp 仍工作)。"""

    def test_claims_interp_still_backfilled(self, monkeypatch):
        """claims 侧 interp(无 argumentation)仍走确定性 backfill。"""
        interp_claim = {
            "text": "The model that produces content must be different from the model that judges it.",
            "anchor": "§E_CLAIMS", "provenance": "akb_interpretation",
            "evidence_gap": "未覆盖 Y", "traces_to": ["§E_CLAIMS"],
        }
        data = {
            "claims": [
                {"text": "grounded A", "anchor": "§A", "provenance": "source_grounded",
                 "evidence_gap": "未提供 X"},
                interp_claim,
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
        _patch_route_llm(monkeypatch, {"retake": []})
        result = retake_traceability(data, _RAW, "concept")
        assert result["diagnostics"]["deterministic_backfill_count"] == 1
        assert result["diagnostics"]["interp_arg_count"] == 0  # 无 arg
        assert result["changed"] is True

    def test_both_claims_and_arg_rescued(self, monkeypatch):
        """claims interp + argumentation interp 同时存在 → 两边都救回。"""
        interp_claim = {
            "text": "The model that produces content must be different from the model that judges it.",
            "anchor": "§E_CLAIMS", "provenance": "akb_interpretation",
            "evidence_gap": "未覆盖 Y", "traces_to": ["§E_CLAIMS"],
        }
        data = _extract_with_interp_arg(traces_to=["§E_CLAIMS"])
        data["claims"].append(interp_claim)
        _patch_route_llm(monkeypatch, {"retake": [], "argumentation_retake": []})
        result = retake_traceability(data, _RAW, "concept")
        assert result["diagnostics"]["deterministic_backfill_count"] == 1  # claims 侧
        assert result["diagnostics"]["arg_deterministic_backfill_count"] == 1  # arg 侧
        assert result["changed"] is True
        # floor 过(两边都有 traces_to)
        floor = evaluate_integrity_floor(data, "concept", raw_anchors={"§A", "§E_CLAIMS"})
        assert FLOOR_INTERPRETATION_TRACEABILITY not in floor.failed_floors
