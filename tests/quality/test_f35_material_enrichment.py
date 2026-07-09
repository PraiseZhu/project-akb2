"""test_f35_material_enrichment.py — F35 料薄增援 + 幻觉组件剔除。

replit R4 尸检(2026-07-08T20:33 账本)钉出的确定性缺口:增援只在 F4 fail 时
触发,"1 条 eligible 压线过 F4"的薄料绕过增援直送 grader 被 H3 击毙(SW R9 同
死面);且幻觉组件 "a_concrete_example"(名字在 raw 定位不到)活到 grader。

三刀(全 maker 侧,判定零动):
  刀A count_eligible_edges 公共口径(floor 判定与增援触发共用,防口径分裂)
  刀1 增援触发扩展:F4 fail OR eligible < ENRICH_MIN_ELIGIBLE_EDGES(=2)
  刀2 concept LLM 路径回填后仍无 evidence 的组件剔除(宁缺勿假;fallback 豁免)

标准零点锁:F4 地板阈值 ≥1 eligible 零动(test_floor_still_passes_with_one_eligible);
增援只加真料不改判定;剔除只删假料,evidence 齐全组件零误伤。
"""

from __future__ import annotations

from src.akb2.quality.distill import (
    ENRICH_MIN_ELIGIBLE_EDGES,
    _normalize_extract_payload,
)
from src.akb2.quality.integrity_floor import (
    _core_value_f4_failed,
    count_eligible_edges,
)

# ---------------------------------------------------------------------------
# 刀A: count_eligible_edges 公共口径
# ---------------------------------------------------------------------------


def _payload(edges: list[dict], comps: list[str] | None = None) -> dict:
    return {
        "mechanism_components": [
            {"name": n, "role": "r", "evidence_anchor": "§A",
             "evidence_sentence": "s"}
            for n in (comps or ["Brain", "Hands", "Loop"])
        ],
        "causal_edges": edges,
    }


def _edge(src: str, dst: str, kind: str, anchor: str = "§A") -> dict:
    return {"from": src, "to": dst, "relation": "calls", "edge_kind": kind,
            "evidence": "Brain calls Hands.", "evidence_anchor": anchor}


class TestCountEligibleEdges:
    def test_eligible_requires_trusted_anchor(self) -> None:
        p = _payload([
            _edge("Brain", "Hands", "source_causal", "§A"),
            _edge("Hands", "Loop", "source_causal", "§FAKE"),
        ])
        c = count_eligible_edges(p, raw_anchors={"§A"})
        assert c == {"eligible": 1, "weak": 0, "untrusted_anchor": 1}

    def test_weak_and_endpoint_rules(self) -> None:
        p = _payload([
            _edge("Brain", "Hands", "inferred_from_structure"),
            _edge("Brain", "Brain", "source_causal"),       # 自环不计
            _edge("Ghost", "Hands", "source_causal"),        # 端点不在组件集不计
        ])
        c = count_eligible_edges(p, raw_anchors={"§A"})
        assert c == {"eligible": 0, "weak": 1, "untrusted_anchor": 0}

    def test_floor_uses_same_counter_consistency(self) -> None:
        """口径一致锁:counter 0 eligible ⟺ F4 fail;≥1 ⟺ F4 pass。"""
        p0 = _payload([_edge("Brain", "Hands", "inferred_from_structure")])
        assert count_eligible_edges(p0, {"§A"})["eligible"] == 0
        failed, _ = _core_value_f4_failed(p0, "concept", raw_anchors={"§A"})
        assert failed is True

    def test_floor_still_passes_with_one_eligible(self) -> None:
        """[标准零点锁] F4 地板阈值 ≥1 零动:1 条 eligible 依旧过地板——
        ENRICH_MIN_ELIGIBLE_EDGES=2 是增援触发线,不是判定线。"""
        p1 = _payload([_edge("Brain", "Hands", "source_causal")])
        failed, _ = _core_value_f4_failed(p1, "concept", raw_anchors={"§A"})
        assert failed is False
        assert ENRICH_MIN_ELIGIBLE_EDGES == 2  # 具名预算(宪法 P1/P4)


# ---------------------------------------------------------------------------
# 刀1: runner 增援触发扩展(源码断言,同 F29 判例)
# ---------------------------------------------------------------------------


class TestEnrichTriggerWidened:
    def test_runner_trigger_includes_thin_material(self) -> None:
        import inspect

        from src.akb2.quality import runner
        src = inspect.getsource(runner)
        assert "_eligible_before < ENRICH_MIN_ELIGIBLE_EDGES" in src
        assert "count_eligible_edges" in src, "须用 floor 同一口径计数"


# ---------------------------------------------------------------------------
# 刀2: 幻觉组件剔除(concept LLM 路径;fallback/procedural 豁免)
# ---------------------------------------------------------------------------

_RAW = (
    "## 锚点目录\n\n| §A | x |\n\n## §A\n\n"
    "The Brain calls the Hands. The Hands execute tasks in the Loop.\n"
)


def _unlocated_payload() -> dict:
    return {
        "mechanism_components": [
            {"name": "Brain", "role": "r", "evidence_anchor": "",
             "evidence_sentence": ""},
            {"name": "Hands", "role": "r", "evidence_anchor": "",
             "evidence_sentence": ""},
            {"name": "Quantum Flux Capacitor", "role": "r",
             "evidence_anchor": "", "evidence_sentence": ""},
        ],
        "causal_edges": [
            _edge("Brain", "Hands", "source_causal"),
            _edge("Brain", "Quantum Flux Capacitor", "source_causal"),
        ],
        "claims": [],
        "argumentation": [],
        "how_steps": [],
    }


class TestUnlocatedComponentDrop:
    def test_hallucinated_component_dropped_with_edges(self) -> None:
        """R4 死面复现:raw 定位不到的组件剔除,指向它的边一并丢。"""
        data = _unlocated_payload()
        diag = _normalize_extract_payload(data, _RAW, "concept")
        names = [c["name"] for c in data["mechanism_components"]]
        assert "Quantum Flux Capacitor" not in names
        assert diag["components_dropped_unlocated"] == 1
        for e in data["causal_edges"]:
            assert "Quantum Flux Capacitor" not in (e.get("from"), e.get("to"))

    def test_locatable_components_survive_backfilled(self) -> None:
        """零误伤:raw 可定位组件经 F8-A 回填后全保留。"""
        data = _unlocated_payload()
        _normalize_extract_payload(data, _RAW, "concept")
        names = {c["name"] for c in data["mechanism_components"]}
        assert {"Brain", "Hands"} <= names

    def test_fallback_path_exempt(self) -> None:
        """fallback 组件按构造来自 raw 扫描,不剔(防误杀标题/表格来源组件)。"""
        data = _unlocated_payload()
        diag = _normalize_extract_payload(data, _RAW, "concept", is_fallback=True)
        assert diag["components_dropped_unlocated"] == 0
        names = [c["name"] for c in data["mechanism_components"]]
        assert "Quantum Flux Capacitor" in names

    def test_procedural_lane_exempt(self) -> None:
        data = _unlocated_payload()
        diag = _normalize_extract_payload(data, _RAW, "procedural")
        assert diag["components_dropped_unlocated"] == 0
