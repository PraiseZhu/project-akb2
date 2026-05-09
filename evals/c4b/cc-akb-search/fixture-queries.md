# Fixture queries — cc-akb-search L1 smoke (only after preflight pass)

> **Do not run if preflight fails.** Codex must call cc-akb-search once per
> query below and capture top-5 results into `run-evidence.md`.

## Pre-call

- [ ] `preflight.md` all 5 items checked pass
- [ ] SKILL.md sha256 still `b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982`

## Queries (5)

| # | query | expected usable hits in top-5 |
|---|------|------------------------------|
| 1 | `claude code` | ≥ 3 |
| 2 | `MCP server` | ≥ 3 |
| 3 | `memory` | ≥ 3 |
| 4 | `planning` | ≥ 3 |
| 5 | `agentic coding` | ≥ 2 |

## "Usable" definition

A top-5 result counts as usable iff:

- title or snippet is semantically related to the query
- record state is NOT `rejected` / `archived` / `non-ok` / `quarantined`
- (if the call returns `best_note_path`) the path resolves to a real file
  OR `best_note_path` is null but the result still has a usable snippet

Per-query: count the usable rows in top-5. Compare against the threshold.

## Capture (per query)

For each query, in `run-evidence.md`:

- query string
- exact Codex command issued
- raw top-5 result list (id, title, snippet, source, score, best_note_path)
- per-row usable verdict + reason (1 line)
- usable count
- pass/fail vs threshold

## Final

- 5/5 queries pass → assertion_status: pass, eval_level_after: L1
- partial → assertion_status: partial, eval_level_after: L0
- 0/5 or preflight blocked → assertion_status: blocked, eval_level_after: L0
- Even when L1 passes: cc-akb-search stays out of active gate this round
  (risk_level=high; needs human risk adjudication separately)
