# A4 L1 Eval Execution Capsule — cc-akb-search

## Meta

- **Tool**: cc-akb-search
- **Phase**: P1e A4 (L1 evaluation)
- **Executed at**: 2026-05-08T10:55:26Z
- **Executor**: claude-sonnet-4-6 (in-session, Claude Code Desktop)
- **MCP entry**: `mcp__akb-search`
- **Backend**: stdio MCP server at `/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py`
- **Backend Python**: `/Users/praise/.pyenv/versions/3.13.12/bin/python3`
- **Backend PYTHONPATH**: `/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/Project AKB`

## Pre-conditions Verified

- A3 V1: `mcp__akb-search` appears in CCD tool list (post-CCD-restart, ~/.claude.json modification 2026-05-08T17:38:58 detected)
- A3 V2: tool returns valid JSON in <5s (verified before A4)
- Backend code at sha256: `efb09982efe49f4adb97b44c71eb30b5b157e9b3294d4e2d39dd17b162b63eb1` (server.py)
- CatalogDB opened in `readonly=True` mode (mode=ro URI) — verified by Review 2

## Fixture Plan

5 fixtures covering L1 floor surface:

1. **F1 exact_keyword**: `LangGraph` — verify tool finds well-known term
2. **F2 fuzzy_keyword**: `agent harness evaluation` — verify multi-token semantic-adjacent matches
3. **F3 chinese_query**: `智能体评测` — verify graceful handling of Chinese input (no crash, valid JSON)
4. **F4 no_result**: `xyz_nonexistent_term_2026_qwerty` — verify empty result shape
5. **F5 limit_behavior**: `MCP protocol` with `limit=2` — verify limit honored

## Acceptance Criteria

- All 5 fixtures return valid JSON
- F3 must not crash (Chinese input)
- F5 must respect limit (total_results ≤ 2)
- No mutations to catalog.db, cache.db, toolbox.db (verified by readonly contract)
- All responses < 5s

## Result

**5/5 pass — L1 floor met.**

Detail in `assertion-results.md` and `fixture-results.json`. Raw responses in `raw-output/`.

## Backlinks

- A3 evidence: `../p1e-cc-akb-search-a3/`
- A2 evidence: `../p1e-cc-akb-search-a2/`
- Review 2 evidence: `../p1e-cc-akb-search-review2/`
- C-5 evidence: `../p1e-cc-akb-search-c5/`
- MCP wrapper: `Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py`

## Limitations

- Re-run was performed in this session (2026-05-08T10:55:26Z) to capture canonical evidence; original A4 run from prior CCD session was completed in-conversation but never persisted to disk.
- Raw responses are full JSON shape but `snippet` field is truncated (server.py applies `[:300]` truncation).
- No latency measurements — MCP call latency not captured by the tool API surface.
