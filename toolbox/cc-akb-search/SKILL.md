---
name: "cc-akb-search"
description: ">-"
---

# akb-search

## Trigger

Use this skill only when the user explicitly invokes it or clearly asks for this specific integration/workflow.

Aliases: `akb-search`, `cc-akb-search`, `akb`, `search`

## Purpose

>-

## Workflow

1. Confirm the user intent matches this skill and check the dependency notes below.
2. Prefer Codex-native tools and current workspace instructions over Claude-only slash commands or tool names from the source skill.
3. Read `references/original-skill.md` only when the lean instructions here are not enough.
4. If the source skill mentions external services, authentication, MCP, deployment, or project-specific paths, stop and verify the required context before taking action.
5. Keep generated work inside the current user-approved workspace unless the user explicitly directs otherwise.

## Dependencies

- github: external or local dependency; verify availability before use.
- mcp: external or local dependency; verify availability before use.

## Safety

- Install status: `restricted_install`; risk: `high`.
- Source flags: claude_runtime_terms, mcp_dependency, external_integration, project_specific.
- Do not assume Claude Code slash commands, hooks, MCP servers, or `~/.claude` runtime behavior exists in Codex.
- Do not use or create secrets. Ask for explicit user direction before any external login, deployment, destructive command, or persistent configuration change.

## Source Notes

- Original Claude skill: `akb-search`.
- Source metadata name: `akb-search`.
- Source hash: `4214f196f73e1ec7f9c2d5d2d749951f48401e9a16621e3ebdf1e5f059edc0af`.
- The original skill is available for reference.
- No bundled source resources were copied for active use.
