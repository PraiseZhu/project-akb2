"""E2E live chain tests for knowledge-compounding pipeline (Plan 3 W4 Step 6).

Tests the full lint → gate → promote → backlinks → index pipeline using
isolated tmp_path vaults. No writes to the real AKB vault.

Test matrix:
  1. test_full_loop            — lint pass → gate active → promote → backlinks → index
  2. test_promote_blocks_conflict — slug conflict exits non-zero
  3. test_backlinks_after_save — two query-result pages both reference source-a
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.resolve()

LINT_SCRIPT = REPO_ROOT / "src" / "akb2" / "lint" / "akb2_lint.py"
GATE_SCRIPT = REPO_ROOT / "src" / "akb2" / "gate" / "akb2_gate_check.py"
PROMOTE_SCRIPT = REPO_ROOT / "src" / "akb2" / "promotion" / "promote.py"
BACKLINKS_SCRIPT = REPO_ROOT / "src" / "akb2" / "backlinks" / "update_backlinks.py"
INDEX_SCRIPT = REPO_ROOT / "src" / "akb2" / "index" / "generate_index.py"


# ---------------------------------------------------------------------------
# Fixture content
# ---------------------------------------------------------------------------

SOURCE_A_CONTENT = textwrap.dedent("""\
    ---
    title: Source A
    slug: source-a
    template_type: synthesis
    page_status: active
    tags: [test]
    tldr: Source A test page
    ---

    ## TLDR
    Source A test page content.
""")

SOURCE_B_CONTENT = textwrap.dedent("""\
    ---
    title: Source B
    slug: source-b
    template_type: synthesis
    page_status: active
    tags: [test]
    tldr: Source B test page
    ---

    ## TLDR
    Source B test page content.
""")

# query-result candidate: satisfies gate hard checks for query-result type
# HC-01: in staging/candidate/
# HC-02: page_status=candidate
# HC-03: skipped for query-result
# HC-04: template_type=query-result (valid)
# HC-05: skipped for query-result
# HC-06: future_questions with 2 items
# HC-07: derived_from non-empty satisfies graph link
# HC-08: has ## TLDR
# HC-09: gate_decision not pre-filled
CANDIDATE_CONTENT = textwrap.dedent("""\
    ---
    title: Test Query Result
    slug: query-result-test
    template_type: query-result
    query: 测试问题是什么？
    derived_from:
      - source-a
      - source-b
    confidence: high
    page_status: candidate
    gate_status: pass
    future_questions:
      - 后续问题一？
      - 后续问题二？
    tags: [test]
    tldr: Test query result
    ---

    ## TLDR
    Test query result content.

    ## Answer
    This is the test answer.
""")


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _build_base_vault(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Create the base directory structure for the live E2E test vault.

    Returns (wiki_synthesis_dir, staging_candidate_dir, data_dir, backlinks_file).
    """
    wiki_synthesis = tmp_path / "wiki" / "synthesis"
    wiki_synthesis.mkdir(parents=True)
    staging_candidate = tmp_path / "staging" / "candidate"
    staging_candidate.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    backlinks_file = data_dir / "backlinks.jsonl"
    backlinks_file.write_text("", encoding="utf-8")  # empty initial file
    return wiki_synthesis, staging_candidate, data_dir, backlinks_file


