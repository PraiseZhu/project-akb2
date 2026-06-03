"""gate：AI 趋势按 audit/decision 放行；GitHub 恒放行。"""

from __future__ import annotations

from akb2.feishu_sync.gate import passes_gate
from akb2.feishu_sync.state import Briefing


def _b(kind, fm):
    return Briefing(kind=kind, date="2026-06-03", path=None, title="t", frontmatter=fm, body="x")


def test_ai_trends_pass():
    assert passes_gate(_b("ai_trends", {"audit_verdict": "pass", "proposed_gate_decision": "active"}))


def test_ai_trends_audit_fail():
    assert not passes_gate(
        _b("ai_trends", {"audit_verdict": "fail", "proposed_gate_decision": "active"})
    )


def test_ai_trends_gate_inactive():
    assert not passes_gate(
        _b("ai_trends", {"audit_verdict": "pass", "proposed_gate_decision": "hold"})
    )


def test_ai_trends_missing_fields():
    assert not passes_gate(_b("ai_trends", {}))


def test_github_always_passes():
    assert passes_gate(_b("github", {}))
