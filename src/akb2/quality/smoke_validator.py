"""Isolated smoke validator for AKB2 main quality pipeline.

This module is read-only: it inspects ledger/grader artifacts after a manual
`--once` run and never loads or starts launchd.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml


LAUNCHD_LABEL = "com.akb2.main-pipeline"
TERMINAL_EVENTS = {"grade_llm", "promote", "finalize"}
SENSITIVE_PATH_MARKERS = (
    "/Users/praise/Obsidian",
    "/Users/praise/Library/Mobile Documents",
)
DEFAULT_SOURCE_LEDGER_MAX_AGE_HOURS = 72
SOURCE_FAILURE_CLASSES = {
    "config_stale",
    "network_timeout",
    "anti_bot_or_edge_block",
    "browser_needed",
    "dead",
}


class SmokeValidationError(RuntimeError):
    """Smoke validation failed."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SmokeValidationError(f"ledger missing: {path}")
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SmokeValidationError(f"ledger line {line_no} is not JSON") from exc
        if not isinstance(row, dict):
            raise SmokeValidationError(f"ledger line {line_no} is not object")
        rows.append(row)
    if not rows:
        raise SmokeValidationError("ledger is empty")
    return rows


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise SmokeValidationError(f"json missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SmokeValidationError(f"json is invalid: {path}") from exc


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        raise SmokeValidationError(f"yaml missing: {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SmokeValidationError(f"yaml is invalid: {path}") from exc


def _parse_utc_ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise SmokeValidationError(f"invalid timestamp: {value}") from exc


def _iter_path_values(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.endswith("_path") or key in {"path", "candidate_path", "raw_path"}:
                if isinstance(child, str):
                    paths.append(child)
            paths.extend(_iter_path_values(child))
    elif isinstance(value, list):
        for child in value:
            paths.extend(_iter_path_values(child))
    return paths


def _assert_paths_under_root(rows: list[dict[str, Any]], vault_root: Path) -> None:
    root = vault_root.expanduser().resolve()
    if root.name != "AKB2.0":
        raise SmokeValidationError(f"vault_root must end with AKB2.0: {root}")
    for row in rows:
        for raw_path in _iter_path_values(row.get("details", {})):
            if not raw_path.startswith("/"):
                continue
            if any(marker in raw_path for marker in SENSITIVE_PATH_MARKERS):
                raise SmokeValidationError(f"path points at real vault: {raw_path}")
            try:
                resolved = Path(raw_path).expanduser().resolve()
            except OSError:
                resolved = Path(raw_path).expanduser().absolute()
            if root not in (resolved, *resolved.parents):
                raise SmokeValidationError(
                    f"path outside isolated vault root: {resolved} (root={root})"
                )


def _collect_event_paths(rows: list[dict[str, Any]], event: str) -> list[Path]:
    found: list[Path] = []
    for row in rows:
        if row.get("event") != event:
            continue
        for raw_path in _iter_path_values(row.get("details", {})):
            if raw_path.startswith("/"):
                found.append(Path(raw_path))
    return found


def _find_latest_candidate_path(
    rows: list[dict[str, Any]],
    vault_root: Path,
) -> Path:
    candidates: list[str] = []
    for row in rows:
        if row.get("event") == "distill_compose":
            details = row.get("details", {})
            candidate_path = details.get("candidate_path")
            if isinstance(candidate_path, str):
                candidates.append(candidate_path)
    if not candidates:
        raise SmokeValidationError("missing distill_compose candidate_path")
    candidate = Path(candidates[-1]).expanduser()
    if not candidate.is_absolute():
        candidate = vault_root / candidate
    candidate = candidate.resolve()
    root = vault_root.expanduser().resolve()
    if root not in (candidate, *candidate.parents):
        raise SmokeValidationError(f"candidate outside isolated root: {candidate}")
    if not candidate.exists():
        raise SmokeValidationError(f"candidate missing: {candidate}")
    return candidate


def _find_latest_raw_path(rows: list[dict[str, Any]], vault_root: Path) -> Path | None:
    raw_paths: list[str] = []
    for row in rows:
        if row.get("event") == "intake_raw":
            raw_path = row.get("details", {}).get("raw_path")
            if isinstance(raw_path, str):
                raw_paths.append(raw_path)
    if not raw_paths:
        return None
    raw = Path(raw_paths[-1]).expanduser()
    if not raw.is_absolute():
        raw = vault_root / raw
    return raw.resolve()


def _find_grader_result_path(vault_root: Path, candidate_path: Path) -> Path:
    return (
        vault_root.expanduser().resolve()
        / "staging"
        / "grader-results"
        / f"{candidate_path.stem}.grader.json"
    )


def _assert_grader_result_pass(
    vault_root: Path,
    candidate_path: Path,
) -> dict[str, Any]:
    grader_path = _find_grader_result_path(vault_root, candidate_path)
    data = _read_json(grader_path)
    if not isinstance(data, dict):
        raise SmokeValidationError(f"grader result is not object: {grader_path}")
    if data.get("status") != "PASS":
        raise SmokeValidationError(f"grader result status != PASS: {data}")
    if data.get("fallback_used") is not False:
        raise SmokeValidationError("grader result fallback_used must be false")
    if data.get("model_used") != "gpt-5.4":
        raise SmokeValidationError(f"grader result model_used != gpt-5.4: {data}")
    findings = data.get("findings", [])
    if findings:
        raise SmokeValidationError(f"grader result findings must be empty: {findings}")
    return {"grader_result_path": str(grader_path), "grader_result_status": "PASS"}


def _assert_no_event_artifacts(
    rows: list[dict[str, Any]],
    event: str,
    label: str,
) -> None:
    paths = _collect_event_paths(rows, event)
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise SmokeValidationError(f"{label} artifacts must be absent: {existing}")


def _load_plist(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SmokeValidationError(f"plist missing: {path}")
    try:
        with path.open("rb") as fh:
            data = plistlib.load(fh)
    except Exception as exc:
        raise SmokeValidationError(f"plist invalid: {path}") from exc
    if not isinstance(data, dict):
        raise SmokeValidationError(f"plist root is not dict: {path}")
    return data


def _assert_path_exists(path: str, label: str) -> None:
    if not Path(path).exists():
        raise SmokeValidationError(f"{label} does not exist: {path}")


def _source_config_urls(config_path: Path) -> set[str]:
    data = _read_yaml(config_path)
    sources = data.get("sources", []) if isinstance(data, dict) else []
    urls: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = source.get("feed_url") or source.get("listing_url")
        if url:
            urls.add(str(url))
    return urls


def validate_source_ledger(
    ledger_path: str | Path,
    config_path: str | Path,
    max_age_hours: int = DEFAULT_SOURCE_LEDGER_MAX_AGE_HOURS,
) -> dict[str, Any]:
    """Validate 76-source ledger for deployment preflight."""
    rows = _read_jsonl(Path(ledger_path))
    if len(rows) != 76:
        raise SmokeValidationError(f"source ledger rows != 76: {len(rows)}")

    run_ids = {row.get("run_id") for row in rows}
    if len(run_ids) != 1 or None in run_ids or "" in run_ids:
        raise SmokeValidationError(f"source ledger must have one run_id: {run_ids}")

    methods = {row.get("method") for row in rows}
    if methods != {"live"}:
        raise SmokeValidationError(f"source ledger method must be live: {methods}")

    config_urls = _source_config_urls(Path(config_path))
    row_urls = {row.get("feed") or row.get("url") for row in rows}
    row_urls = {str(url) for url in row_urls if url}
    if row_urls != config_urls:
        missing = sorted(config_urls - row_urls)[:5]
        extra = sorted(row_urls - config_urls)[:5]
        raise SmokeValidationError(
            f"source ledger urls mismatch: missing={missing} extra={extra}"
        )

    reachable = sum(1 for row in rows if row.get("reachable") is True)
    if reachable < 70:
        raise SmokeValidationError(f"reachable={reachable}<70")

    rich_count = sum(
        1
        for row in rows
        if row.get("reachable") is True and row.get("trial_fetch") and row.get("latest_post")
    )
    if rich_count < 60:
        raise SmokeValidationError(f"rich_count={rich_count}<60")

    now = datetime.now(UTC)
    oldest: datetime | None = None
    for row in rows:
        ts = row.get("validated_at")
        if not isinstance(ts, str) or not ts:
            raise SmokeValidationError("source ledger missing validated_at")
        parsed = _parse_utc_ts(ts)
        oldest = parsed if oldest is None else min(oldest, parsed)
        if now - parsed > timedelta(hours=max_age_hours):
            raise SmokeValidationError(
                f"source ledger stale: validated_at={ts}, max_age_hours={max_age_hours}"
            )

    failed_rows = [row for row in rows if row.get("reachable") is not True]
    for row in failed_rows:
        failure_class = row.get("failure_class") or row.get("error_class")
        if failure_class not in SOURCE_FAILURE_CLASSES:
            raise SmokeValidationError(
                f"source failure_class invalid for {row.get('slug')}: {failure_class}"
            )
        if row.get("error") == "error":
            raise SmokeValidationError(f"generic source error for {row.get('slug')}")

    return {
        "passed": True,
        "rows": len(rows),
        "run_id": next(iter(run_ids)),
        "reachable": reachable,
        "rich_count": rich_count,
        "failed": len(failed_rows),
        "oldest_validated_at": oldest.isoformat() if oldest else "",
    }


def validate_launchd_static(
    plist_path: str | Path,
    expected_mode: str = "proposal",
    require_label_absent: bool = False,
) -> dict[str, Any]:
    """Validate main-pipeline launchd plist without loading it."""
    plist = _load_plist(Path(plist_path))
    label = plist.get("Label")
    if label != LAUNCHD_LABEL:
        raise SmokeValidationError(f"launchd Label mismatch: {label}")

    args = plist.get("ProgramArguments")
    if not isinstance(args, list) or len(args) < 2:
        raise SmokeValidationError("launchd ProgramArguments invalid")
    script = str(args[-1])
    _assert_path_exists(script, "launchd script")

    working_dir = str(plist.get("WorkingDirectory", ""))
    _assert_path_exists(working_dir, "launchd WorkingDirectory")

    stdout_path = str(plist.get("StandardOutPath", ""))
    stderr_path = str(plist.get("StandardErrorPath", ""))
    for label_name, log_path in {
        "StandardOutPath": stdout_path,
        "StandardErrorPath": stderr_path,
    }.items():
        if not log_path:
            raise SmokeValidationError(f"launchd {label_name} missing")
        parent = Path(log_path).parent
        if not parent.exists():
            raise SmokeValidationError(f"launchd {label_name} parent missing: {parent}")

    env = plist.get("EnvironmentVariables", {})
    if not isinstance(env, dict):
        raise SmokeValidationError("launchd EnvironmentVariables invalid")
    auto_promote = str(env.get("AKB2_AUTO_PROMOTE", "0"))
    amendment = str(env.get("AKB2_AMENDMENT_APPLIED", "0"))
    actual_mode = "active" if auto_promote == "1" and amendment == "1" else "proposal"
    if expected_mode not in {"proposal", "active"}:
        raise SmokeValidationError(f"expected_mode invalid: {expected_mode}")
    if actual_mode != expected_mode:
        raise SmokeValidationError(
            f"launchd mode {actual_mode} != expected {expected_mode}"
        )

    if "__AKB2_" in Path(plist_path).read_text(encoding="utf-8"):
        raise SmokeValidationError("launchd plist has unresolved placeholder")

    label_absent = None
    if require_label_absent:
        import subprocess

        proc = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
            capture_output=True,
            text=True,
            check=False,
        )
        label_absent = proc.returncode != 0
        if not label_absent:
            raise SmokeValidationError(f"launchd label already loaded: {LAUNCHD_LABEL}")

    return {
        "passed": True,
        "label": label,
        "mode": actual_mode,
        "label_absent": label_absent,
        "script": script,
        "working_dir": working_dir,
    }


def validate_env_parity(
    plist_path: str | Path,
    smoke_auto_promote: str,
    smoke_amendment_applied: str = "0",
    active_ready: bool = False,
) -> dict[str, Any]:
    """Ensure smoke result is not confused with the plist deployment mode."""
    plist = _load_plist(Path(plist_path))
    env = plist.get("EnvironmentVariables", {})
    if not isinstance(env, dict):
        raise SmokeValidationError("launchd EnvironmentVariables invalid")
    plist_mode = (
        "active"
        if str(env.get("AKB2_AUTO_PROMOTE", "0")) == "1"
        and str(env.get("AKB2_AMENDMENT_APPLIED", "0")) == "1"
        else "proposal"
    )
    smoke_mode = (
        "active"
        if smoke_auto_promote == "1" and smoke_amendment_applied == "1"
        else "proposal"
    )
    if plist_mode == "active" and smoke_mode != "active" and not active_ready:
        raise SmokeValidationError(
            "plist is active but smoke is proposal; isolated active/rollback gate required"
        )
    return {
        "passed": True,
        "plist_mode": plist_mode,
        "smoke_mode": smoke_mode,
        "active_ready": active_ready,
    }


def _load_candidate_raw(
    candidate_path: Path,
    raw_path: Path | None,
) -> str | None:
    if raw_path and raw_path.exists():
        return raw_path.read_text(encoding="utf-8")
    return None


def _assert_candidate_quality(
    candidate_path: Path,
    raw_path: Path | None,
) -> dict[str, Any]:
    try:
        from src.akb2.quality.checks_structural import run_candidate_self_gate
    except ImportError as exc:
        raise SmokeValidationError("cannot import structural checks") from exc

    candidate_text = candidate_path.read_text(encoding="utf-8")
    raw_content = _load_candidate_raw(candidate_path, raw_path)
    defects = run_candidate_self_gate(candidate_text, raw_content=raw_content)
    if defects:
        first = defects[0]
        raise SmokeValidationError(
            f"candidate self-gate defects={len(defects)} first={first.location}: {first.defect}"
        )
    if "[raw §" not in candidate_text:
        raise SmokeValidationError("candidate missing raw anchors")
    if "## 证据地图" not in candidate_text:
        raise SmokeValidationError("candidate missing evidence map")
    if "## 图谱关系" not in candidate_text:
        raise SmokeValidationError("candidate missing graph section")
    return {"candidate_path": str(candidate_path), "structural_defects": 0}


def _find_manifest_records(vault_root: Path, candidate_path: Path) -> list[dict[str, Any]]:
    root = vault_root.expanduser().resolve()
    candidate = candidate_path.expanduser().resolve()
    manifest_dir = root / "manifests" / "ingest"
    if not manifest_dir.exists():
        return []
    rel_candidate = str(candidate.relative_to(root))
    records: list[dict[str, Any]] = []
    for path in sorted(manifest_dir.glob("*.jsonl")):
        for row in _read_jsonl(path):
            if row.get("candidate_path") == rel_candidate:
                records.append(row)
    return records


def _assert_proposal_manifest(vault_root: Path, candidate_path: Path) -> dict[str, Any]:
    records = _find_manifest_records(vault_root, candidate_path)
    if not records:
        raise SmokeValidationError(f"proposal manifest missing for {candidate_path}")
    latest = records[-1]
    if latest.get("proposed_gate_decision") != "active":
        raise SmokeValidationError(
            f"proposal gate decision != active: {latest.get('proposed_gate_decision')}"
        )
    if latest.get("decision_confidence") != "high":
        raise SmokeValidationError(
            f"decision_confidence != high: {latest.get('decision_confidence')}"
        )
    if latest.get("graph_readiness") != "real_link":
        raise SmokeValidationError(
            f"graph_readiness != real_link: {latest.get('graph_readiness')}"
        )
    if latest.get("not_active_promotion") is not True:
        raise SmokeValidationError("proposal manifest must declare not_active_promotion")
    return {
        "proposal_decision": latest.get("proposed_gate_decision"),
        "decision_confidence": latest.get("decision_confidence"),
        "graph_readiness": latest.get("graph_readiness"),
    }


def _assert_active_tree_unchanged(
    before_path: str | Path | None,
    after_path: str | Path | None,
) -> None:
    if not before_path and not after_path:
        return
    if not before_path or not after_path:
        raise SmokeValidationError("both active tree hash files are required")
    before = Path(before_path).read_text(encoding="utf-8").strip()
    after = Path(after_path).read_text(encoding="utf-8").strip()
    if before != after:
        raise SmokeValidationError("active tree hash changed in proposal smoke")


def validate_distill_failure(
    ledger_path: str | Path,
    vault_root: str | Path,
) -> dict[str, Any]:
    """Validate failure gate: distill failure observable, no downstream events."""
    rows = _read_jsonl(Path(ledger_path))
    _assert_paths_under_root(rows, Path(vault_root))
    starts = [
        row
        for row in rows
        if row.get("event") == "distill_extract"
        and row.get("details", {}).get("status") == "start"
    ]
    aborts = [
        row
        for row in rows
        if row.get("event") == "abort" and row.get("node") == "distill_extract"
    ]
    downstream = [row.get("event") for row in rows if row.get("event") in TERMINAL_EVENTS]
    if not starts:
        raise SmokeValidationError("missing distill_extract start event")
    if not aborts:
        raise SmokeValidationError("missing abort node=distill_extract")
    if downstream:
        raise SmokeValidationError(f"downstream events must be absent: {downstream}")
    for event, label in {
        "distill_compose": "candidate",
        "grade_llm": "grader",
        "promote": "promote",
        "finalize": "finalize",
    }.items():
        _assert_no_event_artifacts(rows, event, label)
    details = aborts[-1].get("details", {})
    required = {"error_class", "model_family", "timeout_s", "max_retries", "retries_used"}
    missing = sorted(required - set(details))
    if missing:
        raise SmokeValidationError(f"distill abort details missing: {missing}")
    return {
        "mode": "distill-failure",
        "passed": True,
        "abort_error_class": details.get("error_class"),
    }


def validate_happy_path(
    ledger_path: str | Path,
    vault_root: str | Path,
    *,
    require_candidate_quality: bool = False,
    require_proposal_manifest: bool = False,
    active_tree_before: str | Path | None = None,
    active_tree_after: str | Path | None = None,
) -> dict[str, Any]:
    """Validate proposal happy gate without confusing it with active finalize."""
    rows = _read_jsonl(Path(ledger_path))
    root = Path(vault_root).expanduser().resolve()
    _assert_paths_under_root(rows, root)
    grade_events = [row for row in rows if row.get("event") == "grade_llm"]
    if not grade_events:
        raise SmokeValidationError("missing grade_llm event")
    details = grade_events[-1].get("details", {})
    if details.get("model_used") != "gpt-5.4":
        raise SmokeValidationError(f"grade_llm.model_used != gpt-5.4: {details}")
    if details.get("fallback_used") is not False:
        raise SmokeValidationError("grade_llm.fallback_used must be false")
    if details.get("suspended") is not False:
        raise SmokeValidationError("grade_llm.suspended must be false")
    if details.get("defect_count") != 0:
        raise SmokeValidationError(f"grade_llm.defect_count must be 0: {details}")
    aborts = [row for row in rows if row.get("event") == "abort"]
    if aborts:
        raise SmokeValidationError(f"abort events present in happy path: {aborts[-1]}")
    promote_events = [row for row in rows if row.get("event") == "promote"]
    if not promote_events:
        raise SmokeValidationError("missing promote event")
    promote_mode = promote_events[-1].get("details", {}).get("mode")
    if promote_mode != "proposal":
        raise SmokeValidationError(f"happy path must be proposal mode: {promote_mode}")
    finalize_events = [row for row in rows if row.get("event") == "finalize"]
    if finalize_events:
        finalize_mode = finalize_events[-1].get("details", {}).get("mode")
        if finalize_mode != "proposal":
            raise SmokeValidationError(f"finalize mode is not proposal: {finalize_mode}")

    candidate_result: dict[str, Any] = {}
    candidate_path = _find_latest_candidate_path(rows, root)
    raw_path = _find_latest_raw_path(rows, root)
    grader_result = _assert_grader_result_pass(root, candidate_path)
    if require_candidate_quality:
        candidate_result = _assert_candidate_quality(candidate_path, raw_path)

    manifest_result: dict[str, Any] = {}
    if require_proposal_manifest:
        manifest_result = _assert_proposal_manifest(root, candidate_path)

    _assert_active_tree_unchanged(active_tree_before, active_tree_after)

    return {
        "mode": "happy",
        "passed": True,
        "promotion_mode": promote_mode,
        "model_used": details.get("model_used"),
        "fallback_used": details.get("fallback_used"),
        "defect_count": details.get("defect_count"),
        "initial_defect_count": details.get(
            "initial_defect_count", details.get("defect_count")
        ),
        "suspended": details.get("suspended"),
        **grader_result,
        **candidate_result,
        **manifest_result,
    }


def validate_root_isolation(
    before_metadata: str | Path,
    after_metadata: str | Path,
) -> dict[str, Any]:
    """Validate project-root log/lock metadata snapshots are unchanged."""
    before = _read_json(Path(before_metadata))
    after = _read_json(Path(after_metadata))
    if before != after:
        raise SmokeValidationError("project root log/lock metadata changed")
    return {"passed": True, "root_metadata_unchanged": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate AKB2 isolated smoke output")
    parser.add_argument(
        "--mode",
        choices=[
            "distill-failure",
            "happy",
            "source-ledger",
            "launchd-static",
            "env-parity",
            "root-isolation",
        ],
        required=True,
    )
    parser.add_argument("--ledger")
    parser.add_argument("--vault-root")
    parser.add_argument("--config", default="config/main_sources.yaml")
    parser.add_argument("--plist", default="launchd/com.akb2.main-pipeline.plist")
    parser.add_argument("--expected-mode", choices=["proposal", "active"], default="proposal")
    parser.add_argument("--require-label-absent", action="store_true")
    parser.add_argument("--max-age-hours", type=int, default=DEFAULT_SOURCE_LEDGER_MAX_AGE_HOURS)
    parser.add_argument("--require-candidate-quality", action="store_true")
    parser.add_argument("--require-proposal-manifest", action="store_true")
    parser.add_argument("--active-tree-before")
    parser.add_argument("--active-tree-after")
    parser.add_argument("--smoke-auto-promote", default="0")
    parser.add_argument("--smoke-amendment-applied", default="0")
    parser.add_argument("--active-ready", action="store_true")
    parser.add_argument("--before-metadata")
    parser.add_argument("--after-metadata")
    args = parser.parse_args(argv)

    try:
        if args.mode == "distill-failure":
            if not args.ledger or not args.vault_root:
                raise SmokeValidationError("--ledger and --vault-root are required")
            result = validate_distill_failure(args.ledger, args.vault_root)
        elif args.mode == "happy":
            if not args.ledger or not args.vault_root:
                raise SmokeValidationError("--ledger and --vault-root are required")
            result = validate_happy_path(
                args.ledger,
                args.vault_root,
                require_candidate_quality=args.require_candidate_quality,
                require_proposal_manifest=args.require_proposal_manifest,
                active_tree_before=args.active_tree_before,
                active_tree_after=args.active_tree_after,
            )
        elif args.mode == "source-ledger":
            if not args.ledger:
                raise SmokeValidationError("--ledger is required")
            result = validate_source_ledger(args.ledger, args.config, args.max_age_hours)
        elif args.mode == "launchd-static":
            result = validate_launchd_static(
                args.plist,
                expected_mode=args.expected_mode,
                require_label_absent=args.require_label_absent,
            )
        elif args.mode == "env-parity":
            result = validate_env_parity(
                args.plist,
                smoke_auto_promote=args.smoke_auto_promote,
                smoke_amendment_applied=args.smoke_amendment_applied,
                active_ready=args.active_ready,
            )
        else:
            if not args.before_metadata or not args.after_metadata:
                raise SmokeValidationError(
                    "--before-metadata and --after-metadata are required"
                )
            result = validate_root_isolation(args.before_metadata, args.after_metadata)
    except SmokeValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
