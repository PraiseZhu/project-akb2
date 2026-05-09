"""Strict output-path validation. All checks happen before any write."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MANIFEST_SUBDIR = ("manifests", "workspace-scan")
RANKING_SUBDIR = ("manifests", "toolbox-ranking")
REPORT_SUBDIR = ("docs", "phase-c-reports")


@dataclass(frozen=True)
class ValidatedPaths:
    akb_root: Path
    out_path: Path
    report_path: Path


class PathValidationError(ValueError):
    """Raised when a CLI path argument is outside the allowed AKB2.0 subtree."""


def _ensure_strict_subpath(target: Path, root: Path, subdir: tuple[str, ...], label: str) -> None:
    parent = root.joinpath(*subdir).resolve()
    try:
        target.relative_to(parent)
    except ValueError as exc:
        raise PathValidationError(
            f"--{label} must live strictly under {'/'.join(subdir)}/ relative to akb-root, "
            f"got: {target}"
        ) from exc
    if target == parent:
        raise PathValidationError(
            f"--{label} must be a file inside {'/'.join(subdir)}/, not the directory itself"
        )


def validate_output_paths(akb_root: Path, out: str, report: str) -> ValidatedPaths:
    """Resolve and validate both output paths up-front. Raises on any escape."""
    return _validate_paths(akb_root, out, report, MANIFEST_SUBDIR)


def validate_ranking_paths(akb_root: Path, out: str, report: str) -> ValidatedPaths:
    """Variant for the C-2 ranking output (manifests/toolbox-ranking/)."""
    return _validate_paths(akb_root, out, report, RANKING_SUBDIR)


def _validate_paths(
    akb_root: Path, out: str, report: str, manifest_subdir: tuple[str, ...]
) -> ValidatedPaths:
    akb_root_resolved = akb_root.expanduser().resolve()
    if not akb_root_resolved.exists() or not akb_root_resolved.is_dir():
        raise PathValidationError(f"--akb-root does not exist or is not a dir: {akb_root_resolved}")

    out_path = (akb_root_resolved / out).resolve()
    report_path = (akb_root_resolved / report).resolve()

    _ensure_strict_subpath(out_path, akb_root_resolved, manifest_subdir, "out")
    _ensure_strict_subpath(report_path, akb_root_resolved, REPORT_SUBDIR, "report")

    return ValidatedPaths(akb_root=akb_root_resolved, out_path=out_path, report_path=report_path)
