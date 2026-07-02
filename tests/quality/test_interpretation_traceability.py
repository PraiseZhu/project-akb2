"""test_interpretation_traceability.py — step-3 SC-3c F3 两层验收。

Decision-1：F3 拆两层。
  层① 确定性（integrity_floor._interpretation_traceability_failed）：每条 akb_interpretation
      带 traces_to 指向真实存在的 source_grounded anchor（pre-grader 地板）。
  层② 异族 grader entailment（GPT-5.4）：判每条 interpretation 是否真从 cited anchor 推出，
      拒 poisoned-trace（anchor 真实但与解读语义无关）。生产模型 deepseek 不自证（P7）。
  procedural 保留 ≤40% cap（零回归）；concept 删 cap 改 traces_to 溯源。

真机 canary（poisoned_trace_real_canary / entailment_model_is_gpt54）需 gpt-5.4 网关，
本机无 → 走 fixture 单测 + canary skip-not-run（step-3 文档允许）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.akb2.quality.integrity_floor import (
    FLOOR_INTERPRETATION_TRACEABILITY,
    evaluate_integrity_floor,
)

# ----------------------------- helpers ----------------------------- #


def _make_response(content: dict, model_used: str = "gpt-5.4"):
    resp = MagicMock()
    resp.content = content
    resp.model_used = model_used
    return resp


def _mock_route(primary_content: dict, fallback_content: dict | None = None):
    """构造 mock_route_llm：GPT-5.4 调用返回 primary_content。"""

    def _route(model_family="", messages=None, response_schema=None, **kw):
        fam = str(model_family or "").lower()
        if "gpt" in fam or "5.4" in fam:
            return _make_response(primary_content, "gpt-5.4")
        if fallback_content is not None:
            return _make_response(fallback_content, "gemini-flash")
        return None

    return _route


def _pass_dimensions():
    """全维度 pass（无缺陷）的 dimensions 数组，用真实 RUBRIC_DIMENSIONS 名。"""
    from src.akb2.quality.grader import RUBRIC_DIMENSIONS

    return [{"dimension": d, "pass": True, "defects": []} for d in RUBRIC_DIMENSIONS]


def _concept_extract(claims_extra: list[dict] | None = None) -> dict:
    """concept extract，全地板过；claims_extra 追加更多 claim。"""
    base = [
        {"provenance": "source_grounded", "claim": "组件A", "anchor": "§A",
         "evidence_gap": "未提供 具体接口签名"},
        {"provenance": "source_grounded", "claim": "组件B", "anchor": "§B",
         "evidence_gap": "未量化 吞吐"},
    ]
    interp = {"provenance": "akb_interpretation", "claim": "A→B 因果",
              "anchor": "§A", "traces_to": ["§A", "§B"],
              "evidence_gap": "未覆盖 反压场景"}
    claims = base + [interp] + (claims_extra or [])
    return {
        "claims": claims,
        "argumentation": [],
        "mechanism_components": [
            {"name": "组件A", "role": "input"},
            {"name": "组件B", "role": "process"},
            {"name": "组件C", "role": "output"},
        ],
        "causal_edges": [
            {"from": "组件A", "to": "组件B", "relation": "feeds"},
            {"from": "组件B", "to": "组件C", "relation": "produces"},
        ],
        "how_steps": [],
    }


# ----------------------------- 层① 确定性 ----------------------------- #


class TestLayer1Deterministic:
    def test_high_interpretation_ratio_all_traceable_passes(self):
        """>40% 解读但全溯源 → 层① 过（concept 不卡 ≤40% cap，靠 traces_to）。"""
        extra = [
            {"provenance": "akb_interpretation", "claim": f"解读{i}",
             "anchor": "§A", "traces_to": ["§A"], "evidence_gap": "未覆盖 X"}
            for i in range(5)
        ]
        d = _concept_extract(extra)
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_INTERPRETATION_TRACEABILITY not in r.failed_floors
        # 5/8 = 62.5% akb_interpretation，concept 车道不卡比例，只看 traces_to

    def test_ungrounded_reflection_rejected(self):
        """无据飘浮读后感（akb_interpretation 缺 traces_to）→ 层① 拒。"""
        d = _concept_extract()
        d["claims"][2]["traces_to"] = []  # 解读缺 traces_to
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_INTERPRETATION_TRACEABILITY in r.failed_floors
        assert not r.passed

    def test_traces_to_nonexistent_anchor_rejected(self):
        """traces_to 指向不存在的 anchor → 层① 拒（anchor 须在 grounded_anchors 真实存在）。"""
        d = _concept_extract()
        d["claims"][2]["traces_to"] = ["§Z"]  # §Z 不在 {§A, §B}
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_INTERPRETATION_TRACEABILITY in r.failed_floors

    def test_argumentation_interpretation_also_checked(self):
        """argumentation 里的 akb_interpretation 也要 traces_to。"""
        d = _concept_extract()
        d["argumentation"] = [
            {"provenance": "akb_interpretation", "claim": "飘浮",
             "anchor": "§A", "traces_to": [], "evidence_gap": "未覆盖 X"}
        ]
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_INTERPRETATION_TRACEABILITY in r.failed_floors


# ----------------------------- 层② 异族 grader entailment ----------------------------- #


class TestLayer2GraderEntailment:
    """fixture LLM 响应验 grader 解析 interpretation_entailment 字段。

    真机 canary（真 GPT-5.4 判 poisoned-trace）见 SC-7，本机无网关→skip。
    """

    def test_poisoned_trace_entailment_false(self):
        """poisoned-trace fixture：entailment=false → GraderResult.interpretation_entailment=False。"""
        from src.akb2.quality.grader import run_grader_with_fixture_detailed

        content = {
            "dimensions": _pass_dimensions(),
            "interpretation_entailment": False,
        }
        result = run_grader_with_fixture_detailed("candidate", _mock_route(content))
        assert result.suspended is False
        assert result.interpretation_entailment is False

    def test_entailment_true(self):
        """推导真成立：entailment=true → GraderResult.interpretation_entailment=True。"""
        from src.akb2.quality.grader import run_grader_with_fixture_detailed

        content = {
            "dimensions": _pass_dimensions(),
            "interpretation_entailment": True,
        }
        result = run_grader_with_fixture_detailed("candidate", _mock_route(content))
        assert result.interpretation_entailment is True

    def test_entailment_absent_is_none(self):
        """旧响应/非 concept 无 entailment 字段 → None（零回归，不阻断）。"""
        from src.akb2.quality.grader import run_grader_with_fixture_detailed

        content = {"dimensions": _pass_dimensions()}  # 无 interpretation_entailment
        result = run_grader_with_fixture_detailed("candidate", _mock_route(content))
        assert result.interpretation_entailment is None


# ----------------------------- procedural ≤40% cap 零回归 ----------------------------- #


def _valid_extract(n_akb: int = 0, with_mechanism: bool = False) -> dict:
    """构造满足 _EXTRACT_SCHEMA 的最小 extract（用于测 ≤40% cap 逻辑）。

    claims 用 schema 允许的 text/anchor/provenance/evidence_gap（无 traces_to——
    schema additionalProperties:False；traces_to 由 argumentation 层承载，本 cap 测试不涉及）。
    """
    claims = [
        {"text": "grounded claim A", "anchor": "§A",
         "provenance": "source_grounded", "evidence_gap": "未提供 具体数据"}
    ] + [
        {"text": f"解读 claim {i}", "anchor": "§A",
         "provenance": "akb_interpretation", "evidence_gap": "未覆盖 场景 X"}
        for i in range(n_akb)
    ]
    return {
        "claims": claims,
        "how_steps": [],
        "mechanism_components": (
            [{"name": "A", "role": "x"}, {"name": "B", "role": "y"}, {"name": "C", "role": "z"}]
            if with_mechanism else []
        ),
        "causal_edges": (
            [
                {
                    "from": "A", "to": "B", "relation": "r",
                    "edge_kind": "source_causal", "evidence": "A feeds B", "evidence_anchor": "§A",
                },
                {
                    "from": "B", "to": "C", "relation": "r",
                    "edge_kind": "source_causal", "evidence": "B produces C", "evidence_anchor": "§B",
                },
            ]
            if with_mechanism else []
        ),
        "params": {},
        "referenced_concepts": [],
        "tldr": "满足 schema 的最小 extract fixture，用于测 cap 逻辑。",
        "argumentation": [
            # 须覆盖 claims 的 anchor（§A）；explanation minLength 10。
            {"claim_anchor": "§A",
             "explanation": "A 与 B 的因果链路构成反馈循环的核心机制。",
             "provenance": "akb_interpretation"},
        ],
        "applicability_boundary": "适用边界：本 fixture 仅用于 cap 测试，非真实内容。",
        "graph_edges": [],
    }


class TestProceduralCapRetained:
    def test_procedural_over_40pct_rejected_by_distill_validate(self):
        """procedural >40% akb_interpretation → distill _validate_extract_output 拒（cap 保留）。"""
        from src.akb2.quality.distill import _validate_extract_output

        d = _valid_extract(n_akb=5)  # 5/6 = 83% > 40%
        with pytest.raises(ValueError, match="akb_interpretation 占比"):
            _validate_extract_output(d, content_lane="procedural")

    def test_concept_over_40pct_not_rejected_by_cap(self):
        """concept >40% akb → 不被 ≤40% cap 拒（concept 删 cap，靠 traces_to 溯源 + mechanism≥3）。"""
        from src.akb2.quality.distill import _validate_extract_output

        d = _valid_extract(n_akb=5, with_mechanism=True)  # concept：mechanism 3 过
        # concept 不抛 ≤40% ValueError（cap 跳过，mechanism≥3 过）
        _validate_extract_output(d, content_lane="concept")  # 不 raise


# ----------------------------- 生产路径：traces_to 真接通 schema + floor ----------------------------- #


class TestProductionTracesTo:
    """codex P1：traces_to 进 _EXTRACT_SCHEMA + normalizer，生产层① 现在可达 + 真判。"""

    def test_extract_with_traces_to_passes_schema_and_floor(self):
        """concept extract 带 traces_to → 过 _validate_extract_output schema + layer① floor。"""
        from src.akb2.quality.distill import _validate_extract_output

        d = _valid_extract(n_akb=1, with_mechanism=True)
        # akb_interpretation claim 带 traces_to 指向 grounded §A
        d["claims"][1]["traces_to"] = ["§A"]
        d["argumentation"][0]["traces_to"] = ["§A"]
        _validate_extract_output(d, content_lane="concept")  # schema 过（traces_to 现允许）
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert r.passed, f"层① 应过（traces_to→§A）: {r.failed_floors}"
        assert FLOOR_INTERPRETATION_TRACEABILITY not in r.failed_floors

    def test_extract_without_traces_to_fails_layer1(self):
        """concept extract 的 akb claim 不带 traces_to → layer① fail（生产层① 真判，非 fixture-only）。"""
        d = _valid_extract(n_akb=1, with_mechanism=True)
        # _valid_extract 的 akb claim 无 traces_to（默认）
        r = evaluate_integrity_floor(d, "concept", raw_anchors={"§A", "§B"})
        assert FLOOR_INTERPRETATION_TRACEABILITY in r.failed_floors
        assert not r.passed


# ----------------------------- 真机 canary skip ----------------------------- #


class TestRealCanary:
    """SC-7 真机 canary：真 GPT-5.4（tapsvc 网关）判 poisoned-trace。

    设 OPENAI_BASE_URL=tapsvc + OPENAI_API_KEY=ANTHROPIC_API_KEY（ tapsvc 网关 key），
    真跑 run_grader_detailed，断言模型族==gpt-5.4 且 interpretation_entailment=false。
    预算超限（429 ExceededBudget）→ skip-not-run（key 有效，仅日预算用完，次日 UTC 0点重置）。
    """

    _POISONED_TRACE_CANDIDATE = """---
