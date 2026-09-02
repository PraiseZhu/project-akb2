"""Stage 3: extract contract fills from raw, never fabricates, floor stays hard."""

from __future__ import annotations

from src.akb2.quality.distill import EXTRACT_QUALITY_RETRY_CONCEPT
from src.akb2.quality.integrity_floor import evaluate_integrity_floor
from src.akb2.quality.maker2.extract_contract import fulfill_extract_contract
from src.akb2.quality.raw_anchors import declared_anchor_set

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

_RAW_NO_MECHANISM = """\
## 锚点目录

| §A | x |
| §B | y |

---

Plain body with no headings no causal language no named components here.
"""


def _zero_component_extract() -> dict:
    return {
        "claims": [
            {
                "text": "some claim",
                "anchor": "§A",
                "provenance": "source_grounded",
                "evidence_gap": "未提供 X",
            }
        ],
        "argumentation": [],
        "mechanism_components": [],
        "causal_edges": [],
        "how_steps": [],
    }


def test_extract_quality_retry_concept_stays_zero() -> None:
    assert EXTRACT_QUALITY_RETRY_CONCEPT == 0


def test_evidence_gap_wordlist_accepts_weimingque() -> None:
    """实跑失败句「原文未明确说明…」须被词表收下，不能烧光抽取重试。"""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "src/akb2/quality/distill.py"
    ).read_text(encoding="utf-8")
    start = src.index("gap_words = (")
    end = src.index(")", start)
    block = src[start:end]
    assert '"未明确"' in block
    assert '"未列出"' in block
    from src.akb2.quality.integrity_floor import EVIDENCE_GAP_WORDS

    assert "未明确" in EVIDENCE_GAP_WORDS
    failed = "原文未明确说明目标用户群或与其他动画库的比较。"
    words = ("未提供", "未量化", "未覆盖", "缺少", "没有", "未验证", "未比较",
             "未说明", "未交代", "未列明", "未列出", "未给出", "未解释", "未披露",
             "未明确", "未指出")
    listed = "原文未列出对 Firefox、Safari 等非 Chromium 浏览器的支持计划。"
    assert any(w in listed for w in words)
    from src.akb2.quality.integrity_floor import EVIDENCE_GAP_WORDS
    assert "未列出" in EVIDENCE_GAP_WORDS
    assert any(w in failed for w in words)


def test_zero_components_rescued_from_raw() -> None:
    result = fulfill_extract_contract(
        _zero_component_extract(), _RAW_WITH_CAUSAL, content_lane="concept"
    )
    names = [
        str(item.get("name", "")).strip()
        for item in result.extract_data["mechanism_components"]
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]
    assert result.enriched is True
    assert result.components_added >= 1
    assert names
    for name in names:
        assert name in _RAW_WITH_CAUSAL
    assert result.fabricated is False


def test_zero_eligible_edges_rescued_to_floor() -> None:
    data = _zero_component_extract()
    data["mechanism_components"] = [
        {"name": "Executor Pool", "role": "x"},
        {"name": "Task Graph", "role": "y"},
        {"name": "Lifecycle Manager", "role": "z"},
    ]
    result = fulfill_extract_contract(data, _RAW_WITH_CAUSAL, content_lane="concept")
    assert result.edges_added >= 1
    assert result.floor_passed is True
    assert result.fabricated is False


def test_deterministic_cannot_fabricate_when_raw_has_no_mechanism() -> None:
    result = fulfill_extract_contract(
        _zero_component_extract(), _RAW_NO_MECHANISM, content_lane="concept"
    )
    assert result.extract_data["mechanism_components"] == []
    assert result.extract_data["causal_edges"] == []
    assert result.floor_passed is False
    assert result.fabricated is False


def test_contract_anchors_subset_of_raw() -> None:
    result = fulfill_extract_contract(
        _zero_component_extract(), _RAW_WITH_CAUSAL, content_lane="concept"
    )
    declared = declared_anchor_set(_RAW_WITH_CAUSAL)
    for edge in result.extract_data.get("causal_edges") or []:
        if not isinstance(edge, dict):
            continue
        anchor = str(edge.get("evidence_anchor", "")).strip()
        if anchor:
            assert anchor in declared
    assert result.unanchored == []


_RAW_FULLNAME_MARKERS = """\
## 锚点目录

- §A: CONTENT_START
- §B: INTRODUCTION
- §C: CONTEXT

---

§A_CONTENT_START

The Executor Pool dispatches to the Task Graph for scheduling.

§B_INTRODUCTION

The Task Graph feeds the Lifecycle Manager.

§C_CONTEXT

conclusion.
"""


