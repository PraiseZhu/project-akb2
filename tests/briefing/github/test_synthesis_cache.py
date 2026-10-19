"""GitHub pipeline 的 synthesize 前缓存 —— 2026-10-19 断稿回归锁。

事故：主跑 intake 成功后 synthesize 因瞬时 401 失败；backstop 每轮整链重跑 intake
（35–62min，含 X 429 等窗），11:00 场被 12:00 deadline 切死（rc=124）。
修复：intake→score 完成后把 top10 落盘；同日再次运行直接复用，跳过 intake/enrich/score。
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

from akb2.briefing.github import pipeline as gh_pipeline
from akb2.briefing.github.intake import Candidate
from akb2.briefing.github.pipeline import run_pipeline
from akb2.briefing.github.synthesis import SynthesisAPIError

_OUT_SUBDIR = "Praise Space/AKB2.0/AI TRENDS/GitHub"
_SUMMARY = "这是一个用于测试的 AI 仓库，覆盖机器学习工作流与流水线校验。"
_USE_CASE = "正向场景：搭建 AI 流水线时使用。反向场景：生产环境不要直接依赖。"


def _candidates(n: int) -> list[Candidate]:
    now = datetime.now(UTC).isoformat()
    return [
        Candidate(f"https://github.com/trending/repo{i}", "@github_trending", None, now, None)
        for i in range(n)
    ]


def _enrich_ok(url: str) -> dict:
    h = sum(ord(c) for c in url)
    return {
        "repo_url": url, "stars": 500 + h % 500, "language": "Python",
        "topic_tags": ["ai"], "created_at": "2024-01-01T00:00:00Z",
        "readme_excerpt": "x" * 100, "commits_7d": 5, "enrich_ok": True,
    }


def _synth_ok(repos, api_key=None, *, mock=False):
    return {"top_repos": [{**r, "summary": _SUMMARY, "use_case": _USE_CASE} for r in repos]}


def _synth_401(repos, api_key=None, *, mock=False):
    raise SynthesisAPIError("synthesis 硬失败（不可重试状态码 401）")


def _forbidden_intake(force_layer=None):
    raise AssertionError("intake 不应重跑：同日 top10 缓存已存在")


def _forbidden_enrich(url):
    raise AssertionError("enrich 不应重跑：同日 top10 缓存已存在")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    out = root / _OUT_SUBDIR
    out.mkdir(parents=True)
    monkeypatch.setenv("VAULT_ROOT", str(root))
    monkeypatch.setenv("AKB2_NOVELTY_DB", str(tmp_path / "novelty.sqlite"))
    monkeypatch.setenv("AKB2_STAR_HISTORY_DB", str(tmp_path / "star.sqlite"))
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(gh_pipeline, "_PROJECT_ROOT", tmp_path)
    return out


def _first_run_fails_at_synthesize():
    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=_synth_401),
    ):
        return run_pipeline(mock=False)


def test_rerun_after_synthesize_failure_skips_intake(vault):
    assert _first_run_fails_at_synthesize() == gh_pipeline.EXIT_NOT_DELIVERED
    assert list(vault.iterdir()) == []

    with (
        patch("akb2.briefing.github.pipeline._real_intake", side_effect=_forbidden_intake),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_forbidden_enrich),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=_synth_ok),
    ):
        rc = run_pipeline(mock=False)

    assert rc == gh_pipeline.EXIT_DELIVERED
    md = vault / f"{date.today().isoformat()} GitHub Briefing.md"
    assert md.exists()
    data = json.loads((vault / f"{date.today().isoformat()} GitHub Briefing.json").read_text())
    assert len(data["top_repos"]) >= 5
    assert data["active_curators_today"], "缓存路径丢了 active_curators"


def test_cache_for_other_date_is_not_reused(vault):
    assert _first_run_fails_at_synthesize() == gh_pipeline.EXIT_NOT_DELIVERED

    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})) as intake,
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=_synth_ok),
    ):
        rc = run_pipeline(mock=False, briefing_date="2020-01-01")

    assert rc == gh_pipeline.EXIT_DELIVERED
    assert intake.call_count == 1, "不同日期必须重跑 intake，不得用旧日缓存"


def test_cache_removed_after_delivery(vault):
    assert _first_run_fails_at_synthesize() == gh_pipeline.EXIT_NOT_DELIVERED
    with (
        patch("akb2.briefing.github.pipeline._real_intake", side_effect=_forbidden_intake),
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_forbidden_enrich),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=_synth_ok),
    ):
        assert run_pipeline(mock=False) == gh_pipeline.EXIT_DELIVERED

    # 交付后缓存已清：再跑一次必须重新 intake
    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})) as intake,
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=_synth_ok),
    ):
        run_pipeline(mock=False)
    assert intake.call_count == 1


def test_force_layer_and_mock_bypass_cache(vault):
    assert _first_run_fails_at_synthesize() == gh_pipeline.EXIT_NOT_DELIVERED
    with (
        patch("akb2.briefing.github.pipeline._real_intake",
              return_value=(_candidates(7), "layer2", {})) as intake,
        patch("akb2.briefing.github.pipeline.enrich", side_effect=_enrich_ok),
        patch("akb2.briefing.github.pipeline.synthesize", side_effect=_synth_ok),
    ):
        run_pipeline(mock=False, force_layer=2)
    assert intake.call_count == 1, "显式 --force-layer 必须重跑 intake"
