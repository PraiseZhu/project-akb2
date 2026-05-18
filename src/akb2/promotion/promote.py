#!/usr/bin/env python3
"""
AKB2 staging→wiki promotion script.

Hardened against Codex review findings (Plan 3 hardening patch):
  SC-1 [P0]: ALWAYS subprocess gate_check (never trust frontmatter gate_status)
  SC-2 [P0]: --apply writes append-only manifest under fcntl.flock
  SC-3 [P1]: dest filename = <slug>.md; conflict check scans frontmatter slugs
  SC-4 [P1]: page_status update handles both legacy `status:` and `page_status:`
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import json
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Frontmatter helpers (inline, no external deps)
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_SIMPLE_SCALAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s+(.+)$")


def _parse_frontmatter_simple(text: str) -> tuple[dict, str]:
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
            if (v.startswith('"') and v.endswith('"')) or (
                v.startswith("'") and v.endswith("'")
            ):
                v = v[1:-1]
            fm[k] = v
    return fm, body


def _update_page_status_to_active(text: str) -> str:
    """Ensure frontmatter has `page_status: active`.

    SC-4: Handles both modern `page_status:` and legacy `status:` keys.
    Legacy `status: candidate` is rewritten as `page_status: active` (key renamed).
    """
    new_text, n = re.subn(
        r"(?m)^(page_status:\s*)candidate\s*$",
        r"\1active",
        text,
        count=1,
    )
    if n > 0:
        return new_text

    new_text, _ = re.subn(
        r"(?m)^status:\s*candidate\s*$",
        "page_status: active",
        text,
        count=1,
    )
    return new_text


# ---------------------------------------------------------------------------
# template_type → wiki sub-directory
# ---------------------------------------------------------------------------

TEMPLATE_TYPE_MAP: dict[str, str] = {
    "query-result": "synthesis",
}


def _wiki_subdir(template_type: str) -> str:
    return TEMPLATE_TYPE_MAP.get(template_type, template_type)


# ---------------------------------------------------------------------------
# Gate decision — ALWAYS subprocess (SC-1)
# ---------------------------------------------------------------------------

def _gate_decision_via_subprocess(candidate_path: Path, staging_root: Path) -> str:
    """Run gate_check subprocess; return 'pass' or 'fail'.

    SC-1: ONLY source of truth. Frontmatter gate_status is informational
    only and never trusted.
    """
    gate_script = Path(__file__).parent.parent / "gate" / "akb2_gate_check.py"
    if not gate_script.exists():
        sys.stderr.write(
            f"ERROR: gate script not found at {gate_script}; "
            f"refusing to promote {candidate_path.name}\n"
        )
        return "fail"

    # akb_root is vault root (two levels above staging/candidate/)
    # Convention: <vault>/staging/candidate/<file>.md
    akb_root = staging_root.parent.parent
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

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("proposed_gate_decision:"):
            decision = stripped.split(":", 1)[1].strip()
            return "pass" if decision == "active" else "fail"

    return "fail"


# ---------------------------------------------------------------------------
# Slug-based destination + cross-filename conflict detection (SC-3)
# ---------------------------------------------------------------------------

def _scan_dest_dir_slugs(dest_dir: Path) -> dict[str, Path]:
    """Map slug → existing file path (scans all frontmatter, not filenames)."""
    if not dest_dir.is_dir():
        return {}
    out: dict[str, Path] = {}
    for f in dest_dir.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = _parse_frontmatter_simple(text)
        slug = fm.get("slug") or f.stem
        out[slug] = f
    return out


# ---------------------------------------------------------------------------
# Promotion critical-section lock (SC-2 hardening)
# ---------------------------------------------------------------------------
#
# Originally only the manifest append was wrapped in fcntl, leaving the
# scan→conflict-check→dest-write→manifest-append sequence racy across
# concurrent promote processes. The lock now spans the full critical
# section per candidate.
#
# Lock file: <wiki_root>/.promotion.lock — created on demand, never read,
# used purely as a shared inode for fcntl.flock contention. Two promote
# processes targeting the same wiki tree (including via symlink-resolved
# paths) will serialize through this single lock file.

def _acquire_promotion_lock(wiki_root: Path):
    """Acquire exclusive lock on the promotion critical section.

    Returns an open file object; caller MUST pass it to
    `_release_promotion_lock` (preferably from a `try/finally`).
    Blocks until the lock is granted (LOCK_EX without LOCK_NB).
    """
    wiki_root.mkdir(parents=True, exist_ok=True)
    lock_path = wiki_root / ".promotion.lock"
    # "a+" creates the file if missing, never truncates, and the resulting
    # fd is suitable for fcntl.flock. Each process gets its own fd but
    # contends on the shared inode.
    fd = open(lock_path, "a+")
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
    return fd


def _release_promotion_lock(fd) -> None:
    """Release the promotion critical-section lock and close the fd."""
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    finally:
        fd.close()


def _append_manifest_line(manifests_root: Path, entry: dict) -> None:
    """Append one JSON line to manifests/promotion-<date>.jsonl.

    Caller MUST hold the promotion lock — this function does not lock
    independently, since the manifest write is part of the same critical
    section as the wiki write.
    """
    manifests_root.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    manifest_path = manifests_root / f"promotion-{today}.jsonl"
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with open(manifest_path, "a", encoding="utf-8") as fd:
        fd.write(line)
        fd.flush()


# ---------------------------------------------------------------------------
# Core promotion logic
# ---------------------------------------------------------------------------

def process_candidate(
    md_file: Path,
    staging_root: Path,
    wiki_root: Path,
    manifests_root: Path,
    dry_run: bool,
) -> dict:
    text = md_file.read_text(encoding="utf-8")
    fm, _body = _parse_frontmatter_simple(text)
    slug = fm.get("slug") or md_file.stem
    template_type = fm.get("template_type") or ""
    self_claimed = fm.get("gate_status", "")

    log: dict = {
        "slug": slug,
        "from": str(md_file),
        "to": "",
        "action": "skipped",
        "gate_decision": "fail",
        "candidate_self_claimed_gate_status": self_claimed,
    }

    # SC-1: ALWAYS subprocess gate
    gate_decision = _gate_decision_via_subprocess(md_file, staging_root)
    log["gate_decision"] = gate_decision

    if gate_decision != "pass":
        print(json.dumps(log, ensure_ascii=False))
        return log

    # SC-3: filename = <slug>.md; dest_dir slug-scoped conflict check
    subdir = _wiki_subdir(template_type)
    dest_dir = wiki_root / subdir
    dest_file = dest_dir / f"{slug}.md"
    log["to"] = str(dest_file)

    if dry_run:
        print(f"would promote {slug}: {md_file} → {dest_file}")
        log["action"] = "promoted"
        print(json.dumps(log, ensure_ascii=False))
        return log

    # === Critical section (SC-2 hardening) ===
    # One lock covers: slug scan, conflict decision, wiki write, manifest
    # append. Any concurrent promote against the same wiki blocks here
    # and observes our write after we release.
    lock_fd = _acquire_promotion_lock(wiki_root)
    try:
        existing_slugs = _scan_dest_dir_slugs(dest_dir)
        if slug in existing_slugs and existing_slugs[slug].name != f"{slug}.md":
            log["action"] = "conflict"
            log["existing_holder"] = str(existing_slugs[slug])
            print(json.dumps(log, ensure_ascii=False))
            return log
        if dest_file.exists():
            log["action"] = "conflict"
            print(json.dumps(log, ensure_ascii=False))
            return log

        # SC-4: normalize page_status to active (handles legacy status:)
        dest_dir.mkdir(parents=True, exist_ok=True)
        updated_text = _update_page_status_to_active(text)
        dest_file.write_text(updated_text, encoding="utf-8")
        log["action"] = "promoted"

        # SC-2: append manifest while we still hold the lock
        manifest_entry = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "slug": slug,
            "template_type": template_type,
            "from": str(md_file),
            "to": str(dest_file),
            "gate_decision": gate_decision,
            "candidate_self_claimed_gate_status": self_claimed,
        }
        _append_manifest_line(manifests_root, manifest_entry)
    finally:
        _release_promotion_lock(lock_fd)

    print(json.dumps(log, ensure_ascii=False))
    return log


def promote(
    staging_root: Path,
    wiki_root: Path,
    manifests_root: Path,
    dry_run: bool,
) -> int:
    candidates = sorted(staging_root.glob("*.md"))
    if not candidates:
        sys.stderr.write(f"WARN: no .md files found under {staging_root}\n")
        return 0

    had_conflict = False
    for md_file in candidates:
        log = process_candidate(
            md_file, staging_root, wiki_root, manifests_root, dry_run
        )
        if log["action"] == "conflict":
            had_conflict = True
            sys.stderr.write(
                f"ERROR: slug conflict — slug='{log['slug']}'; "
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
    parser.add_argument("--staging-root", required=True)
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument(
        "--manifests-root",
        default=None,
        help="Where to write promotion-<date>.jsonl. "
             "Defaults to <wiki_root>/../manifests/",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    mode.add_argument("--apply", dest="dry_run", action="store_false")

    args = parser.parse_args(argv)
    staging_root = Path(args.staging_root).resolve()
    wiki_root = Path(args.wiki_root).resolve()
    if args.manifests_root:
        manifests_root = Path(args.manifests_root).resolve()
    else:
        manifests_root = wiki_root.parent / "manifests"

    if not staging_root.is_dir():
        sys.stderr.write(f"ERROR: --staging-root is not a directory: {staging_root}\n")
        return 1
    if not wiki_root.is_dir():
        if not args.dry_run:
            sys.stderr.write(f"ERROR: --wiki-root does not exist: {wiki_root}\n")
            return 1

    return promote(staging_root, wiki_root, manifests_root, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
