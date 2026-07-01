"""test_distill_mechanism_extract.py — WP-3 distill 提炼内核测试 (SC-3)。

覆盖:
    - §0.2/A2 F4 不前移到 extract：concept <3 组件/孤立/重复名 → extract 不 raise
      （F4 deferred to integrity_floor，由 test_integrity_floor TestCoreValueF4 覆盖）；
      3 唯一+连边 → 过
    - D8 concept fallback 不造假机制（WP-3.1）：无 ≥3 命名组件 raw → fail-closed；
      有 ≥3 → 用真实组件（非硬编码 loop 组件）；0 真实因果边 → fallback maker 级 fail-closed
    - D7 poisoned trace（WP-3.1/3.6）：concept fallback how_steps=[]，不生成
      schema/ledger/max_repair_rounds 假步骤（idx5 形态）
    - WP-3.4 frontmatter 同源 resolve：known/pending 提供时 related_concepts 只写
      可 resolve 的 [[wikilink]]，悬空不写；gate _graph_readiness 悬空不产 real_link

mock 边界：直接调 _validate_extract_output / build_deterministic_extract_result /
_build_frontmatter（确定性代码，无 LLM）；gate _graph_readiness 只读 import。
"""

from __future__ import annotations

from pathlib import Path

from src.akb2.quality.distill import (
    _build_frontmatter,
    _build_real_causal_edges,
    _normalize_causal_edges,
    _normalize_mechanism_components,
    _validate_extract_output,
    build_deterministic_extract_result,
    extract_claims,
)
from src.akb2.quality.integrity_floor import (
    FLOOR_CORE_VALUE_F4,
    evaluate_integrity_floor,
)
from src.akb2.quality.llm_router import LLMResponse

# ---------------------------------------------------------------------------
# 辅助：构造 concept extract data（base 过 F4 外所有 _validate_extract_output 检查）
# ---------------------------------------------------------------------------


def _base_concept_data(
    components: list[dict], edges: list[dict]
) -> dict:
    """构造 concept extract data，只变 mechanism_components/causal_edges（测 F4）。"""
    return {
        "claims": [
            {
                "text": "a source grounded claim about the mechanism",
                "anchor": "§A",
                "provenance": "source_grounded",
                "evidence_gap": "未覆盖：原文未展开该组件的生产数据",
            }
        ],
        "how_steps": [],
        "mechanism_components": components,
        "causal_edges": edges,
        "params": {},
        "referenced_concepts": ["loop-engineering"],
        "tldr": "concept tldr with enough characters to pass the min length check",
        "argumentation": [
            {
                "claim_anchor": "§A",
                "explanation": "the claim is grounded in raw",
                "provenance": "source_grounded",
            }
        ],
        "applicability_boundary": "applicability boundary with enough chars to pass min",
        "graph_edges": [],
    }


# ---------------------------------------------------------------------------
# §0.2/M1/D9：fallback raw-grounded 边（edge_kind）+ fallback_quality_basis
# ---------------------------------------------------------------------------


class TestM1FallbackEdgesAndBasis:
    """§0.2/M1/D2/D9：fallback 产 source_causal eligible 边 + inferred 弱边 + basis。

    source_causal（带 evidence + evidence_anchor）计入 F4 eligible；inferred 不计。
    fallback_quality_basis 在成功与失败路径都携带（D9 不靠 error string）。
    """

    _CAPTAIN_RAW = (
        "# \u00a7A Captain Architecture\n\n"
        "### 1. Captain\nThe Captain dispatches to the Executor Pool.\n\n"
        "### 2. Executor Pool\nThe Executor Pool feeds the Task Graph.\n\n"
        "### 3. Task Graph\nThe Task Graph writes to the Lifecycle.\n\n"
        "### 4. Lifecycle\nThe Lifecycle notifies the Captain.\n"
    )

    def test_build_real_causal_edges_source_causal_with_evidence(self):
        """真因果语言句 → source_causal 边（带 evidence 句 + evidence_anchor）。"""
        components = [
            {"name": "Captain", "role": "r"},
            {"name": "Executor Pool", "role": "r"},
            {"name": "Task Graph", "role": "r"},
            {"name": "Lifecycle", "role": "r"},
        ]
        edges = _build_real_causal_edges(components, self._CAPTAIN_RAW)
        source_causal = [e for e in edges if e["edge_kind"] == "source_causal"]
        assert len(source_causal) >= 1, "应有 source_causal eligible 边"
        for e in source_causal:
            assert e["edge_kind"] == "source_causal"
            assert e["evidence"], "source_causal 边须带 evidence 句"
            assert e["evidence_anchor"], "source_causal 边须带 evidence_anchor（F4 eligible）"

    def test_build_real_causal_edges_inferred_weak_edges(self):
        """相邻组件未被 source_causal 覆盖时 → inferred_from_structure 弱边（无 evidence）。"""
        # 无因果语言 → 所有相邻对走 inferred
        components = [
            {"name": "Alpha", "role": "r"},
            {"name": "Beta", "role": "r"},
            {"name": "Gamma", "role": "r"},
        ]
        raw = "# Plain Article\n\n### 1. Alpha\nA thing.\n### 2. Beta\nAnother.\n### 3. Gamma\nThird.\n"
        edges = _build_real_causal_edges(components, raw)
        inferred = [e for e in edges if e["edge_kind"] == "inferred_from_structure"]
        assert len(inferred) >= 1, "相邻组件未被 source_causal 覆盖时应有 inferred 弱边"
        for e in inferred:
            assert e["edge_kind"] == "inferred_from_structure"
            assert e["evidence"] == ""
            assert e["evidence_anchor"] == ""

    def test_build_real_causal_edges_no_causal_language_zero_eligible(self):
        """无因果语言 → 0 source_causal eligible 边（inferred 仍可能存在但不救 F4）。"""
        components = [
            {"name": "Alpha", "role": "r"},
            {"name": "Beta", "role": "r"},
            {"name": "Gamma", "role": "r"},
        ]
        raw = "# Plain Article\n\n### 1. Alpha\nA thing.\n### 2. Beta\nAnother.\n### 3. Gamma\nThird.\n"
        edges = _build_real_causal_edges(components, raw)
        source_causal = [e for e in edges if e["edge_kind"] == "source_causal"]
        assert source_causal == [], "无因果语言 → 0 eligible 边"
        # inferred 弱边仍建（供 grader H3）
        inferred = [e for e in edges if e["edge_kind"] == "inferred_from_structure"]
        assert len(inferred) >= 1

    def test_fallback_success_carries_fallback_quality_basis(self):
        """concept fallback 成功路径也携 fallback_quality_basis（机读质量基线）。"""
        result = build_deterministic_extract_result(
            self._CAPTAIN_RAW, content_lane="concept"
        )
        assert result.success is True, result.error
        basis = result.data.get("fallback_quality_basis")
        assert basis is not None, "成功路径也应携 fallback_quality_basis"
        assert basis["component_count"] >= 3
        assert basis["source_causal_edge_count"] >= 1, "Captain fixture 有因果语言 → ≥1 eligible"
        # inferred 计数可为 0（相邻对全被 source_causal 覆盖时），只要是非负 int
        assert isinstance(basis["inferred_edge_count"], int)
        assert basis["inferred_edge_count"] >= 0
        assert isinstance(basis["component_evidence_anchors"], list)

    def test_fallback_fail_closed_carries_basis_zero_eligible(self):
        """0 eligible edge → fail-closed，但 data 携 fallback_quality_basis（D9 不靠 error）。"""
        # 黄金样张：编号列表有实体名（≥3 组件）但无因果语言 → 0 eligible
        raw = (
            "# Loop Article\n\n"
            "A well-designed loop has five components:\n"
            "1. A trigger (schedule, event, or manual)\n"
            "2. A work unit selector (read ledger, pick next item)\n"
            "3. An action executor (run the actual work)\n"
        )
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success is False, "0 eligible edge 应 fail-closed"
        basis = result.data.get("fallback_quality_basis")
        assert basis is not None, "失败路径也须携 fallback_quality_basis（D9）"
        assert basis["component_count"] >= 3
        assert basis["source_causal_edge_count"] == 0, "无因果语言 → 0 eligible"
        assert "eligible" in result.error or "因果边" in result.error

    def test_fallback_fail_closed_carries_basis_too_few_components(self):
        """<3 组件 → fail-closed，data 携 basis（component_count < 3 可见）。"""
        raw = "# Some Safety Article\n\nPure policy prose without component headings."
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success is False
        basis = result.data.get("fallback_quality_basis")
        assert basis is not None
        assert basis["component_count"] < 3


