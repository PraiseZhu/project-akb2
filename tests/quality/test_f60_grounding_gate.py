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
    2. 三层抽取预算 60/180/210 → 110/240/510(长文 630)算术自洽(实测正经
       how_params 产出 ~90s,60s per-call 必掐 → 210s worker 引爆,infra 超时
       占 83%;worker=2×attempt+30,契约=至少一次完整 attempt 重试可用)。
"""

from __future__ import annotations

import json

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

    def test_worker_covers_one_full_retry_attempt(self):
        # Sol 审 P1-4 修正:重试是整 attempt 从 claims 重启(不存在单 segment 重试
        # 路径),契约取"至少一次完整重试可用" → worker ≥ 2×attempt + slack。
        assert (
            runner_mod.DISTILL_EXTRACT_WORKER_TIMEOUT_S
            >= 2 * DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S + 20
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


def _tokens_compatible(a: str, b: str) -> bool:
    """测试助手:两词是否被 forms 匹配层判为同词(r4 起匹配在 forms 层)。"""
    from src.akb2.quality.distill import _forms_union, _token_forms

    fa = _forms_union(_grounding_tokens(a))
    return any(_token_forms(tok) & fa for tok in _grounding_tokens(b))


class TestGroundingTokens:
    def test_stopwords_and_short_dropped(self):
        toks = _grounding_tokens("The pattern for MCP tools")
        assert "the" not in toks and "for" not in toks and "pattern" not in toks
        assert "mcp" in toks and "tools" in toks  # r4: token 保原形,归并在 forms 层

    def test_plural_matched_at_forms_layer(self):
        assert _tokens_compatible("executors", "executor")

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


# ---------------------------------------------------------------------------
# Sol 独立审核(PR #68 review r1)反例收编——每条都是审出的真实误杀/漏判面
# ---------------------------------------------------------------------------


class TestSolReviewCounterexamples:
    def test_paren_acronym_not_killed(self):
        # P1-2: 正文只写缩写 GHES,全名 token 比例 1/4 曾被误杀;括号内容按命名惯例即别名
        c = _comp(
            "GitHub Enterprise Server (GHES)",
            "GHES requires a dedicated runner group for policy enforcement.",
        )
        assert _evidence_supports_component(c)

    def test_ies_plural_not_killed(self):
        # P1-2: Policies vs policy——裸 strip-s 拆成 policie/policy 两个 token 曾误杀
        c = _comp("Retention Policies", "Each retention policy defines a purge window.")
        assert _evidence_supports_component(c)

    def test_relocation_rechecks_postcondition(self):
        # P1-1: pass-2 单个通用 token(model)可命中不相干句;复验不过必须清空,
        # 不得记 relocated 成功放行原 H3 缺陷
        raw = """---
