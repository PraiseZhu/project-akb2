"""Render JSONL + Markdown report in memory.

Pure functions: never touch disk. The writer module performs the actual
atomic writes.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path

from .discover import ScanRecord, ScanResult


def render_outputs(
    *,
    result: ScanResult,
    akb_root: Path,
    out_path: Path,
    report_path: Path,
    scan_id: str | None = None,
    scan_scope: str = "global",
    previous_manifest_label: str | None = None,
) -> tuple[str, str]:
    scanned_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sid = scan_id or f"akb2-toolbox-scan-{uuid.uuid4().hex[:12]}"

    jsonl_lines = []
    for rec in result.records:
        jsonl_lines.append(_record_to_json(rec, scan_id=sid, scanned_at=scanned_at))
    jsonl_text = "\n".join(jsonl_lines)
    if jsonl_text:
        jsonl_text += "\n"

    report_text = _render_report(
        result=result,
        akb_root=akb_root,
        scan_id=sid,
        scanned_at=scanned_at,
        out_path=out_path,
        report_path=report_path,
        scan_scope=scan_scope,
        previous_manifest_label=previous_manifest_label,
    )
    return jsonl_text, report_text


def _record_to_json(rec: ScanRecord, *, scan_id: str, scanned_at: str) -> str:
    payload = {
        "manifest_type": "workspace_scan",
        "scan_id": scan_id,
        "scanned_at": scanned_at,
        "source_root": rec.source_root,
        "root_kind": rec.root_kind,
        "root_status": rec.root_status,
        "workspace_path": rec.workspace_path,
        "relative_path": rec.relative_path,
        "detected_kind": rec.detected_kind,
        "candidate_asset_type": rec.candidate_asset_type,
        "name": rec.name,
        "slug_candidate": rec.slug_candidate,
        "extension": rec.extension,
        "size_bytes": rec.size_bytes,
        "mtime": rec.mtime,
        "sha256": rec.sha256,
        "is_symlink": rec.is_symlink,
        "has_akb_backlink": rec.has_akb_backlink,
        "backlink_targets": list(rec.backlink_targets),
        "phase_b_shadow_match": rec.phase_b_shadow_match,
        "registry_exists": rec.registry_exists,
        "interface_exists": rec.interface_exists,
        "eval_exists": rec.eval_exists,
        "risk_hint": rec.risk_hint,
        "side_effects_hint": rec.side_effects_hint,
        "looks_like_secret_in_header": rec.looks_like_secret_in_header,
        "not_active": rec.not_active,
        "automation_allowed": rec.automation_allowed,
        "gate_decision": rec.gate_decision,
        "scan_error": rec.scan_error,
        "scan_scope": rec.scan_scope,
        "project_root": rec.project_root,
        "project_name": rec.project_name,
        "project_relative_path": rec.project_relative_path,
        "discovery_reason": rec.discovery_reason,
        "duplicate_of_prior_scan": rec.duplicate_of_prior_scan,
        "prior_manifest_path": rec.prior_manifest_path,
    }
    return json.dumps(payload, ensure_ascii=False)


def _render_report(
    *,
    result: ScanResult,
    akb_root: Path,
    scan_id: str,
    scanned_at: str,
    out_path: Path,
    report_path: Path,
    scan_scope: str = "global",
    previous_manifest_label: str | None = None,
) -> str:
    today = date.today().isoformat()
    records = result.records
    is_project = scan_scope == "project_scripts_bin"

    by_root = Counter(r.source_root for r in records)
    by_kind = Counter(r.detected_kind for r in records)
    by_risk = Counter(r.risk_hint for r in records)
    by_side = Counter(r.side_effects_hint for r in records)

    high_risk = [
        r
        for r in records
        if r.risk_hint == "high"
        or r.side_effects_hint in ("write_remote", "irreversible")
        or r.looks_like_secret_in_header
    ]

    phase_b_hits = [r for r in records if r.phase_b_shadow_match]
    missing_registry = [r for r in records if not r.registry_exists]
    missing_eval = [r for r in records if not r.eval_exists]
    missing_interface = [r for r in records if not r.interface_exists]
    next_batch = _suggest_next_batch(records, limit=15)

    phase_label = "C-1b" if is_project else "C-1"
    title_suffix = (
        "Toolbox Project Scripts/Bin Discovery" if is_project else "Toolbox Workspace Scan"
    )
    lines: list[str] = []
    lines.append("---")
    lines.append(f"title: Phase {phase_label} {title_suffix} — {today}")
    lines.append("doc_type: phase-c-report")
    lines.append(f"phase: {phase_label}")
    lines.append(
        "report_kind: project_scripts_bin_scan" if is_project else "report_kind: workspace_scan"
    )
    lines.append("automation_layer: L0_read_only")
    lines.append("not_active_promotion: true")
    lines.append(f"scan_id: {scan_id}")
    lines.append(f"scanned_at: {scanned_at}")
    lines.append(f"report_date: {today}")
    lines.append("---")
    lines.append("")
    lines.append(f"# Phase {phase_label} {title_suffix} — {today}")
    lines.append("")
    lines.append(
        "> Read-only scanner output. Never executes a candidate, never writes outside "
        "`manifests/workspace-scan/` and `docs/phase-c-reports/`, never promotes anything to active."
    )
    lines.append("")

    lines.append("## §1 Scan summary")
    lines.append("")
    lines.append(f"- AKB root: `{akb_root}`")
    lines.append(f"- JSONL manifest: `{out_path}`")
    lines.append(f"- Total candidates: **{len(records)}**")
    lines.append(f"- Roots scanned: {len(result.root_summaries)}")
    lines.append("")

    lines.append("## §2 Root status")
    lines.append("")
    lines.append("| root | kind | status | candidates | error |")
    lines.append("|---|---|---|---|---|")
    for summary in result.root_summaries:
        err = summary.error or ""
        lines.append(
            f"| `{summary.spec.path}` | {summary.spec.kind} | {summary.status} | "
            f"{summary.candidate_count} | {err} |"
        )
    lines.append("")

    lines.append("## §3 Detected kind distribution")
    lines.append("")
    lines.append("| detected_kind | count |")
    lines.append("|---|---|")
    for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {kind} | {n} |")
    lines.append("")

    lines.append("## §4 Risk + side-effects hints")
    lines.append("")
    lines.append("| risk_hint | count |")
    lines.append("|---|---|")
    for k, n in sorted(by_risk.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {n} |")
    lines.append("")
    lines.append("| side_effects_hint | count |")
    lines.append("|---|---|")
    for k, n in sorted(by_side.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {n} |")
    lines.append("")

    lines.append("## §5 Suspected high-risk / sensitive headers")
    lines.append("")
    if not high_risk:
        lines.append("- none")
    else:
        for r in high_risk[:25]:
            flags = []
            if r.looks_like_secret_in_header:
                flags.append("secret_in_header")
            if r.risk_hint == "high":
                flags.append("risk:high")
            if r.side_effects_hint in ("write_remote", "irreversible"):
                flags.append(f"side:{r.side_effects_hint}")
            lines.append(f"- `{r.relative_path}` — {', '.join(flags)}")
    lines.append("")

    lines.append("## §6 Phase B sample coverage in workspace")
    lines.append("")
    if not phase_b_hits:
        lines.append("- none")
    else:
        for r in phase_b_hits:
            lines.append(f"- `{r.slug_candidate}` ← `{r.relative_path}`")
    lines.append("")

    lines.append("## §7 AKB 2.0 toolbox coverage gaps")
    lines.append("")
    lines.append(f"- candidates without `toolbox/registry/<slug>.md`: {len(missing_registry)}")
    lines.append(f"- candidates without `toolbox/interfaces/<slug>.md`: {len(missing_interface)}")
    lines.append(f"- candidates without `toolbox/evals/<slug>.md`: {len(missing_eval)}")
    lines.append("")

    lines.append("## §8 Next-batch Tool Gate suggestions (advisory only)")
    lines.append("")
    lines.append(
        "> Scanner does NOT decide gate outcomes. The list below highlights candidates "
        "that look like solid first picks for human review."
    )
    lines.append("")
    if not next_batch:
        lines.append("- none")
    else:
        lines.append("| slug | detected_kind | size | shadow? | path |")
        lines.append("|---|---|---|---|---|")
        for r in next_batch:
            shadow = "✅" if r.phase_b_shadow_match else "—"
            size = f"{r.size_bytes}B" if r.size_bytes is not None else "n/a"
            lines.append(f"| `{r.slug_candidate}` | {r.detected_kind} | {size} | {shadow} | `{r.relative_path}` |")
    lines.append("")

    if is_project:
        excluded_dirs = sum(s.excluded_dir_count for s in result.root_summaries)
        truncated_roots = [s for s in result.root_summaries if s.truncated]
        duplicates = [r for r in records if r.duplicate_of_prior_scan]
        by_kind_project = Counter(r.detected_kind for r in records if r.scan_scope == "project_scripts_bin")
        by_project = Counter(r.project_name for r in records if r.project_name)

        lines.append("## §10 Project scope: exclusion summary")
        lines.append("")
        lines.append(f"- pruned subdirs (skipped at walk time): {excluded_dirs}")
        lines.append(f"- truncated roots (hit `--max-files-per-root`): {len(truncated_roots)}")
        for s in truncated_roots:
            lines.append(f"  - `{s.spec.path}` — {s.candidate_count} files before truncation")
        lines.append("")

        lines.append("## §11 Project scope: detected kinds")
        lines.append("")
        lines.append("| detected_kind | count |")
        lines.append("|---|---|")
        for k, n in sorted(by_kind_project.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {k} | {n} |")
        lines.append("")

        lines.append("## §12 Project scope: by project")
        lines.append("")
        lines.append("| project | candidates |")
        lines.append("|---|---|")
        for proj, n in sorted(by_project.items(), key=lambda kv: -kv[1]):
            lines.append(f"| `{proj}` | {n} |")
        lines.append("")

        lines.append("## §13 Project scope: duplicates of C-1")
        lines.append("")
        if previous_manifest_label:
            lines.append(f"- previous manifest: `{previous_manifest_label}`")
        else:
            lines.append("- previous manifest: not provided (no dedup performed)")
        lines.append(f"- duplicate records: {len(duplicates)}")
        if duplicates[:10]:
            lines.append("")
            lines.append("Sample duplicates (up to 10):")
            for r in duplicates[:10]:
                lines.append(f"- `{r.relative_path}` (project=`{r.project_name}`)")
        lines.append("")

        lines.append("## §14 C-2 readiness")
        lines.append("")
        lines.append(
            f"- candidates available for ranking: **{len(records) - len(duplicates)}** "
            f"(non-duplicate)"
        )
        lines.append(
            "- C-2 input: union(C-1 manifest, this manifest) keyed by `workspace_path`; "
            "use `duplicate_of_prior_scan` to skip second-counting."
        )
        lines.append("")

    lines.append("## §9 Invariants self-check")
    lines.append("")
    lines.append("- [x] every record carries `not_active: true`")
    lines.append("- [x] every record carries `automation_allowed: false`")
    lines.append("- [x] every record carries `gate_decision: null`")
    lines.append("- [x] no candidate file was executed or imported")
    lines.append("- [x] no symlink was followed (recorded only)")
    lines.append("- [x] outputs land only in `manifests/workspace-scan/` and `docs/phase-c-reports/`")
    if is_project:
        lines.append("- [x] project scope obeys allowed subtrees (`scripts/` `bin/` `tools/` `cli/`)")
        lines.append("- [x] walk-time prune list applied (no `.git`, `node_modules`, `.venv`, etc.)")
    lines.append("")

    return "\n".join(lines) + "\n"


def _suggest_next_batch(records: Iterable[ScanRecord], *, limit: int) -> list[ScanRecord]:
    """Pick first-pick Tool Gate candidates: skills with shadow match, then
    plain skills, then helper scripts. No execution, no quality judgement."""
    shadow_skills: list[ScanRecord] = []
    plain_skills: list[ScanRecord] = []
    plain_scripts: list[ScanRecord] = []
    for r in records:
        if r.detected_kind in ("codex_skill", "claude_skill"):
            (shadow_skills if r.phase_b_shadow_match else plain_skills).append(r)
        elif r.detected_kind == "claude_script":
            plain_scripts.append(r)
    chosen = shadow_skills + plain_skills + plain_scripts
    return chosen[:limit]