# ---------------------------------------------------------------------------
# §0.2/D2：schema 扩展 + normalize 保留新字段 + edge_kind fail-closed
# ---------------------------------------------------------------------------


class TestD2SchemaNormalize:
    """§0.2/D2：mechanism_components/causal_edges 扩字段 + normalize 保留 + 校验。

    F4 D1 eligible edge 依赖 evidence_anchor；§4 两阶段抽取产稳定 component_id + evidence。
    normalize 必须保留这些字段（不丢），未知 edge_kind fail-closed（不静默丢弃）。
    """

    def test_normalize_mechanism_components_preserves_d2_fields(self):
        """component_id/aliases/evidence_anchor/evidence_sentence 透传 normalize。"""
        raw = [
            {
                "component_id": "captain",
                "name": "Captain",
                "aliases": ["dispatcher", "coordinator"],
                "role": "dispatch",
                "evidence_anchor": "§A",
                "evidence_sentence": "The Captain dispatches to the Executor Pool",
            }
        ]
        out = _normalize_mechanism_components(raw)
        assert len(out) == 1
        c = out[0]
        assert c["component_id"] == "captain"
        assert c["name"] == "Captain"
        assert c["aliases"] == ["dispatcher", "coordinator"]
        assert c["role"] == "dispatch"
        assert c["evidence_anchor"] == "§A"
        assert c["evidence_sentence"].startswith("The Captain")

    def test_normalize_mechanism_components_aliases_coercion(self):
        """aliases 字符串 → 逗号分隔 list；非 list/str → 空数组；默认空。"""
        out = _normalize_mechanism_components(
            [
                {"name": "A", "aliases": "x, y, z"},
                {"name": "B", "aliases": None},
                {"name": "C"},
            ]
        )
        assert out[0]["aliases"] == ["x", "y", "z"]
        assert out[1]["aliases"] == []
        assert out[2]["aliases"] == []

    def test_normalize_mechanism_components_defaults_empty(self):
        """无 D2 字段时 component_id/evidence_* 默认 ""，aliases 默认 []。"""
        out = _normalize_mechanism_components([{"name": "A", "role": "r"}])
        c = out[0]
        assert c["component_id"] == ""
        assert c["evidence_anchor"] == ""
        assert c["evidence_sentence"] == ""
        assert c["aliases"] == []
        assert c["name"] == "A"
        assert c["role"] == "r"

    def test_normalize_causal_edges_preserves_d2_fields(self):
        """edge_kind/evidence/evidence_anchor 透传 normalize。"""
        raw = [
            {
                "from": "Captain",
                "to": "Executor Pool",
                "relation": "dispatches",
                "edge_kind": "source_causal",
                "evidence": "The Captain dispatches to the Executor Pool",
                "evidence_anchor": "§A",
            }
        ]
        out = _normalize_causal_edges(raw)
        assert len(out) == 1
        e = out[0]
        assert e["edge_kind"] == "source_causal"
        assert e["evidence"].startswith("The Captain")
        assert e["evidence_anchor"] == "§A"
        assert e["relation"] == "dispatches"

    def test_normalize_causal_edges_unknown_edge_kind_fail_closed(self):
        """未知 edge_kind → raise ValueError（fail-closed，不静默丢弃）。"""
        import pytest

        raw = [
            {
                "from": "A",
                "to": "B",
                "relation": "r",
                "edge_kind": "bogus_kind",
            }
        ]
        with pytest.raises(ValueError, match="未知 edge_kind"):
            _normalize_causal_edges(raw)

    def test_normalize_causal_edges_valid_edge_kinds(self):
        """三种合法 edge_kind 都接受；空 edge_kind 接受（LLM 未产时）。"""
        for kind in ("source_causal", "llm_evidence", "inferred_from_structure", ""):
            out = _normalize_causal_edges(
                [{"from": "A", "to": "B", "relation": "r", "edge_kind": kind}]
            )
            assert out[0]["edge_kind"] == kind

    def test_normalize_causal_edges_defaults_empty(self):
        """无 D2 字段时 edge_kind/evidence/evidence_anchor 默认 ""。"""
        out = _normalize_causal_edges([{"from": "A", "to": "B", "relation": "r"}])
        e = out[0]
        assert e["edge_kind"] == ""
        assert e["evidence"] == ""
        assert e["evidence_anchor"] == ""

    def test_d2_fields_pass_extract_schema(self):
        """带 D2 字段的 mechanism_components/causal_edges 过 _validate_extract_output
        （_EXTRACT_SCHEMA + _HOW_PARAMS_SCHEMA 都已扩 properties）。"""
        data = _base_concept_data(
            [
                {
                    "component_id": "a",
                    "name": "A",
                    "aliases": ["aa"],
                    "role": "r",
                    "evidence_anchor": "§A",
                    "evidence_sentence": "A does x",
                },
                {"name": "B", "role": "r"},
                {"name": "C", "role": "r"},
            ],
            [
                {
                    "from": "A",
                    "to": "B",
                    "relation": "r",
                    "edge_kind": "source_causal",
                    "evidence": "A feeds B",
                    "evidence_anchor": "§A",
                },
                {
                    "from": "B",
                    "to": "C",
                    "relation": "r",
                    "edge_kind": "inferred_from_structure",
                },
            ],
        )
        _validate_extract_output(data, content_lane="concept")  # 不 raise 即过