title: fixture
---
§A_INTRO
The model registry stores checkpoints for later deployment runs.
§B_MECH
Nothing else relevant appears in this document body here.
"""
        data = {
            "mechanism_components": [
                _comp("Model Runtime Harmonizer", _BOT_SERVICE_SENTENCE, "§A_INTRO")
            ],
            "causal_edges": [],
        }
        diag = _gate_component_evidence_support(data, raw)
        c = data["mechanism_components"][0]
        assert diag == {"unsupported": 1, "relocated": 0}, (
            "重定位句仅词面撞上通用 token,复验必须拒绝"
        )
        assert c["evidence_sentence"] == "" and c["evidence_anchor"] == ""

    def test_edge_alias_endpoint_not_dropped(self):
        # P1-2: F8-D 先把端点归一成 canonical name,evidence 里写的是 alias;
        # 边门必须按组件变体表判,否则真边被丢
        data = {
            "mechanism_components": [
                {
                    "name": "Session Coordination Layer",
                    "role": "r",
                    "aliases": ["Bot Service"],
                    "evidence_anchor": "§A_INTRO",
                    "evidence_sentence": _BOT_SERVICE_SENTENCE,
                },
            ],
            "causal_edges": [
                {
                    "from": "Session Coordination Layer", "to": "Unrelated Thing",
                    "relation": "manages", "edge_kind": "source_causal",
                    "evidence": _BOT_SERVICE_SENTENCE, "evidence_anchor": "§A_INTRO",
                },
                {
                    "from": "Alpha Nine", "to": "Beta Ten",
                    "relation": "feeds", "edge_kind": "source_causal",
                    "evidence": _BOT_SERVICE_SENTENCE, "evidence_anchor": "§A_INTRO",
                },
            ],
        }
        dropped = _gate_edge_evidence_support(data)
        kept_from = [e["from"] for e in data["causal_edges"]]
        assert "Session Coordination Layer" in kept_from, "alias 命中的真边不得丢"
        assert dropped == 1 and "Alpha Nine" not in kept_from


class TestWorkerDiagnosticsRoundtrip:
    def test_worker_payload_carries_diagnostics(self, monkeypatch, capsys):
        # P1-3: 主路径 payload 不带 diagnostics → F8/F60 计数生产恒 0(P4 断链)
        import io
        import sys as _sys

        from src.akb2.quality import distill_worker
        from src.akb2.quality.distill import ExtractResult

        fake = ExtractResult(
            success=True, data={"claims": []}, retries_used=1, error="",
            diagnostics={"components_evidence_unsupported": 2,
                         "edges_dropped_unsupported": 1},
        )
        monkeypatch.setattr(distill_worker, "extract_claims", lambda *a, **k: fake)
        monkeypatch.setattr(
            _sys, "stdin",
            io.StringIO(json.dumps({"raw_content": "x", "content_lane": "concept"})),
        )
        assert distill_worker.main() == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["diagnostics"]["components_evidence_unsupported"] == 2
        assert payload["diagnostics"]["edges_dropped_unsupported"] == 1


class TestSolReviewRound2:
    def test_acronym_only_name_is_undecidable_not_killed(self):
        # r2 P1-2: GH-ESD vs 展开全称——缩写↔全称映射词面判不了,判"不可判"交 grader
        c = _comp(
            "GH-ESD",
            "We propose Grounded Hypothesis-driven Error Slice Discovery for models.",
        )
        assert _evidence_supports_component(c)

    def test_acronym_direct_hit_supported(self):
        c = _comp("GH-ESD", "GH-ESD constructs relational failure hypotheses.")
        assert _evidence_supports_component(c)

    def test_non_acronym_paren_not_treated_as_alias(self):
        # r2 P1-2: "(optional)" 说明性括号不得作别名——否则错证据只含 optional 即放行
        c = _comp(
            "Cache Layer (optional)",
            "This optional flag toggles verbose logging output only.",
        )
        assert not _evidence_supports_component(c)

    def test_relocation_iterates_past_generic_hit(self):
        # r2 P1-1: 首候选是泛词撞句(model),真支撑句在后面——必须继续迭代拿到后者
        raw = """---
title: fixture
---
§A_INTRO
The model registry stores checkpoints for later deployment runs.
§B_MECH
The Model Runtime Harmonizer schedules every runtime shard deterministically.
"""
        data = {
            "mechanism_components": [
                _comp("Model Runtime Harmonizer", _BOT_SERVICE_SENTENCE, "§A_INTRO")
            ],
            "causal_edges": [],
        }
        diag = _gate_component_evidence_support(data, raw)
        c = data["mechanism_components"][0]
        assert diag == {"unsupported": 1, "relocated": 1}
        assert "Harmonizer schedules" in c["evidence_sentence"]
        assert c["evidence_anchor"] == "§B_MECH"

    def test_relocation_searches_via_acronym_variant(self):
        # r2 P1-2: wrong evidence + 正文只写 GHES——搜索必须用受控变体(含缩写)
        raw = """---
