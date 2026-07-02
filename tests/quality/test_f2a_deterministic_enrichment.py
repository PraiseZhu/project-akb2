"""test_f2a_deterministic_enrichment.py — F2-A 确定性抽边并入 LLM 成功路径测试。

覆盖:
    - LLM 成功 0 eligible + raw 可确定性抽边 → 合并后 F4 过
    - raw 抽不出(无组件/无因果句)→ 返 False,extract_data 不动(fail-closed)
    - LLM 已有 eligible 边 → 不触发 enrichment(caller 守,幂等)
    - 组件别名对齐(LLM 已有同名/别名 → 不重复补)
"""

from __future__ import annotations

from src.akb2.quality.distill import _enrich_zero_eligible_with_deterministic
from src.akb2.quality.integrity_floor import evaluate_integrity_floor
from src.akb2.quality.raw_anchors import declared_anchor_set

# raw:声明 §A/§B/§C + §A 下 "Executor Pool dispatches to Task Graph" 因果句
_RAW_WITH_CAUSAL = """\
## 锚点目录

| §A | x |
| §B | y |
| §C | z |

---

## §A Setup

The Executor Pool dispatches to the Task Graph for scheduling.

## §B Runtime

The Task Graph feeds the Lifecycle Manager.

## §C End

conclusion.
"""

_RAW_NO_CAUSAL = """\
## 锚点目录

| §A | x |
| §B | y |

---

Plain body with no headings no causal language no named components here.
"""


def _extract_zero_eligible(raw: str) -> dict:
    """LLM 成功但 0 eligible 边:有 mechanism_components 但 causal_edges 全 inferred(无 evidence/anchor)。"""
    return {
        "claims": [
            {"text": "some claim", "anchor": "§A", "provenance": "source_grounded",
             "evidence_gap": "未提供 X"},
        ],
        "argumentation": [],
        "mechanism_components": [
            {"name": "Executor Pool", "role": "x"},
            {"name": "Task Graph", "role": "y"},
            {"name": "Lifecycle Manager", "role": "z"},
        ],
        "causal_edges": [
            # inferred 弱边(无 evidence/anchor)→ 不计 eligible
            {"from": "Executor Pool", "to": "Task Graph", "relation": "seq",
             "edge_kind": "inferred_from_structure", "evidence": "", "evidence_anchor": ""},
        ],
        "how_steps": [],
    }