# ---------------------------------------------------------------------------
# WP-3.2: F4 extract 即判
# ---------------------------------------------------------------------------


class TestF4ExtractValidation:
    """§0.2/A2：F4 不在 distill extract 前移——extract 不判 F4，deferred to integrity_floor。

    旧 WP-3.2 把 _core_value_f4_failed 前移到 _validate_extract_output，与 runner floor
    （runner.py:651-653）+ 质量段 early-return（runner.py:988-1017）双跑同一 F4，且 floor
    在 A层/grader 之前 early-return，F4 计边发生在正文/A层/grader 不存在时——顺序矛盾
    （§0.2 D1）。A2 删前移：F4 唯一裁决点回 integrity_floor（test_integrity_floor.py
    TestCoreValueF4 已覆盖 <3/孤立/重复名/0边 各 fail）。本类只验 extract 不再早判 F4。
    """

    def test_f4_less_than_three_components_does_not_raise_at_extract(self):
        """concept <3 命名组件 → extract 不 raise（F4 deferred to floor，由 floor fail）。"""
        data = _base_concept_data([{"name": "A"}], [])
        _validate_extract_output(data, content_lane="concept")  # 不 raise 即过 extract
        # F4 fail 由 integrity_floor 兜底（见 test_integrity_floor TestCoreValueF4）

    def test_f4_isolated_components_do_not_raise_at_extract(self):
        """3 组件但无因果边（孤立）→ extract 不 raise（F4 连通性 deferred to floor）。"""
        data = _base_concept_data(
            [{"name": "A"}, {"name": "B"}, {"name": "C"}],
            [],  # 无边 → 全孤立
        )
        _validate_extract_output(data, content_lane="concept")  # 不 raise 即过 extract

    def test_f4_duplicate_names_do_not_raise_at_extract(self):
        """重复组件名 [A,A,B] → extract 不 raise（去重判 uniqueness deferred to floor）。"""
        data = _base_concept_data(
            [{"name": "A"}, {"name": "A"}, {"name": "B"}],
            [{"from": "A", "to": "B", "relation": "r"}],
        )
        _validate_extract_output(data, content_lane="concept")  # 不 raise 即过 extract

    def test_f4_three_unique_connected_passes(self):
        """3 唯一组件 + 连边 → extract 过（floor 也过；双向一致）。"""
        data = _base_concept_data(
            [{"name": "A"}, {"name": "B"}, {"name": "C"}],
            [
                {"from": "A", "to": "B", "relation": "r"},
                {"from": "B", "to": "C", "relation": "r"},
            ],
        )
        _validate_extract_output(data, content_lane="concept")  # 不 raise 即过

    def test_f4_floor_still_rejects_zero_edges_and_isolated(self):
        """A2 不放底线：extract 不判 F4，但 floor 仍判 0 边/孤立 fail。

        防护：确保删前移不等于放弃 F4——floor（integrity_floor._core_value_f4_failed）
        仍是唯一裁决点，0 eligible edge / 孤立组件仍 fail-closed。
        """
        from src.akb2.quality.integrity_floor import (
            FLOOR_CORE_VALUE_F4,
            evaluate_integrity_floor,
        )

        # 0 边 → floor F4 fail
        d_zero_edges = _base_concept_data(
            [{"name": "A"}, {"name": "B"}, {"name": "C"}], []
        )
        r_zero = evaluate_integrity_floor(d_zero_edges, "concept")
        assert FLOOR_CORE_VALUE_F4 in r_zero.failed_floors, "0 边仍应 floor F4 fail"
        # 孤立组件 → floor F4 fail
        d_isolated = _base_concept_data(
            [{"name": "A"}, {"name": "B"}, {"name": "C"}],
            [{"from": "A", "to": "B", "relation": "r"}],  # C 无边
        )
        r_iso = evaluate_integrity_floor(d_isolated, "concept")
        assert FLOOR_CORE_VALUE_F4 in r_iso.failed_floors, "孤立组件仍应 floor F4 fail"


# ---------------------------------------------------------------------------
# WP-3.1/D8: concept fallback 不造假机制
# ---------------------------------------------------------------------------


