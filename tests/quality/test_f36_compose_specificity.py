"""test_f36_compose_specificity.py — F36 compose 侧内容贴合性两刀。

修复后 12 篇尸检(2026-07-10)钉出的两个确定性缺陷源:
  刀1 future_questions 是三条硬编码通用模板(失败模式/停止条件预算/并发重试
      漂移)只塞主题词——三篇不同主题文章问题一字不差,5 条 grader 缺陷全由
      它产出。改为从本文真实材料生发(盲区/机制边/解读论点),每条引真实锚点。
  刀2 操作化边界表零数据行时渲染"只有表头的空表"——3 篇 + SW R9 的 H2
      「表格为空」缺陷确定性来源。concept 零行改从真实机制边渲染边界行
      (evidence 逐字来自 raw);仍无材料省略该节,不出空表。

标准零点不动:grader/量规/地板零改动;两刀全是 maker 产出质量,兜底路径
(材料全空落通用问题)仅在本会死于地板的候选上触达。
"""

from __future__ import annotations

from src.akb2.quality.distill import (
    FUTURE_QUESTIONS_MAX,
    FUTURE_QUESTIONS_MIN,
    _build_how_boundary_table,
    _generate_questions,
)

_CLAIMS = [
    {"anchor": "§E_CLAIMS", "provenance": "source_grounded",
     "text": "Telescope 聚类生产轨迹中的失败模式,把重复故障组织成问题集群",
     "evidence_gap": "未量化:原文未给出聚类精度与漏报率的量化评估"},
    {"anchor": "§K_IMPLICATIONS", "provenance": "akb_interpretation",
     "text": "离线评估良好的更新在真实用户行为中可能退化,需独立测量层",
     "evidence_gap": ""},
]
_EDGES = [
    {"from": "ViBench", "to": "A/B tests", "relation": "precedes",
     "edge_kind": "source_causal", "evidence": "After ViBench, we run A/B tests.",
     "evidence_anchor": "§G_EXAMPLES"},
    {"from": "X", "to": "Y", "relation": "related",
     "edge_kind": "inferred_from_structure", "evidence": "", "evidence_anchor": ""},
]

_OLD_TEMPLATE_MARKERS = (
    "停止条件、预算上限与失败升级路径",
    "并发、重试与状态漂移场景下",
)


class TestQuestionsFromArticleMaterial:
    def test_questions_quote_article_specific_material(self) -> None:
        """问题必须含本文特有内容(盲区片段/组件名),非通用模板。"""
        qs = _generate_questions(_CLAIMS, [], _EDGES)
        joined = "\n".join(qs)
        assert "聚类精度与漏报率" in joined, "① 盲区片段须被引用"
        assert "ViBench → A/B tests" in joined, "② 机制边组件名须被引用"
        for marker in _OLD_TEMPLATE_MARKERS:
            assert marker not in joined, f"旧通用模板句不得出现: {marker}"

    def test_anchors_are_real_and_count_bounded(self) -> None:
        qs = _generate_questions(_CLAIMS, [], _EDGES)
        assert FUTURE_QUESTIONS_MIN <= len(qs) <= FUTURE_QUESTIONS_MAX
        for q in qs:
            assert q.startswith("[raw §"), q
            assert "(akb_interpretation)" in q

    def test_different_articles_get_different_questions(self) -> None:
        """反模板锁:不同材料 → 不同问题(旧实现三篇一字不差)。"""
        other_claims = [{"anchor": "§B", "provenance": "source_grounded",
                         "text": "Renderer rewrite keeps React component model",
                         "evidence_gap": "未覆盖:未给出重写后帧率对比数据"}]
        qa = _generate_questions(_CLAIMS, [], _EDGES)
        qb = _generate_questions(other_claims, [], [])
        assert set(qa) != set(qb)

    def test_weak_edges_not_used(self) -> None:
        """inferred 弱边不进问题(与 F4/H3 口径一致,不给弱边露脸)。"""
        qs = _generate_questions([], [], [_EDGES[1]])
        assert all("X → Y" not in q for q in qs)

    def test_empty_material_falls_back_to_generic_min(self) -> None:
        """材料全空(本会死于地板)→ 通用兜底保 MIN 条。"""
        qs = _generate_questions([], [], [])
        assert len(qs) >= FUTURE_QUESTIONS_MIN

    def test_fragments_sanitized(self) -> None:
        """引用片段消毒:反引号/竖线不得泄入(YAML/表格安全)。"""
        dirty = [{"anchor": "§A", "provenance": "source_grounded",
                  "text": "run `make | grep` now",
                  "evidence_gap": "未验证: `cmd|pipe` 场景"}]
        qs = _generate_questions(dirty, [], [])
        joined = "\n".join(qs)
        assert "`" not in joined and "|" not in joined.replace("[raw", "")


class TestBoundaryTableNoEmptyRender:
    def test_concept_zero_rows_renders_edge_boundaries(self) -> None:
        """concept 零步骤行 → 从真实机制边渲染边界行(治 H2 空表)。"""
        out = _build_how_boundary_table(
            _CLAIMS, [], {}, [],
            content_lane="concept", causal_edges=_EDGES,
        )
        assert "### 操作化边界" in out
        assert "机制边界：ViBench → A/B tests" in out
        assert "[raw §G_EXAMPLES]" in out

    def test_no_material_returns_empty_not_headeronly(self) -> None:
        """无任何数据行 → 返 ""(省略节),不再渲染只有表头的空表。"""
        out = _build_how_boundary_table(
            _CLAIMS, [], {}, [],
            content_lane="concept", causal_edges=[_EDGES[1]],  # 只有弱边
        )
        assert out == ""

    def test_procedural_zero_rows_empty(self) -> None:
        out = _build_how_boundary_table(
            _CLAIMS, [], {}, [],
            content_lane="procedural", causal_edges=_EDGES,
        )
        assert out == ""

    def test_supported_steps_path_unchanged(self) -> None:
        """既有步骤行路径零变化(golden 形态回归锁)。"""
        step = {"instruction": "Define the work item schema", "command": ""}
        out = _build_how_boundary_table(
            _CLAIMS, [step], {}, [(1, step, "§E_CLAIMS")],
            content_lane="concept", causal_edges=_EDGES,
        )
        assert "| 1 |" in out and "Define the work item schema" in out
        assert "机制边界" not in out, "有步骤行时不混入边边界行"