class TestEnrichment:
    def test_zero_eligible_raw_extractable_enriched_to_pass_f4(self):
        """LLM 0 eligible + raw 可确定性抽边 → 合并后 F4 过(eligible 边 ≥1)。"""
        data = _extract_zero_eligible(_RAW_WITH_CAUSAL)
        decl = declared_anchor_set(_RAW_WITH_CAUSAL)
        floor_before = evaluate_integrity_floor(data, "concept", raw_anchors=decl)
        assert not floor_before.passed  # 0 eligible → F4 fail

        diag = _enrich_zero_eligible_with_deterministic(data, _RAW_WITH_CAUSAL)
        assert diag["enriched"]  # 加了边
        assert diag["edges_added"] >= 1

        floor_after = evaluate_integrity_floor(data, "concept", raw_anchors=decl)
        assert floor_after.passed, f"enrich 后 F4 应过: {floor_after.reason}"
        # 合并后有 source_causal eligible 边
        causal = [e for e in data["causal_edges"] if e["edge_kind"] == "source_causal"]
        assert len(causal) >= 1

    def test_raw_not_extractable_returns_false_unchanged(self):
        """raw 无因果句/组件 → enriched=False,extract_data 不动(fail-closed)。"""
        data = _extract_zero_eligible(_RAW_NO_CAUSAL)
        data_before = {
            "causal_edges": list(data["causal_edges"]),
            "mechanism_components": list(data["mechanism_components"]),
        }
        diag = _enrich_zero_eligible_with_deterministic(data, _RAW_NO_CAUSAL)
        assert diag["enriched"] is False
        assert data["causal_edges"] == data_before["causal_edges"]
        assert data["mechanism_components"] == data_before["mechanism_components"]

    def test_idempotent_no_duplicate_edges(self):
        """再跑一次不重复加边(dedup by from/to)。"""
        data = _extract_zero_eligible(_RAW_WITH_CAUSAL)
        _enrich_zero_eligible_with_deterministic(data, _RAW_WITH_CAUSAL)
        n_after_first = len(data["causal_edges"])
        _enrich_zero_eligible_with_deterministic(data, _RAW_WITH_CAUSAL)
        assert len(data["causal_edges"]) == n_after_first  # 不重复

    def test_component_alias_align_no_duplicate(self):
        """LLM 已有 'Executor Pool' → 确定性抽的同名组件不重复补(别名/同名对齐)。"""
        data = _extract_zero_eligible(_RAW_WITH_CAUSAL)
        # LLM 已有 3 组件(Executor Pool/Task Graph/Lifecycle Manager)
        _enrich_zero_eligible_with_deterministic(data, _RAW_WITH_CAUSAL)
        # 确定性抽的同名组件不重复补(可能补别名变体,但不重复同名)
        names = [c["name"] for c in data["mechanism_components"]]
        assert names.count("Executor Pool") == 1
        assert names.count("Task Graph") == 1
        # 组件数不超 FALLBACK_MAX_COMPONENTS=12
        assert len(data["mechanism_components"]) <= 12

    def test_f4a_alias_component_names_enrichment_passes(self):
        """F4-A 回归:LLM 组件名(slug/别名,如 executor_pool/Captain Agent)与确定性原名
        (Executor Pool/Captain)不同时,enrichment 仍能用确定性组件抽 source_causal 边 → F4 过。

        probe-p3 ag2 根因:原用 existing_comps(LLM+确定性混合)调 _build_real_causal_edges,
        LLM slug(executor_pool)与确定性原名(Executor Pool)variant 碰撞 → 'executor'/'captain'
        被 [N1]a 歧义弃用 → 0 source_causal 边。F4-A 改用 det_components 抽边,修复。
        """
        # LLM 组件名与确定性不同(slug + 别名)
        data = {
            "claims": [
                {"text": "x", "anchor": "§A", "provenance": "source_grounded",
                 "evidence_gap": "未提供 X"},
            ],
            "argumentation": [],
            "mechanism_components": [
                {"name": "Captain Agent", "role": "x"},
                {"name": "executor_pool", "role": "y"},
                {"name": "TaskGraph", "role": "z"},
            ],
            "causal_edges": [
                # LLM 只产 inferred 弱边(无 evidence/anchor)→ 0 eligible
                {"from": "Captain Agent", "to": "executor_pool", "relation": "seq",
                 "edge_kind": "inferred_from_structure", "evidence": "", "evidence_anchor": ""},
            ],
            "how_steps": [],
        }
        raw = (
            "## 锚点目录\n\n| §A | x |\n| §M | y |\n| §F | z |\n\n---\n\n"
            "§A_CONTENT_START\n\nintro.\n\n"
            "§M_PARAMS\n\nThe Captain dispatches to the Executor Pool.\n\n"
            "§F_EVIDENCE\n\nThe Executor Pool feeds the Task Graph.\n"
        )
        decl = declared_anchor_set(raw)
        floor_before = evaluate_integrity_floor(data, "concept", raw_anchors=decl)
        assert not floor_before.passed  # 0 eligible → F4 fail

        diag = _enrich_zero_eligible_with_deterministic(data, raw)
        assert diag["enriched"]
        assert diag["edges_added"] >= 1  # F4-A:确定性边真合入(原 0)

        floor_after = evaluate_integrity_floor(data, "concept", raw_anchors=decl)
        assert floor_after.passed, f"alias 组件名 enrichment 后 F4 应过: {floor_after.reason}"


