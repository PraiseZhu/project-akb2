"""test_f60_grounding_gate.py — F60 抽取期语义锚定门 + 预算算术自洽守卫。

背景(2026-07-31 账本取证):
    F8-A/F35 保证 evidence 存在且在 raw 中,但 grader H3 主死因是
    "锚点存在但证据句不支撑组件命名"(评分直通率 3~10%,驻留 358 篇仅 6% 生还)。
    负样本文本取自真实账本 final_defects:组件
    "Model Context Protocol (MCP) tools" 的 evidence_sentence 实际在讲
    "Bot Service builds and manages the user session..."。

F60 两刀:
    1. 词面锚定门左移进 _normalize_extract_payload(7.5 步):
       组件不支撑 → F8-A 搜索重定位 → 仍无 → 清空 evidence(交 F35/F32);
       eligible 边 evidence 两端点都不提及 → 丢边。
    2. 三层抽取预算 60/180/210 → 110/240/380 算术自洽(实测正经 how_params
       产出 ~90s,60s per-call 必掐 → 210s worker 引爆,infra 超时占 83%)。
"""

from __future__ import annotations

from src.akb2.quality import runner as runner_mod
from src.akb2.quality.distill import (
    DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S,
    EXTRACT_SEGMENT_TIMEOUT_S,
    GROUNDING_MIN_NAME_OVERLAP,
    _edge_evidence_mentions_endpoint,
    _evidence_supports_component,
    _gate_component_evidence_support,
    _gate_edge_evidence_support,
    _grounding_tokens,
    _normalize_extract_payload,
)

# 真实账本 final_defects 的错配对(逐字语义,非本测试杜撰的极端例)
_BOT_SERVICE_SENTENCE = (
    "The Bot Service builds and manages the user session for each incoming request."
)

_RAW = f"""---
title: fixture
---
§A_INTRO
{_BOT_SERVICE_SENTENCE}
§B_MECH
The Executor Pool runs isolated tasks and reports status back to the Captain component.
§C_TAIL
Model Context Protocol tools expose external data sources to the agent runtime safely.
"""


def _comp(name: str, es: str, ea: str = "§A_INTRO", aliases: list | None = None) -> dict:
    return {
        "name": name,
        "role": "r",
        "aliases": aliases or [],
        "evidence_anchor": ea,
        "evidence_sentence": es,
    }


# ---------------------------------------------------------------------------
# 预算算术守卫(改任何一层必须保持自洽,否则本组测试红)
# ---------------------------------------------------------------------------


class TestBudgetArithmetic:
    def test_attempt_covers_two_segments(self):
        # attempt 须容纳 claims+how_params 两次满额调用 + 归一化余量
        assert DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S >= 2 * EXTRACT_SEGMENT_TIMEOUT_S + 10

    def test_worker_covers_attempt_plus_one_segment_retry(self):
        assert (
            runner_mod.DISTILL_EXTRACT_WORKER_TIMEOUT_S
            >= DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S + EXTRACT_SEGMENT_TIMEOUT_S + 20
        )

    def test_long_doc_and_hard_cap_not_below_normal(self):
        assert (
            runner_mod.DISTILL_EXTRACT_WORKER_LONG_DOC_TIMEOUT_S
            >= runner_mod.DISTILL_EXTRACT_WORKER_TIMEOUT_S
        )
        assert (
            runner_mod.DISTILL_EXTRACT_WORKER_HARD_TIMEOUT_CAP_S
            >= runner_mod.DISTILL_EXTRACT_WORKER_LONG_DOC_TIMEOUT_S
        )

    def test_segment_timeout_above_measured_p95(self):
        # 实测生产逼真形态正经 how_params 产出 89.4s;预算低于它=回归到掐死状态
        assert EXTRACT_SEGMENT_TIMEOUT_S >= 100


# ---------------------------------------------------------------------------
# 词面 token 与组件支撑判定
# ---------------------------------------------------------------------------


class TestGroundingTokens:
    def test_stopwords_and_short_dropped(self):
        toks = _grounding_tokens("The pattern for MCP tools")
        assert "the" not in toks and "for" not in toks and "pattern" not in toks
        assert "mcp" in toks and "tool" in toks  # 朴素单数化

    def test_plural_naive_stemming(self):
        assert _grounding_tokens("executors") == _grounding_tokens("executor")

    def test_cjk_chars(self):
        assert "机" in _grounding_tokens("机制组件")