class TestConceptFallbackNoFakeMechanism:
    """concept deterministic fallback：扫 raw 找真实组件，不硬编码 loop 组件。"""

    def test_no_mechanism_raw_fail_closed(self):
        """raw 无 ≥3 命名组件 → fallback fail-closed（concept no-mechanism 不被救活）。"""
        raw = (
            "# Some Safety Article\n\n"
            "Pure policy prose without numbered component headings. "
            "The standards body agrees on shared principles."
        )
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success is False, "无机制 raw 不应被 fallback 救活（fail-closed）"
        assert "fail-closed" in result.error or "机制" in result.error

    def test_policy_commitment_numbered_list_not_saved(self):
        """P1-A: 政策承诺编号列表（动词,无机制实体名）→ 不被 fallback 救活。

        "1. Establish shared safety standards / 2. Coordinate incident reporting /
        3. Build public trust" —— 编号项是动词承诺, 无机制实体名（trigger/组件/角色等）
        → scanner 不纳 → <3 组件 → fail-closed（区别于"1. A trigger"实体名编号列表）。
        """
        raw = (
            "# Safety Standards Commitment\n\n"
            "We commit to the following principles:\n"
            "1. Establish shared safety standards across the industry\n"
            "2. Coordinate incident reporting between organizations\n"
            "3. Build public trust through transparency\n"
            "4. Promote accountability and responsible deployment\n"
        )
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success is False, (
            "政策承诺编号列表（动词,无机制实体名）不应被 fallback 救活"
        )
        assert "fail-closed" in result.error or "机制" in result.error

    def test_mechanism_raw_uses_real_components(self):
        """raw 有 ≥3 编号组件 + 真实因果语言 → fallback 用真实组件（非硬编码 loop）。"""
        raw = (
            "# \u00a7A Captain Architecture\n\n"
            "### 1. Captain\nThe Captain dispatches to the Executor Pool.\n\n"
            "### 2. Executor Pool\nThe Executor Pool feeds the Task Graph.\n\n"
            "### 3. Task Graph\nThe Task Graph writes to the Lifecycle.\n\n"
            "### 4. Lifecycle\nThe Lifecycle notifies the Captain.\n"
        )
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success is True, f"有机制 raw 应过 fallback: {result.error}"
        components = result.data.get("mechanism_components", [])
        names = [c["name"] for c in components]
        # 真实组件来自 raw 标题（Captain/Executor Pool/Task Graph/Lifecycle）
        assert "Captain" in names
        assert "Executor Pool" in names
        # 不是硬编码 loop 组件
        hardcoded_loop = {"trigger", "work_selector", "quality_gate", "ledger"}
        assert not any(n.lower() in hardcoded_loop for n in names), (
            f"不应硬编码 loop 组件, got {names}"
        )
        # 因果边存在（顺序连边，F4 连通）
        edges = result.data.get("causal_edges", [])
        assert len(edges) >= 2

    def test_procedural_fallback_unchanged(self):
        """procedural fallback 不受 concept 修复影响（零回归：how_steps 保留）。"""
        raw = "# Some Loop Article\n\nProse about agent loop with ledger and quality gate."
        result = build_deterministic_extract_result(raw, content_lane="procedural")
        # procedural 不扫真实组件（mechanism_components=[]），但 how_steps 保留
        assert result.data.get("mechanism_components") == []
        assert len(result.data.get("how_steps", [])) > 0, "procedural how_steps 应保留"


# ---------------------------------------------------------------------------
# WP-3.1/D7: idx5 poisoned trace — concept fallback 不生成假 AKB 操作化步骤
# ---------------------------------------------------------------------------


class TestPoisonedTraceNoFakeSteps:
    """concept fallback how_steps=[]，不生成 schema/ledger/max_repair_rounds 假步骤。"""

    def test_concept_fallback_how_steps_empty(self):
        """concept fallback how_steps 为空（不注假 AKB 操作）。"""
        raw = (
            "# \u00a7A Concept Article\n\n"
            "### 1. Alpha\nThe Alpha feeds the Beta.\n\n"
            "### 2. Beta\nThe Beta triggers the Gamma.\n\n"
            "### 3. Gamma\nThe Gamma writes to the Alpha.\n"
        )
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success is True
        how_steps = result.data.get("how_steps", [])
        assert how_steps == [], f"concept how_steps 应为空, got {how_steps}"

    def test_no_fake_akb_operation_literals(self):
        """concept fallback data 不含 schema/ledger/max_repair_rounds 假步骤字面量（idx5 形态）。"""
        raw = (
            "# \u00a7A Prompt Caching Article\n\n"
            "### 1. KV Cache\nThe KV Cache feeds the Breakpoint.\n\n"
            "### 2. Breakpoint\nThe Breakpoint triggers the Hit Rate.\n\n"
            "### 3. Hit Rate\nThe Hit Rate writes to the KV Cache.\n"
        )
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success is True
        # how_steps 为空 → 无假步骤
        assert result.data.get("how_steps") == []
        # params 为空 → 无 max_repair_rounds 等假配置
        assert result.data.get("params") == {}
        # 整个 data 的 how_steps/params 不含 idx5 假步骤字面量
        fake_markers = ("max_repair_rounds", "ledger.append", "queue_item", "grader_model")
        how_text = str(result.data.get("how_steps", []))
        params_text = str(result.data.get("params", {}))
        for marker in fake_markers:
            assert marker not in how_text, f"how_steps 不应含假步骤 {marker}"
            assert marker not in params_text, f"params 不应含假配置 {marker}"


# ---------------------------------------------------------------------------
# WP-3.4: frontmatter 同源 resolve + Gate-3 real_link
# ---------------------------------------------------------------------------


def _frontmatter_data(concepts: list[str]) -> dict:
    """构造 extract data 供 _build_frontmatter（只需 referenced_concepts + claims）。"""
    return {
        "claims": [
            {
                "text": "a claim about the mechanism",
                "anchor": "§A",
                "provenance": "source_grounded",
                "evidence_gap": "未覆盖：原文未展开",
            }
        ],
        "how_steps": [],
        "mechanism_components": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
        "causal_edges": [{"from": "A", "to": "B", "relation": "r"}],
        "params": {},
        "referenced_concepts": concepts,
        "tldr": "tldr with enough characters",
        "argumentation": [
            {"claim_anchor": "§A", "explanation": "...", "provenance": "source_grounded"}
        ],
        "applicability_boundary": "boundary with enough characters",
        "graph_edges": [],
    }


