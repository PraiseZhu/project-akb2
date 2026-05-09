# cc-akb-search A4 L1 Eval — Assertion Results

| Fixture | Category | Query | Limit | Expected | Actual | Pass |
|---------|----------|-------|-------|----------|--------|------|
| F1 | exact_keyword | `LangGraph` | 5 | ≥1 result, contains LangGraph/LangChain, valid JSON | 3 results, all relevant | ✅ |
| F2 | fuzzy_keyword | `agent harness evaluation` | 5 | ≥1 result, semantic-related (harness/agent/evaluation) | 4 results, all relevant | ✅ |
| F3 | chinese_query | `智能体评测` | 5 | Graceful handling, valid JSON | 0 results, valid JSON, no crash | ✅ |
| F4 | no_result | `xyz_nonexistent_term_2026_qwerty` | 5 | 0 results, valid JSON | 0 results | ✅ |
| F5 | limit_behavior | `MCP protocol` | 2 | total_results ≤ limit | 2 results | ✅ |

**Total: 5/5 pass — L1 floor met.**

## L1 Floor Acceptance Criteria

L1 floor for an MCP search tool:
- Tool returns valid JSON for all input categories
- Tool handles Chinese/Unicode input without crash
- Tool respects limit parameter
- Tool gracefully returns empty results for non-matches
- No DB writes during read-only search (verified via readonly contract — see Review 2)
- Response time < 5s per call

All criteria met.

## Out-of-Scope (deferred to later plans)

- Chinese FTS tokenization quality (search-quality v0)
- Semantic search behavior with router=None (degrades to keyword — covered in code review)
- Result ranking quality (search-quality v0)
- Multi-round consistency (L2 regression v1)
