"""test_resolve_anchor_ref.py — F-A 单字母锚唯一前缀解析测试。

覆盖:
    - 精确匹配优先(§A ∈ declared → 原样返回 §A)
    - 唯一前缀解析成功(§W + declared{§W_CAVEATS} → §W_CAVEATS)
    - 无匹配 → None(§Q + declared{§W_CAVEATS} → None)
    - 歧义 → None(§W + declared{§W_CAVEATS, §W_WORKERS} → None,不猜)
    - 空引/非 str → None
    - 全链集成:单字母 LLM 输出经 _resolve_short_anchors_to_full → 全名 → A3 过
"""

from __future__ import annotations

from src.akb2.quality.distill import _resolve_short_anchors_to_full
from src.akb2.quality.raw_anchors import resolve_anchor_ref


class TestResolveAnchorRef:
    def test_exact_match_priority(self):
        """ref 精确 ∈ declared → 原样返回(优先于前缀解析)。"""
        assert resolve_anchor_ref("§A", {"§A", "§B"}) == "§A"
        # declared 同时有 §A(精确)和 §A_X(前缀)→ 精确优先,返 §A
        assert resolve_anchor_ref("§A", {"§A", "§A_X"}) == "§A"

    def test_unique_prefix_resolved(self):
        """ref 不在 declared 但恰一个以 ref_ 开头 → 返全名。"""
        assert resolve_anchor_ref("§W", {"§W_CAVEATS"}) == "§W_CAVEATS"
        assert resolve_anchor_ref("§I", {"§I_LIMITATIONS", "§A"}) == "§I_LIMITATIONS"

    def test_no_match_returns_none(self):
        """ref 不在 declared 且无前缀匹配 → None。"""
        assert resolve_anchor_ref("§Q", {"§W_CAVEATS", "§A"}) is None

    def test_ambiguous_returns_none(self):
        """ref_ 前缀匹配 ≥2 个 → None(歧义,不猜)。"""
        assert resolve_anchor_ref("§W", {"§W_CAVEATS", "§W_WORKERS"}) is None
        assert resolve_anchor_ref("§A", {"§A_X", "§A_Y"}) is None

    def test_empty_or_nonstr_returns_none(self):
        assert resolve_anchor_ref("", {"§A"}) is None
        assert resolve_anchor_ref("   ", {"§A"}) is None
        assert resolve_anchor_ref(None, {"§A"}) is None  # type: ignore[arg-type]


