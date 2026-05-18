#!/usr/bin/env python3
"""
AKB2 staging→wiki promotion script.

Reads staging/candidate/*.md, checks gate decision (via frontmatter
gate_status field or subprocess fallback), copies passing pages to
wiki/<template_type>/.

Gate decision logic:
  - frontmatter gate_status: pass  → promote
  - frontmatter gate_status: fail  → skip
  - no gate_status field           → run gate_check subprocess, check proposed_gate_decision == "active"

template_type → wiki sub-directory mapping:
  - query-result → wiki/synthesis/
  - <other>      → wiki/<template_type>/

Modes:
  --dry-run  (default): print "would promote <slug>: <from> → <to>", no file writes
  --apply:              copy file, update page_status candidate→active in frontmatter;
                        exit 1 on slug conflict (same filename already exists in target)

stdout JSON log per item:
  {"slug": ..., "from": ..., "to": ..., "action": "promoted|skipped|conflict"}
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Frontmatter helpers (inline, no external deps)
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_SIMPLE_SCALAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s+(.+)$")


def _parse_frontmatter_simple(text: str) -> tuple[dict, str]:
    """Extract frontmatter dict and body from markdown text.

    Only parses scalar values — enough for gate_status, template_type,
    page_status, title, slug.
    """
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    yaml_text = m.group(1)
    body = text[m.end():]
    fm: dict = {}
    for line in yaml_text.splitlines():
        match = _SIMPLE_SCALAR_RE.match(line)
        if match:
            k, v = match.group(1), match.group(2).strip()
            # strip surrounding quotes
            if (v.startswith('"') and v.endswith('"')) or (
                v.startswith("'") and v.endswith("'")
            ):
                v = v[1:-1]
            fm[k] = v
    return fm, body


def _update_page_status(text: str, new_status: str) -> str:
    """Replace page_status: candidate with page_status: <new_status> in frontmatter."""
    return re.sub(
        r"(^---\n.*?^page_status:\s*)candidate(\s*\n.*?^---\n)",
        lambda m: m.group(0).replace("candidate", new_status, 1),
        text,
        count=1,
        flags=re.DOTALL | re.MULTILINE,
    )


# ---------------------------------------------------------------------------
# template_type → wiki sub-directory
# ---------------------------------------------------------------------------

TEMPLATE_TYPE_MAP: dict[str, str] = {
    "query-result": "synthesis",
}


def _wiki_subdir(template_type: str) -> str:
    """Return the wiki sub-directory name for a given template_type."""
    return TEMPLATE_TYPE_MAP.get(template_type, template_type)


# ---------------------------------------------------------------------------
# Gate decision helpers
# ---------------------------------------------------------------------------

def _gate_decision_from_frontmatter(fm: dict) -> str | None:
    """Return 'pass', 'fail', or None if gate_status not present."""
    gs = fm.get("gate_status")
    if gs is None:
        return None
    return "pass" if gs == "pass" else "fail"


def _gate_decision_from_subprocess(candidate_path: Path, staging_root: Path) -> str:
    """Run gate_check as subprocess and derive pass/fail from proposed_gate_decision.

    Returns 'pass' if proposed_gate_decision == 'active', else 'fail'.
    Falls back to 'fail' on subprocess error.
    """
    gate_script = (
        Path(__file__).parent.parent / "gate" / "akb2_gate_check.py"
    )
    if not gate_script.exists():
        # Cannot find gate script — conservative: skip (fail)
        sys.stderr.write(
            f"WARN: gate script not found at {gate_script}; skipping {candidate_path.name}\n"
        )
        return "fail"

    # gate_check requires --akb-root (parent of staging/) and --input (relative to akb-root)
    akb_root = staging_root.parent
    try:
        rel_input = str(candidate_path.relative_to(akb_root))
    except ValueError:
        sys.stderr.write(
            f"WARN: {candidate_path} not under akb_root {akb_root}; skipping\n"
        )
        return "fail"

    result = subprocess.run(
        [
            sys.executable,
            str(gate_script),
            "check",
            "--akb-root", str(akb_root),
            "--input", rel_input,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "fail"

    # Parse output for proposed_gate_decision
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("proposed_gate_decision:"):
            decision = stripped.split(":", 1)[1].strip()
            return "pass" if decision == "active" else "fail"

    return "fail"


# ---------------------------------------------------------------------------
# Core promotion logic
# ---------------------------------------------------------------------------

def _slug_from_path(path: Path) -> str:
    return path.stem


def process_candidate(
    md_file: Path,
    staging_root: Path,
    wiki_root: Path,
    dry_run: bool,
) -> dict:
    """Process a single candidate .md file.

    Returns a log dict with keys: slug, from, to, action.
    action is one of: promoted | skipped | conflict
    """
    text = md_file.read_text(encoding="utf-8")
    fm, _body = _parse_frontmatter_simple(text)
    slug = fm.get("slug") or _slug_from_path(md_file)
    template_type = fm.get("template_type") or ""

    log: dict = {
        "slug": slug,
        "from": str(md_file),
        "to": "",
        "action": "skipped",
    }

    # Determine gate decision
    gate_result = _gate_decision_from_frontmatter(fm)
    if gate_result is None:
        gate_result = _gate_decision_from_subprocess(md_file, staging_root)

    if gate_result != "pass":
        print(json.dumps(log))
        return log

    # Compute destination
    subdir = _wiki_subdir(template_type)
    dest_dir = wiki_root / subdir
    dest_file = dest_dir / md_file.name
    log["to"] = str(dest_file)

    if dry_run:
        print(f"would promote {slug}: {md_file} → {dest_file}")
        log["action"] = "promoted"  # dry-run marks it as "would be promoted"
        print(json.dumps(log))
        return log

    # --apply mode
    if dest_file.exists():
        log["action"] = "conflict"
        print(json.dumps(log))
        return log

    # Copy and update page_status
    dest_dir.mkdir(parents=True, exist_ok=True)
    updated_text = _update_page_status(text, "active")
    dest_file.write_text(updated_text, encoding="utf-8")
    log["action"] = "promoted"
    print(json.dumps(log))
    return log


def promote(
    staging_root: Path,
    wiki_root: Path,
    dry_run: bool,
) -> int:
    """Main promotion loop.

    Returns exit code: 0 on success, 1 if any conflict was detected.
    """
    candidates = sorted(staging_root.glob("*.md"))
    if not candidates:
        sys.stderr.write(f"WARN: no .md files found under {staging_root}\n")
        return 0

    had_conflict = False
    for md_file in candidates:
        log = process_candidate(md_file, staging_root, wiki_root, dry_run)
        if log["action"] == "conflict":
            had_conflict = True
            sys.stderr.write(
                f"ERROR: slug conflict — {log['to']} already exists; "
                f"not overwriting {log['from']}\n"
            )

    return 1 if had_conflict else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AKB2 staging→wiki promotion script"
    )
    parser.add_argument(
        "--staging-root",
        required=True,
        help="Path to staging/candidate/ directory (source of .md candidates)",
    )
    parser.add_argument(
        "--wiki-root",
        required=True,
        help="Path to wiki/ root directory (promotion target)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Print what would be promoted without writing files (default)",
    )
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Actually copy files and update page_status to active",
    )

    args = parser.parse_args(argv)
    staging_root = Path(args.staging_root).resolve()
    wiki_root = Path(args.wiki_root).resolve()

    if not staging_root.is_dir():
        sys.stderr.write(f"ERROR: --staging-root is not a directory: {staging_root}\n")
        return 1
    if not wiki_root.is_dir():
        # Allow wiki_root to not exist yet in dry-run mode
        if not args.dry_run:
            sys.stderr.write(
                f"ERROR: --wiki-root does not exist: {wiki_root}\n"
            )
            return 1

    return promote(staging_root, wiki_root, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
