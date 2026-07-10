"""test_f43_concept_code_entity_extraction.py — F43 concept 0-eligible 救援测试。

覆盖离线复现暴露的 concept floor-death 根因（Fresh 2.3 / Deno Sandbox 体裁）：
LLM 组件是框架特性名（Fresh/csp middleware/...），与因果句里的机制参与者
（islands/partials/nonce/secrets/allowNet）不匹配 → _build_real_causal_edges
单句 <2 组件共现 → 0 source_causal eligible 边 → F4 floor 死。

F43 修法：扩 _CAUSAL_CONNECTOR_RE（when/if/never enter/...）+ _scan_code_entities
（inline backtick + fenced object-key，verbatim raw）并入 det_components → 救回。

断言绑定可观测行为：eligible 边数从 0 → ≥1（F4 floor 过），非造假（command/anchor 逐字）。
"""

from __future__ import annotations

from src.akb2.quality.distill import (
    _CAUSAL_CONNECTOR_RE,
    _enrich_zero_eligible_with_deterministic,
    _scan_code_entities,
)
from src.akb2.quality.integrity_floor import count_eligible_edges, evaluate_integrity_floor
from src.akb2.quality.raw_anchors import declared_anchor_set

# raw:Fresh 2.3 体裁——散文体博文，结构组件扫描返 0，但机制参与者以 inline backtick
# 与因果句（when/before/never enter）形式逐字存在。锚点目录 + §-heading 形态。
_RAW_FRESH_LIKE = """\
## 锚点目录

| §A | x |
| §B | y |
| §C | z |

---

## §A Intro

Fresh 2.3 introduces zero-JavaScript for static pages.

## §B Mechanism

Fresh now checks whether the page actually uses islands or partials (`f-client-nav`) before injecting anything. If it doesn't, the page ships with no script tag.

## §C Security

CSP nonce injection generates a unique `nonce` per request and adds it to every inline script tag.
"""

# raw:Deno Sandbox 体裁——marker 锚(独行)+ plain H2(非 §-anchor heading,struct 扫描返 0)
# + fenced 代码块 object-key(secrets/allowNet/hosts)+ 因果句 "only when"/"never enter"。
_RAW_SANDBOX_LIKE = """\
## 锚点目录

| §A | x |
| §B | y |
| §C | z |

---

§A_CONTENT_START

Deno Sandbox runs untrusted code with defense-in-depth.

§B_INTRODUCTION

## Secrets That Can't Be Stolen

In Deno Sandbox, secrets never enter the environment. Code sees only a placeholder.

§C_CONTEXT

```
await using sandbox = await Sandbox.create({
  secrets: {
    OPENAI_API_KEY: {
      hosts: ["api.openai.com"],
    },
  },
  allowNet: ["api.openai.com"],
});
```
"""

# raw:纯散文无代码实体 + 无结构组件 → 应 fail-closed（0 eligible，不凑数）。
_RAW_PROSE_NO_CODE = """\
## 锚点目录

| §A | x |
| §B | y |

---

## §A Intro

This is a generic opinion piece with no mechanism and no code entities at all.

## §B Body

The author thinks the framework is nice. It feels good to use.
"""


def _llm_comps_fresh() -> list[dict[str, str]]:
    """LLM 实际产的 Fresh 组件（框架特性名，非因果句参与者）——离线复现原状。"""
    return [
        {"name": "Fresh"},
        {"name": "csp middleware"},
        {"name": "ipFilter middleware"},
        {"name": "trustProxy option"},
    ]


class TestScanCodeEntities:
    """_scan_code_entities：inline backtick + fenced object-key 扫描，stoplist 过滤。"""

    def test_inline_backtick_tokens_extracted(self) -> None:
        ents = {c["name"] for c in _scan_code_entities(_RAW_FRESH_LIKE)}
        assert "f-client-nav" in ents
        assert "nonce" in ents

    def test_fenced_object_keys_extracted(self) -> None:
        ents = {c["name"] for c in _scan_code_entities(_RAW_SANDBOX_LIKE)}
        assert "secrets" in ents
        assert "allowNet" in ents
        assert "hosts" in ents

    def test_code_keywords_stoplisted(self) -> None:
        # JS 代码关键字 / 通用配置泛词不入组件集；hosts 是机制旋钮应保留
        ents = {c["name"] for c in _scan_code_entities(_RAW_SANDBOX_LIKE)}
        assert "await" not in ents  # 代码关键字
        assert "return" not in ents  # 代码关键字
        assert "value" not in ents  # 通用配置泛词
        assert "hosts" in ents  # 机制旋钮保留

    def test_prose_no_code_returns_empty(self) -> None:
        assert _scan_code_entities(_RAW_PROSE_NO_CODE) == []