def _write_gate_candidate(
    vault: Path, name: str, related_concepts: list[str], graph_row: str
) -> Path:
    """构造真实 gate CLI 候选文件（结构完整，变 related_concepts + 图谱行）。

    基于 test_promote_gates.gates_vault_root 通过基线候选，只变 related_concepts
    测图谱闸（HC-07）对悬空/已 resolve 的真实判定（不 mock _run_gate_check）。
    """
    (vault / "staging" / "candidate" / "b").mkdir(parents=True, exist_ok=True)
    (vault / "wiki" / "sources").mkdir(parents=True, exist_ok=True)
    cand = vault / "staging" / "candidate" / "b" / f"{name}.md"
    related_fm = ", ".join(related_concepts)
    graph_section = (
        "## 图谱关系\n\n| 目标 | 关系 | 强度 | 证据 |\n|------|------|------|------|\n"
        f"{graph_row}\n" if graph_row else "## 图谱关系\n\n（无图谱边）\n"
    )
    cand.write_text(f"""---
page_status: candidate
source_type: blog
template_type: source
source_refs: [raw/{name}--raw.md]
future_questions:
  - Q1: Test one?
  - Q2: Test two?
related_concepts: [{related_fm}]
gate_decision: null
slug: {name}
title: {name} Article
source_url: https://example.com/{name}
date: 2026-06-13
evidence_provenance: source_grounded
graph_role: leaf
graph_status: healthy
key_findings:
  - finding one
  - finding two
  - finding three
---

## TLDR

Test article for five-gate validation.

## 关键发现

Finding one. Finding two. Finding three.

## 入选理由

Test inclusion.

## 证据地图

| 证据 | 来源 | 强度 |
|------|------|------|
| Test | [raw] | strong |

## 核心论点

Core argument.

## 论证与解释

Detailed explanation.

## 适用边界

Test scenarios.

{graph_section}
## Future Questions

Q1: Test one?
Q2: Test two?

## 维护记录

| 2026-06-13 | Initial | gate: null |""")
    return cand


class TestFrontmatterResolveAndRealLink:
    """frontmatter related_* 同源 resolve，悬空不产 real_link（round-2 P1）。"""

    def test_resolved_concepts_in_related(self):
        """known 含部分 referenced → related_concepts 只写可 resolve 的（悬空滤除）。"""
        data = _frontmatter_data(["loop-engineering", "unknown-concept"])
        fm = _build_frontmatter(
            "raw/fake--raw--2026-06-29.md",
            data,
            content_lane="concept",
            known_concepts=["loop-engineering"],
        )
        related = fm["related_concepts"]
        # 可 resolve 的写入 [[...]]
        assert "[[loop-engineering]]" in related
        # 悬空的不写
        assert not any("unknown-concept" in r for r in related), (
            f"悬空概念不应写入 related: {related}"
        )

    def test_dangling_no_real_link(self):
        """P1-B: referenced 全不可 resolve（known=[] 提供但空）→ related_concepts=[] → 不产 real_link。

        撤销 c54f327 削弱（known 从 ["some-other-concept"] 改回 []）——P1-B 区分 None vs []，
        known=[]（提供但空）→ resolver 过滤分支生效 → _resolve_graph_node 全 False → related=[]。
        """
        from src.akb2.gate.akb2_gate_check import _graph_readiness

        data = _frontmatter_data(["totally-unknown-concept"])
        fm = _build_frontmatter(
            "raw/fake--raw--2026-06-29.md",
            data,
            content_lane="concept",
            known_concepts=[],  # P1-B: 提供但空 → 过滤生效，全滤除
        )
        assert fm["related_concepts"] == [], "悬空概念应全滤除，related_concepts=[]"
        # gate _graph_readiness 仅查 related_* 非空 → 空则不产 real_link
        assert _graph_readiness(fm) != "real_link", (
            "悬空 frontmatter related 不应产 real_link"
        )
        # §0.2/D8：冷启动写 proposed_graph_nodes → _graph_readiness 返 "proposed_node_only"
        # （gate CLI 本就支持 proposed_node_only + decision_confidence=medium）
        assert _graph_readiness(fm) == "proposed_node_only", (
            "D8 冷启动（proposed_graph_nodes 非空）应返 proposed_node_only"
        )

    def test_cold_start_writes_proposed_graph_nodes(self):
        """§0.2/D8：resolver 提供但 related < MIN（冷启动）→ 写 proposed_graph_nodes + graph_pending。

        冷启动 concept 候选（referenced 未 resolve）写 proposed_graph_nodes（每项
        {slug,label,reason,source_anchor}）+ graph_pending=true；proposal 模式接受
        proposed_graph_nodes>=1（check_a2_frontmatter D8），auto-promote 二次拒写 active。
        """
        data = _frontmatter_data(["totally-unknown-concept", "another-unknown"])
        fm = _build_frontmatter(
            "raw/fake--raw--2026-06-29.md",
            data,
            content_lane="concept",
            known_concepts=[],  # 提供但空 → 冷启动
        )
        # related_concepts 仍空（未 resolve）
        assert fm["related_concepts"] == []
        # D8：写 proposed_graph_nodes + graph_pending
        assert fm.get("graph_pending") is True, "冷启动应 graph_pending=true"
        proposed = fm.get("proposed_graph_nodes", [])
        assert len(proposed) >= 1, "冷启动应写 ≥1 proposed_graph_node"
        for node in proposed:
            assert "slug" in node and "label" in node and "reason" in node and "source_anchor" in node
        # slug 是 slugify 后的稳定 id
        labels = [n["label"] for n in proposed]
        assert "totally-unknown-concept" in labels

    def test_resolved_no_proposed_graph_nodes(self):
        """§0.2/D8：已 resolve ≥MIN（related≥2）→ 不写 proposed_graph_nodes，graph_pending=False。"""
        data = _frontmatter_data(["loop-engineering", "agent-collaboration"])
        fm = _build_frontmatter(
            "raw/fake--raw--2026-06-29.md",
            data,
            content_lane="concept",
            known_concepts=["loop-engineering", "agent-collaboration"],  # 已 resolve ≥2
        )
        assert fm.get("graph_pending") is False
        assert fm.get("proposed_graph_nodes") == []

    def test_resolved_produces_real_link(self):
        """≥1 真实 resolved related → related_concepts 非空 → gate real_link。"""
        from src.akb2.gate.akb2_gate_check import _graph_readiness

        data = _frontmatter_data(["loop-engineering"])
        fm = _build_frontmatter(
            "raw/fake--raw--2026-06-29.md",
            data,
            content_lane="concept",
            known_concepts=["loop-engineering"],
        )
        assert "[[loop-engineering]]" in fm["related_concepts"]
        assert _graph_readiness(fm) == "real_link", "≥1 真实 resolved related 应产 real_link"

    def test_legacy_none_known_keeps_behavior(self):
        """known/pending 均 None（legacy/测试）→ 保留旧 _infer_concepts fallback（零回归）。"""
        data = _frontmatter_data([])  # 空 referenced，靠 _infer_concepts
        # claims 不含关键词 → _infer_concepts 返回 []，related=[] → 但 legacy 不滤除
        fm = _build_frontmatter(
            "raw/fake--raw--2026-06-29.md",
            data,
            content_lane="concept",
            # known_concepts/pending_queue 均 None
        )
        # legacy 行为：related_links 可能空（_infer_concepts 找不到关键词），
        # 关键是不走 resolve 过滤分支（不 raise、返回正常 frontmatter）
        assert "related_concepts" in fm
        assert isinstance(fm["related_concepts"], list)

    def test_evaluate_five_gates_real_cli_dangling_rejected(self, tmp_path):
        """P1-B 真实 CLI（不 mock）: 悬空 candidate(related=[]) → 真实 gate CLI →
        proposed=needs_repair + graph_readiness=none, evaluate_five_gates 拒绝 + 不产 active。

        真实拦截行为: 悬空候选被五闸拒（失败在真实 Gate-1 HC-07 无图谱链接，
        非伪造的 Gate-3）。证明 P1-B 的真实安全性。
        """
        from src.akb2.quality.promote import _run_gate_check, evaluate_five_gates

        vault = tmp_path / "vault"
        cand = _write_gate_candidate(vault, "dangling", related_concepts=[], graph_row="")
        # 真实 gate CLI（无 mock）
        gate_ok, detail, parsed = _run_gate_check(cand, vault)
        assert parsed.get("graph_readiness") == "none", (
            f"悬空(related=[])应 graph_readiness=none: {detail}"
        )
        assert parsed.get("proposed_gate_decision") != "active", (
            f"悬空 candidate 不应 proposed=active: {detail}"
        )
        # evaluate_five_gates 真实拒绝 + 不产 active
        all_passed, _failed_gate, _reason = evaluate_five_gates(cand, vault, None)
        assert all_passed is False, "悬空 candidate 应被五闸真实拒绝"
        active_path = vault / "wiki" / "sources" / "dangling.md"
        assert not active_path.exists(), "悬空 candidate 不应产 active 写入"

    def test_evaluate_five_gates_real_cli_resolved_passes_graph_gate(self, tmp_path):
        """P1-B 真实 CLI（不 mock）: 已 resolve candidate(related=[concept]) →
        graph_readiness=real_link → 过图谱闸（不因悬空 fail；到达后续闸）。

        对照: 同结构候选只把 related_concepts 从 [] 换成 [concept] → graph_readiness
        翻转为 real_link，证明悬空/已 resolve 的真实区分。
        """
        from src.akb2.quality.promote import _run_gate_check

        vault = tmp_path / "vault"
        cand = _write_gate_candidate(
            vault, "resolved", related_concepts=["concept-test"],
            graph_row="| concept-test | supports | medium | Test |",
        )
        gate_ok, detail, parsed = _run_gate_check(cand, vault)
        assert parsed.get("graph_readiness") == "real_link", (
            f"已 resolve(related=[concept])应 graph_readiness=real_link: {detail}"
        )


