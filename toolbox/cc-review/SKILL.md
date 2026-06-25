---
name: "cc-review"
description: "全中文结构化审查，用于计划或已完成工作的 review/final review/计划审查/代码验收。按计划或代码模板输出可复制的 fenced markdown 审查结果。"
---

<!-- akb-backlink: /Volumes/AKB2/Obsidian/Praise Space/AKB2.0/wiki/tools/cc-review.md -->
<!-- akb-interface: /Volumes/AKB2/Obsidian/Praise Space/AKB2.0/toolbox/interfaces/cc-review.md -->

# review

## Trigger

This skill may be used when the user request matches the description or aliases.

Aliases: `review`, `cc-review`

## Purpose

Produce high-signal review feedback with the correct template:

- Use `计划审查结果` for plans, PRDs, proposals, implementation strategies, or "计划一下/审查计划".
- Use `代码审查结果` for completed work, diffs, files, execution results, final review, or "验收/执行完了".
- If the request is ambiguous, infer from the artifact in front of you. No diff or changed files usually means plan review; concrete files or executed changes means code review.

## Workflow

1. Review only; do not edit files unless the user explicitly asks to fix or implement.
2. Select `审查领域`: `AI 基础设施`, `应用代码`, `文档内容`, or `其他`.
   - For plan reviews, first read the plan's `Review 对齐` section if present.
   - If the plan declares `REVIEW_DOMAIN`, use that domain unless it clearly conflicts with the artifact; if overriding it, state the reason in `已检查范围`.
   - If an executable implementation plan lacks `Review 对齐`, treat that as a plan gap and report it when it can cause review drift.
3. Use `REVIEW_FOCUS` from the plan, when present, as the first-pass `已检查范围` rows; translate scope dimensions before final output, and add missing domain-matrix dimensions only when they are P1/P2 relevant.
4. Use the exact template from `references/structured-review-templates.md` — lightweight single-pass format.
5. `审查模式：单轮` — one pass, catch obvious bugs, minimal tokens, no multi-round escalation.
6. 问题 are P1/P2 only with 5 fields: 问题/证据/影响/最小修复/验收. P3 items go in 剩余风险.
7. Follow-up reviews: check prior blockers + direct downstream only. New blocking 问题 allowed only via narrow escape hatch (current-diff P1/P2 blocker with `changed_in_this_round` + `why_not_scope_creep`).
8. Prefer Codex-native tools and current workspace instructions over Claude-only slash commands or tool names from the source skill.
9. Read `references/original-skill.md` only when the lean instructions here are not enough.

## Dependencies

- None detected beyond normal Codex tools.

## Safety

- Install status: `convert_then_install`; risk: `low`.
- Source flags: bundled_resources.
- Do not assume Claude Code slash commands, hooks, MCP servers, or `~/.claude` runtime behavior exists in Codex.
- Do not use or create secrets. Ask for explicit user direction before any external login, deployment, destructive command, or persistent configuration change.

## Output Contract

Use Chinese by default when the user writes Chinese. Keep findings first. `需要修改` is only for goal failure, explicit requirement conflict, data/security/irreversible risk, or missing core validation. Put non-blocking suggestions in `剩余风险`. P3 items never appear as blocking 问题.

All user-facing review results MUST be returned inside one fenced `markdown` code block.
Do this for every `计划审查结果` and `代码审查结果`, regardless of whether the user asks for a reusable block.
Do not add prose outside the fenced block.

When the user writes Chinese, all user-visible review template language MUST be Chinese:

- Titles, section headings, field names, verdict values, review-scope values, status values, empty-state text, and scope-check dimension names must be Chinese.
- Internal English enum values such as `ai_infra`, `single_pass`, `APPROVED`, `pass`, or `trigger surface` may be used for reasoning only; translate them before final output.
- English is allowed only when it is technical evidence: file paths, commands, function names, variable names, config keys, enum values quoted from source material, raw logs, raw errors, and third-party product names.
- Write `无` for empty findings or residual risks; do not write `none`.

## Source Notes

- Original Claude skill: `review`.
- Source metadata name: `review`.
- Source hash: `22d993f4803b53515187daf519f74a1f7a5f55bd741fa229695801b7fadecdb3`.
- The original skill is available for reference.
- 1 safe source resource file(s) were copied under `references/source-bundled-resources/`. Read only the relevant file when needed.
- Do not inherit output format from `references/original-skill.md` or `references/source-bundled-resources/`; `references/structured-review-templates.md` is the only authoritative output template.