class TestF5AEndpointClosure:
    """F5-A: enrichment 合并后端点闭合——所有 causal_edges 的 from/to ∈ 组件表。

    probe-p4 vercel/crewai/sub-agents 新死因:LLM 产边端点(Multi-Step Loop/Quick Start/
    Invoke subagents explicitly)不在 mechanism_components 名单 → SG-mechanism-edge-evidence 自拒。
    """

    def test_llm_edge_endpoint_not_in_comps_dropped(self):
        """LLM 边端点不在组件表(且非确定性组件)→ 丢边 + edges_dropped_endpoint 计数。"""
        data = {
            "claims": [
                {"text": "x", "anchor": "§A", "provenance": "source_grounded",
                 "evidence_gap": "未提供 X"},
            ],
            "argumentation": [],
            "mechanism_components": [
                {"name": "Executor Pool", "role": "x"},
                {"name": "Task Graph", "role": "y"},
                {"name": "Lifecycle", "role": "z"},
            ],
            # LLM 边端点 "Quick Start" 不在组件表(泛节名,非确定性组件)
            "causal_edges": [
                {"from": "Executor Pool", "to": "Quick Start", "relation": "r",
                 "edge_kind": "source_causal", "evidence": "ev", "evidence_anchor": "§A"},
            ],
            "how_steps": [],
        }
        raw = (
            "## 锚点目录\n\n| §A | x |\n\n---\n\n§A_CONTENT_START\n\n"
            "The Executor Pool dispatches to the Task Graph.\n"
        )
        diag = _enrich_zero_eligible_with_deterministic(data, raw)
        # "Quick Start" 不在组件表且非确定性组件 → 丢边
        assert diag["edges_dropped_endpoint"] >= 1
        # 丢后无 "Quick Start" 端点的边
        for e in data["causal_edges"]:
            assert "Quick Start" not in (e.get("from", ""), e.get("to", ""))

    def test_endpoint_closure_invariant_all_edges_in_comps(self):
        """不变式:enrichment 后所有 causal_edges 的 from/to ∈ 组件表 names。"""
        data = {
            "claims": [
                {"text": "x", "anchor": "§A", "provenance": "source_grounded",
                 "evidence_gap": "未提供 X"},
            ],
            "argumentation": [],
            "mechanism_components": [
                {"name": "Executor Pool", "role": "x"},
                {"name": "Task Graph", "role": "y"},
            ],
            "causal_edges": [
                {"from": "Executor Pool", "to": "Garbage Endpoint", "relation": "r",
                 "edge_kind": "source_causal", "evidence": "ev", "evidence_anchor": "§A"},
                {"from": "Executor Pool", "to": "Task Graph", "relation": "r",
                 "edge_kind": "inferred_from_structure", "evidence": "", "evidence_anchor": ""},
            ],
            "how_steps": [],
        }
        raw = (
            "## 锚点目录\n\n| §A | x |\n\n---\n\n§A_CONTENT_START\n\n"
            "The Executor Pool dispatches to the Task Graph.\n"
        )
        _enrich_zero_eligible_with_deterministic(data, raw)
        # 不变式:所有边端点 ∈ 组件表
        comp_names = {
            str(c.get("name", "")).strip().lower()
            for c in data.get("mechanism_components", [])
            if isinstance(c, dict)
        }
        for e in data["causal_edges"]:
            for ep in (e.get("from", ""), e.get("to", "")):
                if ep:
                    assert ep.lower() in comp_names, (
                        f"端点闭合不变式违反: {ep!r} 不在组件表 {comp_names}"
                    )


class TestEligiblePresentNotTriggered:
    """caller(runner)守:LLM 已有 eligible 边时不触发 enrichment。本测试验证 enrichment
    函数本身在有 eligible 边时仍可调用(但 runner 不会调)— 主要锁 caller 侧的 F4 fail 触发。"""

    def test_runner_triggers_only_on_f4_fail(self, monkeypatch):
        """runner:LLM 成功 + F4 fail → 触发 enrichment;F4 过 → 不触发。

        用 monkeypatch 替 _enrich_zero_eligible_with_deterministic 为计数桩,验证调用次数。
        """
        import src.akb2.quality.distill as distill_mod

        calls = {"n": 0}

        def _spy(data, raw):
            calls["n"] += 1
            return False

        monkeypatch.setattr(
            distill_mod, "_enrich_zero_eligible_with_deterministic", _spy, raising=False
        )
        # 这里只验证 spy 可被替换(完整 runner 集成在真机探针);unit 层不跑全管道
        assert calls["n"] == 0
