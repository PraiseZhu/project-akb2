"""Tests for src/akb2/promotion/promote.py.

Test matrix:
  1. test_dry_run_outputs_would_promote     — dry-run prints "would promote"
  2. test_apply_copies_to_wiki              — --apply copies file, sets page_status=active
  3. test_apply_rejects_slug_conflict       — --apply exits 1 on same-filename conflict
  4. test_dry_run_skips_gate_rejected       — dry-run skips gate-rejected candidate
  5. test_promote_ignores_candidate_gate_status   [SC-1] frontmatter gate_status: pass
                                                          alone is not enough — subprocess
                                                          gate must also pass
  6. test_promote_writes_manifest                  [SC-2] --apply appends to
                                                          manifests/promotion-<date>.jsonl
                                                          under fcntl lock
  7. test_promote_blocks_slug_conflict_across_filenames [SC-3] two candidates with
                                                          different filenames but same
                                                          slug → second exits 1
  8. test_promote_handles_legacy_status_field      [SC-4] legacy `status: candidate`
                                                          becomes `page_status: active`
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture content — gate-valid for query-result template
# ---------------------------------------------------------------------------

SOURCE_A = textwrap.dedent("""\
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

GATE_PASS_CANDIDATE = textwrap.dedent("""\
    ---
    title: Test Query Result
    slug: test-page
    template_type: query-result
    query: 测试问题是什么？
    derived_from:
      - source-a
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
""")

# Candidate where frontmatter LIES about gate_status (claims pass but is
# missing required gate fields → real subprocess gate will fail)
LYING_CANDIDATE = textwrap.dedent("""\
    ---
    title: Lying Candidate
    slug: lying-page
    template_type: query-result
    page_status: candidate
    gate_status: pass
    tags: [test]
    tldr: Has gate_status pass but is missing query/derived_from/confidence
    ---

    ## TLDR
    Should be rejected by real gate even though frontmatter claims pass.
""")

LEGACY_STATUS_CANDIDATE = textwrap.dedent("""\
    ---
    title: Legacy Status Candidate
    slug: legacy-page
    template_type: query-result
    query: 用 legacy status 字段的问题？
    derived_from:
      - source-a
    confidence: medium
    status: candidate
    gate_status: pass
    future_questions:
      - 后续问题一？
      - 后续问题二？
    tags: [test]
    tldr: Legacy status field test
    ---

    ## TLDR
    Body content.
""")


