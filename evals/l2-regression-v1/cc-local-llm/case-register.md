# cc-local-llm L2 Case Register

**Phase**: L2 Regression v1
**Date**: 2026-05-08
**Total Fixtures**: 10
**LLM Call Config**: temperature=0, seed=hash(fixture_id), max_tokens=per-fixture, stream=false
**Rounds per Fixture**: 3 (except f10 which is backend-probe, rounds=1)
**Stability Threshold**: 3/3 same verdict (f10: n/a)
**Backend**: 127.0.0.1:8080 /v1/chat/completions (llama-swap)

## Case Table

| ID | Category | Stability Check | Max Tokens | Expected Verdict |
|----|----------|----------------|------------|-----------------|
| f01-summary-stability | summary_stability | semantic_label_match | 200 | pass |
| f02-summary-style | summary_style | semantic_label_match | 100 | pass |
| f03-classification-label | classification | exact_match | 20 | pass |
| f04-json-extract-schema | json_extract | json_schema_match | 100 | pass |
| f05-json-extract-strict | json_extract_strict | json_exact_match | 80 | pass |
| f06-long-text-chunked | long_text | semantic_label_match | 200 | pass |
| f07-system-anchor | system_anchor | exact_prefix_match | 100 | pass |
| f08-reasoning-only-fail | hard_fail_detection | semantic_label_match | 10 | pass |
| f09-empty-content-fail | hard_fail_detection | non_empty_stable | 10 | pass |
| f10-backend-unavailable | backend_probe | n_a | 10 | blocked (runner exit 2) |

## Coverage Map (L1 → L2 Extensions)

| L1 Category | L2 Extension |
|-------------|-------------|
| 01-summary | f01 (stability), f02 (style), f06 (long text) |
| 02-classification | f03 (exact label) |
| 03-json-extract | f04 (schema), f05 (strict) |
| 05-system-anchor | f07 (anchor propagation) |
| (new in L2) | f08 (reasoning-only hard fail), f09 (empty content), f10 (backend probe) |

## Notes

- Seeds are pre-assigned per fixture_id (see input.json), not dynamically computed
- f10 is a meta-fixture: runner checks backend reachability before attempting LLM call
- Fixtures f01-f09 require backend to be available; if backend blocked, all mark blocked
- No `cc-akb-search` or AKB database calls in any fixture
- Output written to `outputs/<fixture_id>/<round>/` only; no writes to AKB2.0 or SKILL.md
