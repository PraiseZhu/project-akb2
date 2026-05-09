# Regression: discovery-update Field Propagation

**Date**: 2026-05-08
**Author**: claude-sonnet-4-6 (v5 closure correction)
**Purpose**: Verify that `merge-baselines.sh` correctly propagates `a1_mcp_discovery_update` from delta to effective manifest top-level `a1_mcp_discovery`, and rewrites `pending_next_phases.phase_3_cc_akb_search_unblock` to `complete`.

## Input Fixture

```
regression/discovery-update-fixture-good.json
```

Identical to `delta-manifest.json` (sha256: 13d890afef87d50fbfdf7f34dd43b0f65286cdf791dfaf3e25104117686f7f54).

Key fields:
- `a1_mcp_discovery_update.status = "mounted"`
- `a1_mcp_discovery_update.verified_at = "2026-05-08T10:55:26Z"`
- `tools_changed[0].slug = "cc-akb-search"`

## Command

```bash
bash merge-baselines.sh \
  --primary ../2026-05-07/closure-manifest.json \
  --delta regression/discovery-update-fixture-good.json \
  --output "$TMPDIR/effective-discovery-test.json"
```

## stdout/stderr

```
✅ effective manifest written: /tmp/claude-501/effective-discovery-test.json
✅ summary written: /tmp/claude-501/effective-discovery-test-summary.md
```

## Exit Code

```
0
```

(See `discovery-update-exit-code.txt`)

## Assertions

| # | Assert | Expected | Actual | Pass? |
|---|--------|----------|--------|-------|
| A1 | exit code | 0 | 0 | ✅ |
| A2 | `.a1_mcp_discovery.status` | `"mounted"` | `"mounted"` | ✅ |
| A3 | `.a1_mcp_discovery.implication` not containing `"blocked candidate"` | 0 occurrences | 0 | ✅ |
| A4 | `.pending_next_phases.phase_3_cc_akb_search_unblock.status` | `"complete"` | `"complete"` | ✅ |
| A5 | existing `bad-delta-cc-review.json` regression still exits 2 | 2 | 2 | ✅ (see `exit-code.txt`) |

## Verdict

**PASS** — `a1_mcp_discovery_update` propagation verified. New exit 6 guard active in script.

## Related Files

- `merge-baselines.sh` — patched script (v5 correction, 2026-05-08)
- `discovery-update-fixture-good.json` — input fixture (copy of delta-manifest.json)
- `discovery-update-exit-code.txt` — captured exit code (0)
- `bad-delta-cc-review.json` — existing regression, unmodified
- `exit-code.txt` — existing regression exit code (2), unmodified
