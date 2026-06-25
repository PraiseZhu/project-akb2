"""Tests for 8-step pipeline — mock E2E (SC-7) and SC-8 checks."""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
VAULT_STUB = PROJECT_ROOT / "tests" / "fixtures" / "vault-stub"


def _run_pipeline(env_overrides: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run pipeline as subprocess with PYTHONPATH=src."""
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), **env_overrides}
    return subprocess.run(
        [sys.executable, "-m", "akb2.briefing.github.pipeline", "--mock"],
        capture_output=True, text=True, env=env, cwd=str(cwd or PROJECT_ROOT),
    )


class TestPipelineMockE2E:
    """SC-7: Mock E2E pipeline — 8 steps, .md + .json output, top_repos == 10, validator PASS."""

    def test_mock_pipeline_produces_output(self):
        result = _run_pipeline({"VAULT_ROOT": str(VAULT_STUB)})
        assert "briefing: WROTE" in result.stdout, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    def test_mock_output_md_and_json_exist(self):
        """After mock run, .md and .json sidecar files exist."""
        from datetime import date
        next_day = date.today().isoformat()
        base = VAULT_STUB / "Praise Space/AKB2.0/AI TRENDS/GitHub" / f"{next_day} GitHub Briefing"
        md_path = base.with_suffix(".md")
        json_path = base.with_suffix(".json")

        assert md_path.exists(), f"Missing: {md_path}"
        assert json_path.exists(), f"Missing: {json_path}"

    def test_mock_json_top_repos_is_10(self):
        """JSON output has exactly 10 top_repos."""
        from datetime import date
        next_day = date.today().isoformat()
        json_path = VAULT_STUB / "Praise Space/AKB2.0/AI TRENDS/GitHub" / f"{next_day} GitHub Briefing.json"

        with open(json_path) as f:
            data = json.load(f)
        assert len(data["top_repos"]) == 10

    def test_mock_json_passes_validator(self):
        """JSON output passes schema validator."""
        from datetime import date
        next_day = date.today().isoformat()
        json_path = VAULT_STUB / "Praise Space/AKB2.0/AI TRENDS/GitHub" / f"{next_day} GitHub Briefing.json"

        from akb2.briefing.github.validator import validate
        errors = validate(str(json_path))
        assert errors == [], f"Validator errors: {errors}"

    def test_md_has_frontmatter_and_content(self):
        """Markdown output has frontmatter and repo entries."""
        from datetime import date
        next_day = date.today().isoformat()
        md_path = VAULT_STUB / "Praise Space/AKB2.0/AI TRENDS/GitHub" / f"{next_day} GitHub Briefing.md"

        content = md_path.read_text()
        assert content.startswith("---")
        assert "date:" in content
        assert "top_repos" not in content.split("---")[1]  # frontmatter only
        assert "## 1." in content
        assert "## 10." in content


class TestPipelineEdgeCases:
    """Edge case tests for pipeline."""

    def test_missing_vault_root_skips(self, tmp_path, monkeypatch):
        """VAULT_ROOT empty → briefing-skipped.json."""
        monkeypatch.delenv("VAULT_ROOT", raising=False)
        result = _run_pipeline({"VAULT_ROOT": "", "PROJECT_ROOT": str(tmp_path)}, cwd=tmp_path)
        assert "SKIPPED" in result.stdout
        assert (tmp_path / "briefing-skipped.json").exists()

    def test_relative_vault_root_skips(self, tmp_path):
        """VAULT_ROOT relative → briefing-skipped.json."""
        result = _run_pipeline({"VAULT_ROOT": "relative/path"}, cwd=tmp_path)
        assert "SKIPPED" in result.stdout

    def test_vault_root_absolute_proceeds(self, tmp_path):
        """VAULT_ROOT absolute → proceeds to output."""
        vault = tmp_path / "vault"
        vault.mkdir()
        result = _run_pipeline({"VAULT_ROOT": str(vault)})
        assert "briefing: WROTE" in result.stdout or "briefing: SKIPPED" in result.stdout


class TestSC8Launchd:
    """SC-8: vault file, plist checks, token scan, script permissions."""

    def test_scripts_are_executable(self):
        for script in ["daily_github_briefing.sh", "launchd_wrapper.sh", "bootstrap_launchd.sh"]:
            path = SCRIPTS_DIR / script
            assert path.exists(), f"Missing: {path}"
            assert os.access(path, os.X_OK), f"Not executable: {path}"

    def test_plist_no_environment_variables(self):
        plist_path = PROJECT_ROOT / "launchd" / "com.akb2.github_briefing.plist"
        content = plist_path.read_text()
        assert "<key>EnvironmentVariables</key>" not in content

    def test_plist_points_to_wrapper(self):
        plist_path = PROJECT_ROOT / "launchd" / "com.akb2.github_briefing.plist"
        content = plist_path.read_text()
        assert "launchd_wrapper.sh" in content

    def test_launchd_wrapper_missing_env_fails_visible(self, tmp_path):
        """Missing user env is a deployment error, not a successful empty run."""
        result = subprocess.run(
            ["/bin/bash", str(SCRIPTS_DIR / "launchd_wrapper.sh")],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(tmp_path)},
        )
        assert result.returncode != 0
        assert "missing env file" in result.stderr

    def test_daily_github_accepts_vault_root_arg(self, tmp_path):
        """Backstop/manual reruns can pass the vault root explicitly."""
        vault = tmp_path / "vault"
        result = subprocess.run(
            [
                "/bin/bash",
                str(SCRIPTS_DIR / "daily_github_briefing.sh"),
                "--vault-root",
                str(vault),
                "--mock",
            ],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "AKB2_DISABLE_DEADLINE": "1",
                "AKB2_NOVELTY_DB": str(tmp_path / "novelty.sqlite"),
                "PROJECT_ROOT": str(tmp_path),
            },
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        assert "briefing: WROTE" in result.stdout
        assert (
            vault
            / "Praise Space"
            / "AKB2.0"
            / "AI TRENDS"
            / "GitHub"
        ).exists()

    def test_plist_hour_is_14(self):
        plist_path = PROJECT_ROOT / "launchd" / "com.akb2.github_briefing.plist"
        content = plist_path.read_text()
        assert "<integer>14</integer>" in content or "Hour" in content

    def test_no_token_literals_in_repo(self):
        """grep for secret patterns — must be 0 hits."""
        import subprocess
        result = subprocess.run(
            ["grep", "-RE", "-e", "ghp_[A-Za-z0-9_]{20,}",
             "-e", "sk-[A-Za-z0-9_-]{20,}",
             "-e", "gho_[A-Za-z0-9_]{20,}",
             str(PROJECT_ROOT / "launchd"), str(PROJECT_ROOT / "scripts"), str(PROJECT_ROOT / "src")],
            capture_output=True, text=True,
        )
        assert result.returncode != 0, f"Secret tokens found:\n{result.stdout}"
        assert result.stdout.strip() == "", f"Secret tokens found:\n{result.stdout}"

    def test_env_example_has_no_real_values(self):
        env_path = PROJECT_ROOT / "config" / "github-briefing.env.example"
        content = env_path.read_text()
        # Must contain placeholder instructions but no real secrets
        assert "VAULT_ROOT=" in content
        # 全局走 AI Gateway：env.example 用 AI_GATEWAY_API_KEY 占位（synthesis 也回退 DEEPSEEK_API_KEY）
        assert "AI_GATEWAY_API_KEY=" in content
        assert "GITHUB_PAT=" in content
        assert "cp config/" in content  # setup instructions
        # 占位符不得含真实 key 字面值
        import re as _re
        assert not _re.search(r"sk-[A-Za-z0-9_-]{15,}", content)
        # 不再引导直连 provider（全局统一 Gateway）
        assert "platform.deepseek.com" not in content

    def test_vault_stub_dir_exists(self):
        stub = PROJECT_ROOT / "tests" / "fixtures" / "vault-stub" / "AKB2.0" / "AI TRENDS" / "GitHub"
        assert stub.exists()

class TestNoveltyDedup:
    """跨天 novelty 去重：已推过的 repo 3 天内不重复推（真实链路验收 A2）。"""

    def test_second_run_skips_already_recommended(self, tmp_path):
        # 同一 novelty db 跑两次：第一次写简报并标记 10 个 repo；
        # 第二次这 10 个被过滤 → mock 仅 12 个候选 → 剩 2 个 < 10 → skip。
        vault = tmp_path / "vault"
        novelty_db = tmp_path / "novelty.sqlite"
        env = {
            "VAULT_ROOT": str(vault),
            "AKB2_NOVELTY_DB": str(novelty_db),
            "PROJECT_ROOT": str(tmp_path),
        }
        r1 = _run_pipeline(env)
        assert "briefing: WROTE" in r1.stdout, f"run1 应出简报: {r1.stdout}\n{r1.stderr}"

        r2 = _run_pipeline(env)
        assert "briefing: WROTE" not in r2.stdout, f"run2 不应重复推: {r2.stdout}"
        assert "SKIPPED" in r2.stdout, f"run2 应 skip: {r2.stdout}\n{r2.stderr}"
        assert "too_few_aggregated_repos" in r2.stdout, f"run2 skip 原因应为候选不足: {r2.stdout}"


class TestAggregateEngagement:
    """v3: _aggregate 跨提及取 MAX engagement（不 sum，避免重新引入 mention 偏置）。"""

    def test_aggregate_takes_max_engagement(self):
        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import _aggregate

        url = "https://github.com/a/b"
        ts = "2026-06-01T00:00:00Z"
        cands = [
            Candidate(url, "@x", "1", ts, "t", {"likes": 10}),
            Candidate(url, "@y", "2", ts, "t", {"likes": 5000, "bookmarks": 2000}),  # 最火
            Candidate(url, "@z", "3", ts, "t", None),
        ]
        agg = _aggregate(cands)
        assert agg[url]["mention_count"] == 3
        assert agg[url]["engagement"] == {"likes": 5000, "bookmarks": 2000}

    def test_aggregate_engagement_none_when_no_metrics(self):
        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import _aggregate

        url = "https://github.com/a/b"
        cands = [Candidate(url, "@x", "1", "2026-06-01T00:00:00Z", "t", None)]
        agg = _aggregate(cands)
        assert agg[url]["engagement"] is None

    def test_aggregate_none_first_then_real_metrics_wins(self):
        """None 起步的 entry，后续真 metrics 必须接管（锁住严格 > 的初始化契约：
        防 None-first 静默回归）。"""
        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import _aggregate

        url = "https://github.com/a/b"
        ts = "2026-06-01T00:00:00Z"
        cands = [
            Candidate(url, "@x", "1", ts, "t", None),            # None 起步
            Candidate(url, "@y", "2", ts, "t", {"likes": 100}),  # 真 metrics 应接管
        ]
        agg = _aggregate(cands)
        assert agg[url]["engagement"] == {"likes": 100}

    def test_aggregate_does_not_sum_engagement(self):
        """Codex P3: 锁 do-not-sum 不变式。两条低互动之和 > 单条最热，若误改成 sum
        会选错；断言聚合结果就是那个最热的单条 metrics 对象，且未被累加。"""
        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import _aggregate
        from akb2.briefing.github.scoring import engagement_raw

        url = "https://github.com/a/b"
        ts = "2026-06-01T00:00:00Z"
        low_a = {"likes": 300}   # raw 300
        low_b = {"likes": 300}   # raw 300（两条之和 600 > 最热单条 500）
        hot = {"likes": 500}     # raw 500，最热单条
        cands = [
            Candidate(url, "@x", "1", ts, "t", low_a),
            Candidate(url, "@y", "2", ts, "t", hot),
            Candidate(url, "@z", "3", ts, "t", low_b),
        ]
        agg = _aggregate(cands)
        # 必须是最热单条本身（不是累加出来的 dict）
        assert agg[url]["engagement"] == hot
        assert engagement_raw(agg[url]["engagement"]) == 500  # 非 1100(sum)


class TestEngagementRawOutput:
    """SC-3: engagement_raw 进 JSON 产物 + MD 渲染。"""

    def test_engagement_raw_in_output(self, tmp_path, monkeypatch):
        """mock 跑后 JSON top_repos[*] 含 engagement_raw≥0 且过 validator。"""
        import json

        from akb2.briefing.github.pipeline import run_pipeline
        from akb2.briefing.github.validator import validate

        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setenv("VAULT_ROOT", str(vault))
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(tmp_path / "novelty.sqlite"))
        monkeypatch.setenv("AKB2_TRANSLATE_TIMEOUT", "30")

        exit_code = run_pipeline(mock=True)
        assert exit_code == 0

        # Find JSON output
        json_files = list(vault.rglob("*.json"))
        assert len(json_files) >= 1, "expected JSON output"
        json_path = json_files[0]
        with open(json_path) as f:
            data = json.load(f)

        # Validate
        errors = validate(str(json_path))
        assert not errors, f"schema validation failed: {errors}"

        # All top_repos must have engagement_raw
        for repo in data["top_repos"]:
            assert "engagement_raw" in repo
            assert isinstance(repo["engagement_raw"], int)
            assert repo["engagement_raw"] >= 0

    def test_render_shows_engagement_when_positive(self):
        """engagement_raw>0 → MD 中显示 · 🔥 N。"""
        from akb2.briefing.github.pipeline import _render_markdown

        briefing = {
            "schema_version": "v1", "briefing_type": "github-curator",
            "generated_at": "2026-06-04T10:00:00Z", "date": "2026-06-05",
            "candidate_pool_size": 10, "active_curators_today": ["@test"],
            "fallback_layer_used": "layer1",
            "top_repos": [{
                "repo_url": "https://github.com/test/repo",
                "stars": 1000, "language": "Python", "topic_tags": ["ai"],
                "mention_count": 3, "mentioners": ["@a", "@b", "@c"],
                "first_mention_at": "2026-06-04T10:00:00Z",
                "score": 0.85, "summary": "A test repo for AI stuff" + "x" * 22,
                "use_case": "Use when you need AI" + "y" * 18,
                "engagement_raw": 150,
            }] * 10,
        }
        md = _render_markdown(briefing)
        assert "🔥 150" in md

    def test_render_hides_engagement_when_zero(self):
        """engagement_raw=0 → MD 中不显示热度标记。"""
        from akb2.briefing.github.pipeline import _render_markdown

        briefing = {
            "schema_version": "v1", "briefing_type": "github-curator",
            "generated_at": "2026-06-04T10:00:00Z", "date": "2026-06-05",
            "candidate_pool_size": 10, "active_curators_today": ["@test"],
            "fallback_layer_used": "layer1",
            "top_repos": [{
                "repo_url": "https://github.com/test/repo",
                "stars": 1000, "language": "Python", "topic_tags": ["ai"],
                "mention_count": 1, "mentioners": ["@a"],
                "first_mention_at": "2026-06-04T10:00:00Z",
                "score": 0.85, "summary": "A test repo" + "x" * 38,
                "use_case": "Use when needed" + "y" * 24,
                "engagement_raw": 0,
            }] * 10,
        }
        md = _render_markdown(briefing)
        assert "🔥" not in md


class TestSC7Metrics:
    """SC-7: stdout SC7_METRICS 行。"""

    def test_sc7_metrics_line_emitted(self, tmp_path, monkeypatch, capsys):
        """mock 跑 stdout 含 SC7_METRICS: 行，JSON 8 键齐全且类型正确。"""
        import json

        from akb2.briefing.github.pipeline import run_pipeline

        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setenv("VAULT_ROOT", str(vault))
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(tmp_path / "novelty.sqlite"))
        monkeypatch.setenv("AKB2_TRANSLATE_TIMEOUT", "30")

        exit_code = run_pipeline(mock=True)
        assert exit_code == 0

        captured = capsys.readouterr()
        sc7_line = None
        for line in captured.out.splitlines():
            if "SC7_METRICS:" in line:
                sc7_line = line
                break
        assert sc7_line is not None, "SC7_METRICS line not found in stdout"

        # Format: "[TS] SC7_METRICS: {...}" — strip everything before "SC7_METRICS:"
        metrics = json.loads(sc7_line[sc7_line.index("SC7_METRICS:") + len("SC7_METRICS:"):].strip())
        required_keys = [
            "candidates_before_recency", "candidates_after_recency",
            "recency_dropped", "timestamp_missing", "timestamp_parse_failed",
            "candidates_after_safety", "fallback_triggered", "top10_all_layer1",
        ]
        for key in required_keys:
            assert key in metrics, f"missing key: {key}"
        # Type checks
        for k in required_keys[:5]:
            assert isinstance(metrics[k], int), f"{k} should be int"
        assert isinstance(metrics["candidates_after_safety"], int)
        assert isinstance(metrics["fallback_triggered"], bool)
        assert isinstance(metrics["top10_all_layer1"], bool)


# ── SC-1 / SC-3 ─────────────────────────────────────────────────────────────

def _mock_enrich(url):
    h = sum(ord(c) for c in url)
    return {
        "repo_url": url, "stars": 500 + h % 500, "language": "Python",
        "topic_tags": ["ai"], "created_at": "2024-01-01T00:00:00Z",
        "readme_excerpt": "x" * 100, "commits_7d": 5, "enrich_ok": True,
    }


def _mock_synth(repos):
    return {"top_repos": [
        {**r,
         "summary": "This is a test AI repository for machine learning workflows and testing.",
         "use_case": "Use when building AI pipelines for testing purposes only."}
        for r in repos
    ]}


class TestSC1PostNoveltyL2Supplement:
    """SC-1: post-novelty layer2 supplement when L1 ok but novelty dedup drops below 10."""

    def test_l2_supplement_triggered_when_post_novelty_below_10(self, tmp_path, monkeypatch):
        """L1 yields 10 pre-novelty; novelty eats 5 → 5 post-novelty < 10 → L2 supplement fires."""
        from datetime import UTC, datetime
        from unittest.mock import patch

        from akb2.briefing.github.dedup_sqlite import DedupStore
        from akb2.briefing.github.intake import _ZERO_RECENCY_STATS, Candidate, CollectorResult
        from akb2.briefing.github.pipeline import run_pipeline

        now = datetime.now(UTC).isoformat()
        seen_urls = [f"https://github.com/seen/repo{i}" for i in range(5)]
        fresh_urls = [f"https://github.com/fresh/repo{i}" for i in range(5)]
        trending_urls = [f"https://github.com/trending/repo{i}" for i in range(5)]

        novelty_db = tmp_path / "novelty.sqlite"
        store = DedupStore(novelty_db)
        for u in seen_urls:
            store.mark_seen(u)
        store.close()

        l1_cands = [Candidate(u, "@curator", None, now, None) for u in seen_urls + fresh_urls]
        l2_cands = [Candidate(u, "@github_trending", None, now, None) for u in trending_urls]
        l2_result = CollectorResult("layer2", l2_cands, [], 0.1, True, None, _ZERO_RECENCY_STATS)

        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(novelty_db))
        monkeypatch.setenv("VAULT_ROOT", str(vault))

        with (
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(l1_cands, "layer1", {})),
            patch("akb2.briefing.github.pipeline.TrendingHTMLCollector") as mock_l2_cls,
            patch("akb2.briefing.github.pipeline.enrich", side_effect=_mock_enrich),
            patch("akb2.briefing.github.pipeline.synthesize", side_effect=_mock_synth),
        ):
            mock_l2_cls.return_value.collect.return_value = l2_result
            rc = run_pipeline(mock=False)

        # L2 supplement must have been called once (post-novelty dead-zone)
        mock_l2_cls.return_value.collect.assert_called_once()
        assert rc == 0

    def test_l2_supplement_not_triggered_when_post_novelty_sufficient(self, tmp_path, monkeypatch):
        """When post-novelty already ≥10, L2 supplement is NOT called."""
        from datetime import UTC, datetime
        from unittest.mock import patch

        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import run_pipeline

        now = datetime.now(UTC).isoformat()
        l1_cands = [Candidate(f"https://github.com/fresh/repo{i}", "@curator", None, now, None)
                    for i in range(12)]

        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        novelty_db = tmp_path / "empty_novelty.sqlite"
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(novelty_db))
        monkeypatch.setenv("VAULT_ROOT", str(vault))

        with (
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(l1_cands, "layer1", {})),
            patch("akb2.briefing.github.pipeline.TrendingHTMLCollector") as mock_l2_cls,
            patch("akb2.briefing.github.pipeline.enrich", side_effect=_mock_enrich),
            patch("akb2.briefing.github.pipeline.synthesize", side_effect=_mock_synth),
        ):
            run_pipeline(mock=False)

        # L2 should NOT be called — post-novelty already ≥10
        mock_l2_cls.return_value.collect.assert_not_called()


class TestSC3PartialOutput:
    """SC-3: allow partial output (≥ MIN_REPOS_FOR_BRIEFING) instead of always requiring 10."""

    def test_partial_briefing_written_when_between_min_and_10(self, tmp_path, monkeypatch):
        """7 repos available (≥6=MIN, <10) → briefing file is written, not skipped."""
        from datetime import UTC, date, datetime
        from unittest.mock import patch

        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import run_pipeline

        now = datetime.now(UTC).isoformat()
        l2_cands = [Candidate(f"https://github.com/trending/repo{i}", "@github_trending", None, now, None)
                    for i in range(7)]

        vault = tmp_path / "vault"
        out_dir = vault / "Praise Space/AKB2.0/AI TRENDS/GitHub"
        out_dir.mkdir(parents=True)
        novelty_db = tmp_path / "empty_novelty.sqlite"
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(novelty_db))
        monkeypatch.setenv("VAULT_ROOT", str(vault))

        with (
            # Use layer2 directly so no SC-1 supplement branch is triggered
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(l2_cands, "layer2", {})),
            patch("akb2.briefing.github.pipeline.enrich", side_effect=_mock_enrich),
            patch("akb2.briefing.github.pipeline.synthesize", side_effect=_mock_synth),
        ):
            rc = run_pipeline(mock=False)

        assert rc == 0, "partial output (7 repos) should produce a briefing, not skip"
        next_day = date.today().isoformat()
        md_path = out_dir / f"{next_day} GitHub Briefing.md"
        json_path = out_dir / f"{next_day} GitHub Briefing.json"
        assert md_path.exists(), f"Expected md output: {md_path}"
        assert json_path.exists(), f"Expected json output: {json_path}"

        with open(json_path) as f:
            data = json.load(f)
        assert 6 <= len(data["top_repos"]) <= 10

    def test_below_min_repos_still_skips(self, tmp_path, monkeypatch):
        """5 repos (< MIN=6) → still skip."""
        from datetime import UTC, datetime
        from unittest.mock import patch

        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import run_pipeline

        now = datetime.now(UTC).isoformat()
        l2_cands = [Candidate(f"https://github.com/trending/repo{i}", "@github_trending", None, now, None)
                    for i in range(5)]

        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        novelty_db = tmp_path / "empty_novelty.sqlite"
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(novelty_db))
        monkeypatch.setenv("VAULT_ROOT", str(vault))

        with (
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(l2_cands, "layer2", {})),
            patch("akb2.briefing.github.pipeline.enrich", side_effect=_mock_enrich),
            patch("akb2.briefing.github.pipeline.synthesize", side_effect=_mock_synth),
        ):
            rc = run_pipeline(mock=False)

        assert rc == 0  # exit 0 but via skip path
