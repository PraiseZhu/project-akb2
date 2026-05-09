---
title: P2 Lint v0 — Fixture Case Register
generated_at: 2026-05-07T12:46:46Z
scope: p2-toolbox-structure-lint-v0
status: complete
---

# Fixture Case Register

8 fixture boundary cases, one per rule. Each case is a minimal synthetic 5件套 designed to
trigger exactly one rule. The trigger rule fires; all other rules may pass or produce benign findings.

## Case Inventory

| # | Case Dir | Trigger Rule | Expected Status | Design Rationale |
|---|----------|-------------|-----------------|-----------------|
| 1 | `r01-missing-fivefile` | r01 | fail | wiki/tools/ file absent → 5件套 incomplete |
| 2 | `r02-split-status` | r02 | handoff | wiki status=active + 4件套 status=candidate → split detected |
| 3 | `r03-workspace-missing` | r03 | fail | workspace_path points to non-existent path |
| 4 | `r04-sha-drift` | r04 | fail | workspace_version sha prefix ≠ actual SKILL.md sha |
| 5 | `r05-backlink-pending` | r05 | fail | backlink_status=pending on non-cc-akb-search tool → hard fail |
| 6 | `r06-active-l0` | r06 | fail | status=active + eval_level=L0 → requires ≥L1 |
| 7 | `r07-eval-stale` | r07 | handoff | last_run_at=2025-01-01 → 120+ days stale, > 30d threshold |
| 8 | `r08-automation-true` | r08 | fail | automation_allowed: true in interface → P0 violation |

## Case Details

### r01-missing-fivefile

- **Rule triggered**: r01 (five_file_present)
- **Design**: Full 5件套 minus wiki/tools/fixture-tool.md
- **Missing file**: `wiki/tools/fixture-tool.md`
- **Expected**: `fail` / `hard_fail`
- **Actual (run 2026-05-07)**: `fail` / `hard_fail` ✓
- **Evidence**: `"missing: wiki/tools/fixture-tool.md"`

### r02-split-status

- **Rule triggered**: r02 (status_consistency)
- **Design**: wiki/tools status=active; toolbox/{interfaces,tools,registry,evals} status=candidate
- **Status set**: {active, candidate} → cardinality 2 → handoff (not fail)
- **Expected**: `handoff`
- **Actual (run 2026-05-07)**: `handoff` ✓
- **Evidence**: split status set detected

### r03-workspace-missing

- **Rule triggered**: r03 (workspace_path_exists)
- **Design**: interface workspace_path points to `/nonexistent/path/to/fake.md`
- **Expected**: `fail` / `hard_fail`
- **Actual (run 2026-05-07)**: `fail` / `hard_fail` ✓
- **Evidence**: workspace_path not found

### r04-sha-drift

- **Rule triggered**: r04 (sha256_match)
- **Design**: workspace_path points to existing `fake-skill.md`; workspace_version sha=`000000000000`; actual sha≠000000000000
- **Expected**: `fail` / `hard_fail`
- **Actual (run 2026-05-07)**: `fail` / `hard_fail` ✓
- **Evidence**: workspace_version sha mismatch detected

### r05-backlink-pending

- **Rule triggered**: r05 (backlink_linked)
- **Design**: backlink_status=pending on slug=fixture-tool (not cc-akb-search) → hard fail path
- **Expected**: `fail` / `hard_fail`
- **Actual (run 2026-05-07)**: `fail` / `hard_fail` ✓
- **Evidence**: backlink_status: pending (non-exception tool)

### r06-active-l0

- **Rule triggered**: r06 (active_needs_l1)
- **Design**: toolcard status=active; eval file eval_level=L0 → violation
- **Expected**: `fail` / `hard_fail`
- **Actual (run 2026-05-07)**: `fail` / `hard_fail` ✓
- **Evidence**: status=active but eval_level=L0

### r07-eval-stale

- **Rule triggered**: r07 (eval_freshness)
- **Design**: last_run_at=2025-01-01 → 120+ days prior to run date 2026-05-07
- **Threshold**: 30 days → handoff (not fail); stale is advisory only
- **Expected**: `handoff`
- **Actual (run 2026-05-07)**: `handoff` ✓
- **Evidence**: last_run_at=2025-01-01 (>30d)

### r08-automation-true

- **Rule triggered**: r08 (automation_allowed_false)
- **Design**: interface frontmatter contains `automation_allowed: true`
- **Expected**: `fail` / `hard_fail` (P0 zero-tolerance gate)
- **Actual (run 2026-05-07)**: `fail` / `hard_fail` ✓
- **Evidence**: automation_allowed: true found in interface file

## Summary

All 8 cases produced expected outcomes on run 2026-05-07T12:46:46Z.
Fixture accuracy: **8/8 (100%)**.
