"""发布门控 — 只放行可发布的简报（SC-3）。

- AI 趋势：frontmatter `audit_verdict == "pass"` 且 `proposed_gate_decision == "active"`
- GitHub：无 audit/gate 字段（已知边界），存在即放行
"""

from __future__ import annotations

from .state import Briefing


def passes_gate(b: Briefing) -> bool:
    if b.kind == "ai_trends":
        fm = b.frontmatter
        return fm.get("audit_verdict") == "pass" and fm.get("proposed_gate_decision") == "active"
    return True
