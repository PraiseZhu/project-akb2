"""Tests for src/akb2/promotion/promote.py.

All tests use tmp_path to create isolated staging/ and wiki/ trees.
No writes to the real vault or wiki/.

Test matrix:
  1. test_dry_run_outputs_would_promote     — dry-run prints "would promote" for passing candidate
  2. test_apply_copies_to_wiki              — --apply copies file and sets page_status=active
  3. test_apply_rejects_slug_conflict       — --apply exits 1 when slug already in wiki
  4. test_dry_run_skips_gate_rejected       — dry-run skips candidate with gate_status: fail
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASSING_CANDIDATE = """\
---
template_type: query-result
title: Test Query Result
slug: test-page
page_status: candidate
gate_status: pass
query: What is LangGraph?
derived_from: []
confidence: high
---

## TLDR

LangGraph is a framework for building stateful multi-actor applications with LLMs.
"""

FAILING_CANDIDATE = """\
---
template_type: query-result
title: Failing Candidate
slug: failing-page
page_status: candidate
gate_status: fail
query: What is LangGraph?
derived_from: []
confidence: low
---

## TLDR

This candidate did not pass gate review.
"""

PASSING_CONCEPT_CANDIDATE = """\
---
template_type: concept
title: Agent Memory
slug: agent-memory
page_status: candidate
gate_status: pass
source_type: docs
confidence: high
---

## TLDR

Agent memory allows LLM agents to recall past interactions.
"""


def _make_staging(tmp_path: Path) -> Path:
    """Create a staging/candidate/ directory under tmp_path."""
    staging = tmp_path / "staging" / "candidate"
    staging.mkdir(parents=True)
    return staging


def _make_wiki(tmp_path: Path) -> Path:
    """Create a wiki/ directory under tmp_path."""
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True)
    return wiki


# ---------------------------------------------------------------------------
# Import promote module via sys.path manipulation
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def promote_module(repo_root: Path):
    """Import promote.py from the source tree."""
    promote_path = repo_root / "src" / "akb2" / "promotion"
    if str(promote_path.parent.parent) not in sys.path:
        sys.path.insert(0, str(promote_path.parent.parent))
    # Also ensure src/ is on path for potential gate imports
    src_path = str(repo_root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    import importlib
    import akb2.promotion.promote as mod
    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# Test 1: dry-run outputs "would promote" for passing candidate
# ---------------------------------------------------------------------------

def test_dry_run_outputs_would_promote(tmp_path: Path, promote_module, capsys):
    """A candidate with gate_status: pass in dry-run mode outputs 'would promote'."""
    staging = _make_staging(tmp_path)
    wiki = _make_wiki(tmp_path)

    candidate_file = staging / "test-page.md"
    candidate_file.write_text(PASSING_CANDIDATE, encoding="utf-8")

    exit_code = promote_module.promote(staging, wiki, dry_run=True)
    captured = capsys.readouterr()

    assert exit_code == 0, f"Expected exit 0, got {exit_code}"
    assert "would promote" in captured.out, (
        f"Expected 'would promote' in stdout, got:\n{captured.out}"
    )
    assert "test-page" in captured.out


# ---------------------------------------------------------------------------
# Test 2: --apply copies file to wiki and sets page_status=active
# ---------------------------------------------------------------------------

def test_apply_copies_to_wiki(tmp_path: Path, promote_module, capsys):
    """A passing candidate under --apply is copied to wiki/synthesis/ with page_status=active."""
    staging = _make_staging(tmp_path)
    wiki = _make_wiki(tmp_path)

    candidate_file = staging / "test-page.md"
    candidate_file.write_text(PASSING_CANDIDATE, encoding="utf-8")

    exit_code = promote_module.promote(staging, wiki, dry_run=False)

    # Check exit code
    assert exit_code == 0, f"Expected exit 0, got {exit_code}"

    # query-result → wiki/synthesis/
    promoted_file = wiki / "synthesis" / "test-page.md"
    assert promoted_file.exists(), (
        f"Expected {promoted_file} to exist after --apply"
    )

    content = promoted_file.read_text(encoding="utf-8")
    assert "page_status: active" in content, (
        f"Expected page_status: active in promoted file, got:\n{content[:500]}"
    )
    # Ensure original candidate status was changed
    assert "page_status: candidate" not in content, (
        "page_status should have been updated from candidate to active"
    )

    # Also check JSON log on stdout
    captured = capsys.readouterr()
    assert '"action": "promoted"' in captured.out


# ---------------------------------------------------------------------------
# Test 3: --apply exits 1 (conflict) when slug already exists in wiki
# ---------------------------------------------------------------------------

def test_apply_rejects_slug_conflict(tmp_path: Path, promote_module, capsys):
    """When a file with the same name already exists in wiki/, --apply exits 1."""
    staging = _make_staging(tmp_path)
    wiki = _make_wiki(tmp_path)

    # Pre-create the target file (simulating an existing wiki page)
    existing_dir = wiki / "synthesis"
    existing_dir.mkdir(parents=True)
    existing_file = existing_dir / "test-page.md"
    existing_file.write_text("# Existing wiki page\n", encoding="utf-8")

    # Write the candidate
    candidate_file = staging / "test-page.md"
    candidate_file.write_text(PASSING_CANDIDATE, encoding="utf-8")

    exit_code = promote_module.promote(staging, wiki, dry_run=False)

    assert exit_code != 0, (
        f"Expected non-zero exit code on conflict, got {exit_code}"
    )

    # Existing file must NOT be overwritten
    assert existing_file.read_text(encoding="utf-8") == "# Existing wiki page\n", (
        "Conflict guard must not overwrite existing wiki file"
    )

    # JSON log should show conflict action
    captured = capsys.readouterr()
    assert '"action": "conflict"' in captured.out


# ---------------------------------------------------------------------------
# Test 4: dry-run skips gate-rejected candidates
# ---------------------------------------------------------------------------

def test_dry_run_skips_gate_rejected(tmp_path: Path, promote_module, capsys):
    """A candidate with gate_status: fail is skipped — no 'would promote' in stdout."""
    staging = _make_staging(tmp_path)
    wiki = _make_wiki(tmp_path)

    candidate_file = staging / "failing-page.md"
    candidate_file.write_text(FAILING_CANDIDATE, encoding="utf-8")

    exit_code = promote_module.promote(staging, wiki, dry_run=True)
    captured = capsys.readouterr()

    assert exit_code == 0, f"Expected exit 0, got {exit_code}"

    # The failing slug must not appear in a "would promote" line
    lines = captured.out.splitlines()
    would_promote_lines = [l for l in lines if "would promote" in l]
    assert not any("failing-page" in l for l in would_promote_lines), (
        f"Gate-rejected candidate should not appear in 'would promote'; stdout:\n{captured.out}"
    )
