"""test_f8a_backfill_frontmatter.py — F8-A F6-D 回填 frontmatter 毒化修复测试。

F7-D 诊断:ag2 Captain 组件 evidence 空回填。F8-A 离线复现根因:旧版句级 split +
break-on-first-match 被 frontmatter 命中(§A 锚前)毒化——Captain 在 raw frontmatter
source_title 先匹配,nearest_preceding_anchor 返空(无锚在前)→ ``if anchor`` 失败 →
但已 break,不再找后面 §E_CLAIMS 真实命中句。

覆盖:
    - frontmatter 含组件名(source_title)→ 仍从 body 回填(不卡 frontmatter)
    - ag2 4 组件集成(Captain/Executor Pool/Task Graph and Checkpointing/Sub-Agent
      Lifecycle)全部回填,锚∈声明集
    - stoplist 词作组件名单字(如 "Agent")→ 回填仍工作(stoplist 不适用回填)
    - 无声明锚 raw → fail-closed 返 0/0(不造假)
"""

from __future__ import annotations

from pathlib import Path

from src.akb2.quality.distill import _backfill_component_evidence
from src.akb2.quality.raw_anchors import declared_anchor_set

_WT = Path(__file__).resolve().parents[2]
_AG2_RAW_PATH = (
    _WT / "reports/akb2-quality-repair/_tmp/raw-store/"
    "2fb4f618c181edf17f8ebbf7f51ef41b1fe4bb73c3525eb857aeb68832359894.md"
)


# frontmatter 含组件名(source_title: Captain Agent ...)→ 旧版被此命中毒化
_RAW_WITH_FRONTMATTER_POISON = """\
---
artifact_type: raw
source_title: Captain Agent Reference
---

## 锚点目录

| §A | x |
| §B | y |

---

§A_CONTENT_START

# Captain Agent Architecture

§B_INTRODUCTION

The Captain agent holds the system-level goal and maintains a task graph.
"""


class TestF8AFrontmatterPoisoning:
    def test_component_name_in_frontmatter_still_backfills_from_body(self):
        """组件名 'Captain' 在 frontmatter source_title(§A 锚前)先出现 →
        旧版 break-on-first-match 卡此处返空锚 → Captain 回填 0。
        F8-A:frontmatter 跳过(首锚前)→ 从 body §B_INTRODUCTION 回填。"""
        data = {"mechanism_components": [
            {"name": "Captain", "role": "coordinator"},
        ]}
        result = _backfill_component_evidence(data, _RAW_WITH_FRONTMATTER_POISON)
        assert result["backfilled"] == 1
        comp = data["mechanism_components"][0]
        # evidence_sentence 来自 body(不是 frontmatter source_title)
        assert "Captain" in comp["evidence_sentence"]
        assert "source_title" not in comp["evidence_sentence"]
        # evidence_anchor ∈ 声明集(§B_INTRODUCTION,不是空)
        decl = declared_anchor_set(_RAW_WITH_FRONTMATTER_POISON)
        assert comp["evidence_anchor"] in decl
        assert comp["evidence_anchor"]  # 非空


class TestF8AAg2FourComponents:
    """ag2 真实 raw + 模拟 LLM 4 组件(probe-p6 真实组件名)→ 全部回填。

    F7-D 报 ag2/crewai H3 残留:evidence_anchor/evidence_sentence 空。F8-A 离线复现:
    旧版 3/4(Captain 空),新版 4/4。
    """

    def test_ag2_four_components_all_backfilled(self):
        if not _AG2_RAW_PATH.exists():
            import pytest
            pytest.skip("ag2 raw fixture 不存在(非 worktree 运行)")
        raw = _AG2_RAW_PATH.read_text()
        components = [
            {"name": "Captain", "role": "coordinator"},
            {"name": "Executor Pool", "role": "execution"},
            {"name": "Task Graph and Checkpointing", "role": "state"},
            {"name": "Sub-Agent Lifecycle", "role": "lifecycle"},
        ]
        data = {"mechanism_components": components}
        result = _backfill_component_evidence(data, raw)
        # 4 组件全部回填,无 unlocated
        assert result["backfilled"] == 4, f"backfilled={result['backfilled']}, 期望 4"
        assert result["unlocated"] == 0
        decl = declared_anchor_set(raw)
        for comp in components:
            assert comp["evidence_sentence"], f"{comp['name']} evidence_sentence 空"
            assert comp["evidence_anchor"], f"{comp['name']} evidence_anchor 空"
            # evidence_sentence 逐字在 raw
            assert comp["evidence_sentence"] in raw, (
                f"{comp['name']} evidence_sentence 非逐字"
            )
            # evidence_anchor ∈ 声明集
            assert comp["evidence_anchor"] in decl, (
                f"{comp['name']} anchor {comp['evidence_anchor']} 不在声明集"
            )


class TestF8AStoplistRelaxed:
    """lead 裁定:回填是给已认定组件找证据,stoplist/collision 不适用(单组件无歧义)。"""

    def test_stoplist_word_as_component_name_still_backfills(self):
        """组件名 'Agent'(agent 在 _COMPONENT_VARIANT_STOPLIST)→ 旧版 variant_map
        被 stoplist 弃 → 无 pattern → 回填 0。F8-A:回填专用搜索词不走 stoplist →
        'agent' 作全名 pass-1 仍搜 → 回填。"""
        raw = (
            "## 锚点目录\n\n| §A | x |\n\n---\n\n§A_CONTENT_START\n\n"
            "The Agent processes all incoming requests and delegates tasks.\n"
        )
        data = {"mechanism_components": [{"name": "Agent", "role": "x"}]}
        result = _backfill_component_evidence(data, raw)
        assert result["backfilled"] == 1
        comp = data["mechanism_components"][0]
        assert "Agent" in comp["evidence_sentence"]
        assert comp["evidence_anchor"] == "§A"


class TestF8AFailClosedNoAnchor:
    def test_no_declared_anchor_returns_zero(self):
        """无声明锚 raw → 无法映射 evidence_anchor → fail-closed 返 0/0(不造假)。"""
        raw = "Some content without any anchor declarations.\nThe Captain runs."
        data = {"mechanism_components": [{"name": "Captain", "role": "x"}]}
        result = _backfill_component_evidence(data, raw)
        assert result["backfilled"] == 0
        assert result["unlocated"] == 0  # 无锚不计 unlocated(fail-closed 静默)
        comp = data["mechanism_components"][0]
        assert not comp.get("evidence_anchor")
        assert not comp.get("evidence_sentence")
