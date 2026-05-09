# A4 L1 Eval Run Evidence — cc-akb-search

## Run identity

- Date: 2026-05-08
- Window: 10:55:26Z – 10:55:30Z (sub-5s for all 5 fixtures combined)
- Channel: Claude Code Desktop, MCP tool `mcp__akb-search`
- Session: continuation of P1/P2 review-fix cycle

## Procedure

1. Snapshot SHA256 of read-only baselines (primary closure, runner, MCP wrapper, 5-file drafts)
2. Execute 5 fixtures via parallel MCP calls (single tool-call batch)
3. Record raw JSON response per fixture to `raw-output/F{1-5}-*.json`
4. Compute assertions per fixture, record to `assertion-results.md`
5. Aggregate to `fixture-results.json`

## Verification facts

| Item | Value |
|------|-------|
| Primary closure SHA256 (pre-run) | `0fbd53703ff58802299ba8cb04f5290e4b973c124729376dc341f862f06f90cc` |
| Matrix runner SHA256 (pre-run) | `78dfc2a688bba254b840ac3cfccc6b5646e4cdf39d45b04f0a3a3d22151d5b13` |
| MCP server.py SHA256 (pre-run) | `efb09982efe49f4adb97b44c71eb30b5b157e9b3294d4e2d39dd17b162b63eb1` |
| Fixtures executed | 5 |
| Fixtures passed | 5 |
| Tool crashes | 0 |
| Invalid JSON responses | 0 |
| DB write evidence | None observed (readonly contract) |
| Network call evidence | None observed (router=None) |

## Readonly contract evidence

The MCP server is invoked with:
- `CatalogDB(readonly=True)` → opens `file:path?mode=ro` URI; SQLite engine rejects writes
- `router=None` → no LLM, no `KnowledgeCache()`, no semantic embedding
- `_search_toolbox()` checks `_TOOLBOX_DB_PATH.exists()` then `ToolboxDB(skip_init=True)` → no DDL writes

These were verified via 3 rounds of code-reviewer subagent (see `../p1e-cc-akb-search-review2/`).

## Outcome

**5/5 PASS — cc-akb-search meets L1 floor.**

This evidence is consumed by:
- C-5 verdict (`../p1e-cc-akb-search-c5/c5-verdict.json`)
- Delta manifest (`../../akb2-toolbox-closure/2026-05-08-p1e-delta/delta-manifest.json`)
- Phase C report (`AKB2.0/docs/phase-c-reports/toolbox-l1-floor-closure-2026-05-08.md`)
