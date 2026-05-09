---
generated_at: 2026-05-07T17:34:23Z
scope: current_5_tools
lint_run: p2-toolbox-structure-lint-v1
---

# Lint v1 — Reviewer Handoff Items

Items below require human review. These are NOT hard fails — they represent ambiguous states that need contextual judgment.

| tool_slug | rule_id | evidence | reviewer_note |
|-----------|---------|----------|---------------|
| cc-akb-search | r02 | status split: {active,candidate} | status inconsistency across 5件套; review if intentional transition state (eg wiki active + 4件套 candidate) |
| cc-akb-search | r04 | sha12 match (interface): b669380de8a0; eval workspace_version field absent | blocked candidate (status=candidate); eval field absent expected; no hard fail per P1a exception |
| cc-akb-search | r05 | backlink_status: pending [cc-akb-search blocked candidate] | cc-akb-search: mcp__akb-search not mounted (per P1a); pending is expected state; not a hard fail |

## Resolution Guide

- **r02 status split**: Confirm if split is intentional transition state (wiki active + 4件套 candidate is acceptable). If yes: no action needed. If no: update 5件套 to consistent status.
- **r05 cc-akb-search pending**: Expected due to blocked candidate / mcp__akb-search not mounted. No action until mcp__akb-search is mounted.
- **r07 eval stale**: Review if stale threshold is acceptable given tool state. New tools are naturally stale; running L1 eval resolves this.
