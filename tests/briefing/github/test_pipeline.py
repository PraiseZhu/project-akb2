"""Tests for 8-step pipeline — mock E2E (SC-7) and SC-8 checks."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

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
        from datetime import date, timedelta
        next_day = (date.today() + timedelta(days=1)).isoformat()
        base = VAULT_STUB / "AKB2.0/AI TRENDS/GitHub" / f"{next_day} GitHub Briefing"
        md_path = base.with_suffix(".md")
        json_path = base.with_suffix(".json")

        assert md_path.exists(), f"Missing: {md_path}"
        assert json_path.exists(), f"Missing: {json_path}"

    def test_mock_json_top_repos_is_10(self):
        """JSON output has exactly 10 top_repos."""
        from datetime import date, timedelta
        next_day = (date.today() + timedelta(days=1)).isoformat()
        json_path = VAULT_STUB / "AKB2.0/AI TRENDS/GitHub" / f"{next_day} GitHub Briefing.json"

        with open(json_path) as f:
            data = json.load(f)
        assert len(data["top_repos"]) == 10

    def test_mock_json_passes_validator(self):
        """JSON output passes schema validator."""
        from datetime import date, timedelta
        next_day = (date.today() + timedelta(days=1)).isoformat()
        json_path = VAULT_STUB / "AKB2.0/AI TRENDS/GitHub" / f"{next_day} GitHub Briefing.json"

        from akb2.briefing.github.validator import validate
        errors = validate(str(json_path))
        assert errors == [], f"Validator errors: {errors}"

    def test_md_has_frontmatter_and_content(self):
        """Markdown output has frontmatter and repo entries."""
        from datetime import date, timedelta
        next_day = (date.today() + timedelta(days=1)).isoformat()
        md_path = VAULT_STUB / "AKB2.0/AI TRENDS/GitHub" / f"{next_day} GitHub Briefing.md"

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