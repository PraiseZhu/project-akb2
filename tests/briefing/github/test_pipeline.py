"""Tests for 8-step pipeline — mock E2E (SC-7) and SC-8 checks."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
VAULT_STUB = PROJECT_ROOT / "tests" / "fixtures" / "vault-stub"


@pytest.fixture(autouse=True)
def _isolate_diagnostic_root(tmp_path, monkeypatch):
    """把诊断落点隔离到 tmp_path —— 本文件的 in-process 测试此前写进真实仓库根。

    2026-08-03 实测：`TestSC4FailVisible::test_failed_exit_on_synthesis_failure` 是
    in-process 调 `run_pipeline`，只 monkeypatch 了 env、没管 `_PROJECT_ROOT`，于是
    `_write_failed()` 把 `briefing-failed.json` 写到了仓库根（残留文件的 reason 字段里
    还留着那个测试的 `<locals>` 函数名，可直接指认）。两个后果：

    1. **跨测试共享可变状态** —— 任何断言诊断内容的测试都可能读到别的测试的产物，
       表现为顺序/并发相关的偶发失败（本轮观察到过一次不可复现的红）。
    2. **被 Syncthing 跨机同步** —— 这两个文件在 `.gitignore` 里（不进 git），但 Syncthing
       不看 gitignore，仓库根因此积累了 `briefing-*.sync-conflict-*.json`。

    subprocess 形态的测试（`_run_pipeline`）不受影响：它们靠 `cwd` + env 隔离，
    读不到本进程的模块全局。此处只补 in-process 那条路径。
    """
    from akb2.briefing.github import pipeline as _gh_pipeline

    monkeypatch.setattr(_gh_pipeline, "_PROJECT_ROOT", tmp_path, raising=False)


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
        """VAULT_ROOT relative → briefing-skipped.json。

        `PROJECT_ROOT` 必须一起隔离：不给的话 `_PROJECT_ROOT` 回落到真实仓库根
        （`pipeline.py:34-38`），诊断 JSON 就写进仓库根 = 跨测试共享可变状态。
        本用例走 preflight 早退、不读 `POOL_CONFIG`，所以重定向是安全的。
        """
        result = _run_pipeline(
            {"VAULT_ROOT": "relative/path", "PROJECT_ROOT": str(tmp_path)}, cwd=tmp_path
        )
        assert "SKIPPED" in result.stdout
        assert (tmp_path / "briefing-skipped.json").exists(), "诊断应落隔离根"

    def test_vault_root_absolute_proceeds(self, tmp_path):
        """VAULT_ROOT absolute → proceeds to output.

        ⚠️ 这条**故意不隔离** `PROJECT_ROOT`：它要走到产出，途中需要真实仓库里的
        `POOL_CONFIG`（`config/github_curator_pool.yaml`，由 `_PROJECT_ROOT` 解析）。
        重定向会让 pool 配置读不到 → 走 SKIPPED 分支 → 断言仍然通过但**理由是错的**
        （断言容忍 WROTE 或 SKIPPED），变成空过测试。代价是它会往仓库根写一份
        `briefing-skipped.json`（已在 .gitignore，不进 git）。
        彻底解法是把诊断落点从仓库根挪到状态根，但那会改动 feishu_sync 的读取侧
        （`feishu_sync/blocked.py` 依赖仓库根路径），属跨模块变更，不在本轮范围。
        """
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

    def test_plist_environment_variables_guard_new_contract(self):
        """SC-8 于 2026-08-03 已废弃，原因见 docs/allowed-residuals.md；本用例现守卫相反契约。

        原名 test_plist_no_environment_variables 断言该 plist **不得**含 EnvironmentVariables
        （SC-8 原契约：连 VAULT_ROOT 也走 wrapper 注入）。现契约相反：生产依赖 plist 级
        PYTHONPATH 与 ~/.local/bin，故断言这些键**必须存在**。已随契约反转一并改名 ——
        保留原名会让 `pytest -k no_environment_variables` 的读者得出与事实相反的结论。
        """
        plist_path = PROJECT_ROOT / "launchd" / "com.akb2.github_briefing.plist"
        content = plist_path.read_text()
        assert "<key>EnvironmentVariables</key>" in content
        assert "<key>PYTHONPATH</key>" in content
        assert "<string>__AKB2_REPO_ROOT__/src</string>" in content
        assert "<key>PATH</key>" in content
        assert "__AKB2_HOME__/.local/bin" in content

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


def _mock_synth(repos, api_key=None, *, mock=False):
    # 必须是中文：schema 的 summary/use_case 带 pattern "[一-鿿]"（至少一个 CJK
    # 字符），英文占位文本会被 render 阶段的 schema 校验判为 schema_validation_failed，
    # 于是这些用例断言的 SC-1/SC-3 行为（L2 补位、部分产出）根本走不到。
    return {"top_repos": [
        {**r,
         "summary": "这是一个用于测试的 AI 仓库，覆盖机器学习工作流与流水线校验。",
         "use_case": "正向场景：搭建 AI 流水线时使用。反向场景：生产环境不要直接依赖。"}
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
        """5 repos (< MIN=6) → skip, SC-4 fail-visible: 未交付 → exit≠0（不再 exit 0 假成功）。"""
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

        assert rc != 0  # SC-4: skip 路径未交付 → fail-visible 非 0

    def test_layer2_fallback_writes_when_novelty_drops_below_min(self, tmp_path, monkeypatch):
        """08-27 事故：layer2 16 仓被 7 天 novelty 剪到 4（< MIN=6）仍须写出降级稿。

        layer1 空、整批来自 trending HTML 时，再用主推 novelty 窗剪仓 = 结构性空窗。
        真新仓不足 6 的 layer1 路径仍 skip（见 test_below_min_repos_still_skips）。
        """
        from datetime import UTC, date, datetime
        from unittest.mock import patch

        from akb2.briefing.github.dedup_sqlite import DedupStore
        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import run_pipeline

        now = datetime.now(UTC).isoformat()
        seen_urls = [f"https://github.com/seen/repo{i}" for i in range(12)]
        fresh_urls = [f"https://github.com/fresh/repo{i}" for i in range(4)]
        l2_cands = [
            Candidate(u, "@github_trending", None, now, None)
            for u in seen_urls + fresh_urls
        ]

        vault = tmp_path / "vault"
        out_dir = vault / "Praise Space/AKB2.0/AI TRENDS/GitHub"
        out_dir.mkdir(parents=True)
        novelty_db = tmp_path / "novelty.sqlite"
        store = DedupStore(novelty_db)
        for u in seen_urls:
            store.mark_seen(u)
        store.close()
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(novelty_db))
        monkeypatch.setenv("VAULT_ROOT", str(vault))

        with (
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(l2_cands, "layer2", {})),
            patch("akb2.briefing.github.pipeline.enrich", side_effect=_mock_enrich),
            patch("akb2.briefing.github.pipeline.synthesize", side_effect=_mock_synth),
        ):
            rc = run_pipeline(mock=False)

        assert rc == 0, "layer2 降级稿被 novelty 剪到 <6 仍应写出，而不是 skip"
        today = date.today().isoformat()
        md_path = out_dir / f"{today} GitHub Briefing.md"
        json_path = out_dir / f"{today} GitHub Briefing.json"
        assert md_path.exists(), f"Expected md output: {md_path}"
        assert json_path.exists(), f"Expected json output: {json_path}"
        with open(json_path) as f:
            data = json.load(f)
        assert len(data["top_repos"]) >= 4

    def test_layer2_bypass_does_not_admit_fresh_alias_of_window_canonical(
        self, tmp_path, monkeypatch
    ):
        """bypass 只放行初次 window_blocked 的仓；fresh alias 301 到窗口内 canonical 仍拦。"""
        from datetime import UTC, date, datetime
        from unittest.mock import patch

        from akb2.briefing.github.dedup_sqlite import DedupStore
        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import run_pipeline

        now = datetime.now(UTC).isoformat()
        seen_urls = [f"https://github.com/seen/repo{i}" for i in range(5)]
        canonical = "https://github.com/seen/canonical"
        alias = "https://github.com/fresh/alias"
        l2_cands = [
            Candidate(u, "@github_trending", None, now, None)
            for u in seen_urls + [alias]
        ]

        vault = tmp_path / "vault"
        out_dir = vault / "Praise Space/AKB2.0/AI TRENDS/GitHub"
        out_dir.mkdir(parents=True)
        novelty_db = tmp_path / "novelty.sqlite"
        store = DedupStore(novelty_db)
        for u in seen_urls + [canonical]:
            store.mark_seen(u)
        store.close()
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(novelty_db))
        monkeypatch.setenv("VAULT_ROOT", str(vault))

        def enrich_with_alias(url):
            data = _mock_enrich(url)
            if url == alias:
                data["repo_url"] = canonical
                data["requested_repo_url"] = alias
            return data

        with (
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(l2_cands, "layer2", {})),
            patch("akb2.briefing.github.pipeline.enrich", side_effect=enrich_with_alias),
            patch("akb2.briefing.github.pipeline.synthesize", side_effect=_mock_synth),
        ):
            rc = run_pipeline(mock=False)

        today = date.today().isoformat()
        json_path = out_dir / f"{today} GitHub Briefing.json"
        if rc == 0 and json_path.exists():
            with open(json_path) as f:
                data = json.load(f)
            urls = [r["repo_url"] for r in data["top_repos"]]
            assert canonical not in urls, "fresh alias 不得借 bypass 把窗口内 canonical 写进产物"

    def test_layer2_bypass_keeps_canonical_when_alias_enriches_first(
        self, tmp_path, monkeypatch
    ):
        """alias 先于窗口内 canonical 归一时，合并条目仍应保留 bypass 资格并出稿。"""
        from datetime import UTC, date, datetime
        from unittest.mock import patch

        from akb2.briefing.github.dedup_sqlite import DedupStore
        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import run_pipeline

        now = datetime.now(UTC).isoformat()
        seen_urls = [f"https://github.com/seen/repo{i}" for i in range(5)]
        canonical = "https://github.com/seen/canonical"
        alias = "https://github.com/fresh/alias"
        # alias 必须先于 canonical，复现 _merge_canonical 只留首个 requested_repo_url。
        # 5 个其他窗口内仓 + canonical + alias = 7 输入，301 归一后仍应 ≥6。
        intake_urls = [alias, canonical, *seen_urls]
        l2_cands = [
            Candidate(u, "@github_trending", None, now, None) for u in intake_urls
        ]

        vault = tmp_path / "vault"
        out_dir = vault / "Praise Space/AKB2.0/AI TRENDS/GitHub"
        out_dir.mkdir(parents=True)
        novelty_db = tmp_path / "novelty.sqlite"
        store = DedupStore(novelty_db)
        for u in seen_urls + [canonical]:
            store.mark_seen(u)
        store.close()
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(novelty_db))
        monkeypatch.setenv("VAULT_ROOT", str(vault))

        def enrich_with_alias(url):
            data = _mock_enrich(url)
            if url == alias:
                data["repo_url"] = canonical
                data["requested_repo_url"] = alias
            return data

        with (
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(l2_cands, "layer2", {})),
            patch("akb2.briefing.github.pipeline.enrich", side_effect=enrich_with_alias),
            patch("akb2.briefing.github.pipeline.synthesize", side_effect=_mock_synth),
        ):
            rc = run_pipeline(mock=False)

        assert rc == 0, "窗口内 canonical 与 alias 合并后仍应写出降级稿"
        today = date.today().isoformat()
        json_path = out_dir / f"{today} GitHub Briefing.json"
        assert json_path.exists()
        with open(json_path) as f:
            data = json.load(f)
        urls = [r["repo_url"] for r in data["top_repos"]]
        assert canonical in urls
        assert len(data["top_repos"]) >= 6


# ── SC-4: github 未交付 fail-visible ───────────────────────────────────────


class TestSC4FailVisible:
    """SC-4: 所有"当日未写出 briefing 文件"的分支 exit≠0；保留诊断 JSON。

    锁死 constitution 原则 4/12：exit code 必须反映真实交付，禁止 0/missing 假成功。
    """

    def test_enrich_degraded_returns_nonzero_and_no_file(self, tmp_path, monkeypatch, capsys):
        """enrich 全 enrich_ok=False → run_pipeline 非0 + vault 无当日文件 + stdout 含 enrich_degraded。"""
        from datetime import UTC, date, datetime
        from unittest.mock import patch

        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import run_pipeline

        now = datetime.now(UTC).isoformat()
        l2_cands = [Candidate(f"https://github.com/trending/repo{i}", "@github_trending", None, now, None)
                    for i in range(7)]  # 7 ≥ MIN=6 → 过 too_few，进 enrich

        vault = tmp_path / "vault"
        out_dir = vault / "Praise Space/AKB2.0/AI TRENDS/GitHub"
        out_dir.mkdir(parents=True)
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(tmp_path / "novelty.sqlite"))
        monkeypatch.setenv("VAULT_ROOT", str(vault))

        def enrich_all_fail(url):
            return {"repo_url": url, "enrich_ok": False}

        with (
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(l2_cands, "layer2", {})),
            patch("akb2.briefing.github.pipeline.enrich", side_effect=enrich_all_fail),
        ):
            rc = run_pipeline(mock=False)

        assert rc != 0, "enrich 全失败必须 fail-visible (非0)"
        today = date.today().isoformat()
        assert not (out_dir / f"{today} GitHub Briefing.md").exists(), "不应写出当日文件"
        captured = capsys.readouterr()
        assert "enrich_degraded" in captured.out, f"stdout 应含 enrich_degraded: {captured.out}"

    def test_no_delivery_skip_returns_nonzero(self, tmp_path, monkeypatch):
        """候选不足 skip → 非0（未交付 = fail-visible，不再 exit 0 假成功）。"""
        from datetime import UTC, datetime
        from unittest.mock import patch

        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import run_pipeline

        now = datetime.now(UTC).isoformat()
        l2_cands = [Candidate(f"https://github.com/trending/repo{i}", "@github_trending", None, now, None)
                    for i in range(3)]  # 3 < MIN=6 → too_few_aggregated_repos

        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(tmp_path / "novelty.sqlite"))
        monkeypatch.setenv("VAULT_ROOT", str(vault))

        with (
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(l2_cands, "layer2", {})),
            patch("akb2.briefing.github.pipeline.enrich", side_effect=_mock_enrich),
        ):
            rc = run_pipeline(mock=False)

        assert rc != 0, "候选不足 skip 必须 fail-visible 非 0"

    def test_failed_exit_on_synthesis_failure(self, tmp_path, monkeypatch):
        """synthesize 抛异常 → _write_failed(synthesis_failed) → 非0（hard failure 可见）。"""
        from datetime import UTC, datetime
        from unittest.mock import patch

        from akb2.briefing.github.intake import Candidate
        from akb2.briefing.github.pipeline import run_pipeline

        now = datetime.now(UTC).isoformat()
        l2_cands = [Candidate(f"https://github.com/trending/repo{i}", "@github_trending", None, now, None)
                    for i in range(7)]

        vault = tmp_path / "vault"
        (vault / "Praise Space/AKB2.0/AI TRENDS/GitHub").mkdir(parents=True)
        monkeypatch.setenv("AKB2_NOVELTY_DB", str(tmp_path / "novelty.sqlite"))
        monkeypatch.setenv("VAULT_ROOT", str(vault))

        def synth_boom(repos):
            raise RuntimeError("synthesis boom")

        with (
            patch("akb2.briefing.github.pipeline._real_intake",
                  return_value=(l2_cands, "layer2", {})),
            patch("akb2.briefing.github.pipeline.enrich", side_effect=_mock_enrich),
            patch("akb2.briefing.github.pipeline.synthesize", side_effect=synth_boom),
        ):
            rc = run_pipeline(mock=False)

        assert rc != 0, "synthesis 失败必须 fail-visible 非 0"
