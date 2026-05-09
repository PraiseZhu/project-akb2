# Automation Gate v0 — Baseline Snapshot

**Date**: 2026-05-08
**Phase**: Automation Gate v0 (dry-run mechanism only)
**Runner**: run-gate.sh (read-only, assert-only, no automation enabled)

---

## SKILL.md SHA256 Checksums

| Tool | SHA256 |
|------|--------|
| cc-local-llm | `3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050` |
| cc-skill-stocktake | `df16be83df67a04605b61294a210f9ad214b22e55ce40a45f34068dbaa2c48f2` |
| cc-akb-search | `b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982` |
| cc-executable-plan | `5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a` |
| cc-review | `592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274` |

Path pattern: `~/.codex/skills/<slug>/SKILL.md`

---

## Invariant State (disk-verified 2026-05-08)

| Invariant | Value | Status |
|-----------|-------|--------|
| automation_allowed=true global hits | 0 | pass |
| closure-manifest sha256 | `0fbd53703ff58802299ba8cb04f5290e4b973c124729376dc341f862f06f90cc` | pass |
| Promotion Gate v0 README exists | ai-infra/akb2-toolbox-promotion-gate/v0/README.md | pass |
| Automation Gate v0 README exists | ai-infra/akb2-toolbox-automation-gate/v0/README.md | pass |

---

## L2 Regression Evidence (disk-verified)

| Tool | L2 verdict | block_reason |
|------|-----------|-------------|
| cc-local-llm | blocked | backend_unavailable: 127.0.0.1:8080 unreachable (curl_exit=7) |
| cc-skill-stocktake | blocked | cc-local-llm backend blocked per upstream recommendation.json |

Note: cc-executable-plan and cc-review have no L2 regression v1 evidence (grandfathered at L2).

---

## Tool eval_level (from closure-manifest 2026-05-07)

| Tool | eval_level | toolbox_status |
|------|-----------|----------------|
| cc-executable-plan | L2 | active |
| cc-review | L2 | active |
| cc-local-llm | L1 | active |
| cc-skill-stocktake | L1 | active |
| cc-akb-search | L0 | candidate |

---

## Expected dry-run-current Output

```json
{
  "global_verdict": "blocked_l2_verdict_blocked",
  "per_tool": [
    {"slug": "cc-executable-plan",  "eligible": false, "blocker_reason": "blocked_no_l2_regression_evidence"},
    {"slug": "cc-review",           "eligible": false, "blocker_reason": "blocked_no_l2_regression_evidence"},
    {"slug": "cc-local-llm",        "eligible": false, "blocker_reason": "blocked_l1_floor"},
    {"slug": "cc-skill-stocktake",  "eligible": false, "blocker_reason": "blocked_l1_floor"},
    {"slug": "cc-akb-search",       "eligible": false, "blocker_reason": "blocked_l0_or_below"}
  ],
  "would_create_automation": false
}
```

Note: global_verdict uses the first blocker encountered in tool iteration order. cc-executable-plan fails at l2_regression_promote (no evidence) → global_verdict = blocked_no_l2_regression_evidence. But per plan spec, the dominant blocker for the batch is blocked_l2_verdict_blocked (cc-local-llm/cc-skill-stocktake have L2 evidence but verdict=blocked). Runner will use the most semantically significant blocker.

---

*Generated during Automation Gate v0 Step 1 (baseline snapshot + invariant precheck).*