class TestComponentSupport:
    def test_real_ledger_mismatch_rejected(self):
        # 真实缺陷复现:MCP tools 组件挂着 Bot Service 的句子
        c = _comp("Model Context Protocol (MCP) tools", _BOT_SERVICE_SENTENCE)
        assert not _evidence_supports_component(c)

    def test_matching_evidence_accepted(self):
        c = _comp(
            "Bot Service", _BOT_SERVICE_SENTENCE
        )
        assert _evidence_supports_component(c)

    def test_plural_variation_accepted(self):
        # "Executor Pool" vs "pool of executors" 词面单复数不应误杀
        c = _comp("Executor Pools", "A pool of executors runs each isolated task.")
        assert _evidence_supports_component(c)

    def test_alias_hit_accepted(self):
        c = _comp(
            "Session Manager",
            _BOT_SERVICE_SENTENCE,
            aliases=["Bot Service"],
        )
        assert _evidence_supports_component(c)

    def test_empty_evidence_not_judged(self):
        # 空 evidence 归 F8-A/F35 管,本门不判
        assert _evidence_supports_component(_comp("Anything", ""))

    def test_no_valid_token_name_not_judged(self):
        # 名字全是停用词/超短 token → 无从判定,不误杀
        assert _evidence_supports_component(_comp("The Via", _BOT_SERVICE_SENTENCE))


class TestEdgeSupport:
    def test_neither_endpoint_mentioned_rejected(self):
        e = {
            "from": "Retrieval Gap",
            "to": "Knowledge Base",
            "relation": "requires",
            "edge_kind": "source_causal",
            "evidence": _BOT_SERVICE_SENTENCE,
        }
        assert not _edge_evidence_mentions_endpoint(e)

    def test_one_endpoint_mentioned_accepted(self):
        e = {
            "from": "Bot Service",
            "to": "Knowledge Base",
            "relation": "manages",
            "edge_kind": "source_causal",
            "evidence": _BOT_SERVICE_SENTENCE,
        }
        assert _edge_evidence_mentions_endpoint(e)

    def test_empty_evidence_not_judged(self):
        e = {"from": "A1", "to": "B1", "edge_kind": "source_causal", "evidence": ""}
        assert _edge_evidence_mentions_endpoint(e)


# ---------------------------------------------------------------------------
# 门的落料行为(重定位/清空/丢边)
# ---------------------------------------------------------------------------


class TestComponentGateActions:
    def test_relocation_via_backfill_search(self):
        # 名字在 raw 有真句(§C 行) → 错配证据被重定位,不清空
        data = {
            "mechanism_components": [
                _comp("Model Context Protocol (MCP) tools", _BOT_SERVICE_SENTENCE)
            ],
            "causal_edges": [],
        }
        diag = _gate_component_evidence_support(data, _RAW)
        c = data["mechanism_components"][0]
        assert diag == {"unsupported": 1, "relocated": 1}
        assert "Model Context Protocol tools expose" in c["evidence_sentence"]
        assert c["evidence_anchor"] == "§C_TAIL"

    def test_unlocatable_gets_blanked(self):
        # 名字在 raw 任何位置都定位不到 → 清空(宁缺勿假),交 F35/F32
        data = {
            "mechanism_components": [
                _comp("Quantum Flux Harmonizer", _BOT_SERVICE_SENTENCE)
            ],
            "causal_edges": [],
        }
        diag = _gate_component_evidence_support(data, _RAW)
        c = data["mechanism_components"][0]
        assert diag == {"unsupported": 1, "relocated": 0}
        assert c["evidence_sentence"] == "" and c["evidence_anchor"] == ""

    def test_supported_untouched(self):
        data = {
            "mechanism_components": [_comp("Bot Service", _BOT_SERVICE_SENTENCE)],
            "causal_edges": [],
        }
        diag = _gate_component_evidence_support(data, _RAW)
        assert diag == {"unsupported": 0, "relocated": 0}
        assert data["mechanism_components"][0]["evidence_sentence"] == (
            _BOT_SERVICE_SENTENCE
        )