def _write_source_pages(wiki_synthesis: Path) -> None:
    """Write source-a.md and source-b.md as active synthesis pages."""
    (wiki_synthesis / "source-a.md").write_text(SOURCE_A_CONTENT, encoding="utf-8")
    (wiki_synthesis / "source-b.md").write_text(SOURCE_B_CONTENT, encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1: Full pipeline loop
# ---------------------------------------------------------------------------

def test_full_loop(tmp_path: Path) -> None:
    """Exercise lint → gate → promote → backlinks → index on a single candidate."""
    wiki_synthesis, staging_candidate, _data_dir, backlinks_file = _build_base_vault(tmp_path)
    _write_source_pages(wiki_synthesis)

    # Place the candidate in staging/candidate/
    candidate_file = staging_candidate / "query-result-test.md"
    candidate_file.write_text(CANDIDATE_CONTENT, encoding="utf-8")

    wiki_root = tmp_path / "wiki"

    # ------------------------------------------------------------------
    # Step 1: lint — single-file mode, v0.3, with --vault-root so R23
    #         can find source pages under wiki/
    # ------------------------------------------------------------------
    lint_result = subprocess.run(
        [
            sys.executable, str(LINT_SCRIPT),
            "--version", "v0.3",
            "--vault-root", str(tmp_path),
            str(candidate_file),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert lint_result.returncode == 0, (
        f"lint expected exit 0, got {lint_result.returncode}.\n"
        f"stdout:\n{lint_result.stdout}\n"
        f"stderr:\n{lint_result.stderr}"
    )

    # ------------------------------------------------------------------
    # Step 2: gate — check that proposed_gate_decision is active
    # ------------------------------------------------------------------
    gate_result = subprocess.run(
        [
            sys.executable, str(GATE_SCRIPT),
            "check",
            "--akb-root", str(tmp_path),
            "--input", "staging/candidate/query-result-test.md",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert gate_result.returncode == 0, (
        f"gate expected exit 0, got {gate_result.returncode}.\n"
        f"stdout:\n{gate_result.stdout}\n"
        f"stderr:\n{gate_result.stderr}"
    )
    gate_out = gate_result.stdout
    # Look for active decision in per-candidate proposals block
    assert "proposed_gate_decision: active" in gate_out, (
        f"gate expected 'proposed_gate_decision: active' in stdout.\n"
        f"stdout:\n{gate_out}"
    )

    # ------------------------------------------------------------------
    # Step 3: promote — apply, expect query-result-test.md in wiki/synthesis/
    # ------------------------------------------------------------------
    promote_result = subprocess.run(
        [
            sys.executable, str(PROMOTE_SCRIPT),
            "--staging-root", str(staging_candidate),
            "--wiki-root", str(wiki_root),
            "--apply",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert promote_result.returncode == 0, (
        f"promote expected exit 0, got {promote_result.returncode}.\n"
        f"stdout:\n{promote_result.stdout}\n"
        f"stderr:\n{promote_result.stderr}"
    )
    promoted_file = wiki_synthesis / "query-result-test.md"
    assert promoted_file.exists(), (
        f"Expected promoted file at {promoted_file} after --apply"
    )
    promoted_content = promoted_file.read_text(encoding="utf-8")
    assert "page_status: active" in promoted_content, (
        f"Expected page_status: active in promoted file.\n"
        f"Promoted content:\n{promoted_content[:500]}"
    )
    assert "page_status: candidate" not in promoted_content, (
        "page_status must be updated from candidate to active"
    )

    # ------------------------------------------------------------------
    # Step 4: backlinks — rebuild, expect backlinks.jsonl non-empty and
    #         source-a referenced by query-result-test
    # ------------------------------------------------------------------
    backlinks_result = subprocess.run(
        [
            sys.executable, str(BACKLINKS_SCRIPT),
            "--rebuild",
            "--wiki-root", str(wiki_root),
            "--backlinks-file", str(backlinks_file),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert backlinks_result.returncode == 0, (
        f"backlinks expected exit 0, got {backlinks_result.returncode}.\n"
        f"stdout:\n{backlinks_result.stdout}\n"
        f"stderr:\n{backlinks_result.stderr}"
    )
    bl_content = backlinks_file.read_text(encoding="utf-8").strip()
    assert bl_content, "backlinks.jsonl should be non-empty after rebuild"

    # Parse backlinks and verify source-a has query-result-test in referenced_by
    source_a_entry: dict | None = None
    for line in bl_content.splitlines():
        entry = json.loads(line)
        if entry.get("source_slug") == "source-a":
            source_a_entry = entry
            break
    assert source_a_entry is not None, (
        f"Expected 'source-a' entry in backlinks.jsonl.\n"
        f"backlinks content:\n{bl_content}"
    )
    assert "query-result-test" in source_a_entry.get("referenced_by", []), (
        f"Expected 'query-result-test' in source-a's referenced_by.\n"
        f"source-a entry: {source_a_entry}"
    )

    # ------------------------------------------------------------------
    # Step 5: index — dry-run, stdout should mention query-result-test or title
    # ------------------------------------------------------------------
    index_result = subprocess.run(
        [
            sys.executable, str(INDEX_SCRIPT),
            "--dry-run",
            "--vault-root", str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert index_result.returncode == 0, (
        f"index expected exit 0, got {index_result.returncode}.\n"
        f"stdout:\n{index_result.stdout}\n"
        f"stderr:\n{index_result.stderr}"
    )
    index_out = index_result.stdout
    # The promoted page has title "Test Query Result" and slug "query-result-test"
    assert ("query-result-test" in index_out or "Test Query Result" in index_out), (
        f"index stdout should mention slug or title of promoted page.\n"
        f"stdout:\n{index_out}"
    )


# ---------------------------------------------------------------------------
# Test 2: Promote blocks slug conflict
# ---------------------------------------------------------------------------

def test_promote_blocks_conflict(tmp_path: Path) -> None:
    """Promote exits non-zero when the slug already exists in wiki/."""
    wiki_synthesis, staging_candidate, _data_dir, _backlinks_file = _build_base_vault(tmp_path)
    _write_source_pages(wiki_synthesis)

    wiki_root = tmp_path / "wiki"

    # Pre-create the conflicting target file
    existing_file = wiki_synthesis / "query-result-test.md"
    existing_file.write_text(
        "# Existing wiki page — must not be overwritten\n", encoding="utf-8"
    )

    # Write the candidate in staging
    candidate_file = staging_candidate / "query-result-test.md"
    candidate_file.write_text(CANDIDATE_CONTENT, encoding="utf-8")

    promote_result = subprocess.run(
        [
            sys.executable, str(PROMOTE_SCRIPT),
            "--staging-root", str(staging_candidate),
            "--wiki-root", str(wiki_root),
            "--apply",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert promote_result.returncode != 0, (
        f"promote should exit non-zero on slug conflict, got 0.\n"
        f"stdout:\n{promote_result.stdout}\n"
        f"stderr:\n{promote_result.stderr}"
    )
    # Existing file must not be overwritten
    assert existing_file.read_text(encoding="utf-8") == (
        "# Existing wiki page — must not be overwritten\n"
    ), "Conflict guard must not overwrite existing wiki file"


# ---------------------------------------------------------------------------
# Test 3: Backlinks after saving two query-result pages
# ---------------------------------------------------------------------------

def test_backlinks_after_save(tmp_path: Path) -> None:
    """Two query-result pages both reference source-a; backlinks reflect both."""
    wiki_synthesis, _staging_candidate, _data_dir, backlinks_file = _build_base_vault(tmp_path)
    _write_source_pages(wiki_synthesis)

    wiki_root = tmp_path / "wiki"

    # query-result-1: derived_from [source-a]
    qr1_content = textwrap.dedent("""\
        ---
        title: Query Result 1
        slug: query-result-1
        template_type: query-result
        query: First question?
        derived_from:
          - source-a
        confidence: high
        page_status: active
        future_questions:
          - Follow-up 1?
          - Follow-up 2?
        tags: [test]
        tldr: First result
        ---

        ## TLDR
        First result content.
    """)

    # query-result-2: derived_from [source-a, source-b]
    qr2_content = textwrap.dedent("""\
        ---
        title: Query Result 2
        slug: query-result-2
        template_type: query-result
        query: Second question?
        derived_from:
          - source-a
          - source-b
        confidence: medium
        page_status: active
        future_questions:
          - Follow-up A?
          - Follow-up B?
        tags: [test]
        tldr: Second result
        ---

        ## TLDR
        Second result content.
    """)

    # Write both directly to wiki/synthesis/ (already active, skip promote)
    (wiki_synthesis / "query-result-1.md").write_text(qr1_content, encoding="utf-8")
    (wiki_synthesis / "query-result-2.md").write_text(qr2_content, encoding="utf-8")

    # Rebuild backlinks
    backlinks_result = subprocess.run(
        [
            sys.executable, str(BACKLINKS_SCRIPT),
            "--rebuild",
            "--wiki-root", str(wiki_root),
            "--backlinks-file", str(backlinks_file),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert backlinks_result.returncode == 0, (
        f"backlinks expected exit 0, got {backlinks_result.returncode}.\n"
        f"stdout:\n{backlinks_result.stdout}\n"
        f"stderr:\n{backlinks_result.stderr}"
    )

    bl_content = backlinks_file.read_text(encoding="utf-8").strip()
    assert bl_content, "backlinks.jsonl should be non-empty after rebuild"

    # Find source-a entry
    source_a_entry: dict | None = None
    for line in bl_content.splitlines():
        entry = json.loads(line)
        if entry.get("source_slug") == "source-a":
            source_a_entry = entry
            break

    assert source_a_entry is not None, (
        f"Expected 'source-a' entry in backlinks.jsonl.\n"
        f"backlinks content:\n{bl_content}"
    )
    referenced_by = source_a_entry.get("referenced_by", [])
    assert len(referenced_by) == 2, (
        f"source-a should be referenced by 2 query-result pages, "
        f"got {len(referenced_by)}: {referenced_by}"
    )
    assert "query-result-1" in referenced_by, (
        f"query-result-1 should be in source-a's referenced_by: {referenced_by}"
    )
    assert "query-result-2" in referenced_by, (
        f"query-result-2 should be in source-a's referenced_by: {referenced_by}"
    )
