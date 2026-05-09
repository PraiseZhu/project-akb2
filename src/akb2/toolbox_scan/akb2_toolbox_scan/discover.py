"""Walk user-controlled roots and emit ScanRecord rows.

Strict no-execution policy:
  * we never `subprocess` a candidate
  * we never `import` a candidate
  * we never follow symlinks (we record the symlink itself)
  * we never recurse beyond a per-root file count guard
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .fingerprint import FingerprintResult, HeaderSniff, fingerprint, sniff_header


SCRIPT_EXTENSIONS = {".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".py", ".rb"}
PROJECT_SCRIPT_EXTENSIONS = {
    ".sh", ".bash", ".zsh",
    ".py",
    ".js", ".mjs", ".cjs", ".ts",
    ".rb",
}
PROJECT_TOOL_DIRS = ("scripts", "bin", "tools", "cli")
PROJECT_PRUNE_DIRNAMES = frozenset({
    ".git",
    "node_modules",
    ".venv",
    "venv",
    ".cache",
    ".pytest_cache",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".next",
    ".turbo",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    "plugins",          # plugin caches
    "wiki",             # AKB-shaped content
    "staging",
    "raw",
    "archive",
    "attachments",
})


@dataclass(frozen=True)
class RootSpec:
    path: Path
    kind: str  # "user" | "vendor" | "project"
    label: str  # short label like "codex_skills" used in reports
    project_name: str | None = None


@dataclass
class RootSummary:
    spec: RootSpec
    status: str  # "scanned" | "empty" | "missing" | "skipped_vendor" | "truncated"
    candidate_count: int = 0
    error: str | None = None
    excluded_dir_count: int = 0
    excluded_file_count: int = 0
    truncated: bool = False


@dataclass
class ScanRecord:
    source_root: str
    root_kind: str
    root_status: str
    workspace_path: str
    relative_path: str
    detected_kind: str
    candidate_asset_type: str
    name: str
    slug_candidate: str
    extension: str
    size_bytes: int | None
    mtime: str | None
    sha256: str | None
    is_symlink: bool
    has_akb_backlink: bool
    backlink_targets: list[str]
    risk_hint: str
    side_effects_hint: str
    looks_like_secret_in_header: bool
    not_active: bool = True
    automation_allowed: bool = False
    gate_decision: str | None = None
    scan_error: str | None = None
    # Filled in later by AKB shadow lookup
    phase_b_shadow_match: bool = False
    registry_exists: bool = False
    interface_exists: bool = False
    eval_exists: bool = False
    # Phase C-1b additions (default values keep C-1 records backward-compatible)
    scan_scope: str = "global"
    project_root: str | None = None
    project_name: str | None = None
    project_relative_path: str | None = None
    discovery_reason: str | None = None
    duplicate_of_prior_scan: bool = False
    prior_manifest_path: str | None = None


@dataclass
class ScanResult:
    records: list[ScanRecord] = field(default_factory=list)
    root_summaries: list[RootSummary] = field(default_factory=list)


def discover_candidates(
    roots: list[RootSpec],
    *,
    limit: int | None = None,
    max_files_per_project_root: int = 20000,
    previous_manifest_paths: set[str] | None = None,
    previous_manifest_path_label: str | None = None,
) -> ScanResult:
    result = ScanResult()
    seen_paths: set[Path] = set()
    total = 0
    prior = previous_manifest_paths or set()

    for spec in roots:
        summary = RootSummary(spec=spec, status="scanned")
        result.root_summaries.append(summary)

        if not spec.path.exists():
            summary.status = "missing"
            continue

        if not spec.path.is_dir():
            summary.status = "missing"
            summary.error = "not a directory"
            continue

        if spec.kind == "vendor" and not _vendor_explicitly_enabled(spec):
            summary.status = "skipped_vendor"
            continue

        candidates_in_root = 0
        try:
            if spec.kind == "project":
                iterator = _iter_project_files(
                    spec.path, summary=summary, max_files=max_files_per_project_root
                )
            else:
                iterator = _iter_files(spec.path)

            for path in iterator:
                if limit is not None and total >= limit:
                    break
                if path in seen_paths:
                    continue
                record = _build_record(path, spec)
                if record is None:
                    continue
                # Only dedupe paths we actually accept as candidates; skipping
                # at classification time must NOT shadow later roots that would
                # accept the same path.
                seen_paths.add(path)
                if prior and record.workspace_path in prior:
                    record.duplicate_of_prior_scan = True
                    record.prior_manifest_path = previous_manifest_path_label
                result.records.append(record)
                candidates_in_root += 1
                total += 1
        except (OSError, PermissionError) as exc:
            summary.error = str(exc)

        summary.candidate_count = candidates_in_root
        if summary.truncated:
            summary.status = "truncated"
        elif candidates_in_root == 0 and summary.status == "scanned":
            summary.status = "empty"

        if limit is not None and total >= limit:
            break

    return result


def _vendor_explicitly_enabled(spec: RootSpec) -> bool:
    # The CLI marks explicit --vendor-root entries by setting kind="vendor"
    # AND attaching them to RootSpec. Discover-time gate is a no-op here
    # because the CLI only adds them when the flag is present.
    return True


def _iter_files(root: Path) -> Iterator[Path]:
    """Yield all regular files under root without following symlinks."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dpath = Path(dirpath)
        for name in filenames:
            yield dpath / name
        # also yield symlinks to dirs at this level (recorded, not traversed)
        for name in list(dirnames):
            link = dpath / name
            if link.is_symlink():
                yield link
                dirnames.remove(name)


