# Review

Dual review by Claude (initial assessment) and Codex watch (cross-review).

## Modes

- `mode=step`: Review single step execution (used by /tr Step 7)
- `mode=task`: Review entire task completion (used by /tr Step 9.1)

## Input

| Field | step mode | task mode |
|-------|-----------|-----------|
| target | Step title | Task name |
| doneConditions | Step done conditions | Acceptance criteria |
| changedFiles | Files changed in step | All files changed |
| proof | Execution output | All step summaries |

## Execution Flow

### 1. Claude Initial Assessment

Evaluate against done conditions / acceptance criteria:
- What was accomplished?
- Are all conditions met?
- Any issues or risks?

Preliminary verdict: **PASS** / **FIX** / **UNCERTAIN**

### 2. Cross-Review (Codex Watch)

生成 Review Capsule，通过 `codex-send-wait` 发送给 Codex watch session。

完整 capsule 格式和发送流程见 `~/.claude/commands/review.md`。

```bash
~/.claude/handoff/bin/codex-send-wait --cwd "$PWD" --mode review-only --payload-file "$CAPSULE_FILE"
```

如果 `codex-send-wait` 不可用或失败（TEMPORARY_DEGRADED_MODE）：
- 输出警告，不阻塞工作流
- 不输出 APPROVED，不假装 review 完成
- 详见 `commands/review.md` Step 3

### 3. Final Decision

| Claude | Codex watch | Result |
|--------|-------------|--------|
| PASS | APPROVED | → PASS (continue) |
| PASS | REQUIRES_CHANGES | → FIX (Codex items) |
| FIX | APPROVED | → FIX (Claude items) |
| FIX | REQUIRES_CHANGES | → FIX (merge items) |
| UNCERTAIN | * | → Claude makes final call |
| * | degraded_unreviewed | → Claude verdict stands, 标记未经 Codex 审查 |

## Output Schema

```json
{
  "mode": "step|task",
  "target": "<step title or task name>",
  "verdict": "PASS|FIX|BLOCKED",
  "claudeAssessment": {
    "verdict": "PASS|FIX|UNCERTAIN",
    "reason": "<reason>"
  },
  "codexAssessment": {
    "verdict": "APPROVED|REQUIRES_CHANGES|degraded_unreviewed",
    "fixItems": ["<item>"]
  },
  "finalDecision": {
    "verdict": "PASS|FIX|BLOCKED",
    "reason": "<reason>",
    "fixItems": ["<if FIX>"]
  }
}
```

## Mode-Specific Checklist

### step mode
- Done conditions satisfied?
- Code changes correct?
- No regressions introduced?

### task mode
- All acceptance criteria met?
- Gaps or missing pieces?
- Code quality issues?
- Documentation complete?
- Tests passing?

## Principles

1. **Unified schema**: Same output format for both modes
2. **Mode-specific prompts**: Different checklists per mode
3. **Traceable**: Full assessment captured for plan_log/report
4. **Codex watch 是唯一 cross-review 通道**: 不使用本地 agent 代替
