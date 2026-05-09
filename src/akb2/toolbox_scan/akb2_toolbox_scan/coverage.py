"""AKB2.0 coverage lookup. Read-only existence checks against the AKB tree."""

from __future__ import annotations

from pathlib import Path

from .discover import ScanRecord


PHASE_B_DIR = ("staging", "candidate", "toolbox-phase-b-2026-05-04")


def annotate_with_akb_coverage(records: list[ScanRecord], akb_root: Path) -> None:
    for r in records:
        slug = r.slug_candidate
        if not slug or slug == "unknown":
            continue
        r.registry_exists = (akb_root / "toolbox" / "registry" / f"{slug}.md").exists()
        r.interface_exists = (akb_root / "toolbox" / "interfaces" / f"{slug}.md").exists()
        r.eval_exists = (akb_root / "toolbox" / "evals" / f"{slug}.md").exists()
        phase_b = akb_root.joinpath(*PHASE_B_DIR) / slug
        r.phase_b_shadow_match = phase_b.exists() and phase_b.is_dir()
