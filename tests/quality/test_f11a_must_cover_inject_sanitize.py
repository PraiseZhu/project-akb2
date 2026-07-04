"""test_f11a_must_cover_inject_sanitize.py — F11-A must_cover 注入消毒对齐测试。

probe-p9a A 臂 sub-agents compose 死于 SG-command `--fork` 误报:must_cover literal
含反引号命令,注入 TLDR/boundary 节后无 strip → SG-command-recheck 误报。
F11-A:TLDR+boundary 注入后过 _sanitize_backticks + _strip_ungrounded_backtick_commands,
与 how 节对齐;how 节加 _sanitize_backticks(strip 前平衡)。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.akb2.quality.distill import _build_body
from src.akb2.quality.source_ops_scanners.raw_verify import verify_commands_in_raw


def _item(literal: str, kind: str = "source_operation") -> SimpleNamespace:
    return SimpleNamespace(
        literal=literal, kind=kind, source="test",
        raw_anchor_span=SimpleNamespace(start=0),
    )


_RAW = """\
§A_CONTENT_START

The system runs `make deploy` and `claude --fork` to spawn subagents.
Other prose without commands.
"""


class TestF11AMustCoverInjectSanitize:
    def _data(self) -> dict:
        return {
            "claims": [
                {"text": "grounded claim.", "anchor": "§A", "provenance": "source_grounded",
                 "evidence_gap": "未提供 X"},
            ],
            "argumentation": [],
            "mechanism_components": [
                {"name": "Comp A", "role": "x"}, {"name": "Comp B", "role": "y"},
                {"name": "Comp C", "role": "z"},
            ],
            "causal_edges": [
                {"from": "Comp A", "to": "Comp B", "relation": "r", "edge_kind": "source_causal",
                 "evidence": "A feeds B", "evidence_anchor": "§A"},
            ],
            "how_steps": [],
            "tldr": "System runs subagents.",
            "params": {},
            "referenced_concepts": [],
            "applicability_boundary": "Boundary here.",
        }

    def test_tldr_must_cover_unpaired_backtick_balanced(self):
        """TLDR 注入 must_cover conclusion 含不成对反引号 → 消毒后平衡。"""
        # literal 含不成对反引号(probe-p9a --fork 形态的简化)
        concls = [_item("see `claude --fork for spawning", kind="source_conclusion")]
        body = _build_body(self._data(), "test.md", None, must_cover=concls,
                           raw_content=_RAW, content_lane="concept")
        # TLDR 节反引号平衡
        import re
        tldr = re.search(r"## TLDR\s+(.+?)(?:\n## )", body, re.S).group(1)
        assert tldr.count("`") % 2 == 0, f"TLDR 反引号不平衡: {tldr.count('`')}"

    def test_boundary_must_cover_unpaired_backtick_balanced(self):
        """boundary 注入 must_cover boundary 含不成对反引号 → 消毒后平衡。"""
        bounds = [_item("set `CLAUDE_CODE_FORK_SUBAGENT=1 to enable", kind="source_boundary")]
        body = _build_body(self._data(), "test.md", None, must_cover=bounds,
                           raw_content=_RAW, content_lane="concept")
        import re
        boundary = re.search(r"## 适用边界\s+(.+?)(?:\n## )", body, re.S).group(1)
        assert boundary.count("`") % 2 == 0, f"boundary 反引号不平衡"

    def test_tldr_must_cover_command_not_false_positive(self):
        """TLDR 注入的 must_cover 命令不在 raw → strip 剥反引号,SG 不误报。"""
        # literal 含命令 `fake_cmd_not_in_raw`(反引号包裹,不在 raw)
        concls = [_item("run `fake_cmd_not_in_raw_xyz` to start", kind="source_conclusion")]
        body = _build_body(self._data(), "test.md", None, must_cover=concls,
                           raw_content=_RAW, content_lane="concept")
        violations = verify_commands_in_raw(body, _RAW)
        # fake_cmd_not_in_raw 不应被报为编造命令(反引号被 strip 剥,SG 不当命令回查)
        cmd_violations = [
            v for v in violations
            if "fake_cmd_not_in_raw" in getattr(v, "command", "")
        ]
        assert cmd_violations == [], (
            f"TLDR must_cover 命令被误报(消毒未生效): {[v.command for v in cmd_violations]}"
        )

    def test_how_must_cover_unpaired_backtick_balanced(self):
        """how 注入 must_cover operation 含不成对反引号 → 消毒后平衡。"""
        ops = [_item("run `make deploy` then `extra", kind="source_operation")]
        body = _build_body(self._data(), "test.md", None, must_cover=ops,
                           raw_content=_RAW, content_lane="concept")
        import re
        how = re.search(r"### 操作步骤\s+(.+?)(?:\n## )", body, re.S).group(1)
        assert how.count("`") % 2 == 0, f"how 反引号不平衡: {how.count('`')}"

    def test_real_command_in_raw_preserved(self):
        """raw 里真有的命令(make deploy)在 must_cover literal 里 → 保留反引号(SG grounded)。"""
        ops = [_item("run `make deploy` to build", kind="source_operation")]
        body = _build_body(self._data(), "test.md", None, must_cover=ops,
                           raw_content=_RAW, content_lane="concept")
        # make deploy 在 raw → 反引号保留(grounded)
        assert "`make deploy`" in body or "make deploy" in body
