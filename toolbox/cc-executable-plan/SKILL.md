---
name: cc-executable-plan
description: Create decision-complete implementation plans using the Codex workspace plan template. Use whenever the user asks for a 方案, 计划, 执行计划, implementation plan, technical plan, roadmap, task plan, refactor plan, migration plan, rollout plan, or asks to plan before coding; especially use when the plan must be detailed enough to avoid review churn.
---

<!-- akb-backlink: /Volumes/AKB2/Obsidian/Praise Space/AKB2.0/wiki/tools/cc-executable-plan.md -->
<!-- akb-interface: /Volumes/AKB2/Obsidian/Praise Space/AKB2.0/toolbox/interfaces/cc-executable-plan.md -->

# Executable Plan

## Rule

When producing a plan, use the workspace implementation-plan template unless the user explicitly requests a different format.

Claude live template source of truth:

`/Users/praise/.claude/templates/plan-template.md`

Primary template:

`/Users/praise/AI-Agent/Codex/templates/plan-template.md`

The Codex primary template is a local copy of the Claude live template and must stay byte-for-byte aligned with it. Before producing a final user-facing plan, run a read-only drift check:

`diff -q /Users/praise/.claude/templates/plan-template.md /Users/praise/Library/Mobile\ Documents/com~apple~CloudDocs/Codex/templates/plan-template.md`

If the diff check reports drift, stop and report the template drift instead of producing the final plan.

If the current task is outside this workspace or the template file cannot be read, reproduce the same section structure from memory.

## Workflow

1. Ground the plan in repository facts before writing it: inspect relevant files, entrypoints, configs, schemas, tests, and dirty worktree state when available.
2. Fill every template section. Use `无` only when a section truly has no applicable content.
3. Fill `Review 对齐` explicitly:
   - Choose exactly one `REVIEW_DOMAIN`: `ai_infra`, `app_code`, `docs_content`, or `other`.
   - Set `REVIEW_FOCUS` to the 3-5 review dimensions most likely to catch P1/P2 failures.
   - Mark domain-specific notes as applicable or `无`; do not leave generic placeholders.
4. Make the plan decision-complete: implementers should not need to choose modules, function signatures, CLI/API contracts, schema migration behavior, fallback behavior, test fixtures, or out-of-bounds files.
5. Include explicit `不修改` boundaries for live runtimes, secrets, external services, persistent config, generated data, and unrelated user changes.
6. Include test isolation details whenever data stores, caches, workspace files, or external state are involved.
7. Before finalizing, self-review the plan against the checklist in the template. Fix blockers in the plan, not in prose after the plan.

## Output

For user-facing plans:

- Prefer Chinese when the user writes Chinese.
- Use the template headings exactly.
- If the user asks for a saved plan, write it under the workspace `plans/` directory using `YYYY-MM-DD-topic.md`.
- If the user asks only for chat output, return the plan in the message and avoid creating files.

## Minimum Bar

A valid executable plan must specify:

- Success criteria and non-goals.
- Review domain and focus dimensions aligned with the review template.
- Files/modules to add, modify, and not modify.
- CLI/API/function contracts with inputs, outputs, and error behavior.
- Data or schema migration strategy, or explicitly `无`.
- Edge cases and failure modes.
- Tests, fixtures, and validation commands.
- A clear boundary around live runtime changes and external side effects.
