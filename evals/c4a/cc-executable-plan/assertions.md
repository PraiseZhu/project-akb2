# Assertions — cc-executable-plan L1 smoke

All MUST pass for L1 promotion. Codex records each assertion as
pass/fail/n_a in `run-evidence.md` and must include the evidence snippet
that proves the verdict.

## A. Template drift check

- [ ] `diff -q ~/.claude/templates/plan-template.md ~/.codex/templates/plan-template.md` exits 0
- [ ] If non-zero: abort, write blocked evidence, do NOT proceed to B-D

## B. Mandatory section headings present in skill output

The plan output must contain ALL of the following section headings
(plain `grep -c "^## "` against the markdown):

- [ ] `## Review 对齐`
- [ ] `## 目标与验收`
- [ ] `## 当前状态`
- [ ] `## 代码执行方向`
- [ ] `## 接口与数据合约`
- [ ] `## 实现步骤`
- [ ] `## 边界与失败模式`
- [ ] `## 测试计划`

Note: the plan asks the assertion list to include 5 sections explicitly
(Review 对齐 / 目标与验收 / 代码执行方向 / 接口与数据合约 / 测试计划). The
8 listed above include all 5 plus 3 adjacent mandatory sections from the
template; this is more conservative.

## C. Review 对齐 alignment-block contents

- [ ] `REVIEW_DOMAIN:` line present and one of {ai_infra, app_code, docs_content, other}
- [ ] `REVIEW_FOCUS:` line present and lists ≥ 3 dimensions

## D. Output integrity

- [ ] Output is well-formed Markdown (no truncated fenced blocks, balanced ``` markers)
- [ ] Output length ≥ 200 lines (small plans are fine but a placeholder under 50 lines is suspicious)
- [ ] Output mentions the actual file paths from fixture.md context
  (`legacy-skill-importer.sh` and `skill-importer-v2.sh`)

## L1 promotion gate

- C-4a pass = A passes AND B 8/8 pass AND C 2/2 pass AND D 3/3 pass
- Any single failure → record as fail, eval stays L0
- Drift in A → record as blocked (not fail), eval stays L0

## What Codex writes back

In `run-evidence.md`:
- per-assertion verdict (pass/fail/blocked) with evidence snippet
- final summary line: `assertion_status: pass | fail | partial | blocked`
- if pass: `eval_level_after: L1` recommendation
- if not pass: `eval_level_after: L0` + reason
