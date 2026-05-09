# cc-skill-stocktake L2 Case Register

**Phase**: L2 Regression v1
**Date**: 2026-05-08
**Total Fixtures**: 13 (10 contract + 3 real_evaluator)
**Upstream Dependency**: `l2-regression-v1/cc-local-llm/recommendation.json.verdict`

## Lane Architecture

| Lane | Fixtures | LLM Required | Blocked When |
|------|----------|-------------|-------------|
| contract | c01-c10 | No (mock) | Never (deterministic) |
| real_evaluator | r01-r03 | Yes (cc-local-llm) | upstream verdict=blocked |

## Verdict Rule

- Both lanes pass → `recommend_promote_L2`
- Any lane blocked → `blocked`
- Any lane fail → `needs_patch`
- Total verdict = weakest lane

## Contract Lane (c01–c10)

| ID | Operation | Expected Result |
|----|-----------|----------------|
| c01-baseline-scan | baseline_scan | 3 skills found, schema valid |
| c02-quick-diff-no-change | quick_diff | no_change, empty changed_skills |
| c03-quick-diff-changed | quick_diff | changed, changed_skills ≥ 1 |
| c04-save-results-schema | save_results | saved=true, schema valid |
| c05-invalid-results | save_results | error, no crash |
| c06-missing-skill-path | baseline_scan | 0 skills or error, no crash |
| c07-mock-evaluator-pass | evaluate_skill | verdict=pass |
| c08-mock-evaluator-fail | evaluate_skill | verdict=fail, failure_detail |
| c09-stocktake-dry-run | full_stocktake (dry-run) | 3 total, 2 pass, 1 fail |
| c10-isolation-guard | full_stocktake (dry-run) | no writes to formal dirs |

## real_evaluator Lane (r01–r03)

Requires `cc-local-llm` backend at 127.0.0.1:8080.
All r* fixtures run 3 rounds with temperature=0 + pre-assigned seed.

| ID | Skill | Stability Check | Expected |
|----|-------|----------------|---------|
| r01-evaluator-json-schema | skill-a | verdict_label_match | JSON valid 3/3 |
| r02-evaluator-verdict-stable | skill-c | exact_verdict_match | same verdict 3/3 |
| r03-evaluator-no-mutation | all skills | no_mutation_all_rounds | mutations_detected=false |

## Cross-Tool Dependency Contract

```
upstream: ai-infra/akb2-toolbox-evals/l2-regression-v1/cc-local-llm/recommendation.json
read_field: .verdict
decision:
  verdict == "blocked" → real_evaluator lane: blocked (block_reason: upstream_blocked)
  verdict in ["recommend_promote_L2", "needs_patch"] → real_evaluator lane: execute r01-r03
  file missing → exit 3 (runner error)
```
