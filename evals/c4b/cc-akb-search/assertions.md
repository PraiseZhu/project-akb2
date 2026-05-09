# Assertions — cc-akb-search C-4b

## Phase 1 — Preflight

See `preflight.md` for 5 items (P1..P5). Any blocked → stop.

## Phase 2 — Query smoke (only if preflight all pass)

See `fixture-queries.md`.

| query | usable threshold |
|---|---|
| `claude code` | ≥ 3 |
| `MCP server` | ≥ 3 |
| `memory` | ≥ 3 |
| `planning` | ≥ 3 |
| `agentic coding` | ≥ 2 |

## L1 promotion gate

| Outcome | eval_level_after | last_run_status | enters active gate? |
|---|---|---|---|
| Preflight blocked | L0 | not_run | no — blocked |
| Preflight pass + 5/5 queries pass | L1 | pass | **NO** — risk_level=high; human adjudication required separately |
| Preflight pass + partial query pass | L0 | partial | no |
| Preflight pass + 0/5 query pass | L0 | fail | no |

## What Codex writes back

In `run-evidence.md`:
- preflight per-item verdict (P1..P5) with evidence
- if preflight pass: per-query usable count + verdict
- final summary line: `assertion_status: pass | partial | fail | blocked`
- recommended `eval_level_after`
- explicit `enters_active_gate: false` (always false this round)
