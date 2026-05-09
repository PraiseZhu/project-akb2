# cc-skill-stocktake L2 Baseline Snapshot

**Captured**: 2026-05-08
**Phase**: L2 Regression v1 — Step 2
**L1 Source**: `ai-infra/akb2-toolbox-evals/p1d-cc-skill-stocktake/`

## L1 Floor Status (at L2 start)

| Field | Value |
|-------|-------|
| toolbox_status | active |
| wiki_status | active |
| eval_level | L1 |
| automation_allowed | false |
| backlink_status | linked |
| last_phase | P1d-2026-05-06 |

## Five-File sha256 (byte-identical to L1 floor baseline — zero drift)

| File | sha256 |
|------|--------|
| toolbox/interfaces/cc-skill-stocktake.md | `5ea0fac2c095ec69d4272ee6a92f3c747d4e2052cdb5b07c7692fc3a90427192` |
| toolbox/tools/cc-skill-stocktake.md | `00789a38e59902c3bea036ba5a2ad46de3d43f210f62f00c5d5b098b5691e1c7` |
| toolbox/evals/cc-skill-stocktake.md | `9661931eb0d93978c01260bda2cd4ff70c1a95f4e4be3c228d250dc9e652a70a` |
| toolbox/registry/cc-skill-stocktake.md | `c4b14ea17c3c69e66d858dddd5866c58586735233eb74d937d885ea98add0cd7` |
| wiki/tools/cc-skill-stocktake.md | `27386cb87e356847e0b5961efcda184150e06e4e5f40f79fd4092978a7de844d` |

## SKILL.md sha256

`df16be83df67a04605b61294a210f9ad214b22e55ce40a45f34068dbaa2c48f2`  `~/.codex/skills/cc-skill-stocktake/SKILL.md`

## automation_allowed Invariant

`grep -rn "^automation_allowed: true" AKB2.0/toolbox/tools AKB2.0/wiki/tools` → **0 命中** ✓

## L1 Evidence Paths

- `ai-infra/akb2-toolbox-evals/p1d-cc-skill-stocktake/run-smoke.sh`
- `ai-infra/akb2-toolbox-evals/p1d-cc-skill-stocktake/fixtures/`
- `ai-infra/akb2-toolbox-evals/p1d-cc-skill-stocktake/outputs/`
- `ai-infra/akb2-toolbox-evals/p1d-cc-skill-stocktake/assertions.md`
- `ai-infra/akb2-toolbox-evals/p1d-cc-skill-stocktake/case-register.md`
- `ai-infra/akb2-toolbox-evals/p1d-cc-skill-stocktake/run-evidence.md`
- `ai-infra/akb2-toolbox-evals/p1d-cc-skill-stocktake/baseline-snapshot.md`

## Cross-Tool Dependency Note

`cc-skill-stocktake` `real_evaluator` lane in L2 regression will read:
`ai-infra/akb2-toolbox-evals/l2-regression-v1/cc-local-llm/recommendation.json`

Step 4 is blocked from starting until Step 3 (cc-local-llm) completes.

## Drift Check

All five-file sha256 values match `ai-infra/akb2-toolbox-closure/2026-05-07/file-sha256.txt`
(primary baseline captured 2026-05-08T00:20:00Z). **No drift detected.**