title: poisoned-trace canary
content_lane: concept
---

## 核心论点

- [raw §A] (source_grounded) 原文：Loop engineering 把 agent 工作收束为有停止条件的反馈循环。
- (akb_interpretation) AKB 解读：React 的 useEffect hook 在组件挂载后执行副作用清理。[raw §A]

（解读 cited §A 关于 Loop engineering，但解读内容是 React useEffect——anchor 真实但语义无关 = poisoned-trace）
"""

    def _setup_tapsvc_gateway(self, monkeypatch):
        """强制 gpt-5.4 走 tapsvc 网关（避免外部官方 OPENAI_API_KEY 打 api.openai.com 401）。"""
        monkeypatch.setenv("OPENAI_BASE_URL", "https://llm-proxy.tapsvc.com")
        # tapsvc 网关 key（与 ANTHROPIC_API_KEY 同源）；若外部 OPENAI_API_KEY 是官方错 key 则覆盖
        tapsvc_key = (
            __import__("os").environ.get("AI_GATEWAY_API_KEY")
            or __import__("os").environ.get("TAPTAP_API_KEY")
            or __import__("os").environ.get("ANTHROPIC_API_KEY", "")
        )
        if tapsvc_key:
            monkeypatch.setenv("OPENAI_API_KEY", tapsvc_key)

    def test_poisoned_trace_real_canary(self, monkeypatch):
        """真 GPT-5.4 判 poisoned-trace → interpretation_entailment=false。"""
        if not __import__("os").environ.get("AKB2_GRADER_CANARY_LIVE"):
            pytest.skip("设 AKB2_GRADER_CANARY_LIVE=1 启用真机 canary")
        self._setup_tapsvc_gateway(monkeypatch)
        from src.akb2.quality.grader import PRIMARY_GRADER_MODEL, run_grader_detailed
        from src.akb2.quality.llm_router import LLMProviderError

        assert "gpt" in PRIMARY_GRADER_MODEL.lower() or "5.4" in PRIMARY_GRADER_MODEL
        try:
            result = run_grader_detailed(self._POISONED_TRACE_CANDIDATE, content_lane="concept")
        except LLMProviderError as exc:
            msg = str(exc)
            if "ExceededBudget" in msg or "429" in msg or "daily cap" in msg.lower():
                pytest.skip(f"tapsvc 网关日预算超限（key 有效），次日 UTC 0点重置后重跑: {msg[:120]}")
            if "401" in msg or "invalid_api_key" in msg:
                pytest.fail(f"tapsvc 网关 key 无效（401）— 检查 ANTHROPIC_API_KEY/AI_GATEWAY_API_KEY: {msg[:120]}")
            pytest.skip(f"网关暂不可用（{type(exc).__name__}）: {msg[:120]}")
        # canary 不可降级 gemini（异族硬判，P7）；suspended/未真跑 gpt-5.4 → skip（预算/网关）
        if result.suspended or "gpt" not in (result.model_used or "").lower() and "5.4" not in (result.model_used or ""):
            reason = result.primary_failure_reason or f"suspended={result.suspended} model={result.model_used}"
            if "ExceededBudget" in reason or "429" in reason or "daily cap" in reason.lower() or result.suspended:
                pytest.skip(f"canary 未真跑 gpt-5.4（预算/网关）：{reason[:140]}")
            pytest.fail(f"canary 未用 GPT-5.4（model={result.model_used}）：{reason[:140]}")
        # poisoned-trace → entailment=false（真 GPT-5.4 应拒）
        assert result.interpretation_entailment is False, (
            f"poisoned-trace 应被 GPT-5.4 判 entailment=false，实际={result.interpretation_entailment}"
        )

    def test_entailment_model_is_gpt54(self, monkeypatch):
        """canary 须证明 entailment 由 GPT-5.4 判（非 deepseek 自证，P7 异族）。"""
        from src.akb2.quality.grader import FALLBACK_GRADER_MODEL, PRIMARY_GRADER_MODEL
        # 模型族断言（不需真调网关）
        assert PRIMARY_GRADER_MODEL != "deepseek", "entailment 不可由 deepseek 自证"
        assert "gpt" in PRIMARY_GRADER_MODEL.lower() or "5.4" in PRIMARY_GRADER_MODEL
        assert FALLBACK_GRADER_MODEL != "deepseek"
