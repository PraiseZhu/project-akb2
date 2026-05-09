---
title: P2 Lint v0 — Run Evidence
generated_at: 2026-05-07T12:46:46Z
scope: p2-toolbox-structure-lint-v0
runner: run-lint.sh
status: verified
---

# Lint v0 Run Evidence

Two runs were executed:
- Run 1: 2026-05-07T12:46:18Z (initial run; jq if-then-else missing `end`)
- Run 2: 2026-05-07T12:46:46Z (after fix; canonical run, all outputs valid)

All outputs from Run 2 are used as canonical evidence.

---

## §1 — Runner Execution

**Command**: `bash run-lint.sh`
**Exit code**: 0 (both runs)
**Stdout (Run 2)**:

```
=== AKB2.0 P2 Toolbox Structure Lint v0 ===
Run at: 2026-05-07T12:46:46Z

--- Part 1: Lint current 5 tools ---
  ✓ cc-executable-plan
  ✓ cc-review
  ✓ cc-local-llm
  ✓ cc-skill-stocktake
  ✓ cc-akb-search

Part 1 results:
  tools_scanned:       5
  rules_evaluated:     8
  total_findings:      40
  hard_fail_findings:  0
  handoff_findings:    2
  pass_count:          38

--- Part 2: Fixture boundary cases ---
  ✓ r01-missing-fivefile → fail (expected fail)
  ✓ r02-split-status → handoff (expected handoff)
  ✓ r03-workspace-missing → fail (expected fail)
  ✓ r04-sha-drift → fail (expected fail)
  ✓ r05-backlink-pending → fail (expected fail)
  ✓ r06-active-l0 → fail (expected fail)
  ✓ r07-eval-stale → handoff (expected handoff)
  ✓ r08-automation-true → fail (expected fail)

=== Lint v0 Complete ===
Part 1 (current 5 tools): 0 hard fail, 2 handoff, 38 pass
Part 2 (fixture cases):   8/8+0 pass
```

---

## §2 — Part 1 Summary: Current 5 Tools

| Metric | Value |
|--------|-------|
| tools_scanned | 5 |
| rules_evaluated | 8 |
| total_findings | 40 (= 5×8) |
| hard_fail_findings | **0** |
| handoff_findings | **2** |
| pass_count | 38 |

**Handoff findings** (2 total, both cc-akb-search, both expected):

| tool_slug | rule_id | evidence | reviewer_note |
|-----------|---------|----------|---------------|
| cc-akb-search | r02 | status split: {active,candidate} | status inconsistency; wiki active + 4件套 candidate; review if intentional |
| cc-akb-search | r05 | backlink_status: pending [blocked candidate] | mcp__akb-search not mounted (per P1a); pending is expected |

---

## §3 — Read-Only Verification: 5 SKILL.md sha256

All sha256 match §1 plan baseline (byte-identical, 0 mutations from lint runner):

| Tool | sha256 (full) | Baseline match |
|------|--------------|----------------|
| cc-executable-plan | `5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a` | ✓ |
| cc-review | `592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274` | ✓ |
| cc-local-llm | `3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050` | ✓ |
| cc-skill-stocktake | `df16be83df67a04605b61294a210f9ad214b22e55ce40a45f34068dbaa2c48f2` | ✓ |
| cc-akb-search | `b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982` | ✓ |

---

## §4 — P0 Gate: automation_allowed: true = 0

Global scan of all 25 tool 5件套 files:
```bash
grep -rn "^automation_allowed: true$" \
  "$AKB/toolbox" "$AKB/wiki/tools"
```
**Result**: (no output — 0 matches)

P0 gate: **CLEAR** (all 5 tools × 5件套 = 25 files clean)

---

## §5 — Part 2: Fixture Case Results

All 8 fixture boundary cases verified correct:

| Case | Rule | Expected | Actual | Result |
|------|------|---------|--------|--------|
| r01-missing-fivefile | r01 | fail | fail | ✓ PASS |
| r02-split-status | r02 | handoff | handoff | ✓ PASS |
| r03-workspace-missing | r03 | fail | fail | ✓ PASS |
| r04-sha-drift | r04 | fail | fail | ✓ PASS |
| r05-backlink-pending | r05 | fail | fail | ✓ PASS |
| r06-active-l0 | r06 | fail | fail | ✓ PASS |
| r07-eval-stale | r07 | handoff | handoff | ✓ PASS |
| r08-automation-true | r08 | fail | fail | ✓ PASS |

Fixture accuracy: **8/8 (100%)**

---

## §6 — Output Files Produced

| File | Status |
|------|--------|
| `outputs/lint-current-5-tools.json` | ✓ written, schema valid |
| `outputs/review-handoff.md` | ✓ written, 2 handoff items |
| `outputs/case-results/r01-missing-fivefile/result.json` | ✓ written |
| `outputs/case-results/r02-split-status/result.json` | ✓ written |
| `outputs/case-results/r03-workspace-missing/result.json` | ✓ written |
| `outputs/case-results/r04-sha-drift/result.json` | ✓ written |
| `outputs/case-results/r05-backlink-pending/result.json` | ✓ written |
| `outputs/case-results/r06-active-l0/result.json` | ✓ written |
| `outputs/case-results/r07-eval-stale/result.json` | ✓ written |
| `outputs/case-results/r08-automation-true/result.json` | ✓ written |
| `case-register.md` | ✓ written |
| `assertions.md` | ✓ written |
| `run-evidence.md` | ✓ this file |

---

## §7 — Runner Patch Log

One bug was found and fixed during §4 execution:

| ID | Description | Fix | Impact |
|----|------------|-----|--------|
| FIX-01 | jq if-then-else missing `end` keyword in fixture result.json assembly | Added `end` to close jq if expression (run-lint.sh line 258) | result.json files were empty in Run 1; fixed in Run 2 |

Run 1 outputs (lint-current-5-tools.json, review-handoff.md) were unaffected (different jq block).
Only case-results/*.json were affected; all regenerated correctly in Run 2.

Fix is a minimal 1-line patch to a jq filter; does not affect any rule logic or evidence.
