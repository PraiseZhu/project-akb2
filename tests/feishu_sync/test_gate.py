"""gate：AI 趋势按 audit/decision 放行；GitHub 走 schema fail-closed。"""

from __future__ import annotations

from akb2.feishu_sync.gate import gate_decision, passes_gate
from akb2.feishu_sync.state import Briefing


def _b(kind, fm, *, json_data=None, date="2026-06-03"):
    return Briefing(
        kind=kind,
        date=date,
        path=None,
        title="t",
        frontmatter=fm,
        body="x",
        json_data=json_data,
    )


def _github_json(date="2026-06-03"):
    return {
        "schema_version": "v1",
        "briefing_type": "github-curator",
        "generated_at": f"{date}T10:00:00+08:00",
        "date": date,
        "candidate_pool_size": 10,
        "active_curators_today": ["@curator1"],
        "fallback_layer_used": "layer1",
        "top_repos": [
            {
                "repo_url": f"https://github.com/foo/bar-{idx}",
                "stars": 123 + idx,
                "language": "Python",
                "topic_tags": ["ai"],
                "mention_count": 1,
                "mentioners": [f"@curator{idx}"],
                "first_mention_at": f"{date}T0{idx}:00:00+00:00",
                "score": 0.7,
                "summary": f"这是中文摘要示例 {idx}，用于 gate 校验通过并覆盖主要能力描述。",
                "use_case": (
                    f"这是中文场景说明 {idx}，适合自动化团队使用，"
                    "不适合只看标题而不执行流程的场景。"
                ),
                "engagement_raw": 10 + idx,
                "repo_full_name": f"foo/bar-{idx}",
                "grounding_source": "description",
                "grounding_excerpt": f"excerpt {idx}",
            }
            for idx in range(1, 7)
        ],
    }


def test_ai_trends_pass():
    assert passes_gate(_b("ai_trends", {"audit_verdict": "pass", "proposed_gate_decision": "active"}))


def test_ai_trends_audit_fail():
    assert not passes_gate(
        _b("ai_trends", {"audit_verdict": "fail", "proposed_gate_decision": "active"})
    )


def test_ai_trends_audit_warn_passes():
    """08-27：warn 已写进 vault，gate 丢掉等于群里当天当没写。"""
    assert passes_gate(
        _b("ai_trends", {"audit_verdict": "warn", "proposed_gate_decision": "active"})
    )


def test_ai_trends_gate_inactive():
    assert not passes_gate(
        _b("ai_trends", {"audit_verdict": "pass", "proposed_gate_decision": "hold"})
    )


def test_ai_trends_missing_fields():
    assert not passes_gate(_b("ai_trends", {}))


def test_github_requires_schema_valid_json():
    briefing = _b("github", {"type": "github-briefing"}, json_data=_github_json())
    assert passes_gate(briefing)


def test_github_rejects_missing_json():
    passed, reason = gate_decision(_b("github", {"type": "github-briefing"}))
    assert not passed
    assert "缺少 GitHub Briefing.json" in reason


def test_github_rejects_invalid_schema_fail_visible():
    invalid = _github_json()
    invalid["top_repos"][0].pop("summary")
    passed, reason = gate_decision(_b("github", {"type": "github-briefing"}, json_data=invalid))
    assert not passed
    assert "schema 校验失败" in reason


def test_github_rejects_wrong_frontmatter_type():
    passed, reason = gate_decision(_b("github", {"type": "other"}, json_data=_github_json()))
    assert not passed
    assert "frontmatter.type" in reason
