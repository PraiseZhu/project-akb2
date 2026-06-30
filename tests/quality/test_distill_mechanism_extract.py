"""test_distill_mechanism_extract.py — WP-3 distill 提炼内核测试 (SC-3)。

覆盖:
    - F4 extract 即判（WP-3.2）：concept <3 组件/孤立/重复名 → distill extract 即 raise；
      3 唯一+连边 → 过
    - D8 concept fallback 不造假机制（WP-3.1）：无 ≥3 命名组件 raw → fail-closed；
      有 ≥3 → 用真实组件（非硬编码 loop 组件）
    - D7 poisoned trace（WP-3.1/3.6）：concept fallback how_steps=[]，不生成
      schema/ledger/max_repair_rounds 假步骤（idx5 形态）
    - WP-3.4 frontmatter 同源 resolve：known/pending 提供时 related_concepts 只写
      可 resolve 的 [[wikilink]]，悬空不写；gate _graph_readiness 悬空不产 real_link

mock 边界：直接调 _validate_extract_output / build_deterministic_extract_result /
_build_frontmatter（确定性代码，无 LLM）；gate _graph_readiness 只读 import。
"""

from __future__ import annotations

from src.akb2.quality.distill import (
    _build_frontmatter,
    _validate_extract_output,
    build_deterministic_extract_result,
)

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
# WP-3.2: F4 extract 即判
# ---------------------------------------------------------------------------


class TestF4ExtractValidation:
    """F4 connectivity 前移到 distill extract（去重唯一≥3 + 每组件连边）。"""

    def test_f4_less_than_three_components_raises(self):
        """concept <3 命名组件 → distill extract 即 raise（不待下游 floor）。"""
        data = _base_concept_data([{"name": "A"}], [])
        try:
            _validate_extract_output(data, content_lane="concept")
        except ValueError as exc:
            assert "F4" in str(exc), f"应 F4 失败: {exc}"
            return
        raise AssertionError("concept <3 组件应在 extract 即判 raise")

    def test_f4_isolated_components_raise(self):
        """3 组件但无因果边（孤立）→ F4 连通性失败 raise。"""
        data = _base_concept_data(
            [{"name": "A"}, {"name": "B"}, {"name": "C"}],
            [],  # 无边 → 全孤立
        )
        try:
            _validate_extract_output(data, content_lane="concept")
        except ValueError as exc:
            assert "F4" in str(exc)
            return
        raise AssertionError("孤立组件应 F4 raise")

    def test_f4_duplicate_names_dedup_raise(self):
        """重复组件名 [A,A,B] 去重后 <3 → F4 raise（防重复名伪装 3 组件）。"""
        data = _base_concept_data(
            [{"name": "A"}, {"name": "A"}, {"name": "B"}],
            [{"from": "A", "to": "B", "relation": "r"}],
        )
        try:
            _validate_extract_output(data, content_lane="concept")
        except ValueError as exc:
            assert "F4" in str(exc)
            return
        raise AssertionError("重复名去重 <3 应 F4 raise")

    def test_f4_three_unique_connected_passes(self):
        """3 唯一组件 + 连边 → F4 过（_validate_extract_output 不 raise）。"""
        data = _base_concept_data(
            [{"name": "A"}, {"name": "B"}, {"name": "C"}],
            [
                {"from": "A", "to": "B", "relation": "r"},
                {"from": "B", "to": "C", "relation": "r"},
            ],
        )
        _validate_extract_output(data, content_lane="concept")  # 不 raise 即过


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
            "# Captain Architecture\n\n"
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
            "# Concept Article\n\n"
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
            "# Prompt Caching Article\n\n"
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
        assert _graph_readiness(fm) == "none"

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

    def test_evaluate_five_gates_empty_related_fails_gate3(self, tmp_path):
        """P1-B 真实五闸: 悬空 frontmatter（known=[] 提供但空 → related=[]）→ Gate-3 fail, 不产 active。"""
        from unittest.mock import patch

        from src.akb2.gate.akb2_gate_check import _graph_readiness
        from src.akb2.quality import promote

        fm = _build_frontmatter(
            "raw/fake--raw--2026-06-29.md",
            _frontmatter_data(["totally-unknown-concept"]),
            content_lane="concept",
            known_concepts=[],  # P1-B: 提供但空 → related=[]
        )
        assert fm["related_concepts"] == []
        graph = _graph_readiness(fm)
        assert graph == "none"
        gate_parsed = {
            "decision_confidence": "high",
            "graph_readiness": graph,
            "hard_checks": {},
        }
        cand = tmp_path / "candidate.md"
        with patch(
            "src.akb2.quality.promote._run_gate_check",
            return_value=(True, "", gate_parsed),
        ):
            all_passed, failed_gate, _reason = promote.evaluate_five_gates(
                cand, tmp_path
            )
        assert all_passed is False, "悬空 frontmatter（related=[]）不应过五闸"
        assert "Gate-3" in failed_gate, f"应 fail 在 Gate-3: {failed_gate}"

    def test_evaluate_five_gates_resolved_related_passes_gate3(self, tmp_path):
        """P1-B 真实五闸: 已 resolve related（known 含目标 → related 非空）→ Gate-3 过 + 全闸过。"""
        from unittest.mock import patch

        from src.akb2.gate.akb2_gate_check import _graph_readiness
        from src.akb2.quality import promote

        fm = _build_frontmatter(
            "raw/fake--raw--2026-06-29.md",
            _frontmatter_data(["loop-engineering"]),
            content_lane="concept",
            known_concepts=["loop-engineering"],
        )
        assert "[[loop-engineering]]" in fm["related_concepts"]
        graph = _graph_readiness(fm)
        assert graph == "real_link"
        gate_parsed = {
            "decision_confidence": "high",
            "graph_readiness": graph,
            "hard_checks": {},
        }
        cand = tmp_path / "candidate.md"
        with patch(
            "src.akb2.quality.promote._run_gate_check",
            return_value=(True, "", gate_parsed),
        ), patch(
            "src.akb2.quality.promote._run_lint_check", return_value=(True, "")
        ), patch(
            "src.akb2.quality.promote._read_grader_result", return_value=(True, "")
        ):
            all_passed, failed_gate, reason = promote.evaluate_five_gates(
                cand, tmp_path
            )
        assert all_passed is True, (
            f"已 resolve related 应过五闸（可产 active）: {failed_gate} {reason}"
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
            "# Prompt Caching Article\n\n"
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
