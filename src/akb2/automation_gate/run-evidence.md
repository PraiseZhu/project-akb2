# Automation Gate v0 — Run Evidence

**Generated**: 2026-05-08T14:15:10Z
**Plan**: Automation Gate v0 (Dry-Run Mechanism)

---

## Self-Test

```
[self-test] Fixtures: .../fixtures
[PASS] blocked-l1-floor (global_verdict=blocked_l1_floor)
[PASS] blocked-l2-verdict-blocked (global_verdict=blocked_l2_verdict_blocked)
[PASS] blocked-no-promotion-evidence (global_verdict=blocked_no_promotion_evidence)
[PASS] hard-fail-automation-already-true (global_verdict=hard_fail_automation_already_true)
[PASS] positive (global_verdict=all_eligible)

Self-test: 5 passed, 0 failed
exit=0
```

## Dry-Run Current

```
[result] global_verdict=blocked_l2_verdict_blocked
[result] outputs written → .../outputs/current/
exit=1
```

```json
{
  "global_verdict": "blocked_l2_verdict_blocked",
  "would_create_automation": false,
  "invariants": {
    "automation_allowed_true_count": 0,
    "closure_manifest_sha256_match": true
  }
}
```

## Per-Tool Results

| Tool | eligible | blocker_reason |
|------|----------|----------------|
| cc-executable-plan | false | blocked_no_l2_regression_evidence |
| cc-review | false | blocked_no_l2_regression_evidence |
| cc-local-llm | false | blocked_l1_floor |
| cc-skill-stocktake | false | blocked_l1_floor |
| cc-akb-search | false | blocked_l0_or_below |

## Invariant Checks

| Check | Result |
|-------|--------|
| automation_allowed=true global count | 0 (pass) |
| closure-manifest sha256 match | true (pass) |

## Review

- Canonical Codex Watch `/review code`
- **VERDICT: APPROVED**
- Result: `~/.claude/review-baselines/7c8cec4e795a/result-20260508T141327Z.md`
- Findings: none