# ---------------------------------------------------------------------------
# P2-E: D7 idx5 poisoned trace 回归 fixture（langchain prompt-caching 形态）
# ---------------------------------------------------------------------------


class TestD7Idx5PoisonedTraceFixture:
    """P2-E: D7 idx5 poisoned trace 回归 fixture。

    旧候选（pre-fix deterministic fallback）注入假 AKB 操作化步骤 → grader 判
    interpretation_entailment=False（未从 cited anchor 推出，poisoned trace）。
    新候选（post-fix D8: concept fallback how_steps=[]）无假步骤。
    entailment 真值由 WP-5 端到端 LLM 验收（worker env 无 DEEPSEEK_API_KEY，不跑真 GPT-5.4）。
    """

    _IDX5_FAKE_MARKERS = (
        "max_repair_rounds", "ledger.append", "queue_item", "grader_model",
    )

    def test_old_candidate_fake_steps_poison_source(self):
        """旧候选含假 AKB 操作步骤（poison 源）→ entailment=False 根因。

        旧 deterministic fallback 硬编码 how_steps 注入 max_repair_rounds=3 / ledger.append /
        queue_item schema（raw 未支持），grader 判 interpretation_entailment=False。
        注: entailment=False 真值由 WP-5 端到端 LLM 验收；本测试验 poison 源（假步骤）在场。
        """
        # 旧 fallback 硬编码的 how_steps 字面量（D8 修复前的形态，distill.py 旧版）
        old_how_steps_text = (
            "Define the work item schema queue_item={source_url,raw_anchor,content_hash}\n"
            "Write a ledger start event ledger.append({event:'distill_extract'})\n"
            "Stop after bounded repair max_repair_rounds=3; assert remaining_defects == 0\n"
            "assert grader_model == 'gpt-5.4' and fallback_used is False\n"
        )
        for marker in self._IDX5_FAKE_MARKERS:
            assert marker in old_how_steps_text, (
                f"旧候选应含假步骤 {marker}（entailment=False 的 poison 源）"
            )

    def test_new_candidate_idx5_no_fake_steps(self):
        """新候选（post-fix D8）idx5 形态（prompt-caching concept）→ 无假 AKB 操作步骤。

        修复后 concept fallback how_steps=[]+params={}，不注入 max_repair_rounds/ledger/
        queue_item → entailment 不再被假步骤毒化（真值由 WP-5 LLM 验收）。
        """
        raw = (
            "# \u00a7A Prompt Caching Article\n\n"
            "### 1. KV Cache\nThe KV Cache feeds the Breakpoint.\n\n"
            "### 2. Breakpoint\nThe Breakpoint triggers the Hit Rate.\n\n"
            "### 3. Hit Rate\nThe Hit Rate writes to the KV Cache.\n"
        )
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success is True
        assert result.data.get("how_steps") == []
        assert result.data.get("params") == {}
        for marker in self._IDX5_FAKE_MARKERS:
            assert marker not in str(result.data.get("how_steps", []))
            assert marker not in str(result.data.get("params", {}))


# ---------------------------------------------------------------------------
# §4 两阶段结构化抽取（LLM 路径，mock 响应产 D2 字段 → eligible edge）
# ---------------------------------------------------------------------------


