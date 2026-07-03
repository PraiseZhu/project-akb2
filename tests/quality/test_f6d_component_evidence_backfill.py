"""test_f6d_component_evidence_backfill.py — F6-D 组件 evidence 确定性回填测试。

覆盖:
    - 空 evidence 组件被回填(evidence_sentence 逐字在 raw + evidence_anchor ∈ 声明集)
    - 定位不到(组件名不在 raw)→ 留空(不造假)
    - 已有 evidence 不覆盖
"""

from __future__ import annotations

from src.akb2.quality.distill import _backfill_component_evidence
from src.akb2.quality.raw_anchors import declared_anchor_set

_RAW = """\
## 锚点目录

| §A | x |
| §B | y |

---

§A_CONTENT_START

intro paragraph.

§B_INTRODUCTION

The Executor Pool dispatches to the Task Graph for scheduling.
"""


def _comps(*names: str) -> list[dict]:
    return [{"name": n, "role": "x"} for n in names]


class TestF6DComponentEvidenceBackfill:
    def test_empty_evidence_backfilled(self):
        """空 evidence 组件 → 回填 evidence_sentence(逐字在 raw)+ evidence_anchor(∈声明集)。"""
        data = {"mechanism_components": _comps("Executor Pool")}
        result = _backfill_component_evidence(data, _RAW)
        assert result["backfilled"] == 1
        comp = data["mechanism_components"][0]
        # evidence_sentence 逐字在 raw
        assert comp["evidence_sentence"] in _RAW
        assert "Executor Pool" in comp["evidence_sentence"]
        # evidence_anchor ∈ 声明集
        decl = declared_anchor_set(_RAW)
        assert comp["evidence_anchor"] in decl

    def test_not_found_leaves_empty(self):
        """组件名不在 raw → 定位不到 → 留空(不造假),计 unlocated。"""
        data = {"mechanism_components": _comps("Nonexistent Component")}
        result = _backfill_component_evidence(data, _RAW)
        assert result["backfilled"] == 0
        assert result["unlocated"] == 1
        comp = data["mechanism_components"][0]
        assert not comp.get("evidence_sentence")
        assert not comp.get("evidence_anchor")

    def test_existing_evidence_not_overwritten(self):
        """已有 evidence_anchor + evidence_sentence → 不覆盖。"""
        data = {"mechanism_components": [
            {"name": "Executor Pool", "role": "x",
             "evidence_anchor": "§A", "evidence_sentence": "existing sentence."},
        ]}
        result = _backfill_component_evidence(data, _RAW)
        assert result["backfilled"] == 0
        comp = data["mechanism_components"][0]
        assert comp["evidence_sentence"] == "existing sentence."
        assert comp["evidence_anchor"] == "§A"

    def test_partial_evidence_filled(self):
        """有 evidence_anchor 但无 evidence_sentence → 只回填 sentence(不覆盖 anchor)。"""
        data = {"mechanism_components": [
            {"name": "Executor Pool", "role": "x", "evidence_anchor": "§B"},
        ]}
        result = _backfill_component_evidence(data, _RAW)
        assert result["backfilled"] == 1
        comp = data["mechanism_components"][0]
        assert comp["evidence_anchor"] == "§B"  # 不覆盖
        assert comp["evidence_sentence"]  # 回填了
        assert "Executor Pool" in comp["evidence_sentence"]

    def test_heading_table_lines_not_picked(self):
        """标题行(#)/表格行(|)不被当 evidence sentence 摘。"""
        raw = (
            "## 锚点目录\n\n| §A | x |\n\n---\n\n§A_CONTENT_START\n\n"
            "## Executor Pool Heading\n"  # 标题行,不摘
            "The Executor Pool runs tasks.\n"  # 正文句,摘
        )
        data = {"mechanism_components": _comps("Executor Pool")}
        _backfill_component_evidence(data, raw)
        comp = data["mechanism_components"][0]
        assert "The Executor Pool runs tasks" in comp["evidence_sentence"]
        assert not comp["evidence_sentence"].startswith("#")
