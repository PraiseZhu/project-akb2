"""CLI: python -m akb2_toolbox_scan ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .coverage import annotate_with_akb_coverage
from .discover import RootSpec, discover_candidates
from .paths import PathValidationError, validate_output_paths
from .render import render_outputs
from .writer import PairedWriteError, write_output_pair


SCAN_SCOPES = ("global", "project_scripts_bin")
KNOWN_USER_LABELS = {
    str(Path("~/.codex/skills").expanduser().resolve()): "codex_skills",
    str(Path("~/.claude/skills").expanduser().resolve()): "claude_skills",
    str(Path("~/.claude/scripts").expanduser().resolve()): "claude_scripts",
    str(Path("~/.claude/scripts/hooks").expanduser().resolve()): "claude_hooks",
}


def _label_for_root(path: Path, kind: str) -> str:
    resolved = str(path.expanduser().resolve())
    if resolved in KNOWN_USER_LABELS:
        return KNOWN_USER_LABELS[resolved]
    if kind == "vendor":
        return "vendor_plugin_cache"
    name = path.name.lower()
    if name == "skills" and ".codex" in resolved:
        return "codex_skills"
    if name == "skills" and ".claude" in resolved:
        return "claude_skills"
    if name == "hooks":
        return "claude_hooks"
    if name == "scripts":
        return "claude_scripts"
    return "user_unknown"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="akb2-toolbox-scan", description=__doc__)
    p.add_argument("--akb-root", required=True)
    p.add_argument("--root", action="append", default=[], help="user-controlled root to scan (repeatable)")
    p.add_argument("--vendor-root", action="append", default=[], help="vendor/plugin-cache root (repeatable, opt-in)")
    p.add_argument("--project-root", action="append", default=[], help="project root for scripts/bin/tools/cli scan (repeatable)")
    p.add_argument("--scan-scope", choices=SCAN_SCOPES, default="global")
    p.add_argument("--previous-manifest", default=None, help="prior C-1 JSONL for duplicate detection")
    p.add_argument("--max-files-per-root", type=int, default=20000)
    p.add_argument("--out", required=True, help="manifest output path relative to akb-root")
    p.add_argument("--report", required=True, help="report output path relative to akb-root")
    p.add_argument("--limit", type=int, default=None, help="debug-only candidate cap")
    args = p.parse_args(argv)

    try:
        validated = validate_output_paths(Path(args.akb_root), args.out, args.report)
    except PathValidationError as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 2

    if not args.root and not args.vendor_root and not args.project_root:
        print(
            "[fatal] at least one --root, --vendor-root, or --project-root is required",
            file=sys.stderr,
        )
        return 2

    if args.scan_scope == "project_scripts_bin" and not args.project_root:
        print(
            "[fatal] --scan-scope project_scripts_bin requires at least one --project-root",
            file=sys.stderr,
        )
        return 2

    specs: list[RootSpec] = []
    for r in args.root:
        path = Path(r).expanduser().resolve()
        specs.append(RootSpec(path=path, kind="user", label=_label_for_root(path, "user")))
    for r in args.vendor_root:
        path = Path(r).expanduser().resolve()
        specs.append(RootSpec(path=path, kind="vendor", label=_label_for_root(path, "vendor")))
    for r in args.project_root:
        path = Path(r).expanduser().resolve()
        specs.append(
            RootSpec(
                path=path,
                kind="project",
                label=f"project:{path.name}",
                project_name=path.name,
            )
        )

    previous_paths: set[str] = set()
    previous_label: str | None = None
    if args.previous_manifest:
        prev = Path(args.previous_manifest).expanduser().resolve()
        if not prev.exists():
            print(f"[fatal] --previous-manifest does not exist: {prev}", file=sys.stderr)
            return 2
        previous_label = str(prev)
        try:
            for line in prev.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                wp = rec.get("workspace_path")
                if wp:
                    previous_paths.add(wp)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[fatal] cannot read --previous-manifest: {exc}", file=sys.stderr)
            return 2

    result = discover_candidates(
        specs,
        limit=args.limit,
        max_files_per_project_root=args.max_files_per_root,
        previous_manifest_paths=previous_paths,
        previous_manifest_path_label=previous_label,
    )
    annotate_with_akb_coverage(result.records, validated.akb_root)

    jsonl_text, report_text = render_outputs(
        result=result,
        akb_root=validated.akb_root,
        out_path=validated.out_path,
        report_path=validated.report_path,
        scan_scope=args.scan_scope,
        previous_manifest_label=previous_label,
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

    print(f"[ok] wrote {len(result.records)} candidates → {pair.out_path}")
    print(f"[ok] wrote report → {pair.report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