def _claims_seg_concept() -> dict:
    """概念型 claims 段（≤40% akb，每条带 anchor + evidence_gap）。"""
    return {
        "claims": [
            {
                "text": "Loop engineering 把 agent 工作收束为有停止条件的反馈循环",
                "anchor": "§A", "provenance": "source_grounded",
                "evidence_gap": "未量化：原文未提供该机制的成功率",
            },
            {
                "text": "工作队列携带 source URL 与生命周期状态",
                "anchor": "§B", "provenance": "source_grounded",
                "evidence_gap": "未覆盖：原文未展开并发冲突处理",
            },
            {
                "text": "修复路径固定轮数、只在缺陷清零时接受候选",
                "anchor": "§C", "provenance": "source_grounded",
                "evidence_gap": "未比较：原文未比较修复策略效果",
            },
        ],
        "tldr": "Loop engineering 用命名机制组件与因果边把 agent 主链路讲清为可观测反馈循环。",
    }


def _how_seg_with_eligible_edges(components, edges) -> dict:
    """构造 how_params 段：含 D2 字段的 components + source_causal eligible edges。"""
    return {
        "how_steps": [
            {"instruction": "trigger 通过调度启动 loop", "command": "", "executable": False, "how_level": "H3"},
        ],
        "mechanism_components": components,
        "causal_edges": edges,
        "param_entries": [{"key": "max_rounds", "value": "3"}],
    }


def _make_segment_route_v2(claims_seg, how_seg):
    """mock route_llm：按 prompt 关键词返回 claims/how_params 段。"""
    def route(model_family, messages, response_schema=None, **kwargs):
        joined = " ".join(m.get("content", "") for m in messages)
        if "mechanism_components" in joined or "causal_edges" in joined or "how_steps" in joined:
            return LLMResponse(content=how_seg, model_used=model_family)
        return LLMResponse(content=claims_seg, model_used=model_family)
    return route


def _golden_loop_components_edges():
    """golden loop 风格：trigger/selector/executor/gate/ledger 组件 + 散文因果 eligible 边。"""
    components = [
        {"name": "trigger", "aliases": ["scheduler"], "role": "启动 loop",
         "evidence_anchor": "§B", "evidence_sentence": "A trigger (schedule, event, or manual)"},
        {"name": "work_selector", "aliases": ["selector"], "role": "选下一个工作单元",
         "evidence_anchor": "§B", "evidence_sentence": "A work unit selector (read ledger, pick next)"},
        {"name": "action_executor", "aliases": ["executor"], "role": "执行工作",
         "evidence_anchor": "§B", "evidence_sentence": "An action executor (run the actual work)"},
        {"name": "quality_gate", "role": "校验输出",
         "evidence_anchor": "§B", "evidence_sentence": "A quality gate (verify output meets standards)"},
        {"name": "ledger_writer", "aliases": ["ledger"], "role": "记录",
         "evidence_anchor": "§B", "evidence_sentence": "A ledger writer (record what happened)"},
    ]
    # 散文因果：trigger 启动 → selector 选 → executor 执行（eligible，带 evidence+anchor）
    edges = [
        {"from": "trigger", "to": "work_selector", "relation": "activates",
         "edge_kind": "source_causal", "evidence": "the trigger activates the work selector",
         "evidence_anchor": "§B"},
        {"from": "work_selector", "to": "action_executor", "relation": "feeds",
         "edge_kind": "source_causal", "evidence": "the selector feeds the executor",
         "evidence_anchor": "§B"},
    ]
    return components, edges


def _ag2_captain_components_edges():
    """ag2 captain 风格：Captain/Executor Pool/Task Graph + 跨句代词因果 eligible 边。"""
    components = [
        {"name": "Captain", "aliases": ["coordinator"], "role": "dispatch",
         "evidence_anchor": "§A", "evidence_sentence": "The Captain dispatches to the Executor Pool"},
        {"name": "Executor Pool", "role": "run",
         "evidence_anchor": "§A", "evidence_sentence": "The Executor Pool feeds the Task Graph"},
        {"name": "Task Graph", "role": "track",
         "evidence_anchor": "§A", "evidence_sentence": "The Task Graph writes to the Lifecycle"},
    ]
    edges = [
        {"from": "Captain", "to": "Executor Pool", "relation": "dispatches",
         "edge_kind": "source_causal", "evidence": "The Captain dispatches to the Executor Pool",
         "evidence_anchor": "§A"},
    ]
    return components, edges


def _vercel_components_edges():
    """vercel 风格：streaming parser + buffer + renderer 组件 + eligible 边。"""
    components = [
        {"name": "StreamingParser", "aliases": ["parser"], "role": "parse chunks",
         "evidence_anchor": "§A", "evidence_sentence": "The streaming parser parses incoming chunks"},
        {"name": "Buffer", "role": "accumulate",
         "evidence_anchor": "§A", "evidence_sentence": "The buffer accumulates partial chunks"},
        {"name": "Renderer", "role": "render",
         "evidence_anchor": "§A", "evidence_sentence": "The renderer renders the buffered output"},
    ]
    edges = [
        {"from": "StreamingParser", "to": "Buffer", "relation": "feeds",
         "edge_kind": "source_causal", "evidence": "the parser feeds the buffer",
         "evidence_anchor": "§A"},
    ]
    return components, edges


