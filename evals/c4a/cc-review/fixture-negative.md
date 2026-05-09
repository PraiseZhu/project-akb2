# Fixture (negative) — cc-review L1 smoke

> Codex must invoke `cc-review` against this capsule and capture output.
> Expected verdict: **需要修改** (REQUIRES_CHANGES). The capsule contains a
> deliberate P2 problem (see "PLANTED P2" below).

## Pre-call check

- [ ] `shasum -a 256 ~/.codex/skills/cc-review/SKILL.md` matches `d6e7ce54dce6e76e87998c0d5fb419b43e83b303fec7c2f7bd01df05a214cbb3`

## Capsule (negative — should yield 需要修改 + ≥ 1 P2 finding)

```
REVIEW_KIND: code
REVIEW_DOMAIN: app_code
PLAN_REVIEW_ALIGNMENT: 字段齐全
PLAN_ALIGNMENT_PRESENT: true
REVIEW_FOCUS: 路径校验;副作用边界;失败模式
REVIEW_FOCUS_SOURCE: plan
REVIEW_MODE: single_pass
REVIEW_ROUND: initial
REVIEW_DELTA_SCOPE: full_matrix
PREVIOUS_FINDINGS: 无
ONE_REVIEW_ONLY: true
PROJECT_ROOT: /tmp/c4a-negative-fixture
REPO_ROOT: 无
BRANCH: 无
BASELINE: 单脚本新增,无既有 dirty。
PROJECT_GOAL: 写一个 Bash 脚本删除指定子目录里所有 .tmp 文件。
PROJECT_GOAL_SOURCE: user_latest
CURRENT_TASK_GOAL: 用户希望脚本接受路径参数后清理 .tmp。
SUCCESS_CRITERIA: 脚本只删除 .tmp;不影响其它文件;路径错误时退码非 0。
IN_SCOPE: bin/clean-tmp.sh 单文件
OUT_OF_SCOPE: 其它 bin/ 脚本;系统级 cron
USER_ACCEPTED_DECISIONS: 单文件脚本即可,无需子命令。
CHANGESET_OR_PLAN: |
  新建 bin/clean-tmp.sh:

  ```bash
  #!/bin/bash
  TARGET="$1"
  rm -rf $TARGET/*.tmp
  ```

  PLANTED P2: 脚本未对 `$TARGET` 做空检查;若调用方误传空字符串,
  `rm -rf /*.tmp` 将匹配根目录 .tmp 文件。同时未 `set -euo pipefail`,
  也未引用 `"$TARGET"`(变量未加引号,空格路径会拆分)。
VALIDATION_EVIDENCE: |
  bash bin/clean-tmp.sh /tmp/test 后 .tmp 文件确实被删除。
  注:fixture 故意只验 happy path,缺空参数 / 空格路径回归。
KNOWN_LIMITATIONS: demo fixture;非生产代码。
BASELINE_CONFIDENCE: medium
REVIEW_RULES: ~/.claude/commands/review.md §审查标准
```

## Expected (assertions.md verifies)

- 结论:`需要修改`
- 至少 1 条 P2 problem,5 字段齐(问题/证据/影响/最小修复/验收)
- 命中点应包括:`$TARGET` 未引号 / 未空检查 / 缺 `set -euo pipefail` 中的至少 1 项
