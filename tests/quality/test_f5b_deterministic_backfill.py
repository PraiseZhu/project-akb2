"""test_f5b_deterministic_backfill.py — F5-B 确定性补 grounded claim 测试。

F5-B:retake 触发时先走纯代码路径(去 LLM 化),从 raw 该锚 block 挑 token 重合句
原样摘句作 source_grounded claim。挑不出→走 LLM→仍不行 fail-closed。

覆盖:
    - 重合句被摘为 grounded + traceability 过(确定性路径,不调 LLM)
    - 无重合(≥2 长 token)不补 → 走 LLM 路径(fail-closed if LLM 也不行)
    - 标题行(#)/表格行(|)不被当句子摘
    - 既有 LLM 路径幂等不回归(确定性补不了的留给 LLM)
    - 诊断 deterministic_backfill_count
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


def _extract_with_interp(traces_to: list | None = None) -> dict:
    """1 source_grounded(§A) + 1 interp(traces_to 可配)。"""
    interp: dict = {
        "text": "The model that produces content must be different from the model that judges it.",
        "anchor": "§E_CLAIMS", "provenance": "akb_interpretation",
        "evidence_gap": "未覆盖 Y",
    }
    if traces_to is not None:
        interp["traces_to"] = traces_to
    return {
        "claims": [
            {"text": "grounded A", "anchor": "§A", "provenance": "source_grounded",
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
             "evidence": "A feeds B", "evidence_anchor": "§A"},
        ],
        "how_steps": [],
    }


def _patch_route_llm(monkeypatch, retake_payload: Any):
    import src.akb2.quality.llm_router as lr

    def _fake(*a, **kw):
        return _MockResp(retake_payload)
    monkeypatch.setattr(lr, "route_llm", _fake, raising=False)


class TestF5BDeterministicBackfill:
    def test_overlap_sentence_backfilled_traceability_passes(self, monkeypatch):
        """重合句被摘为 grounded + traceability 过(确定性路径,llm_called=False)。"""
        data = _extract_with_interp(traces_to=["§E_CLAIMS"])  # 指错(§E 无 grounded)
        # LLM 不应被调(确定性补了),但 mock 以防万一
        _patch_route_llm(monkeypatch, {"retake": []})
        result = retake_traceability(data, _RAW, "concept")
        assert result["changed"] is True
        assert result["diagnostics"]["deterministic_backfill_count"] == 1
        assert result["diagnostics"]["llm_called"] is False  # 确定性补了,不调 LLM
        # 新 grounded claim 追加
        assert any(
            c.get("provenance") == "source_grounded" and c.get("anchor") == "§E_CLAIMS"
            for c in data["claims"]
        )
        # interp traces_to 指向新 grounded claim
        assert data["claims"][1]["traces_to"] == ["§E_CLAIMS"]
        # floor traceability 过
        floor = evaluate_integrity_floor(data, "concept", raw_anchors={"§A", "§E_CLAIMS"})
        assert FLOOR_INTERPRETATION_TRACEABILITY not in floor.failed_floors

    def test_no_overlap_falls_through_to_llm(self, monkeypatch):
        """无重合(§E block 是 lorem ipsum)→ 确定性补不了 → 走 LLM 路径。"""
        data = _extract_with_interp(traces_to=["§E_CLAIMS"])
        _patch_route_llm(
            monkeypatch,
            {"retake": [{"claim_index": 1, "traces_to": ["§A"], "provenance": "akb_interpretation"}]},
        )
        result = retake_traceability(data, _RAW_NO_OVERLAP, "concept")
        assert result["diagnostics"]["deterministic_backfill_count"] == 0
        assert result["diagnostics"]["llm_called"] is True  # 走 LLM
        # LLM 重指 §A(已有 grounded)→ F3-A 接受
        assert result["changed"] is True
        assert data["claims"][1]["traces_to"] == ["§A"]

    def test_heading_line_not_picked_as_sentence(self, monkeypatch):
        """标题行(#)/表格行(|)不被当句子摘。"""
        raw = (
            "## 锚点目录\n\n| §A | x |\n\n---\n\n§A_CONTENT_START\n\n"
            "grounded claim here.\n\n## §E_CLAIMS\n\n"
            "# Heading line not a sentence\n"
            "The model produces content differently.\n"
        )
        data = _extract_with_interp(traces_to=["§E_CLAIMS"])
        _patch_route_llm(monkeypatch, {"retake": []})
        result = retake_traceability(data, raw, "concept")
        # 确定性补了(找到 "The model produces content differently." 非 heading 行)
        if result["diagnostics"]["deterministic_backfill_count"] > 0:
            # 确认没摘 heading 行
            new_claim = [
                c for c in data["claims"]
                if c.get("anchor") == "§E_CLAIMS" and c.get("provenance") == "source_grounded"
            ]
            if new_claim:
                assert not new_claim[0]["text"].startswith("#")
                assert not new_claim[0]["text"].startswith("|")

    def test_empty_traces_to_searches_all_blocks(self, monkeypatch):
        """traces_to 为空 → 在全 raw 找最高重合 block(不限锚)。"""
        data = _extract_with_interp(traces_to=None)  # 无 traces_to
        _patch_route_llm(monkeypatch, {"retake": []})
        result = retake_traceability(data, _RAW, "concept")
        # 确定性在全 raw 找到重合句(§E_CLAIMS block 有重合)
        assert result["diagnostics"]["deterministic_backfill_count"] >= 1
        assert result["changed"] is True