class TestSection4TwoPhaseExtraction:
    """§4 两阶段抽取：LLM 产 D2 字段 → _enrich_mechanism 后处理 → eligible edge → F4 pass。

    验收（spec §4.5 + §8.3）：golden loop / ag2 captain / vercel 各产 ≥3 唯一组件 +
    ≥1 条 eligible edge（带 evidence_anchor）；负样本（政策承诺）仍 0 组件 fail-closed。
    """

    def test_golden_loop_produces_eligible_edge_and_passes_f4(self):
        """golden loop 风格：LLM 产 trigger→selector→executor eligible 边 → F4 pass。"""
        comps, edges = _golden_loop_components_edges()
        route = _make_segment_route_v2(
            _claims_seg_concept(), _how_seg_with_eligible_edges(comps, edges)
        )
        result = extract_claims("# §A Loop\n\ncontent", route_llm_fn=route, content_lane="concept")
        assert result.success is True, result.error
        merged = result.data
        assert len(merged["mechanism_components"]) >= 3
        eligible = [e for e in merged["causal_edges"]
                    if e.get("edge_kind") in ("source_causal", "llm_evidence")
                    and e.get("evidence") and e.get("evidence_anchor")]
        assert len(eligible) >= 1, "golden loop 应产 ≥1 eligible edge（§4 救率硬证据）"
        # floor F4 pass（D1：≥3 组件 + ≥1 eligible edge）
        r = evaluate_integrity_floor(merged, "concept")
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors, f"golden F4 应 pass: {r.reason}"

    def test_ag2_captain_produces_eligible_edge_and_passes_f4(self):
        """ag2 captain 风格：跨句代词因果 → eligible edge → F4 pass。"""
        comps, edges = _ag2_captain_components_edges()
        route = _make_segment_route_v2(
            _claims_seg_concept(), _how_seg_with_eligible_edges(comps, edges)
        )
        result = extract_claims("# §A Captain\n\ncontent", route_llm_fn=route, content_lane="concept")
        assert result.success is True, result.error
        eligible = [e for e in result.data["causal_edges"]
                    if e.get("edge_kind") in ("source_causal", "llm_evidence")
                    and e.get("evidence") and e.get("evidence_anchor")]
        assert len(eligible) >= 1, "ag2 captain 应产 ≥1 eligible edge"
        r = evaluate_integrity_floor(result.data, "concept")
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors

    def test_vercel_produces_eligible_edge_and_passes_f4(self):
        """vercel 风格：parser→buffer eligible 边 → F4 pass。"""
        comps, edges = _vercel_components_edges()
        route = _make_segment_route_v2(
            _claims_seg_concept(), _how_seg_with_eligible_edges(comps, edges)
        )
        result = extract_claims("# §A Vercel\n\ncontent", route_llm_fn=route, content_lane="concept")
        assert result.success is True, result.error
        eligible = [e for e in result.data["causal_edges"]
                    if e.get("edge_kind") in ("source_causal", "llm_evidence")
                    and e.get("evidence") and e.get("evidence_anchor")]
        assert len(eligible) >= 1, "vercel 应产 ≥1 eligible edge"
        r = evaluate_integrity_floor(result.data, "concept")
        assert FLOOR_CORE_VALUE_F4 not in r.failed_floors

    def test_enrich_generates_component_id(self):
        """§4 阶段A：_enrich_mechanism 为每个组件生成稳定 component_id（slugify name）。"""
        from src.akb2.quality.distill import _enrich_mechanism
        comps = [{"name": "Captain", "role": "r", "aliases": [], "evidence_anchor": "", "evidence_sentence": ""},
                 {"name": "Executor Pool", "role": "r", "aliases": [], "evidence_anchor": "", "evidence_sentence": ""}]
        edges = []
        out_comps, _ = _enrich_mechanism(comps, edges)
        assert out_comps[0]["component_id"] == "captain"
        assert out_comps[1]["component_id"] == "executor_pool"

    def test_enrich_alias_alignment_resolves_alias_to_name(self):
        """§4 阶段B：edge.from/to 用 alias → _enrich 对齐到 component name。"""
        from src.akb2.quality.distill import _enrich_mechanism
        comps = [{"name": "Captain", "aliases": ["coordinator", "dispatcher"], "role": "r"}]
        edges = [{"from": "coordinator", "to": "Captain", "relation": "r",
                  "edge_kind": "source_causal", "evidence": "x", "evidence_anchor": "§A"}]
        # 单组件，from=coordinator(alias)→to=Captain，但 src!=dst 要求两不同组件；
        # 加第二个组件让边端点都在 components
        comps = [{"name": "Captain", "aliases": ["coordinator"], "role": "r"},
                 {"name": "Executor", "aliases": ["worker"], "role": "r"}]
        edges = [{"from": "coordinator", "to": "worker", "relation": "dispatches",
                  "edge_kind": "source_causal", "evidence": "dispatches", "evidence_anchor": "§A"}]
        out_comps, out_edges = _enrich_mechanism(comps, edges)
        assert out_edges[0]["from"] == "Captain", "alias coordinator 应对齐到 Captain"
        assert out_edges[0]["to"] == "Executor", "alias worker 应对齐到 Executor"

    def test_enrich_drops_edges_with_unaligned_endpoints(self):
        """§4 阶段B：端点不在 components（也不在 aliases）的边 → 丢弃（不进 causal_edges）。"""
        from src.akb2.quality.distill import _enrich_mechanism
        comps = [{"name": "A", "role": "r"}, {"name": "B", "role": "r"}]
        edges = [{"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
                  "evidence": "x", "evidence_anchor": "§A"},
                 {"from": "A", "to": "Nonexistent", "relation": "r", "edge_kind": "source_causal",
                  "evidence": "y", "evidence_anchor": "§A"}]  # Nonexistent 不在 components
        _, out_edges = _enrich_mechanism(comps, edges)
        # 第二条边端点不在 components → 丢弃；只剩第一条 + inferred（A→B 已存在，不补）
        froms_tos = [(e["from"], e["to"]) for e in out_edges]
        assert ("A", "Nonexistent") not in froms_tos, "未对齐端点的边应丢弃"
        assert ("A", "B") in froms_tos

    def test_enrich_adds_inferred_weak_edges_for_adjacent_uncovered(self):
        """§4：相邻组件未被 eligible 覆盖 → 补 inferred_from_structure 弱边（不计 F4）。"""
        from src.akb2.quality.distill import _enrich_mechanism
        comps = [{"name": "A", "role": "r"}, {"name": "B", "role": "r"}, {"name": "C", "role": "r"}]
        edges = [{"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
                  "evidence": "x", "evidence_anchor": "§A"}]  # B→C 未覆盖
        _, out_edges = _enrich_mechanism(comps, edges)
        inferred = [e for e in out_edges if e["edge_kind"] == "inferred_from_structure"]
        assert len(inferred) >= 1, "B→C 相邻未覆盖应补 inferred 弱边"
        assert ("B", "C") in [(e["from"], e["to"]) for e in inferred]

    def test_policy_commitment_negative_still_fail_closed(self):
        """政策承诺编号列表（动词，无机制实体名）→ fallback 0 组件 fail-closed（§4 负样本仍拒）。"""
        raw = (
            "# Safety Standards Commitment\n\n"
            "We commit to the following principles:\n"
            "1. Establish shared safety standards across the industry\n"
            "2. Coordinate incident reporting between organizations\n"
            "3. Build public trust through transparency\n"
        )
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success is False, "政策承诺编号列表应 fail-closed"
        assert result.data.get("fallback_quality_basis", {}).get("component_count", 0) < 3
