# akb2-toolbox-scan

Read-only **Phase C-1** scanner for AKB2.0. Discovers real workspace tool
assets (Codex skills, Claude skills, Claude scripts and hooks) and emits
a paired JSONL manifest + Markdown report.

> **Read-only by design.** Never executes a candidate, never imports a
> candidate, never follows symlinks, never writes outside the two AKB2.0
> allowed directories.

## What it produces

| Output | Path (relative to AKB2.0 root) |
|--------|--------------------------------|
| L0 manifest | `manifests/workspace-scan/<YYYY-MM-DD>-...jsonl` |
| L0 report   | `docs/phase-c-reports/phase-c1-...md` |

Both files are written atomically as a pair: if either fails, the other
is rolled back so no orphan official file is left behind.

## What every record carries

Every JSONL row:

- `not_active: true`
- `automation_allowed: false`
- `gate_decision: null`
- `manifest_type: workspace_scan`
- fingerprints: `sha256` (16-hex prefix), `size_bytes`, `mtime`
- classification (advisory only): `detected_kind`, `candidate_asset_type`,
  `risk_hint`, `side_effects_hint`
- AKB coverage: `phase_b_shadow_match`, `registry_exists`,
  `interface_exists`, `eval_exists`
- safety: `is_symlink`, `looks_like_secret_in_header`, `scan_error`

The scanner never produces an `active` decision and never sets
`automation_allowed: true`. Tool Gate is a separate human-driven step.

## CLI

```bash
python -m akb2_toolbox_scan \
  --akb-root "/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0" \
  --root "/Users/praise/.codex/skills" \
  --root "/Users/praise/.claude/scripts" \
  --root "/Users/praise/.claude/scripts/hooks" \
  --root "/Users/praise/.claude/skills" \
  --out "manifests/workspace-scan/2026-05-04-toolbox-workspace-scan.jsonl" \
  --report "docs/phase-c-reports/phase-c1-toolbox-workspace-scan-2026-05-04.md"
```

Optional:

- `--vendor-root <path>` (repeatable) — opt-in scan of plugin/cache trees,
  marked `root_kind: vendor`. Default: not scanned.
- `--limit N` — debug-only candidate cap.

Path validation is strict (`Path.relative_to`); non-allowed `--out` /
`--report` exit `2` and write nothing.

## C-2 ranking (`python -m akb2_toolbox_scan.rank`)

```bash
python -m akb2_toolbox_scan.rank \
  --akb-root "/Users/praise/.../AKB2.0" \
  --manifest "/.../manifests/workspace-scan/2026-05-04-toolbox-workspace-scan.jsonl" \
  --manifest "/.../manifests/workspace-scan/2026-05-04-toolbox-project-scripts-bin-scan.jsonl" \
  --out  "manifests/toolbox-ranking/2026-05-04-toolbox-candidate-ranking.jsonl" \
  --report "docs/phase-c-reports/phase-c2-toolbox-candidate-ranking-2026-05-04.md" \
  --pilot-limit 20
```

Reads C-1 + C-1b workspace-scan manifests (read-only), dedupes by
`workspace_path` / `sha256` / `slug_candidate`, scores on a deterministic
matrix, and assigns every candidate to one of four tiers:

- `pilot_next` (≤ `--pilot-limit`) — recommended for the next C-3 batch
- `manual_review` — needs a human triage before C-3
- `defer` — valid candidate, just not the priority
- `exclude_this_round` — **not** a permanent rejection; just a per-round skip

Hard contracts: every output record stays `not_active=true`,
`automation_allowed=false`, `gate_decision=null`. Outputs land only in
`manifests/toolbox-ranking/` (strict `Path.relative_to` check) and
`docs/phase-c-reports/`. Bad JSON, missing `not_active`, or path escape
exits `2` and writes nothing.

## Tests

```bash
python3 -m unittest discover -s tests
```

14 unit tests cover: detection per root kind, hook deduplication, empty
root, vendor opt-in, symlink not followed, strict path boundaries,
no-orphan on report-write failure, uniform `not_active` / `automation_allowed`,
no-secret in report, Phase B shadow match.

## Hard guarantees

1. Never `subprocess`, never `exec`, never `import` a candidate file.
2. Never follow symlinks (recorded as a record with `is_symlink: true`).
3. Never write outside `manifests/workspace-scan/` and
   `docs/phase-c-reports/`.
4. Any IO failure during the paired write rolls back to leave zero
   orphan official files.
5. Header sniff is bounded to the first 4 KB and never echoes the body
   into the report. Secret-shaped strings flip the
   `looks_like_secret_in_header` flag without leaking the value.
