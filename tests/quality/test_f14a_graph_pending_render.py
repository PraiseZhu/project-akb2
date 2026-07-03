"""test_f14a_graph_pending_render.py — F14-A 图谱冷启动 pending 边渲染 + A4 豁免测试。

sub-agents 只差图谱可调用性:compose 旧版把 resolve 不中的边全丢 → 空图谱 → grader fail。
F14-A:D8 proposal 语义渲染侧落地——target ∈ proposed_graph_nodes + evidence 带真实 raw 锚
的边渲染为 pending 行(weak/akb_interpretation),不丢;A4 豁免(三条件缺一不可)。
"""

from __future__ import annotations

import re

from src.akb2.quality.checks_structural import check_a4_graph_edges
from src.akb2.quality.distill import _build_body, _filter_resolvable_graph_edges


def _edge(target: str, evidence: str, relation: str = "related") -> dict:
    return {
        "concept": target,
        "relation_type": relation,
        "strength": "weak",
        "provenance": "akb_interpretation",
        "evidence": evidence,
        "anchor": "§A",
    }


class TestF14APendingEdgeRender:
    def test_pending_edge_with_evidence_recovered(self):
        """target ∈ proposed_graph_nodes + evidence 带 [raw §X] → 渲染为 pending 行,不丢。"""
        graph_edges = [
            _edge("loop-engineering", "[raw §A] (akb_interpretation) some evidence"),
        ]
        # known 空 → 边被 resolve 丢;但 target ∈ proposed_graph_nodes → pending 恢复
        proposed = [{"slug": "loop-engineering", "label": "Loop Eng", "reason": "cold", "source_anchor": "§A"}]
        kept, concepts, dropped, dropped_edges = _filter_resolvable_graph_edges(
            graph_edges, [], known_concepts=[], pending_queue=[]
        )
        assert not kept  # 被 resolve 丢
        assert dropped_edges  # 完整 edge dict 返回
        # compose 渲染 pending
        data = {
            "claims": [{"text": "x", "anchor": "§A", "provenance": "source_grounded", "evidence_gap": "未提供"}],
            "argumentation": [],
            "mechanism_components": [{"name": "A", "role": "x"}, {"name": "B", "role": "y"}, {"name": "C", "role": "z"}],
            "causal_edges": [{"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal", "evidence": "ev", "evidence_anchor": "§A"}],
            "how_steps": [],
            "tldr": "tldr here for test.",
            "params": {},
            "referenced_concepts": [],
            "applicability_boundary": "boundary text here.",
            "graph_edges": graph_edges,
            "proposed_graph_nodes": proposed,
            "graph_pending": True,
        }
        raw = "§A_CONTENT_START\n\nsome evidence here.\n"
        body = _build_body(data, "test.md", None, must_cover=[], raw_content=raw,
                           content_lane="concept", known_concepts=[], pending_queue=[])
        # 图谱节含 pending 边行(target loop-engineering)
        graph_section = re.search(r"## 图谱关系\s+(.+?)(?:## )", body, re.S).group(1)
        assert "loop-engineering" in graph_section
        assert "pending" in graph_section.lower()
        # 不是"暂无可证实"(有空段)
        assert "暂无可证实的图谱边" not in graph_section

    def test_no_evidence_edge_still_dropped(self):
        """target ∈ proposed 但 evidence 无 [raw §X] → 仍丢(无证据不渲染 pending)。"""
        graph_edges = [
            _edge("loop-engineering", "no raw anchor here"),  # 无 [raw §X]
        ]
        proposed = [{"slug": "loop-engineering", "label": "L", "reason": "x", "source_anchor": "§A"}]
        data = {
            "claims": [{"text": "x", "anchor": "§A", "provenance": "source_grounded", "evidence_gap": "未提供"}],
            "argumentation": [], "mechanism_components": [{"name": "A", "role": "x"}, {"name": "B", "role": "y"}, {"name": "C", "role": "z"}],
            "causal_edges": [{"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal", "evidence": "ev", "evidence_anchor": "§A"}],
            "how_steps": [], "tldr": "tldr here.", "params": {}, "referenced_concepts": [],
            "applicability_boundary": "boundary text.", "graph_edges": graph_edges,
            "proposed_graph_nodes": proposed, "graph_pending": True,
        }
        raw = "§A_CONTENT_START\n\ncontent.\n"
        body = _build_body(data, "test.md", None, must_cover=[], raw_content=raw,
                           content_lane="concept", known_concepts=[], pending_queue=[])
        graph_section = re.search(r"## 图谱关系\s+(.+?)(?:## )", body, re.S).group(1)
        # 无证据 → 不渲染 pending → "暂无可证实" 空段(或 dropped 注释)
        assert "loop-engineering" not in graph_section or "dropped" in graph_section.lower()


class TestF14AA4PendingExempt:
    def _candidate(self, target: str, evidence: str, graph_pending: bool, proposed_slugs: list[str]) -> str:
        proposed_yaml = "\n".join(f"- slug: {s}\n  label: {s}\n  reason: cold\n  source_anchor: §A" for s in proposed_slugs)
        return f"""---
graph_pending: {str(graph_pending).lower()}
proposed_graph_nodes:
{proposed_yaml}
---

## 图谱关系

x

| target | relation | strength | provenance | evidence |
|--------|----------|----------|------------|----------|
| {target} | related | weak | akb_interpretation | {evidence} |

## 维护记录

| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-07-03 | 初始 | x |"""

    def test_pending_exempt_no_defect(self):
        """graph_pending=true + target∈proposed + evidence 含 [raw §X] → 不报 A4-图谱resolve。"""
        body = self._candidate("loop-engineering", "[raw §A] (akb_interpretation) ev", True, ["loop-engineering"])
        defects = check_a4_graph_edges(body, known_concepts=[], pending_queue=[], content_lane="concept")
        a4_resolve = [d for d in defects if d.dimension == "A4-图谱resolve"]
        assert a4_resolve == [], f"pending 边应豁免却报了: {a4_resolve}"

    def test_target_not_in_proposed_still_reported(self):
        """target 不在 proposed_graph_nodes → 仍报 A4-图谱resolve(编造 target 照拒)。"""
        body = self._candidate("fake-concept", "[raw §A] ev", True, ["loop-engineering"])
        defects = check_a4_graph_edges(body, known_concepts=[], pending_queue=[], content_lane="concept")
        a4_resolve = [d for d in defects if d.dimension == "A4-图谱resolve"]
        assert a4_resolve, "target 不在 proposed 集应报 A4-图谱resolve"

    def test_no_graph_pending_still_reported(self):
        """graph_pending=false(或无) → 即使 target∈proposed 也报(三条件缺一不可)。"""
        body = self._candidate("loop-engineering", "[raw §A] ev", False, ["loop-engineering"])
        defects = check_a4_graph_edges(body, known_concepts=[], pending_queue=[], content_lane="concept")
        a4_resolve = [d for d in defects if d.dimension == "A4-图谱resolve"]
        assert a4_resolve, "graph_pending=false 不应豁免"

    def test_no_raw_anchor_still_reported(self):
        """evidence 无 [raw §X] → 即使 graph_pending+target∈proposed 也报(三条件缺一)。"""
        body = self._candidate("loop-engineering", "no anchor evidence", True, ["loop-engineering"])
        defects = check_a4_graph_edges(body, known_concepts=[], pending_queue=[], content_lane="concept")
        a4_resolve = [d for d in defects if d.dimension == "A4-图谱resolve"]
        assert a4_resolve, "无 [raw §X] 证据不应豁免"
