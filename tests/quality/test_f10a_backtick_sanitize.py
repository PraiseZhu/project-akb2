"""test_f10a_backtick_sanitize.py — F10-A crewai 反引号污染渲染 bug 测试。

probe-p8 crewai 死因:机制叙述文本被吸进命令 span——evidence 原文里的不成对反引号
跨段把叙述段落框成了"命令"(SG-command-recheck 误报 '命令原文不存在')。

F10-A:所有渲染进 body 的 raw 摘录(evidence/TLDR/叙述/组件表句)统一过 _sanitize_backticks
(奇数反引号→移除末个不成对,偶数不动),保证渲染产物反引号平衡。
"""

from __future__ import annotations

from src.akb2.quality.distill import (
    _build_body,
    _build_mechanism_evidence_section,
    _render_mechanism_narrative,
    _sanitize_backticks,
)
from src.akb2.quality.source_ops_scanners.raw_verify import verify_commands_in_raw


class TestF10ASanitizeBackticks:
    def test_odd_backtick_last_removed(self):
        """奇数反引号→移除末个不成对(成偶数平衡)。"""
        text = "to resume:)。 Creating Crews`"
        out = _sanitize_backticks(text)
        assert out.count("`") % 2 == 0, "消毒后反引号须平衡"
        assert out == "to resume:)。 Creating Crews"

    def test_even_backticks_untouched(self):
        """偶数反引号(成对 code span)不动。"""
        text = "run `make deploy` now"
        assert _sanitize_backticks(text) == text

    def test_no_backticks_untouched(self):
        assert _sanitize_backticks("plain text no backtick") == "plain text no backtick"

    def test_empty(self):
        assert _sanitize_backticks("") == ""

    def test_multiple_unpaired_still_balanced(self):
        """3 个反引号→移除末个→2 个(成对)。"""
        text = "a`b`c`"
        out = _sanitize_backticks(text)
        assert out.count("`") % 2 == 0


class TestF10AMechanismNarrativeNoBacktickPollution:
    def _data_with_odd_backtick_evidence(self) -> dict:
        """evidence 含不成对反引号(probe-p8 crewai 形态)。"""
        return {
            "claims": [
                {"text": "grounded claim.", "anchor": "§A", "provenance": "source_grounded",
                 "evidence_gap": "未提供 X"},
            ],
            "argumentation": [],
            "mechanism_components": [
                {"name": "Crew", "role": "x", "component_id": "crew",
                 "evidence_anchor": "§A", "evidence_sentence": "Crew runs"},
                {"name": "Crew Output Attributes", "role": "y", "component_id": "crew_output",
                 "evidence_anchor": "§A", "evidence_sentence": "attrs here"},
                {"name": "Task", "role": "z", "component_id": "task",
                 "evidence_anchor": "§A", "evidence_sentence": "task runs"},
            ],
            "causal_edges": [
                {"from": "Crew", "to": "Crew Output Attributes", "relation": "produces",
                 "edge_kind": "source_causal",
                 "evidence": "to resume:)。 Creating Crews`",  # 不成对反引号
                 "evidence_anchor": "§A"},
            ],
            "graph_edges": [],
            "how_steps": [],
            "tldr": "Crew produces output.",
            "params": {},
            "referenced_concepts": [],
            "applicability_boundary": "",
        }

    def _raw(self) -> str:
        return (
            "§A_CONTENT_START\n\n"
            "grounded claim. Crew runs. attrs here. task runs. "
            "to resume:)。 Creating Crews\n"
        )

    def test_narrative_evidence_backticks_balanced(self):
        """叙述段落 evidence 含不成对反引号→渲染后平衡。"""
        data = self._data_with_odd_backtick_evidence()
        narr = _render_mechanism_narrative(data, self._raw())
        assert narr  # 非空
        assert narr.count("`") % 2 == 0, f"叙述段落反引号不平衡: {narr.count('`')}"
        # 原始不成对反引号被移除
        assert "Creating Crews`" not in narr

    def test_mechanism_table_evidence_balanced(self):
        """组件表/边表 evidence 含反引号→渲染后平衡。"""
        data = self._data_with_odd_backtick_evidence()
        section = _build_mechanism_evidence_section(data)
        assert section.count("`") % 2 == 0, f"机制表反引号不平衡: {section.count('`')}"

    def test_full_body_no_sg_command_false_positive(self):
        """完整 body 的叙述段不被 SG-command-recheck 误报为编造命令。

        probe-p8 crewai 原话:SG 报 '命令原文不存在: `to resume:)。 Creating Crews
        通过 raw_causal 导致...'——不成对反引号把叙述框成命令 span。消毒后应无此误报。
        """
        data = self._data_with_odd_backtick_evidence()
        raw = self._raw()
        body = _build_body(
            data, "test.md", None, must_cover=[], raw_content=raw, content_lane="concept"
        )
        # body 整体反引号平衡(叙述段不跨段框命令)
        # 注:how_text 可能含合法命令反引号,这里 focus on 机制段
        violations = verify_commands_in_raw(body, raw)
        # 不应有 'to resume' 或 'Creating Crews' 被当命令报
        cmd_violations = [
            v for v in violations
            if "Creating Crews" in getattr(v, "command", "")
            or "to resume" in getattr(v, "command", "")
            or "raw_causal" in getattr(v, "command", "")
        ]
        assert cmd_violations == [], (
            f"叙述文本被误报为命令(反引号污染未消): {[v.command for v in cmd_violations]}"
        )