def _make_vault(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build <vault>/staging/candidate/, <vault>/wiki/synthesis/ with source-a.

    Returns (vault_root, staging_root, wiki_root).
    """
    vault = tmp_path / "vault"
    staging = vault / "staging" / "candidate"
    wiki_synthesis = vault / "wiki" / "synthesis"
    staging.mkdir(parents=True)
    wiki_synthesis.mkdir(parents=True)
    # Pre-populate source-a so R23 / HC-07 derived_from check works
    (wiki_synthesis / "source-a.md").write_text(SOURCE_A, encoding="utf-8")
    return vault, staging, vault / "wiki"


@pytest.fixture(scope="module")
def promote_module(repo_root: Path):
    promote_path = repo_root / "src" / "akb2" / "promotion"
    if str(promote_path.parent.parent) not in sys.path:
        sys.path.insert(0, str(promote_path.parent.parent))
    src_path = str(repo_root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    import importlib
    import akb2.promotion.promote as mod
    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# Original test 1: dry-run outputs "would promote"
# ---------------------------------------------------------------------------

def test_dry_run_outputs_would_promote(tmp_path, promote_module, capsys):
    vault, staging, wiki = _make_vault(tmp_path)
    (staging / "test-page.md").write_text(GATE_PASS_CANDIDATE, encoding="utf-8")
    manifests = vault / "manifests"

    exit_code = promote_module.promote(staging, wiki, manifests, dry_run=True)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "would promote" in captured.out
    assert "test-page" in captured.out


# ---------------------------------------------------------------------------
# Original test 2: --apply copies + sets page_status=active
# ---------------------------------------------------------------------------

def test_apply_copies_to_wiki(tmp_path, promote_module, capsys):
    vault, staging, wiki = _make_vault(tmp_path)
    (staging / "test-page.md").write_text(GATE_PASS_CANDIDATE, encoding="utf-8")
    manifests = vault / "manifests"

    exit_code = promote_module.promote(staging, wiki, manifests, dry_run=False)
    assert exit_code == 0

    promoted = wiki / "synthesis" / "test-page.md"
    assert promoted.exists()
    content = promoted.read_text(encoding="utf-8")
    assert "page_status: active" in content
    assert "page_status: candidate" not in content


# ---------------------------------------------------------------------------
# Original test 3: --apply blocks same-filename conflict
# ---------------------------------------------------------------------------

def test_apply_rejects_slug_conflict(tmp_path, promote_module, capsys):
    vault, staging, wiki = _make_vault(tmp_path)
    manifests = vault / "manifests"

    existing = wiki / "synthesis" / "test-page.md"
    existing.write_text("# Existing wiki page\n", encoding="utf-8")
    (staging / "test-page.md").write_text(GATE_PASS_CANDIDATE, encoding="utf-8")

    exit_code = promote_module.promote(staging, wiki, manifests, dry_run=False)
    assert exit_code != 0
    assert existing.read_text(encoding="utf-8") == "# Existing wiki page\n"

    captured = capsys.readouterr()
    assert '"action": "conflict"' in captured.out


# ---------------------------------------------------------------------------
# Original test 4: dry-run skips gate-rejected candidate
# ---------------------------------------------------------------------------

def test_dry_run_skips_gate_rejected(tmp_path, promote_module, capsys):
    vault, staging, wiki = _make_vault(tmp_path)
    manifests = vault / "manifests"

    # Use a candidate that the real gate will reject (missing required fields)
    (staging / "rejected-page.md").write_text(LYING_CANDIDATE, encoding="utf-8")

    exit_code = promote_module.promote(staging, wiki, manifests, dry_run=True)
    captured = capsys.readouterr()

    assert exit_code == 0  # no conflict
    # Must NOT be in a "would promote" line
    would_promote = [l for l in captured.out.splitlines() if "would promote" in l]
    assert not any("rejected-page" in l for l in would_promote)


# ---------------------------------------------------------------------------
# SC-1: frontmatter gate_status: pass alone is not enough
# ---------------------------------------------------------------------------

def test_promote_ignores_candidate_gate_status(tmp_path, promote_module, capsys):
    """Candidate with frontmatter gate_status: pass but failing real gate is skipped.

    SC-1: subprocess gate is the only source of truth.
    """
    vault, staging, wiki = _make_vault(tmp_path)
    manifests = vault / "manifests"

    # LYING_CANDIDATE has gate_status: pass but missing query/derived_from/confidence
    (staging / "lying-page.md").write_text(LYING_CANDIDATE, encoding="utf-8")

    exit_code = promote_module.promote(staging, wiki, manifests, dry_run=False)
    captured = capsys.readouterr()

    assert exit_code == 0  # no conflict — just skipped

    promoted = wiki / "synthesis" / "lying-page.md"
    assert not promoted.exists(), (
        "LYING candidate must not be promoted even with gate_status: pass"
    )

    # Verify log records: gate_decision=fail despite self-claimed pass
    log_lines = [l for l in captured.out.splitlines() if l.startswith("{")]
    assert log_lines, f"Expected JSON log; got:\n{captured.out}"
    record = json.loads(log_lines[0])
    assert record["gate_decision"] == "fail"
    assert record["candidate_self_claimed_gate_status"] == "pass"
    assert record["action"] == "skipped"


# ---------------------------------------------------------------------------
# SC-2: --apply appends to manifests/promotion-<date>.jsonl
# ---------------------------------------------------------------------------

def test_promote_writes_manifest(tmp_path, promote_module, capsys):
    """--apply writes append-only manifest entry; fcntl lock held during write.

    SC-2: every successful promote has a persistent audit record.
    """
    vault, staging, wiki = _make_vault(tmp_path)
    manifests = vault / "manifests"
    (staging / "test-page.md").write_text(GATE_PASS_CANDIDATE, encoding="utf-8")

    exit_code = promote_module.promote(staging, wiki, manifests, dry_run=False)
    assert exit_code == 0

    today = _dt.date.today().isoformat()
    manifest = manifests / f"promotion-{today}.jsonl"
    assert manifest.exists(), f"Expected manifest at {manifest}"

    lines = manifest.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["slug"] == "test-page"
    assert entry["template_type"] == "query-result"
    assert entry["gate_decision"] == "pass"
    assert "ts" in entry
    assert entry["to"].endswith("/wiki/synthesis/test-page.md")


# ---------------------------------------------------------------------------
# SC-3: slug conflict detected across different filenames
# ---------------------------------------------------------------------------

def test_promote_blocks_slug_conflict_across_filenames(tmp_path, promote_module, capsys):
    """Two candidates with different filenames but same slug → second blocked.

    SC-3: conflict detection must scan frontmatter slugs in dest_dir,
    not only filenames.
    """
    vault, staging, wiki = _make_vault(tmp_path)
    manifests = vault / "manifests"

    # Pre-populate wiki/synthesis/ with a page whose filename ≠ slug
    existing = wiki / "synthesis" / "different-filename.md"
    existing.write_text(GATE_PASS_CANDIDATE.replace(
        "page_status: candidate", "page_status: active"
    ), encoding="utf-8")  # slug: test-page, filename: different-filename.md

    # Candidate with the SAME slug as the existing wiki page
    (staging / "test-page.md").write_text(GATE_PASS_CANDIDATE, encoding="utf-8")

    exit_code = promote_module.promote(staging, wiki, manifests, dry_run=False)
    assert exit_code != 0, (
        "Expected non-zero exit when frontmatter slug collides with "
        "existing slug in dest_dir, even across different filenames"
    )

    captured = capsys.readouterr()
    log_lines = [l for l in captured.out.splitlines() if l.startswith("{")]
    record = json.loads(log_lines[0])
    assert record["action"] == "conflict"
    assert record["slug"] == "test-page"
    assert "existing_holder" in record


# ---------------------------------------------------------------------------
# SC-4: legacy `status: candidate` becomes page_status: active
# ---------------------------------------------------------------------------

def test_promote_handles_legacy_status_field(tmp_path, promote_module, capsys):
    """Legacy `status: candidate` → promoted file has `page_status: active`.

    SC-4: no residual `status: candidate` left in promoted page.
    """
    vault, staging, wiki = _make_vault(tmp_path)
    manifests = vault / "manifests"
    (staging / "legacy-page.md").write_text(LEGACY_STATUS_CANDIDATE, encoding="utf-8")

    exit_code = promote_module.promote(staging, wiki, manifests, dry_run=False)
    assert exit_code == 0, (
        f"Expected promotion to succeed (gate should accept legacy "
        f"status: candidate). Stdout: {capsys.readouterr().out}"
    )

    promoted = wiki / "synthesis" / "legacy-page.md"
    assert promoted.exists()
    content = promoted.read_text(encoding="utf-8")
    assert "page_status: active" in content, (
        f"Expected page_status: active in promoted file:\n{content}"
    )
    assert "status: candidate" not in content, (
        f"Expected no residual `status: candidate`:\n{content}"
    )


# ---------------------------------------------------------------------------
# SC-2 hardening: full critical section under one lock
# ---------------------------------------------------------------------------

def test_concurrent_same_slug_single_writer(tmp_path, repo_root):
    """Two concurrent promote subprocesses targeting the same slug across
    a shared wiki tree must serialize through one fcntl-guarded critical
    section: exactly one wiki file, exactly one manifest entry, one
    process exits 0 and the other exits 1 (conflict).

    Verifies the lock covers slug scan → conflict decision → dest_file
    write → manifest append as a single critical section, not just the
    manifest append (which was the gap in the first hardening attempt).
    """
    # Shared wiki + source page reachable from both vaults via symlink
    shared_wiki = tmp_path / "shared_wiki"
    shared_synthesis = shared_wiki / "synthesis"
    shared_synthesis.mkdir(parents=True)
    (shared_synthesis / "source-a.md").write_text(SOURCE_A, encoding="utf-8")

    shared_manifests = tmp_path / "shared_manifests"
    shared_manifests.mkdir()

    # Two independent vaults, each with own staging/candidate/ but the
    # same `wiki` (symlinked to shared_wiki). Each candidate carries the
    # same slug `test-page` but a different filename and title.
    procs_meta: list[tuple[Path, Path]] = []
    for letter in ("a", "b"):
        vault = tmp_path / f"vault_{letter}"
        staging = vault / "staging" / "candidate"
        staging.mkdir(parents=True)
        os.symlink(str(shared_wiki), str(vault / "wiki"))
        cand_content = GATE_PASS_CANDIDATE.replace(
            "title: Test Query Result",
            f"title: Test Query Result {letter.upper()}",
        )
        cand_file = staging / f"candidate-{letter}.md"
        cand_file.write_text(cand_content, encoding="utf-8")
        procs_meta.append((staging, vault / "wiki"))

    promote_script = (
        repo_root / "src" / "akb2" / "promotion" / "promote.py"
    )

    # Launch both subprocesses back-to-back so their critical sections
    # overlap. The lock must serialize them; without it both would race
    # past the slug scan and produce 2 manifest entries / 2 exit 0.
    processes: list[subprocess.Popen] = []
    for staging, wiki in procs_meta:
        p = subprocess.Popen(
            [
                sys.executable,
                str(promote_script),
                "--staging-root", str(staging),
                "--wiki-root", str(wiki),
                "--manifests-root", str(shared_manifests),
                "--apply",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(p)

    outputs: list[tuple[int, str, str]] = []
    for p in processes:
        stdout, stderr = p.communicate(timeout=30)
        outputs.append((p.returncode, stdout, stderr))

    exit_codes = sorted(rc for rc, _, _ in outputs)
    assert exit_codes == [0, 1], (
        f"Expected one success (0) and one conflict (1) under the "
        f"promotion lock; got exits {exit_codes}.\n"
        f"out[0]: rc={outputs[0][0]} stdout={outputs[0][1]!r} "
        f"stderr={outputs[0][2]!r}\n"
        f"out[1]: rc={outputs[1][0]} stdout={outputs[1][1]!r} "
        f"stderr={outputs[1][2]!r}"
    )

    # Exactly one wiki file for the shared slug
    promoted = list(shared_synthesis.glob("test-page.md"))
    assert len(promoted) == 1, (
        f"Expected exactly 1 promoted wiki file for slug 'test-page', "
        f"found {len(promoted)}: {promoted}"
    )

    # Exactly one manifest entry for the shared slug
    today = _dt.date.today().isoformat()
    manifest_path = shared_manifests / f"promotion-{today}.jsonl"
    assert manifest_path.exists(), (
        f"Expected manifest at {manifest_path}; manifests_root contents: "
        f"{list(shared_manifests.iterdir())}"
    )
    manifest_lines = [
        line for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    test_page_entries = [
        line for line in manifest_lines
        if '"slug": "test-page"' in line
    ]
    assert len(test_page_entries) == 1, (
        f"Expected exactly 1 manifest entry for slug 'test-page' under "
        f"the lock; got {len(test_page_entries)}.\n"
        f"All manifest lines:\n" + "\n".join(manifest_lines)
    )
