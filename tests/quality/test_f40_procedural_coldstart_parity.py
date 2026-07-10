"""test_f40_procedural_coldstart_parity.py — A2 冷启动豁免的车道对齐。

全修复时代 2.2 天漏斗尸检(2026-07-11):compose 自审门 22 篇死亡全是
procedural 车道,其中 11 篇唯一死因是 `related_concepts 0 < 2`——compose 侧
D8 块 lane-agnostic 写 proposed_graph_nodes,但 A2 检查旧版只对 concept 认
该替代 → procedural 冷启动确定性团灭(操作步骤等其余门全过的真 how-to 文
也救不回)。

F40:豁免条件从 concept+proposal 扩为 (concept|procedural)+proposal。
语义对齐非放宽:替代仍要求 proposed_graph_nodes>=1(真实引用概念);
active 模式(非 proposal)两车道都仍要求真实 related>=2(D8 底线零动)。
"""

from __future__ import annotations

from src.akb2.quality.checks_structural import check_a2_frontmatter


def _candidate(lane_related: str, proposed: str) -> str:
    return f"""---
slug: t--x
source_refs: [raw/t--x--raw--2026-07-10.md]
future_questions:
  - "[raw §A] (akb_interpretation) q1？"
  - "[raw §B] (akb_interpretation) q2？"
related_concepts: {lane_related}
{proposed}gate_decision: null
gate_reason: "候选来源锚点 [raw §A]"
---

# 正文
"""


_PROPOSED = (
    "proposed_graph_nodes:\n"
    "  - slug: loop_engineering\n"
    "    label: loop-engineering\n"
    "    reason: 冷启动\n"
    "    source_anchor: §A\n"
)


def _related_defects(defects) -> list:
    return [d for d in defects
            if d.location == "frontmatter.related_concepts"]


class TestProceduralColdstartParity:
    def test_procedural_proposal_with_proposed_nodes_passes(self) -> None:
        """[F40 核心] procedural+proposal+proposed_nodes≥1 → 不再死于 related。"""
        text = _candidate("[]", _PROPOSED)
        defects = check_a2_frontmatter(
            text, content_lane="procedural", promotion_mode="proposal")
        assert _related_defects(defects) == []

    def test_concept_behavior_unchanged(self) -> None:
        text = _candidate("[]", _PROPOSED)
        defects = check_a2_frontmatter(
            text, content_lane="concept", promotion_mode="proposal")
        assert _related_defects(defects) == []

    def test_procedural_without_proposed_nodes_still_fails(self) -> None:
        """替代要求真材料:无 proposed_graph_nodes → 照拒(不白给)。"""
        text = _candidate("[]", "")
        defects = check_a2_frontmatter(
            text, content_lane="procedural", promotion_mode="proposal")
        assert len(_related_defects(defects)) == 1

    def test_active_mode_still_requires_real_related(self) -> None:
        """D8 底线零动:active 写入两车道都不吃替代。"""
        text = _candidate("[]", _PROPOSED)
        for lane in ("procedural", "concept"):
            defects = check_a2_frontmatter(
                text, content_lane=lane, promotion_mode="active")
            assert len(_related_defects(defects)) == 1, lane

    def test_real_related_passes_any_lane(self) -> None:
        text = _candidate("[loop-engineering, quality-gate]", "")
        for lane in ("procedural", "concept", ""):
            defects = check_a2_frontmatter(
                text, content_lane=lane, promotion_mode="proposal")
            assert _related_defects(defects) == [], lane

    def test_empty_lane_not_exempted(self) -> None:
        """未知车道不吃豁免(fail-closed:豁免只给两条已知车道)。"""
        text = _candidate("[]", _PROPOSED)
        defects = check_a2_frontmatter(
            text, content_lane="", promotion_mode="proposal")
        assert len(_related_defects(defects)) == 1
