"""Phase C-2 candidate ranking.

Read C-1 / C-1b workspace-scan manifests, dedupe by (workspace_path, sha256,
slug_candidate), score on a deterministic matrix, and assign every candidate
to one of four tiers:

    pilot_next       — recommended for the next C-3 small batch
    manual_review    — needs a human look before C-3
    defer            — valid candidate, just not the priority for C-3
    exclude_this_round — unsafe / unreadable / generated; not an active
                          rejection, just skipped this round

Hard contracts:
  * Read-only (no candidate execution, no import).
  * Outputs only land in `manifests/toolbox-ranking/` and
    `docs/phase-c-reports/`.
  * Every output record carries `not_active: true`,
    `automation_allowed: false`, `gate_decision: null`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from .paths import PathValidationError, validate_ranking_paths
from .writer import PairedWriteError, write_output_pair

# ---------------------------------------------------------------------------
# Scoring + tier dictionaries
# ---------------------------------------------------------------------------

ASSET_KIND_PRIORITY = {
    # higher = preferred when deduping the same logical candidate
    "phase_b_shadow": 100,
    "codex_skill":     90,
    "claude_skill":    85,
    "vendor_skill":    60,
    "project_cli":     55,
    "project_bin":     50,
    "project_script":  45,
    "project_tool_script": 45,
    "claude_script":   40,
    "claude_hook":     30,
    "project_symlink": 5,
    "unknown":         1,
}

TIERS = ("pilot_next", "manual_review", "defer", "exclude_this_round")

# Hard exclusion reasons — any one routes the record to exclude_this_round.
EXCLUDE_REASONS = {
    "scan_error",
    "symlink",
    "secret_in_header",
    "duplicate_secondary",
}

# Hard not-pilot reasons — record stays a valid candidate but cannot enter
# pilot_next without explicit human approval.
NO_PILOT_REASONS = {
    "medium_risk_hook",
    "high_risk_or_irreversible",
    "missing_fingerprint",
    "unknown_kind",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DuplicateGroup:
    group_id: str
    key: str  # the dedupe key that grouped them
    workspace_paths: list[str] = field(default_factory=list)
    preferred_workspace_path: str = ""


@dataclass
class ScoredRecord:
    record: dict
    score_total: int
    score_dimensions: dict[str, int]
    rank_reason: list[str]
    tier: str = "defer"
    next_action: str = ""
    rank: int = 0
    duplicate_group_id: str | None = None
    preferred_record: bool = True
    source_manifest: str = ""


# ---------------------------------------------------------------------------
# Loading + validation
# ---------------------------------------------------------------------------


class RankingError(Exception):
    pass


def load_manifests(paths: Iterable[Path]) -> list[dict]:
    """Read every JSONL manifest. Each loaded record is annotated with the
    manifest path it came from (`_source_manifest`).

    Raises RankingError on an empty input list, missing file, or unparsable
    line. The plan asks us to fail closed when manifest input is broken so
    the ranking output can never be silently wrong.
    """
    paths = list(paths)
    if not paths:
        raise RankingError("at least one --manifest path is required")
    out: list[dict] = []
    for p in paths:
        if not p.exists():
            raise RankingError(f"manifest does not exist: {p}")
        for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RankingError(f"invalid JSON in {p}:{lineno}: {exc}") from exc
            if "not_active" not in rec:
                raise RankingError(
                    f"record in {p}:{lineno} is missing 'not_active'; refusing to rank"
                )
            rec["_source_manifest"] = str(p)
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def dedupe_candidates(records: list[dict]) -> tuple[list[dict], list[DuplicateGroup]]:
    """Group records by composite key. The first key with >=2 members wins,
    in this order:

        1. workspace_path  (exact same file on disk)
        2. sha256          (same content under a different path)
        3. slug_candidate  (same logical tool name across roots)

    For each group, pick a preferred record using ASSET_KIND_PRIORITY and
    a Phase B shadow tie-breaker. Non-preferred records keep their data
    but are flagged `preferred_record=False` and assigned the same
    `duplicate_group_id` so the report can audit them.
    """
    if not records:
        return [], []

    # First pass: assign group ids.
    groups: dict[tuple[str, str], list[int]] = {}
    for idx, rec in enumerate(records):
        keyed = False
        for key_name in ("workspace_path", "sha256", "slug_candidate"):
            value = rec.get(key_name)
            if not value:
                continue
            groups.setdefault((key_name, value), []).append(idx)
            keyed = True
        if not keyed:
            groups.setdefault(("uncategorised", str(idx)), []).append(idx)

    # Collapse: each record may end up in multiple group keys above; we want
    # the strongest signal (workspace_path) to win. Walk record-by-record and
    # find their first non-singleton group via the priority order.
    record_group: dict[int, tuple[str, str]] = {}
    for idx, rec in enumerate(records):
        for key_name in ("workspace_path", "sha256", "slug_candidate"):
            value = rec.get(key_name)
            if not value:
                continue
            members = groups.get((key_name, value), [])
            if len(members) >= 2:
                record_group[idx] = (key_name, value)
                break
        else:
            record_group[idx] = ("uncategorised", str(idx))

    # Materialise groups + pick preferred per group.
    by_group: dict[tuple[str, str], list[int]] = {}
    for idx, key in record_group.items():
        by_group.setdefault(key, []).append(idx)

    duplicate_groups: list[DuplicateGroup] = []
    for key, indices in by_group.items():
        if len(indices) <= 1:
            # Singleton — annotate but no DuplicateGroup record needed.
            rec = records[indices[0]]
            rec["_duplicate_group_id"] = None
            rec["_preferred_record"] = True
            continue
        # Preferred = highest ASSET_KIND_PRIORITY, tie-break by phase_b_shadow_match,
        # then by has_akb_backlink, then by workspace_path lexical order for stability.
        def _rank_key(i: int) -> tuple[int, int, int, str]:
            r = records[i]
            return (
                -ASSET_KIND_PRIORITY.get(r.get("detected_kind", "unknown"), 0),
                0 if r.get("phase_b_shadow_match") else 1,
                0 if r.get("has_akb_backlink") else 1,
                r.get("workspace_path", ""),
            )

        ordered = sorted(indices, key=_rank_key)
        preferred_idx = ordered[0]
        gid = _stable_group_id(key, [records[i] for i in indices])
        for i in indices:
            r = records[i]
            r["_duplicate_group_id"] = gid
            r["_preferred_record"] = i == preferred_idx
        duplicate_groups.append(
            DuplicateGroup(
                group_id=gid,
                key=f"{key[0]}={key[1]}",
                workspace_paths=[records[i].get("workspace_path", "") for i in indices],
                preferred_workspace_path=records[preferred_idx].get("workspace_path", ""),
            )
        )

    # Stable order across runs: sort the returned DuplicateGroup list by group_id.
    duplicate_groups.sort(key=lambda g: g.group_id)
    return records, duplicate_groups


def _stable_group_id(key: tuple[str, str], members: list[dict]) -> str:
    """Derive a deterministic 8-hex group id from the dedupe key + sorted
    member identifiers. Same input -> same id across runs.
    """
    parts: list[str] = [key[0], key[1]]
    member_ids = sorted(
        f"{r.get('workspace_path', '')}|{r.get('sha256', '')}|{r.get('slug_candidate', '')}"
        for r in members
    )
    parts.extend(member_ids)
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return f"dup-{digest[:8]}"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_candidate(record: dict) -> ScoredRecord:
    dims: dict[str, int] = {}
    reasons: list[str] = []

    # Phase B shadow is the strongest positive signal.
    if record.get("phase_b_shadow_match"):
        dims["phase_b_shadow"] = 30
        reasons.append("phase_b_shadow_match")
    else:
        dims["phase_b_shadow"] = 0

    # Risk hint: low > medium > high; "unknown" treated mildly negative.
    risk = record.get("risk_hint") or "unknown"
    risk_scores = {"low": 8, "medium": 0, "high": -10, "unknown": -2, "safe": 10}
    dims["risk"] = risk_scores.get(risk, -2)
    if risk == "high":
        reasons.append("risk:high")
    elif risk == "unknown":
        reasons.append("risk:unknown")

    # Side effects: read_only > write_local > write_remote > irreversible.
    side = record.get("side_effects_hint") or "unknown"
    side_scores = {
        "read_only": 8,
        "write_local": 2,
        "write_remote": -8,
        "irreversible": -15,
        "unknown": -2,
    }
    dims["side_effects"] = side_scores.get(side, -2)
    if side in ("write_remote", "irreversible"):
        reasons.append(f"side:{side}")

    # Asset kind: skill > workflow > agent_procedure > prompt > checklist.
    kind = record.get("detected_kind") or "unknown"
    dims["asset_kind"] = max(0, ASSET_KIND_PRIORITY.get(kind, 0) // 10)

    # AKB backlink: tiny boost (already wired into AKB).
    if record.get("has_akb_backlink"):
        dims["akb_backlink"] = 3
        reasons.append("has_akb_backlink")
    else:
        dims["akb_backlink"] = 0

    # Coverage gap: small boost — we want to fill empty registry/eval slots.
    coverage_gap = (
        not record.get("registry_exists")
        and not record.get("interface_exists")
        and not record.get("eval_exists")
    )
    dims["coverage_gap"] = 2 if coverage_gap else 0

    # Secret header: heavy penalty + flag.
    if record.get("looks_like_secret_in_header"):
        dims["secret_header"] = -20
        reasons.append("looks_like_secret_in_header")
    else:
        dims["secret_header"] = 0

    # Scan error / symlink: cannot fingerprint -> not pilot material.
    scan_error = record.get("scan_error")
    if scan_error:
        dims["scan_error"] = -25
        reasons.append(f"scan_error:{scan_error}")
    else:
        dims["scan_error"] = 0

    if record.get("is_symlink"):
        dims["symlink"] = -10
        reasons.append("is_symlink")
    else:
        dims["symlink"] = 0

    # Size band: very small or very large is suspicious.
    size = record.get("size_bytes")
    if size is None:
        dims["size_band"] = -2
    elif size < 100:
        dims["size_band"] = -3
        reasons.append("size:tiny")
    elif size > 200_000:
        dims["size_band"] = -2
        reasons.append("size:large")
    else:
        dims["size_band"] = 4

    # Project source bias: project_* is real but slightly lower than dedicated
    # skills because it has not been packaged as a skill yet.
    if (record.get("scan_scope") == "project_scripts_bin"
        or kind in ("project_script", "project_tool_script", "project_cli", "project_bin")):
        dims["project_source"] = -2
        reasons.append("project_source")
    else:
        dims["project_source"] = 0

    # Duplicate-secondary: a dedupe loser in a group must rank below winner.
    if record.get("_duplicate_group_id") and not record.get("_preferred_record", True):
        dims["duplicate_secondary"] = -50
        reasons.append("duplicate_secondary")
    else:
        dims["duplicate_secondary"] = 0

    score_total = sum(dims.values())
    return ScoredRecord(
        record=record,
        score_total=score_total,
        score_dimensions=dims,
        rank_reason=reasons,
        source_manifest=record.get("_source_manifest", ""),
    )


# ---------------------------------------------------------------------------
# Tier assignment
# ---------------------------------------------------------------------------


def assign_tiers(scored: list[ScoredRecord], *, pilot_limit: int) -> None:
    """Mutate `scored` in place: set tier, next_action, rank."""
    # Step 1: tag hard excludes / hard no-pilot reasons up front.
    #
    # ORDERING NOTE: dedupe-secondary MUST be the first hard-exclude check.
    # A record that is both a dedupe loser AND carries a secret-in-header
    # flag should still land in exclude_this_round, not manual_review —
    # the dedupe winner already represents the candidate, so the loser has
    # no value as a manual triage row.
    hard_excluded: list[ScoredRecord] = []
    hard_no_pilot: list[ScoredRecord] = []
    rest: list[ScoredRecord] = []
    for s in scored:
        rec = s.record
        # Hard exclude routes (in priority order):
        if rec.get("_duplicate_group_id") and not rec.get("_preferred_record", True):
            s.tier = "exclude_this_round"
            s.next_action = "skip; duplicate of preferred candidate in same group"
            hard_excluded.append(s)
            continue
        if rec.get("scan_error"):
            s.tier = "exclude_this_round"
            s.next_action = "skip; cannot fingerprint this round"
            hard_excluded.append(s)
            continue
        if rec.get("is_symlink"):
            s.tier = "exclude_this_round"
            s.next_action = "skip; symlink not followed (record only)"
            hard_excluded.append(s)
            continue
        # Hard no-pilot routes:
        if rec.get("looks_like_secret_in_header"):
            s.tier = "manual_review"
            s.next_action = "human review for secret-in-header flag before C-3"
            hard_no_pilot.append(s)
            continue
        if rec.get("detected_kind") == "claude_hook":
            s.tier = "manual_review"
            s.next_action = "manual review (hook = medium risk by default)"
            hard_no_pilot.append(s)
            continue
        if rec.get("detected_kind") == "unknown":
            s.tier = "manual_review"
            s.next_action = "manual review (unknown asset kind)"
            hard_no_pilot.append(s)
            continue
        if rec.get("risk_hint") == "high" or rec.get("side_effects_hint") in (
            "write_remote",
            "irreversible",
        ):
            s.tier = "manual_review"
            s.next_action = "manual review (high risk or remote/irreversible side effects)"
            hard_no_pilot.append(s)
            continue
        rest.append(s)

    # Step 2: rank the rest; top N -> pilot_next, the remainder -> defer.
    rest.sort(
        key=lambda s: (
            -s.score_total,
            0 if s.record.get("phase_b_shadow_match") else 1,
            -ASSET_KIND_PRIORITY.get(s.record.get("detected_kind", "unknown"), 0),
            s.record.get("slug_candidate", ""),
            s.record.get("workspace_path", ""),
        )
    )

    pilot_count = 0
    for s in rest:
        if pilot_count < pilot_limit:
            s.tier = "pilot_next"
            s.next_action = "C-3 candidate: build draft registry/interface/eval"
            pilot_count += 1
        else:
            s.tier = "defer"
            s.next_action = "defer; revisit after C-3 first batch"

    # Step 3: assign final ranks across the whole population for stable output.
    everything = sorted(
        scored,
        key=lambda s: (
            _tier_order(s.tier),
            -s.score_total,
            0 if s.record.get("phase_b_shadow_match") else 1,
            s.record.get("slug_candidate", ""),
            s.record.get("workspace_path", ""),
        ),
    )
    for i, s in enumerate(everything, 1):
        s.rank = i


def _tier_order(tier: str) -> int:
    return {"pilot_next": 0, "manual_review": 1, "defer": 2, "exclude_this_round": 3}.get(tier, 9)


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------


def render_ranking_outputs(
    *,
    scored: list[ScoredRecord],
    duplicate_groups: list[DuplicateGroup],
    akb_root: Path,
    out_path: Path,
    report_path: Path,
    input_manifests: list[Path],
    pilot_limit: int,
    operation_id: str | None = None,
) -> tuple[str, str]:
    op_id = operation_id or f"akb2-c2-rank-{uuid.uuid4().hex[:12]}"
    ranked_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # JSONL: every input record gets a ranking line.
    lines: list[str] = []
    for s in scored:
        lines.append(_record_to_json(s, operation_id=op_id, ranked_at=ranked_at))
    jsonl_text = "\n".join(lines) + ("\n" if lines else "")

    report_text = _render_report(
        scored=scored,
        duplicate_groups=duplicate_groups,
        akb_root=akb_root,
        out_path=out_path,
        report_path=report_path,
        input_manifests=input_manifests,
        pilot_limit=pilot_limit,
        operation_id=op_id,
        ranked_at=ranked_at,
    )
    return jsonl_text, report_text


def _record_to_json(s: ScoredRecord, *, operation_id: str, ranked_at: str) -> str:
    rec = s.record
    payload = {
        "manifest_type": "toolbox_ranking",
        "operation_id": operation_id,
        "ranked_at": ranked_at,
        "rank": s.rank,
        "tier": s.tier,
        "score_total": s.score_total,
        "score_dimensions": dict(s.score_dimensions),
        "rank_reason": list(s.rank_reason),
        "next_action": s.next_action,
        "duplicate_group_id": rec.get("_duplicate_group_id"),
        "preferred_record": rec.get("_preferred_record", True),
        "source_manifest": rec.get("_source_manifest", ""),
        # Carry through the source fields for downstream C-3 consumers.
        "workspace_path": rec.get("workspace_path"),
        "relative_path": rec.get("relative_path"),
        "detected_kind": rec.get("detected_kind"),
        "candidate_asset_type": rec.get("candidate_asset_type"),
        "name": rec.get("name"),
        "slug_candidate": rec.get("slug_candidate"),
        "extension": rec.get("extension"),
        "size_bytes": rec.get("size_bytes"),
        "mtime": rec.get("mtime"),
        "sha256": rec.get("sha256"),
        "is_symlink": rec.get("is_symlink", False),
        "has_akb_backlink": rec.get("has_akb_backlink", False),
        "phase_b_shadow_match": rec.get("phase_b_shadow_match", False),
        "registry_exists": rec.get("registry_exists", False),
        "interface_exists": rec.get("interface_exists", False),
        "eval_exists": rec.get("eval_exists", False),
        "risk_hint": rec.get("risk_hint"),
        "side_effects_hint": rec.get("side_effects_hint"),
        "looks_like_secret_in_header": rec.get("looks_like_secret_in_header", False),
        "scan_error": rec.get("scan_error"),
        "scan_scope": rec.get("scan_scope", "global"),
        "project_name": rec.get("project_name"),
        "project_relative_path": rec.get("project_relative_path"),
        # Hard invariants — never derived from input.
        "not_active": True,
        "automation_allowed": False,
        "gate_decision": None,
    }
    return json.dumps(payload, ensure_ascii=False)


def _render_report(
    *,
    scored: list[ScoredRecord],
    duplicate_groups: list[DuplicateGroup],
    akb_root: Path,
    out_path: Path,
    report_path: Path,
    input_manifests: list[Path],
    pilot_limit: int,
    operation_id: str,
    ranked_at: str,
) -> str:
    today = date.today().isoformat()
    by_tier = Counter(s.tier for s in scored)
    by_kind = Counter(s.record.get("detected_kind") for s in scored)
    pilot = [s for s in scored if s.tier == "pilot_next"]
    manual = [s for s in scored if s.tier == "manual_review"]
    excluded = [s for s in scored if s.tier == "exclude_this_round"]

    lines: list[str] = []
    lines.append("---")
    lines.append(f"title: Phase C-2 Toolbox Candidate Ranking — {today}")
    lines.append("doc_type: phase-c-report")
    lines.append("phase: C-2")
    lines.append("report_kind: toolbox_candidate_ranking")
    lines.append("automation_layer: L0_read_only")
    lines.append("not_active_promotion: true")
    lines.append(f"operation_id: {operation_id}")
    lines.append(f"ranked_at: {ranked_at}")
    lines.append(f"report_date: {today}")
    lines.append("---")
    lines.append("")
    lines.append(f"# Phase C-2 Toolbox Candidate Ranking — {today}")
    lines.append("")
    lines.append(
        "> Read-only ranking. Never executes a candidate, never promotes anything to active, "
        "and never modifies workspace tool source files."
    )
    lines.append("")

    lines.append("## §1 Inputs")
    lines.append("")
    lines.append(f"- AKB root: `{akb_root}`")
    lines.append(f"- Output JSONL: `{out_path}`")
    lines.append(f"- Pilot limit: {pilot_limit}")
    lines.append("- Source manifests:")
    for m in input_manifests:
        lines.append(f"  - `{m}`")
    lines.append("")

    lines.append("## §2 Population")
    lines.append("")
    lines.append(f"- Total records ingested: **{len(scored)}**")
    lines.append(f"- Duplicate groups detected: {len(duplicate_groups)}")
    deduped_unique = sum(1 for s in scored if s.record.get("_preferred_record", True))
    lines.append(f"- Unique candidates after dedupe: {deduped_unique}")
    lines.append("")

    lines.append("## §3 Tier distribution")
    lines.append("")
    lines.append("| tier | count |")
    lines.append("|---|---|")
    for tier in TIERS:
        lines.append(f"| {tier} | {by_tier.get(tier, 0)} |")
    lines.append("")

    lines.append("## §4 Detected kind distribution (informational)")
    lines.append("")
    lines.append("| detected_kind | count |")
    lines.append("|---|---|")
    for kind, n in by_kind.most_common():
        lines.append(f"| {kind or 'unknown'} | {n} |")
    lines.append("")

    lines.append("## §5 Pilot batch (top of pilot_next)")
    lines.append("")
    lines.append(
        "> Recommended for C-3 small-batch drafting. Each record stays "
        "`not_active=true`; C-3 must still walk Tool Intake Gate."
    )
    lines.append("")
    if not pilot:
        lines.append("- empty (no records cleared the safety filter this round)")
    else:
        lines.append("| rank | tier | score | kind | slug | reason |")
        lines.append("|---|---|---|---|---|---|")
        for s in pilot:
            slug = s.record.get("slug_candidate") or s.record.get("name") or "?"
            kind = s.record.get("detected_kind") or "?"
            reason = ", ".join(s.rank_reason) or "—"
            lines.append(
                f"| {s.rank} | {s.tier} | {s.score_total} | {kind} | `{slug}` | {reason} |"
            )
    lines.append("")

    lines.append("## §6 Manual review queue")
    lines.append("")
    if not manual:
        lines.append("- none")
    else:
        lines.append(f"- queue size: {len(manual)} (capped sample below: 25)")
        lines.append("")
        lines.append("| score | kind | slug | reason | path |")
        lines.append("|---|---|---|---|---|")
        for s in manual[:25]:
            slug = s.record.get("slug_candidate") or s.record.get("name") or "?"
            kind = s.record.get("detected_kind") or "?"
            reason = ", ".join(s.rank_reason) or "—"
            rel = s.record.get("relative_path") or s.record.get("workspace_path") or "?"
            lines.append(f"| {s.score_total} | {kind} | `{slug}` | {reason} | `{rel}` |")
    lines.append("")

    lines.append("## §7 Excluded this round (NOT a permanent rejection)")
    lines.append("")
    lines.append(
        "> `exclude_this_round` only means this candidate is skipped for the **next C-3 batch**. "
        "It is **not** a permanent rejection, **not** a deletion, and **not** a permanent ban. "
        "Future ranking rounds may revisit any record here."
    )
    lines.append("")
    if not excluded:
        lines.append("- none")
    else:
        # Sample by reason for clarity.
        sampled: dict[str, list[ScoredRecord]] = {}
        for s in excluded:
            tag = s.next_action.split(";", 1)[0].strip() or "skip"
            sampled.setdefault(tag, []).append(s)
        lines.append("| reason | count | sample |")
        lines.append("|---|---|---|")
        for reason, items in sorted(sampled.items(), key=lambda kv: -len(kv[1])):
            sample = ", ".join(
                f"`{(it.record.get('slug_candidate') or it.record.get('name') or '?')}`"
                for it in items[:3]
            )
            lines.append(f"| {reason} | {len(items)} | {sample} |")
    lines.append("")

    if duplicate_groups:
        lines.append("## §8 Duplicate groups (preferred kept, rest demoted)")
        lines.append("")
        lines.append(f"- groups: {len(duplicate_groups)} (sample below: 10)")
        lines.append("")
        for g in duplicate_groups[:10]:
            lines.append(
                f"- `{g.group_id}` keyed on `{g.key}`; "
                f"preferred = `{g.preferred_workspace_path}`"
            )
            for wp in g.workspace_paths[:6]:
                marker = "★" if wp == g.preferred_workspace_path else " "
                lines.append(f"   {marker} `{wp}`")
        lines.append("")
    else:
        lines.append("## §8 Duplicate groups")
        lines.append("")
        lines.append("- none")
        lines.append("")

    lines.append("## §9 C-3 recommendation")
    lines.append("")
    if pilot:
        lines.append(
            f"- Take 5-10 from the top of §5 (pilot_next, currently {len(pilot)}) into a "
            "C-3 small batch. Each picked record gets a draft registry / interface / eval; "
            "no record is allowed to skip Tool Intake Gate by virtue of a high C-2 score."
        )
    else:
        lines.append(
            "- Pilot batch is empty; do not start C-3 until the safety filter "
            "has at least one survivor."
        )
    lines.append(
        "- `manual_review` queue (§6) needs human triage before any of those records can move "
        "into a future pilot batch."
    )
    lines.append(
        "- `exclude_this_round` (§7) is a per-round skip flag, not a verdict — re-rank as new "
        "evidence arrives."
    )
    lines.append("")

    lines.append("## §10 Invariants self-check")
    lines.append("")
    lines.append("- [x] every record carries `not_active: true`")
    lines.append("- [x] every record carries `automation_allowed: false`")
    lines.append("- [x] every record carries `gate_decision: null`")
    lines.append("- [x] no candidate file was executed or imported")
    lines.append(
        "- [x] outputs land only in `manifests/toolbox-ranking/` "
        "and `docs/phase-c-reports/`"
    )
    lines.append("- [x] `exclude_this_round` is a per-round skip, not a deletion")
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="akb2-toolbox-rank",
        description="Phase C-2 read-only candidate ranking",
    )
    p.add_argument("--akb-root", required=True)
    p.add_argument(
        "--manifest",
        action="append",
        default=[],
        required=False,
        help="C-1/C-1b workspace-scan JSONL (repeatable, at least one required)",
    )
    p.add_argument("--out", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--pilot-limit", type=int, default=20)
    args = p.parse_args(argv)

    if not args.manifest:
        print("[fatal] at least one --manifest is required", file=sys.stderr)
        return 2

    try:
        validated = validate_ranking_paths(Path(args.akb_root), args.out, args.report)
    except PathValidationError as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 2

    manifest_paths = [Path(m).expanduser().resolve() for m in args.manifest]

    try:
        records = load_manifests(manifest_paths)
    except RankingError as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 2

    if not records:
        # Empty input is not an error; we still emit an empty manifest + a
        # report flagged "no_candidates" for traceability.
        empty_jsonl = ""
        empty_report = _empty_report(
            akb_root=validated.akb_root,
            out_path=validated.out_path,
            input_manifests=manifest_paths,
            pilot_limit=args.pilot_limit,
        )
        try:
            write_output_pair(
                jsonl_text=empty_jsonl,
                report_text=empty_report,
                out_path=validated.out_path,
                report_path=validated.report_path,
            )
        except PairedWriteError as exc:
            print(f"[fatal] {exc}", file=sys.stderr)
            return 3
        print("[ok] no candidates; emitted no_candidates report")
        return 0

    records, duplicate_groups = dedupe_candidates(records)
    scored = [score_candidate(r) for r in records]
    assign_tiers(scored, pilot_limit=args.pilot_limit)

    jsonl_text, report_text = render_ranking_outputs(
        scored=scored,
        duplicate_groups=duplicate_groups,
        akb_root=validated.akb_root,
        out_path=validated.out_path,
        report_path=validated.report_path,
        input_manifests=manifest_paths,
        pilot_limit=args.pilot_limit,
    )

    try:
        pair = write_output_pair(
            jsonl_text=jsonl_text,
            report_text=report_text,
            out_path=validated.out_path,
            report_path=validated.report_path,
        )
    except PairedWriteError as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 3

    pilot_count = sum(1 for s in scored if s.tier == "pilot_next")
    print(f"[ok] ranked {len(scored)} records → {pair.out_path}")
    print(f"[ok] pilot_next: {pilot_count} (limit {args.pilot_limit})")
    print(f"[ok] report → {pair.report_path}")
    return 0


def _empty_report(
    *,
    akb_root: Path,
    out_path: Path,
    input_manifests: list[Path],
    pilot_limit: int,
) -> str:
    today = date.today().isoformat()
    lines = [
        "---",
        f"title: Phase C-2 Toolbox Candidate Ranking — {today}",
        "doc_type: phase-c-report",
        "phase: C-2",
        "report_kind: toolbox_candidate_ranking",
        "automation_layer: L0_read_only",
        "not_active_promotion: true",
        "ranking_status: no_candidates",
        f"report_date: {today}",
        "---",
        "",
        f"# Phase C-2 Toolbox Candidate Ranking — {today}",
        "",
        (
            "> No candidates were found in the supplied manifests; "
            "ranking aborted with `no_candidates`."
        ),
        "",
        "## Inputs",
        f"- AKB root: `{akb_root}`",
        f"- Pilot limit: {pilot_limit}",
        "- Source manifests:",
    ]
    for m in input_manifests:
        lines.append(f"  - `{m}`")
    lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
