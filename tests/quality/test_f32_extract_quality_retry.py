"""test_f32_extract_quality_retry.py — F32 concept 抽取质量重试(+1 有界)。

第八轮实锤:同一篇 SW 提炼产出在 2 与 9 组件间掷骰子,<3 组件下游 F4 必死。
修:concept 车道组件 <3 且预算内 → 当场重抽一次;预算尽仍 <3 → 原样返回
(F4 照拦,阈值不动);procedural 车道不触发。
"""
from __future__ import annotations

import json

from src.akb2.quality.distill import (
    EXTRACT_QUALITY_RETRY_CONCEPT,
    extract_claims,
)
from src.akb2.quality.llm_router import LLMResponse

_CLAIMS = {
    "claims": [
        {"text": "Loop engineering designs bounded convergent observable agent loops",
         "anchor": "§A", "provenance": "source_grounded",
         "evidence_gap": "缺少量化判据"},
        {"text": "A loop has trigger selector executor gate ledger five components",
         "anchor": "§B", "provenance": "source_grounded",
         "evidence_gap": "未提供负载对比"},
        {"text": "Fix defects rather than restarting the pipeline for convergence",
         "anchor": "§C", "provenance": "source_grounded",
         "evidence_gap": "未验证实测成本"},
    ],
    "tldr": "Loop engineering is designing bounded convergent observable agent loops today.",
}


_CMDS = ["make deploy", "make test", "python -m akb2.quality.runner --once"]


def _how(n_comp: int) -> dict:
    comps = [{"name": f"Comp{i}", "role": "r", "evidence_anchor": "§A",
              "evidence_sentence": f"Comp{i} does r."} for i in range(n_comp)]
    steps = [{"instruction": f"Run {c}", "command": c, "executable": True}
             for c in _CMDS]
    return {"how_steps": steps, "param_entries": [{"key": "k", "value": "1"}],
            "mechanism_components": comps, "causal_edges": []}


def _mk_llm(comp_seq: list[int], calls: list) -> object:
    """按调用序返回:claims / how(comp_seq[0]) / claims / how(comp_seq[1]) ..."""
    state = {"i": 0}

    def fn(model, messages, response_schema=None, **k):
        calls.append(response_schema["required"][0])
        if response_schema["required"][0] == "claims":
            return LLMResponse(content=json.dumps(_CLAIMS), model_used="mock")
        n = comp_seq[min(state["i"], len(comp_seq) - 1)]
        state["i"] += 1
        return LLMResponse(content=json.dumps(_how(n)), model_used="mock")

    return fn


def test_budget_named_and_bounded() -> None:
    assert EXTRACT_QUALITY_RETRY_CONCEPT == 1


def test_concept_low_components_retries_once(sample_raw_content) -> None:
    """首轮 2 组件 → 重抽 → 次轮 4 组件被接受(how_steps 调 2 次)。"""
    calls: list = []
    r = extract_claims(sample_raw_content, route_llm_fn=_mk_llm([2, 4], calls),
                       content_lane="concept")
    assert r.success is True
    assert len(r.data["mechanism_components"]) == 4
    assert calls.count("how_steps") == 2


def test_budget_exhausted_returns_as_is_failclosed_downstream(sample_raw_content) -> None:
    """预算尽仍 2 组件 → 原样返回(不无限重试;F4 在下游照拦,阈值不动)。"""
    calls: list = []
    r = extract_claims(sample_raw_content, route_llm_fn=_mk_llm([2, 2], calls),
                       content_lane="concept")
    assert r.success is True
    assert len(r.data["mechanism_components"]) == 2
    assert calls.count("how_steps") == 2  # 1 首轮 + 1 质量重试,不再多


def test_procedural_lane_not_triggered(sample_raw_content) -> None:
    calls: list = []
    r = extract_claims(sample_raw_content, route_llm_fn=_mk_llm([0], calls),
                       content_lane="procedural")
    assert r.success is True
    assert calls.count("how_steps") == 1