class TestResolveShortAnchorsFull:
    """全链集成:LLM 单字母输出 → _resolve_short_anchors_to_full → 全名。"""

    def _raw_with_full_anchors(self) -> str:
        """raw_archiver 格式:声明全名 §W_CAVEATS / §I_LIMITATIONS / §A_CONTENT_START(→§A)。"""
        return (
            "---\nartifact_type: raw\n---\n\n"
            "§A_CONTENT_START\n\nintro paragraph.\n\n"
            "§I_LIMITATIONS\n\nlimits paragraph.\n\n"
            "§W_CAVEATS\n\ncaveats paragraph.\n"
        )

    def test_claims_anchor_short_to_full(self):
        """claim.anchor=§W(单字母)→ 解析为 §W_CAVEATS。"""
        raw = self._raw_with_full_anchors()
        merged = {
            "claims": [
                {"text": "caveat text", "anchor": "§W", "provenance": "source_grounded",
                 "evidence_gap": "未提供 X"},
            ],
            "argumentation": [],
        }
        _resolve_short_anchors_to_full(merged, raw)
        assert merged["claims"][0]["anchor"] == "§W_CAVEATS"

    def test_traces_to_short_to_full(self):
        """traces_to=[§W, §I] → [§W_CAVEATS, §I_LIMITATIONS]。"""
        raw = self._raw_with_full_anchors()
        merged = {
            "claims": [
                {"text": "x", "anchor": "§A", "provenance": "akb_interpretation",
                 "evidence_gap": "未提供 X", "traces_to": ["§W", "§I"]},
            ],
            "argumentation": [],
        }
        _resolve_short_anchors_to_full(merged, raw)
        assert merged["claims"][0]["traces_to"] == ["§W_CAVEATS", "§I_LIMITATIONS"]

    def test_argumentation_claim_anchor_resolved(self):
        """argumentation.claim_anchor=§I → §I_LIMITATIONS。"""
        raw = self._raw_with_full_anchors()
        merged = {
            "claims": [],
            "argumentation": [
                {"claim_anchor": "§I", "explanation": "解读 limits。",
                 "provenance": "akb_interpretation"},
            ],
        }
        _resolve_short_anchors_to_full(merged, raw)
        assert merged["argumentation"][0]["claim_anchor"] == "§I_LIMITATIONS"

    def test_edge_evidence_anchor_resolved(self):
        """causal_edges.evidence_anchor=§W → §W_CAVEATS。"""
        raw = self._raw_with_full_anchors()
        merged = {
            "claims": [],
            "argumentation": [],
            "causal_edges": [
                {"from": "A", "to": "B", "relation": "r", "edge_kind": "source_causal",
                 "evidence": "...", "evidence_anchor": "§W"},
            ],
        }
        _resolve_short_anchors_to_full(merged, raw)
        assert merged["causal_edges"][0]["evidence_anchor"] == "§W_CAVEATS"

    def test_how_steps_anchor_resolved(self):
        raw = self._raw_with_full_anchors()
        merged = {
            "claims": [],
            "argumentation": [],
            "how_steps": [{"instruction": "do x", "command": "cmd", "executable": True,
                           "anchor": "§I"}],
        }
        _resolve_short_anchors_to_full(merged, raw)
        assert merged["how_steps"][0]["anchor"] == "§I_LIMITATIONS"

    def test_ambiguous_anchor_left_unchanged(self):
        """歧义锚(§A_X/§A_Y 并存)→ 解析失败保持原值(后续 A3 fail-closed 接手)。"""
        raw = "## §A_X\n\nx\n\n## §A_Y\n\ny\n"  # declared {§A_X, §A_Y}
        merged = {
            "claims": [
                {"text": "t", "anchor": "§A", "provenance": "source_grounded",
                 "evidence_gap": "未提供 X"},
            ],
            "argumentation": [],
        }
        _resolve_short_anchors_to_full(merged, raw)
        assert merged["claims"][0]["anchor"] == "§A"  # 歧义,保持原值

    def test_no_declared_anchors_noop(self):
        """raw 无声明锚 → no-op(fail-closed)。"""
        raw = "plain text no anchors"
        merged = {
            "claims": [{"text": "t", "anchor": "§A", "provenance": "source_grounded",
                        "evidence_gap": "未提供 X"}],
            "argumentation": [],
        }
        _resolve_short_anchors_to_full(merged, raw)
        assert merged["claims"][0]["anchor"] == "§A"  # 未改

    def test_full_chain_single_letter_llm_output_passes_a3(self):
        """全链:LLM 产单字母 claims/edges → resolve → 全名 → A3 检查过(引用 ∈ 声明集)。"""
        from src.akb2.quality.checks_structural import _extract_raw_anchors, check_a3_raw_anchors

        raw = self._raw_with_full_anchors()
        declared = _extract_raw_anchors(raw)
        # LLM 产单字母 anchor §W
        merged = {
            "claims": [{"text": "caveat", "anchor": "§W", "provenance": "source_grounded",
                        "evidence_gap": "未提供 X"}],
            "argumentation": [],
        }
        _resolve_short_anchors_to_full(merged, raw)
        # resolve 后 anchor 是全名 §W_CAVEATS ∈ declared
        assert merged["claims"][0]["anchor"] == "§W_CAVEATS"
        assert merged["claims"][0]["anchor"] in declared
        # 候选页 body 用 resolve 后的 anchor 渲染 [raw §W_CAVEATS] → A3 过
        candidate = (
            "---\nstatus: candidate\ngate_decision: null\n---\n\n"
            "## 核心论点\n\n- [raw §W_CAVEATS] (source_grounded) caveat text\n"
        )
        defects = check_a3_raw_anchors(candidate, raw)
        assert defects == [], f"resolve 后 A3 应过: {[d.defect for d in defects]}"