class TestExpandedCausalConnector:
    """F43 扩展的因果连接词（when/if/never enter/only when/materializes）。"""

    def test_when_matches(self) -> None:
        assert _CAUSAL_CONNECTOR_RE.search("only when the sandbox makes an outbound request")

    def test_never_enter_matches(self) -> None:
        assert _CAUSAL_CONNECTOR_RE.search("secrets never enter the environment")

    def test_if_matches(self) -> None:
        assert _CAUSAL_CONNECTOR_RE.search("If it doesn't, the page ships with no script")


class TestConceptZeroEligibleRescue:
    """_enrich_zero_eligible_with_deterministic：0 eligible → ≥1 eligible（F4 floor 过）。"""

    def test_fresh_like_0_to_eligible(self) -> None:
        raw = _RAW_FRESH_LIKE
        anchors = declared_anchor_set(raw)
        data: dict = {
            "mechanism_components": _llm_comps_fresh(),
            "causal_edges": [],
            "graph_edges": [],
            "claims": [{"text": "Fresh 2.3 zero-JS", "anchor": "§A", "provenance": "source_grounded"}],
            "argumentation": [],
        }
        before = count_eligible_edges(data, raw_anchors=anchors)
        assert before["eligible"] == 0  # 修前 0 eligible（复现 floor-death 根因）

        diag = _enrich_zero_eligible_with_deterministic(data, raw)

        after = count_eligible_edges(data, raw_anchors=anchors)
        assert diag["enriched"] is True
        assert after["eligible"] >= 1, f"expected ≥1 eligible after F43, got {after}"
        # 救回的边须有 verbatim evidence + 真实 anchor（不造假）
        scs = [e for e in data["causal_edges"] if e.get("edge_kind") == "source_causal"]
        assert scs, "expected ≥1 source_causal edge"
        for e in scs:
            assert e.get("evidence"), "edge must carry verbatim evidence sentence"
            assert e.get("evidence_anchor") in anchors, "edge anchor must be real raw §-anchor"

    def test_sandbox_like_0_to_eligible(self) -> None:
        raw = _RAW_SANDBOX_LIKE
        anchors = declared_anchor_set(raw)
        data: dict = {
            "mechanism_components": [
                {"name": "Deno Sandbox"},
                {"name": "outbound proxy"},
                {"name": "Network Egress Control"},
            ],
            "causal_edges": [],
            "graph_edges": [],
            "claims": [{"text": "Deno Sandbox isolates code", "anchor": "§A", "provenance": "source_grounded"}],
            "argumentation": [],
        }
        before = count_eligible_edges(data, raw_anchors=anchors)
        assert before["eligible"] == 0

        _enrich_zero_eligible_with_deterministic(data, raw)

        after = count_eligible_edges(data, raw_anchors=anchors)
        assert after["eligible"] >= 1, f"expected ≥1 eligible after F43, got {after}"

    def test_prose_no_code_fail_closed(self) -> None:
        """纯散文无代码实体 + 无结构组件 → 0 eligible（fail-closed 不凑数）。"""
        raw = _RAW_PROSE_NO_CODE
        anchors = declared_anchor_set(raw)
        data: dict = {
            "mechanism_components": [{"name": "framework"}],
            "causal_edges": [],
            "graph_edges": [],
            "claims": [],
            "argumentation": [],
        }
        before = count_eligible_edges(data, raw_anchors=anchors)
        diag = _enrich_zero_eligible_with_deterministic(data, raw)
        after = count_eligible_edges(data, raw_anchors=anchors)
        # 无代码实体可抽 → 不造假 → 仍 0 eligible（F4 fail-closed 不变）
        assert after["eligible"] == 0
        assert diag["enriched"] in (False, True)  # 可能加 0 组件，enriched 不强求
        # 关键不变式：没造出 eligible 边蒙混
        assert after["eligible"] == before["eligible"]

    def test_floor_passes_after_rescue(self) -> None:
        """端到端：concept F4 floor 修前 fail（0 eligible）→ 修后 pass。"""
        raw = _RAW_FRESH_LIKE
        anchors = declared_anchor_set(raw)
        data: dict = {
            "mechanism_components": _llm_comps_fresh(),
            "causal_edges": [],
            "graph_edges": [],
            "claims": [{"text": "Fresh 2.3 zero-JS", "anchor": "§A", "provenance": "source_grounded"}],
            "argumentation": [],
        }
        floor_before = evaluate_integrity_floor(data, "concept", raw_anchors=anchors)
        assert "core_value_f4" in floor_before.failed_floors  # 修前 F4 fail

        _enrich_zero_eligible_with_deterministic(data, raw)
        floor_after = evaluate_integrity_floor(data, "concept", raw_anchors=anchors)
        assert "core_value_f4" not in floor_after.failed_floors  # 修后 F4 过
