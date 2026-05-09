# Fixture (positive) — cc-review L1 smoke

> Codex must invoke `cc-review` against this capsule and capture the full
> Chinese review output into `run-evidence.md`.

## Skill metadata at smoke time

- skill path: `~/.codex/skills/cc-review/SKILL.md`
- expected sha256: `d6e7ce54dce6e76e87998c0d5fb419b43e83b303fec7c2f7bd01df05a214cbb3`
- workspace_owner: `official-codex-skills`

## Pre-call check

- [ ] `shasum -a 256 ~/.codex/skills/cc-review/SKILL.md` matches expected

## Capsule (positive — should yield 通过)

```
REVIEW_KIND: plan
REVIEW_DOMAIN: docs_content
PLAN_REVIEW_ALIGNMENT: 文档结构清楚;字段齐全;无 active 风险
PLAN_ALIGNMENT_PRESENT: true
REVIEW_FOCUS: 字段完整性;无 active 提升;无 automation_allowed=true;路径校验
REVIEW_FOCUS_SOURCE: plan
REVIEW_MODE: single_pass
REVIEW_ROUND: initial
REVIEW_DELTA_SCOPE: full_matrix
PREVIOUS_FINDINGS: 无
ONE_REVIEW_ONLY: true
PROJECT_ROOT: /tmp/c4a-positive-fixture
REPO_ROOT: 无
BRANCH: 无
BASELINE: 仅一份新建 markdown 文档,无既有 dirty。
PROJECT_GOAL: 验证 cc-review 在最低复杂度 plan capsule 上输出"通过"。
PROJECT_GOAL_SOURCE: user_latest
CURRENT_TASK_GOAL: 输出一行"今日完成事项已记录"到 daily log。
SUCCESS_CRITERIA: (1) markdown 含一级标题;(2) 文件落到 daily/<日期>.md;(3) 不修改其它文件。
IN_SCOPE: tmp 目录下新增一份 daily/<日期>.md
OUT_OF_SCOPE: 任何 git 操作 / 远程同步 / 用户 dotfile
USER_ACCEPTED_DECISIONS: 不需要 git commit;不需要审计 manifest;只是 daily log。
CHANGESET_OR_PLAN: |
  新建 daily/<日期>.md,内容仅一行 "今日完成事项已记录"。无其它改动。
VALIDATION_EVIDENCE: |
  ls daily/ 显示新文件;cat 内容匹配。
KNOWN_LIMITATIONS: 仅 demo;无生产数据。
BASELINE_CONFIDENCE: high
REVIEW_RULES: ~/.claude/commands/review.md §审查标准
```

## What to capture

In `run-evidence.md`:
- timestamp + Codex command used
- full skill output (raw markdown, fenced)
- explicit extraction of `结论：` line
- pass/fail per assertion (see `assertions.md`)