title: fixture
---
§A_INTRO
Something entirely unrelated sits here to occupy the intro anchor block.
§B_MECH
GHES requires a dedicated runner group for policy enforcement decisions.
"""
        data = {
            "mechanism_components": [
                _comp(
                    "GitHub Enterprise Server (GHES)",
                    _BOT_SERVICE_SENTENCE,
                    "§A_INTRO",
                )
            ],
            "causal_edges": [],
        }
        diag = _gate_component_evidence_support(data, raw)
        c = data["mechanism_components"][0]
        assert diag == {"unsupported": 1, "relocated": 1}
        assert c["evidence_sentence"].startswith("GHES requires")

    def test_abort_details_budget_fields_truthful(self):
        # r2 P1-3: 两层重试分列 + worker cap 真值入账本
        from src.akb2.quality.distill import MAX_EXTRACT_RETRIES
        from src.akb2.quality.runner import _distill_abort_details

        d = _distill_abort_details("x", "distill_extract", worker_timeout_s=510.0)
        assert d["provider_max_retries"] == 0
        assert d["extract_max_attempts"] == MAX_EXTRACT_RETRIES
        assert d["worker_timeout_s"] == 510.0
        assert "max_retries" not in d

    def test_extract_max_attempts_lane_aware(self):
        from src.akb2.quality.distill import (
            EXTRACT_QUALITY_RETRY_CONCEPT,
            MAX_EXTRACT_RETRIES,
        )
        from src.akb2.quality.runner import _distill_abort_details, _extract_max_attempts

        assert _extract_max_attempts("concept") == MAX_EXTRACT_RETRIES + EXTRACT_QUALITY_RETRY_CONCEPT
        assert _extract_max_attempts("procedural") == MAX_EXTRACT_RETRIES
        assert _extract_max_attempts("") == MAX_EXTRACT_RETRIES
        d = _distill_abort_details("x", "distill_extract", content_lane="concept")
        assert d["extract_max_attempts"] == MAX_EXTRACT_RETRIES + EXTRACT_QUALITY_RETRY_CONCEPT


class TestSolReviewRound3:
    def test_short_paren_name_not_leaked_by_paren_word(self):
        # r3 ①: 非 acronym 括号必须从 base 剥除——"Cache (optional)" 的 token 集
        # 不得含 optional,否则错证据只含 optional 即达 1/2 比例被放行
        c = _comp(
            "Cache (optional)",
            "This optional flag toggles verbose logging output only.",
        )
        assert not _evidence_supports_component(c)

    def test_singular_pairs_consistent(self):
        # r3 ②+r4 ②: 单 stem 修不完的词对,forms 候选形相交必须全部判同词
        for plural, singular in (
            ("Processes", "process"),
            ("Statuses", "status"),
            ("Analyses", "analysis"),
            # Sol r4 动态复现的四对
            ("Biases", "bias"),
            ("Gases", "gas"),
            ("Uses", "use"),
            ("Houses", "house"),
            # 旧五对回归
            ("policies", "policy"),
            ("classes", "class"),
            ("boxes", "box"),
            ("tools", "tool"),
            ("cases", "case"),
        ):
            assert _tokens_compatible(plural, singular), (plural, singular)

    def test_edge_acronym_endpoint_undecidable_not_dropped(self):
        # r3 ③: acronym 不可判豁免必须同样作用于边端点——GH-ESD 端点 + 展开全称
        # 证据不得丢边(与组件门同规)
        data = {
            "mechanism_components": [
                {
                    "name": "GH-ESD", "role": "r", "aliases": [],
                    "evidence_anchor": "§A", "evidence_sentence": "x",
                },
            ],
            "causal_edges": [
                {
                    "from": "GH-ESD", "to": "Slice Verifier",
                    "relation": "feeds", "edge_kind": "source_causal",
                    "evidence": (
                        "Grounded Hypothesis-driven Error Slice Discovery feeds "
                        "candidate slices downstream."
                    ),
                    "evidence_anchor": "§A",
                },
            ],
        }
        assert _gate_edge_evidence_support(data) == 0
        assert len(data["causal_edges"]) == 1


class TestSolReviewRound4:
    def test_edge_unknown_endpoint_plus_missed_endpoint_kept(self):
        # r4 ③: 「GH-ESD 端不可判 + 另一端判失」不得被全局 OR 聚成删——
        # 任一端不可判即留边(疑边归 grader)。此测试特意让另一端 miss
        # (r3 版测试的 Slice Verifier 被 slices 词形命中,没测到目标分支)。
        data = {
            "mechanism_components": [
                {"name": "GH-ESD", "role": "r", "aliases": [],
                 "evidence_anchor": "§A", "evidence_sentence": "x"},
                {"name": "Decision Router", "role": "r", "aliases": [],
                 "evidence_anchor": "§A", "evidence_sentence": "x"},
            ],
            "causal_edges": [
                {
                    "from": "GH-ESD", "to": "Decision Router",
                    "relation": "feeds", "edge_kind": "source_causal",
                    "evidence": (
                        "Grounded Hypothesis-driven Error Slice Discovery emits "
                        "candidate groups downstream."
                    ),
                    "evidence_anchor": "§A",
                },
            ],
        }
        assert _gate_edge_evidence_support(data) == 0
        assert len(data["causal_edges"]) == 1

    def test_edge_both_endpoints_judged_and_missed_dropped(self):
        # r4 ③ 对照:两端均可判且均未命中 → 删(门的存在意义)
        data = {
            "mechanism_components": [],
            "causal_edges": [
                {
                    "from": "Decision Router", "to": "Replay Buffer",
                    "relation": "feeds", "edge_kind": "source_causal",
                    "evidence": _BOT_SERVICE_SENTENCE,
                    "evidence_anchor": "§A",
                },
            ],
        }
        assert _gate_edge_evidence_support(data) == 1
        assert data["causal_edges"] == []
