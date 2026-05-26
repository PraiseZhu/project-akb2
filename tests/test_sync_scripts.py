"""Tests for scripts/sync-*.sh and scripts/verify-no-drift.sh.

Strategy: override HOME to a tmp_path subdirectory so all $HOME-based install
targets (~/.codex/skills, ~/AI-Agent/Claude/capabilities/source/skills/)
redirect into a temp tree.
No production paths are touched. Sync scripts use their real source (repo
toolbox/), with isolated install targets in the temp HOME.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from .conftest import sha256_file

SLUGS = ["executable-plan", "review", "local-llm", "skill-stocktake", "akb-search"]


# ---------------------------------------------------------------------------
# sync-skills-to-codex.sh
# ---------------------------------------------------------------------------


def test_sync_skills_to_codex_dry_run_no_write(tmp_path, repo_root, run_script):
    """Dry-run must NOT create files in the Codex install target."""
    home = tmp_path / "home"
    home.mkdir()

    r = run_script("scripts/sync-skills-to-codex.sh", home=home)

    assert r.returncode == 0, f"dry-run failed:\n{r.stdout}\n{r.stderr}"
    codex_dir = home / ".codex" / "skills"
    assert not codex_dir.exists(), "dry-run must not create install target directory"


def test_sync_skills_to_codex_write_creates_skill_md(tmp_path, repo_root, run_script):
    """--write creates SKILL.md for all 5 slugs in tmp HOME/.codex/skills."""
    home = tmp_path / "home"
    home.mkdir()

    r = run_script("scripts/sync-skills-to-codex.sh", ["--write"], home=home)

    assert r.returncode == 0, f"--write failed:\n{r.stdout}\n{r.stderr}"
    for slug in SLUGS:
        skill_md = home / ".codex" / "skills" / f"cc-{slug}" / "SKILL.md"
        assert skill_md.exists(), f"SKILL.md missing for cc-{slug}"
        assert skill_md.stat().st_size > 0, f"SKILL.md empty for cc-{slug}"


def test_sync_skills_to_codex_idempotent(tmp_path, repo_root, run_script):
    """Second --write run reports MATCH (idempotent)."""
    home = tmp_path / "home"
    home.mkdir()

    run_script("scripts/sync-skills-to-codex.sh", ["--write"], home=home)
    r2 = run_script("scripts/sync-skills-to-codex.sh", ["--write"], home=home)

    assert r2.returncode == 0, f"second --write failed:\n{r2.stdout}\n{r2.stderr}"
    assert "MATCH" in r2.stdout, "second --write should report MATCH (idempotent)"


def test_sync_skills_to_codex_sha_preserved(tmp_path, repo_root, run_script):
    """SKILL.md SHA after --write matches toolbox source SHA."""
    home = tmp_path / "home"
    home.mkdir()

    run_script("scripts/sync-skills-to-codex.sh", ["--write"], home=home)

    for slug in SLUGS:
        src = repo_root / "toolbox" / f"cc-{slug}" / "SKILL.md"
        dst = home / ".codex" / "skills" / f"cc-{slug}" / "SKILL.md"
        assert sha256_file(src) == sha256_file(dst), f"SHA mismatch for cc-{slug} after sync"


# ---------------------------------------------------------------------------
# sync-skills-to-claude-backup.sh
# ---------------------------------------------------------------------------


def test_sync_skills_to_claude_backup_dry_run_no_write(tmp_path, run_script):
    """Dry-run must not create any files in the claude backup targets."""
    home = tmp_path / "home"
    home.mkdir()

    r = run_script("scripts/sync-skills-to-claude-backup.sh", home=home)

    assert r.returncode == 0, f"dry-run failed:\n{r.stdout}\n{r.stderr}"
    skills_dir = home / "AI-Agent" / "Claude" / "capabilities" / "source" / "skills"
    assert not skills_dir.exists(), "dry-run must not create target directories"


def test_sync_skills_to_claude_backup_write_creates_both_targets(tmp_path, run_script):
    """--write populates both backup mirror and claude-active mirror."""
    home = tmp_path / "home"
    home.mkdir()

    r = run_script("scripts/sync-skills-to-claude-backup.sh", ["--write"], home=home)

    assert r.returncode == 0, f"--write failed:\n{r.stdout}\n{r.stderr}"
    skills_base = home / "AI-Agent" / "Claude" / "capabilities" / "source" / "skills"
    for slug in SLUGS:
        backup = skills_base / f"cc-{slug}" / "SKILL.md"
        active = skills_base / "claude-active" / f"cc-{slug}" / "SKILL.md"
        assert backup.exists(), f"backup SKILL.md missing for cc-{slug}"
        assert active.exists(), f"claude-active SKILL.md missing for cc-{slug}"


# ---------------------------------------------------------------------------
# verify-no-drift.sh
# ---------------------------------------------------------------------------


def test_verify_no_drift_exits_1_when_targets_missing(tmp_path, run_script):
    """verify-no-drift exits 1 and emits MISSING when install targets don't exist."""
    home = tmp_path / "home"
    home.mkdir()

    r = run_script("scripts/verify-no-drift.sh", home=home)

    assert r.returncode == 1, "drift check must fail (exit 1) when targets are missing"
    assert "MISSING" in r.stdout, "output must contain MISSING lines"


def test_verify_no_drift_missing_reason_label(tmp_path, run_script):
    """MISSING targets include explicit reason= label for diagnostics."""
    home = tmp_path / "home"
    home.mkdir()

    r = run_script("scripts/verify-no-drift.sh", home=home)

    assert "reason=" in r.stdout, "MISSING lines must include reason= label"


def test_verify_no_drift_after_codex_sync_shows_match(tmp_path, run_script):
    """After syncing to Codex, those entries report MATCH."""
    home = tmp_path / "home"
    home.mkdir()

    run_script("scripts/sync-skills-to-codex.sh", ["--write"], home=home)
    r = run_script("scripts/verify-no-drift.sh", home=home)

    # Exit still 1 (claude-backup still MISSING), but codex entries are MATCH
    assert "MATCH" in r.stdout, "codex entries must be MATCH after sync"


def test_verify_no_drift_json_mode(tmp_path, run_script):
    """--json mode produces parseable JSON output with total/failures/checks keys."""
    home = tmp_path / "home"
    home.mkdir()

    r = run_script("scripts/verify-no-drift.sh", ["--json"], home=home)

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        pytest.fail(f"--json output is not valid JSON: {e}\nstdout:\n{r.stdout}")

    assert "total" in data, "JSON must have 'total' key"
    assert "failures" in data, "JSON must have 'failures' key"
    assert "checks" in data, "JSON must have 'checks' key"
    assert len(data["checks"]) == data["total"], "checks length must match total"


def test_verify_no_drift_closure_manifest_always_match(tmp_path, run_script):
    """closure-manifest SHA lock reports MATCH regardless of HOME (file is in repo)."""
    home = tmp_path / "home"
    home.mkdir()

    r = run_script("scripts/verify-no-drift.sh", home=home)

    # Result lines have format: "[MATCH]   closure-manifest  sha=..."
    # Skip section header lines (e.g. "--- closure-manifest SHA lock ---")
    result_indicators = {"[MATCH]", "[DRIFT]", "[MISSING]", "result=MATCH", "result=DRIFT", "result=MISSING"}
    for line in r.stdout.splitlines():
        if "closure-manifest" in line and any(ind in line for ind in result_indicators):
            assert "MATCH" in line, f"closure-manifest must be MATCH, got: {line}"
            return
    pytest.fail("closure-manifest result line not found in verify-no-drift output")
