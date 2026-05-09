# Assertions — cc-review L1 smoke (positive + negative)

Both fixtures MUST pass for L1 promotion. Either passing alone is `partial`
and eval stays L0.

## Positive fixture (`fixture-positive.md`)

- [ ] Output is a single fenced markdown block
- [ ] Title is `# 计划审查结果` (kind=plan)
- [ ] Output contains a line `结论：通过`
- [ ] No P2 problems listed
- [ ] `已检查范围` section present with ≥ 3 rows
- [ ] No mention of any path outside `/tmp/c4a-positive-fixture`

## Negative fixture (`fixture-negative.md`)

- [ ] Output is a single fenced markdown block
- [ ] Title is `# 代码审查结果` (kind=code)
- [ ] Output contains a line `结论：需要修改`
- [ ] At least 1 P2 problem listed
- [ ] Each P2 problem has 5 fields: 问题, 证据, 影响, 最小修复, 验收
- [ ] At least 1 P2 problem mentions one of: `$TARGET 未引号`, `空检查`, `set -euo pipefail`

## L1 promotion gate

| Outcome | eval_level_after | last_run_status |
|---|---|---|
| Both fixtures pass all assertions | L1 | pass |
| One fixture passes, other fails | L0 | partial |
| Both fail | L0 | fail |
| Codex watch / skill blocked | L0 | not_run + blocked_reason |

## What Codex writes back

In `run-evidence.md`:
- timestamp + Codex commands for both fixtures
- full skill output for each fixture (raw markdown, fenced)
- per-assertion verdict (pass/fail) with evidence snippet
- final summary line: `assertion_status: pass | partial | fail | blocked`
- recommended `eval_level_after` and `last_run_status`
