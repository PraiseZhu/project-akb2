"""feishu_sync 测试夹具 — tmp vault 构造 + 假飞书环境。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from akb2.feishu_sync.feishu import send as send_mod

_SOCIAL = ("Praise Space", "AKB2.0", "AI TRENDS", "Social")
_GITHUB = ("Praise Space", "AKB2.0", "AI TRENDS", "GitHub")


def write_ai_trends(
    vault: Path,
    date: str,
    *,
    audit: str = "pass",
    gate: str = "active",
    body: str = "## 总览\n\n今日要点。",
    raw: str | None = None,
) -> Path:
    p = vault.joinpath(*_SOCIAL, f"{date} Daily Briefing.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        p.write_text(raw, encoding="utf-8")
    else:
        p.write_text(
            f"---\ndate: {date}\naudit_verdict: {audit}\n"
            f"proposed_gate_decision: {gate}\n---\n# {date} AI 社交趋势日报\n\n{body}\n",
            encoding="utf-8",
        )
    return p


def write_github(
    vault: Path,
    date: str,
    *,
    repos: list | None = None,
    raw: str | None = None,
    with_json: bool = True,
) -> Path:
    md = vault.joinpath(*_GITHUB, f"{date} GitHub Briefing.md")
    md.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        md.write_text(raw, encoding="utf-8")
    else:
        md.write_text(
            f"---\ndate: {date}\ntype: github-briefing\n---\n# {date} GitHub\n\n正文。\n",
            encoding="utf-8",
        )
    if with_json:
        j = vault.joinpath(*_GITHUB, f"{date} GitHub Briefing.json")
        sample_repos = repos if repos is not None else [
            {
                "repo_url": f"https://github.com/foo/bar-{idx}",
                "stars": 123 + idx,
                "language": "Python",
                "topic_tags": ["ai", "tools"],
                "mention_count": 1,
                "mentioners": [f"@curator{idx}"],
                "first_mention_at": f"{date}T0{idx}:00:00+00:00",
                "score": 0.8,
                "summary": f"这是用于自动化与分析流程的中文摘要示例 {idx}，覆盖主要能力与价值。",
                "use_case": (
                    f"适合需要自动化与分析协作的团队场景 {idx}，"
                    "不适合只想看标题而不处理数据的场景。"
                ),
                "engagement_raw": 100 + idx,
                "repo_full_name": f"foo/bar-{idx}",
                "grounding_source": "description",
                "grounding_excerpt": f"README 摘要 {idx}",
            }
            for idx in range(1, 7)
        ]
        data = {
            "schema_version": "v1",
            "briefing_type": "github-curator",
            "generated_at": f"{date}T10:00:00+08:00",
            "date": date,
            "candidate_pool_size": 10,
            "active_curators_today": ["@curator1"],
            "fallback_layer_used": "layer1",
            "top_repos": sample_repos,
        }
        j.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return md


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def feishu_env(monkeypatch):
    """注入假飞书环境变量（值为占位，非真实凭证）。"""
    monkeypatch.setenv("FEISHU_APP_ID", "app_dummy")
    monkeypatch.setenv("FEISHU_APP_SECRET", "dummy_value")
    monkeypatch.setenv("FEISHU_PUSH_CHAT_ID", "oc_group_dummy")
    monkeypatch.setenv("FEISHU_DM_OPEN_ID", "ou_me_dummy")


@pytest.fixture(autouse=True)
def _reset_send_state(monkeypatch):
    monkeypatch.setattr(send_mod, "_last_send_monotonic", 0.0)
