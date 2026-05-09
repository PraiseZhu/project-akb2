---
name: "cc-skill-stocktake"
description: "Use when auditing Claude skills and commands for quality. Supports Quick Scan (changed skills only) and Full Stocktake modes with sequential subagent batch evaluation."
---

<!-- akb-backlink: /Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0/wiki/tools/cc-skill-stocktake.md -->
<!-- akb-interface: /Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0/toolbox/interfaces/cc-skill-stocktake.md -->

# skill-stocktake

## Trigger

Use this skill only when the user explicitly invokes it or clearly asks for this specific integration/workflow.

Aliases: `skill-stocktake`, `cc-skill-stocktake`, `skill`, `stocktake`

## Purpose

Use when auditing Claude skills and commands for quality. Supports Quick Scan (changed skills only) and Full Stocktake modes with sequential subagent batch evaluation.

## Workflow

1. Confirm the user intent matches this skill and check the dependency notes below.
2. Prefer Codex-native tools and current workspace instructions over Claude-only slash commands or tool names from the source skill.
3. Read `references/original-skill.md` only when the lean instructions here are not enough.
4. If the source skill mentions external services, authentication, MCP, deployment, or project-specific paths, stop and verify the required context before taking action.
5. Keep generated work inside the current user-approved workspace unless the user explicitly directs otherwise.

## Dependencies

- None detected beyond normal Codex tools.

## Safety

- Install status: `restricted_install`; risk: `high`.
- Source flags: frontmatter_fix_required, bundled_resources, script_resources, claude_runtime_terms, credential_or_auth, project_specific.
- Do not assume Claude Code slash commands, hooks, MCP servers, or `~/.claude` runtime behavior exists in Codex.
- Do not use or create secrets. Ask for explicit user direction before any external login, deployment, destructive command, or persistent configuration change.

## Source Notes

- Original Claude skill: `skill-stocktake`.
- Source metadata name: `missing`.
- Source hash: `c03bfae29e91ada17e818a058a6489e19e64dcc52179d65885c40e7afc25d1cc`.
- The original skill is available for reference.
- 3 safe source resource file(s) were copied under `references/source-bundled-resources/`. Read only the relevant file when needed.
