"""Tests for src/akb2/closure/.

Validates the immutable closure-manifest.json SHA lock, manifest content
invariants (5 tools, required fields, automation_allowed=False), and the
presence of the p1e-delta merge artifacts. No live merge execution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import sha256_file

# Immutable SHA lock — must never change (Step 5 regression guard)
CLOSURE_MANIFEST_SHA = "0fbd53703ff58802299ba8cb04f5290e4b973c124729376dc341f862f06f90cc"
EXPECTED_SLUGS = {
    "cc-executable-plan",
    "cc-review",
    "cc-local-llm",
    "cc-skill-stocktake",
    "cc-akb-search",
}
REQUIRED_TOOL_FIELDS = {
    "slug", "toolbox_status", "wiki_status", "eval_level",
    "automation_allowed", "skill_sha256",
}


@pytest.fixture(scope="module")
def closure_dir(repo_root: Path) -> Path:
    d = repo_root / "src" / "akb2" / "closure"
    assert d.is_dir(), f"closure/ missing at {d}"
    return d


@pytest.fixture(scope="module")
def manifest_path(closure_dir: Path) -> Path:
    p = closure_dir / "2026-05-07" / "closure-manifest.json"
    assert p.exists(), f"closure-manifest.json missing at {p}"
    return p


@pytest.fixture(scope="module")
def manifest(manifest_path: Path) -> dict:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# SHA lock (immutable invariant)
# ---------------------------------------------------------------------------


def test_closure_manifest_sha_locked(manifest_path: Path):
    """closure-manifest.json SHA must match the locked value forever."""
    actual = sha256_file(manifest_path)
    assert actual == CLOSURE_MANIFEST_SHA, (
        f"closure-manifest.json SHA CHANGED!\n"
        f"  expected: {CLOSURE_MANIFEST_SHA}\n"
        f"  actual:   {actual}\n"
        "This file is immutable — do not modify it."
    )


# ---------------------------------------------------------------------------
# Manifest content invariants
# ---------------------------------------------------------------------------


def test_closure_manifest_valid_json(manifest_path: Path):
    """closure-manifest.json must parse as valid JSON."""
    try:
        json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        pytest.fail(f"closure-manifest.json invalid JSON: {e}")


def test_closure_manifest_has_5_tools(manifest: dict):
    """Manifest must list exactly 5 tools."""
    tools = manifest.get("tools", [])
    assert len(tools) == 5, f"Expected 5 tools in manifest, found {len(tools)}"


def test_closure_manifest_tool_slugs(manifest: dict):
    """Manifest tools must have exactly the 5 expected slugs."""
    actual_slugs = {t["slug"] for t in manifest.get("tools", [])}
    assert actual_slugs == EXPECTED_SLUGS, (
        f"Tool slug mismatch.\n"
        f"  expected: {sorted(EXPECTED_SLUGS)}\n"
        f"  actual:   {sorted(actual_slugs)}"
    )


def test_closure_manifest_tool_fields(manifest: dict):
    """Every tool entry must have all required fields."""
    for tool in manifest.get("tools", []):
        missing = REQUIRED_TOOL_FIELDS - set(tool.keys())
        assert not missing, (
            f"Tool {tool.get('slug', '?')} missing fields: {missing}"
        )


def test_closure_manifest_automation_allowed_false(manifest: dict):
    """automation_allowed must be False for ALL 5 tools (pre-A2 invariant)."""
    for tool in manifest.get("tools", []):
        assert tool.get("automation_allowed") is False, (
            f"automation_allowed must be False for {tool.get('slug')}, "
            f"got: {tool.get('automation_allowed')}"
        )


# ---------------------------------------------------------------------------
# p1e-delta merge artifacts
# ---------------------------------------------------------------------------


def test_p1e_delta_directory_exists(closure_dir: Path):
    """2026-05-08-p1e-delta/ must exist as a committed artifact."""
    delta = closure_dir / "2026-05-08-p1e-delta"
    assert delta.is_dir(), "2026-05-08-p1e-delta/ directory must exist"


def test_p1e_delta_has_merge_baselines_script(closure_dir: Path):
    """merge-baselines.sh must exist in the p1e-delta directory."""
    script = closure_dir / "2026-05-08-p1e-delta" / "merge-baselines.sh"
    assert script.exists(), "merge-baselines.sh must exist in 2026-05-08-p1e-delta/"


def test_p1e_delta_merge_script_bash_shebang(closure_dir: Path):
    """merge-baselines.sh must have a bash shebang."""
    script = closure_dir / "2026-05-08-p1e-delta" / "merge-baselines.sh"
    first_line = script.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    assert first_line.startswith("#!"), (
        f"merge-baselines.sh must have a shebang, got: {first_line!r}"
    )


def test_p1e_delta_effective_manifest_valid_json(closure_dir: Path):
    """effective-manifest.json in p1e-delta must be valid JSON."""
    ef = closure_dir / "2026-05-08-p1e-delta" / "effective-manifest.json"
    assert ef.exists(), "effective-manifest.json must exist in p1e-delta/"
    try:
        json.loads(ef.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        pytest.fail(f"effective-manifest.json invalid JSON: {e}")