def _iter_project_files(
    root: Path, *, summary: RootSummary, max_files: int
) -> Iterator[Path]:
    """Walk a project root with aggressive prune list.

    Yields every regular file that has at least one ancestor named in
    PROJECT_TOOL_DIRS (scripts/bin/tools/cli). Does not follow symlinks
    (records the symlink path itself if it lives inside a tool dir).
    """

    yielded = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dpath = Path(dirpath)

        # Prune: drop excluded dir names in-place (so os.walk skips them).
        kept: list[str] = []
        for d in dirnames:
            if d in PROJECT_PRUNE_DIRNAMES or d.startswith("."):
                # Hidden dirs (.git, .venv, .turbo, .DS_Store, etc.) are pruned.
                # The project tool dirs (scripts/bin/tools/cli) are NOT hidden,
                # so this rule never accidentally hides them.
                summary.excluded_dir_count += 1
                continue
            kept.append(d)
        dirnames[:] = kept

        rel = dpath.relative_to(root)
        in_tool_dir = _in_tool_dir(rel)

        # Yield symlinked dirs as records (without traversing) if they live
        # inside a tool dir.
        for name in list(dirnames):
            link = dpath / name
            if link.is_symlink():
                if in_tool_dir or name in PROJECT_TOOL_DIRS:
                    yield link
                    yielded += 1
                    if yielded >= max_files:
                        summary.truncated = True
                        return
                dirnames.remove(name)

        if not in_tool_dir:
            # Files at this level cannot be project script candidates unless
            # this directory itself is named scripts/bin/tools/cli, which
            # only triggers when in_tool_dir flips to True at the next
            # iteration. So skip filenames here.
            continue

        for name in filenames:
            summary.excluded_file_count += 0  # placeholder for future filtering
            yield dpath / name
            yielded += 1
            if yielded >= max_files:
                summary.truncated = True
                return


def _in_tool_dir(rel_path: Path) -> bool:
    """True iff any segment of rel_path is in PROJECT_TOOL_DIRS."""
    parts = rel_path.parts
    return any(p in PROJECT_TOOL_DIRS for p in parts)


def _build_record(path: Path, spec: RootSpec) -> ScanRecord | None:
    try:
        rel = path.relative_to(spec.path)
    except ValueError:
        return None

    is_symlink = path.is_symlink()

    if spec.kind == "project":
        cls = _classify_project(rel, is_symlink, path)
    else:
        cls = _classify(spec, rel, is_symlink, path)
    if cls is None:
        return None
    detected_kind, candidate_asset_type, discovery_reason = cls

    if is_symlink:
        fp = FingerprintResult(sha256=None, size_bytes=None, mtime=None, error="symlink_not_followed")
        sniff = HeaderSniff()
    else:
        fp = fingerprint(path)
        sniff = sniff_header(path) if fp.error is None else HeaderSniff()

    name = sniff.name or _name_from_path(spec, rel)
    slug = _slug_from_path(spec, rel)
    risk_hint, side_effects_hint = _risk_hints(detected_kind, path, sniff)

    record = ScanRecord(
        source_root=str(spec.path),
        root_kind=spec.kind,
        root_status="scanned",
        workspace_path=str(path),
        relative_path=str(rel),
        detected_kind=detected_kind,
        candidate_asset_type=candidate_asset_type,
        name=name,
        slug_candidate=slug,
        extension=path.suffix,
        size_bytes=fp.size_bytes,
        mtime=fp.mtime,
        sha256=fp.sha256,
        is_symlink=is_symlink,
        has_akb_backlink=sniff.has_akb_backlink,
        backlink_targets=sniff.backlink_targets,
        risk_hint=risk_hint,
        side_effects_hint=side_effects_hint,
        looks_like_secret_in_header=sniff.looks_like_secret,
        scan_error=fp.error,
    )

    if spec.kind == "project":
        record.scan_scope = "project_scripts_bin"
        record.project_root = str(spec.path)
        record.project_name = spec.project_name or spec.path.name
        record.project_relative_path = str(rel)
        record.discovery_reason = discovery_reason
    return record