def test_short_component_anchor_resolves_to_declared_fullname() -> None:
    """实跑 abort：LLM 写 §B，raw 声明 §B_INTRODUCTION。唯一前缀须解析，不得当伪造。"""
    data = _zero_component_extract()
    data["mechanism_components"] = [
        {
            "name": "Executor Pool",
            "role": "dispatch",
            "evidence_anchor": "§B",
            "evidence_sentence": "The Task Graph feeds the Lifecycle Manager.",
        },
        {
            "name": "Task Graph",
            "role": "schedule",
            "evidence_anchor": "§B",
            "evidence_sentence": "The Task Graph feeds the Lifecycle Manager.",
        },
        {
            "name": "Lifecycle Manager",
            "role": "lifecycle",
            "evidence_anchor": "§C",
            "evidence_sentence": "conclusion.",
        },
    ]
    data["causal_edges"] = [
        {
            "from": "Executor Pool",
            "to": "Task Graph",
            "relation": "dispatches",
            "edge_kind": "source_causal",
            "evidence": "The Executor Pool dispatches to the Task Graph",
            "evidence_anchor": "§B",
        }
    ]
    result = fulfill_extract_contract(
        data, _RAW_FULLNAME_MARKERS, content_lane="concept"
    )
    declared = declared_anchor_set(_RAW_FULLNAME_MARKERS)
    assert "§B_INTRODUCTION" in declared
    assert "§B" not in declared
    for item in result.extract_data["mechanism_components"]:
        anchor = str(item.get("evidence_anchor", "")).strip()
        if anchor:
            assert anchor in declared
    for edge in result.extract_data["causal_edges"]:
        anchor = str(edge.get("evidence_anchor", "")).strip()
        if anchor:
            assert anchor in declared
    assert result.unanchored == []
    assert result.fabricated is False


def test_floating_interpretation_without_traces_is_dropped() -> None:
    """实跑 abort：akb_interpretation 缺 traces_to。合同丢掉飘浮解读，不造锚。"""
    data = _zero_component_extract()
    data["claims"].append(
        {
            "text": "floating takeaway",
            "anchor": "§A",
            "provenance": "akb_interpretation",
            "evidence_gap": "未提供 X",
        }
    )
    data["argumentation"] = [
        {
            "explanation": "also floating",
            "provenance": "akb_interpretation",
            "evidence_gap": "未提供 X",
        }
    ]
    data["mechanism_components"] = [
        {"name": "Executor Pool", "role": "x"},
        {"name": "Task Graph", "role": "y"},
        {"name": "Lifecycle Manager", "role": "z"},
    ]
    data["causal_edges"] = [
        {
            "from": "Executor Pool",
            "to": "Task Graph",
            "relation": "dispatches",
            "edge_kind": "source_causal",
            "evidence": "The Executor Pool dispatches to the Task Graph",
            "evidence_anchor": "§A",
        }
    ]
    result = fulfill_extract_contract(data, _RAW_WITH_CAUSAL, content_lane="concept")
    provenances = [
        item.get("provenance")
        for item in result.extract_data["claims"]
        if isinstance(item, dict)
    ]
    assert "akb_interpretation" not in provenances
    assert result.extract_data["argumentation"] == []
    assert result.floor_passed is True


def test_content_start_suffix_canonicalizes_to_declared() -> None:
    """实跑 abort：LLM 写 §A_CONTENT_START，声明集是 §A。哨兵后缀须剥离。"""
    data = _zero_component_extract()
    data["mechanism_components"] = [
        {
            "name": "Executor Pool",
            "role": "dispatch",
            "evidence_anchor": "§A_CONTENT_START",
            "evidence_sentence": "The Executor Pool dispatches to the Task Graph for scheduling.",
        },
        {
            "name": "Task Graph",
            "role": "schedule",
            "evidence_anchor": "§A_CONTENT_START",
            "evidence_sentence": "The Executor Pool dispatches to the Task Graph for scheduling.",
        },
        {
            "name": "Lifecycle Manager",
            "role": "lifecycle",
            "evidence_anchor": "§B_INTRODUCTION",
            "evidence_sentence": "The Task Graph feeds the Lifecycle Manager.",
        },
    ]
    data["causal_edges"] = [
        {
            "from": "Executor Pool",
            "to": "Task Graph",
            "relation": "dispatches",
            "edge_kind": "source_causal",
            "evidence": "The Executor Pool dispatches to the Task Graph",
            "evidence_anchor": "§A_CONTENT_START",
        }
    ]
    result = fulfill_extract_contract(
        data, _RAW_FULLNAME_MARKERS, content_lane="concept"
    )
    declared = declared_anchor_set(_RAW_FULLNAME_MARKERS)
    assert "§A" in declared
    assert "§A_CONTENT_START" not in declared
    for item in result.extract_data["mechanism_components"]:
        anchor = str(item.get("evidence_anchor", "")).strip()
        if anchor:
            assert anchor in declared
            assert not anchor.endswith("_CONTENT_START")
    assert result.unanchored == []
    assert result.fabricated is False


def test_unknown_short_anchor_stays_fabricated() -> None:
    data = _zero_component_extract()
    data["mechanism_components"] = [
        {"name": "Ghost", "role": "x", "evidence_anchor": "§Z"}
    ]
    result = fulfill_extract_contract(
        data, _RAW_FULLNAME_MARKERS, content_lane="concept"
    )
    assert "§Z" in result.unanchored
    assert result.fabricated is True


def test_floor_uncompensated_after_contract() -> None:
    data = _zero_component_extract()
    data["claims"] = [
        {"text": "ungrounded", "anchor": "", "provenance": "source_grounded"}
    ]
    result = fulfill_extract_contract(data, _RAW_WITH_CAUSAL, content_lane="concept")
    floor = evaluate_integrity_floor(
        result.extract_data,
        "concept",
        raw_anchors=declared_anchor_set(_RAW_WITH_CAUSAL),
    )
    assert floor.passed is False
    assert result.floor_passed is False