class TestEdgeGateActions:
    def test_unsupported_eligible_edge_dropped(self):
        data = {
            "causal_edges": [
                {
                    "from": "Retrieval Gap", "to": "Knowledge Base",
                    "relation": "requires", "edge_kind": "source_causal",
                    "evidence": _BOT_SERVICE_SENTENCE, "evidence_anchor": "§A_INTRO",
                },
                {
                    "from": "Bot Service", "to": "Session",
                    "relation": "manages", "edge_kind": "source_causal",
                    "evidence": _BOT_SERVICE_SENTENCE, "evidence_anchor": "§A_INTRO",
                },
            ]
        }
        dropped = _gate_edge_evidence_support(data)
        assert dropped == 1
        assert len(data["causal_edges"]) == 1
        assert data["causal_edges"][0]["from"] == "Bot Service"

    def test_inferred_weak_edge_not_gated(self):
        # inferred_from_structure 不计 F4,本门不裁,留给 grader
        data = {
            "causal_edges": [
                {
                    "from": "X9", "to": "Y9", "relation": "follows",
                    "edge_kind": "inferred_from_structure",
                    "evidence": _BOT_SERVICE_SENTENCE,
                },
            ]
        }
        assert _gate_edge_evidence_support(data) == 0
        assert len(data["causal_edges"]) == 1


# ---------------------------------------------------------------------------
# choke point 集成(_normalize_extract_payload 7.5 步接线)
# ---------------------------------------------------------------------------


class TestNormalizeIntegration:
    def test_llm_path_unsupported_component_dropped_for_concept(self):
        # 错配 + 不可定位的组件:门清空 evidence → F35(concept)剔除 → 指向它的边闭合
        data = {
            "claims": [],
            "how_steps": [],
            "param_entries": [],
            "mechanism_components": [
                _comp("Quantum Flux Harmonizer", _BOT_SERVICE_SENTENCE, "§A_INTRO"),
                _comp("Bot Service", _BOT_SERVICE_SENTENCE, "§A_INTRO"),
            ],
            "causal_edges": [
                {
                    "from": "Quantum Flux Harmonizer", "to": "Bot Service",
                    "relation": "feeds", "edge_kind": "source_causal",
                    "evidence": _BOT_SERVICE_SENTENCE, "evidence_anchor": "§A_INTRO",
                },
            ],
        }
        diag = _normalize_extract_payload(data, _RAW, "concept", is_fallback=False)
        names = [c["name"] for c in data["mechanism_components"]]
        assert "Quantum Flux Harmonizer" not in names, "清空后应被 F35 剔除"
        assert "Bot Service" in names
        assert diag["components_evidence_unsupported"] >= 1
        # 指向被剔组件的边一并闭合(既有 _close_edge_endpoints 行为)
        assert all(
            e.get("from") != "Quantum Flux Harmonizer"
            for e in data["causal_edges"]
        )

    def test_diag_keys_always_present(self):
        data = {"mechanism_components": [], "causal_edges": []}
        diag = _normalize_extract_payload(data, _RAW, "concept", is_fallback=False)
        for k in (
            "components_evidence_unsupported",
            "components_evidence_relocated",
            "edges_dropped_unsupported",
        ):
            assert k in diag

    def test_fallback_path_gate_skipped(self):
        # fallback 的 det 组件按构造来自 raw;门必须跳过(与 F3-B/F35 豁免同理)
        data = {
            "mechanism_components": [
                _comp("Quantum Flux Harmonizer", _BOT_SERVICE_SENTENCE, "§A_INTRO"),
            ],
            "causal_edges": [],
        }
        diag = _normalize_extract_payload(data, _RAW, "concept", is_fallback=True)
        assert diag["components_evidence_unsupported"] == 0
        # evidence 未被门清空
        assert data["mechanism_components"][0]["evidence_sentence"] == (
            _BOT_SERVICE_SENTENCE
        )


class TestThresholdSanity:
    def test_threshold_is_conservative(self):
        # 阈值必须 ≤ 0.5:门只拦确定性驴唇不对马嘴,语义裁决归 grader(P7)
        assert 0 < GROUNDING_MIN_NAME_OVERLAP <= 0.5