def _classify_project(
    rel: Path, is_symlink: bool, path: Path
) -> tuple[str, str, str] | None:
    """Return (detected_kind, candidate_asset_type, discovery_reason) or None."""
    parts = rel.parts
    tool_segment = next((p for p in parts if p in PROJECT_TOOL_DIRS), None)
    if tool_segment is None:
        return None

    if is_symlink:
        return ("project_symlink", "unknown", f"symlink under {tool_segment}/")

    suffix = path.suffix.lower()
    has_allowed_ext = suffix in PROJECT_SCRIPT_EXTENSIONS

    if tool_segment == "scripts":
        if not has_allowed_ext:
            return None
        return ("project_script", "agent_procedure", "under scripts/")
    if tool_segment == "tools":
        if not has_allowed_ext:
            return None
        return ("project_tool_script", "agent_procedure", "under tools/")
    if tool_segment == "cli":
        if not has_allowed_ext:
            return None
        return ("project_cli", "agent_procedure", "under cli/")
    if tool_segment == "bin":
        # bin/ accepts either an allowed extension OR an executable bit.
        if has_allowed_ext:
            return ("project_bin", "agent_procedure", "under bin/ (extension)")
        try:
            mode = path.stat().st_mode
        except OSError:
            return None
        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            return ("project_bin", "agent_procedure", "under bin/ (executable bit)")
        return None
    return None


def _classify(
    spec: RootSpec, rel: Path, is_symlink: bool, path: Path
) -> tuple[str, str, str] | None:
    """Return (detected_kind, candidate_asset_type, discovery_reason) or None."""
    name = path.name
    parts = rel.parts

    if spec.label == "vendor_plugin_cache":
        if name == "SKILL.md":
            return ("vendor_skill", "tool", "vendor SKILL.md")
        return None

    if spec.label == "codex_skills":
        if name == "SKILL.md":
            return ("codex_skill", "tool", "codex SKILL.md")
        return None

    if spec.label == "claude_skills":
        if name == "SKILL.md":
            return ("claude_skill", "tool", "claude SKILL.md")
        return None

    if spec.label == "claude_hooks":
        if path.suffix in SCRIPT_EXTENSIONS or path.suffix == "":
            return ("claude_hook", "agent_procedure", "claude hook script")
        return None

    if spec.label == "claude_scripts":
        # exclude hooks subtree (those should come from claude_hooks root)
        if "hooks" in parts:
            return None
        if path.suffix in SCRIPT_EXTENSIONS:
            return ("claude_script", _asset_type_for_script(rel), "claude script")
        return None

    # Fallback for user-controlled roots whose label we don't recognise:
    # preserve the C-1 contract that any file under a user root produces an
    # "unknown" record so reviewers can decide whether it's a candidate.
    if spec.kind == "user":
        return ("unknown", "unknown", "unrecognised user root")
    return None


def _asset_type_for_script(rel: Path) -> str:
    name = rel.name.lower()
    if name.startswith("review-") or "review" in name:
        return "workflow"
    if name.endswith("-helper.sh") or "helper" in name:
        return "agent_procedure"
    return "agent_procedure"


def _name_from_path(spec: RootSpec, rel: Path) -> str:
    parts = rel.parts
    if rel.name == "SKILL.md" and len(parts) >= 2:
        return parts[-2]
    return rel.stem


def _slug_from_path(spec: RootSpec, rel: Path) -> str:
    parts = rel.parts
    if rel.name == "SKILL.md" and len(parts) >= 2:
        slug = parts[-2]
    else:
        slug = rel.stem
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", slug).strip("-").lower()
    return slug or "unknown"


def _risk_hints(detected_kind: str, path: Path, sniff: HeaderSniff) -> tuple[str, str]:
    if detected_kind in ("codex_skill", "claude_skill", "vendor_skill"):
        return ("low", "read_only")
    if detected_kind == "claude_hook":
        return ("medium", "write_local")
    if detected_kind == "claude_script":
        return ("low", "write_local")
    if detected_kind in ("project_script", "project_tool_script", "project_cli"):
        return ("low", "write_local")
    if detected_kind == "project_bin":
        return ("medium", "write_local")
    if detected_kind == "project_symlink":
        return ("unknown", "unknown")
    return ("unknown", "unknown")
