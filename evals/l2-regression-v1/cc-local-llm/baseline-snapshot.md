# cc-local-llm L2 Baseline Snapshot

**Captured**: 2026-05-08
**Phase**: L2 Regression v1 — Step 2
**L1 Source**: `ai-infra/akb2-toolbox-evals/p1c-cc-local-llm/`

## L1 Floor Status (at L2 start)

| Field | Value |
|-------|-------|
| toolbox_status | active |
| wiki_status | active |
| eval_level | L1 |
| automation_allowed | false |
| backlink_status | linked |
| last_phase | P1c-2026-05-06 |

## Five-File sha256 (byte-identical to L1 floor baseline — zero drift)

| File | sha256 |
|------|--------|
| toolbox/interfaces/cc-local-llm.md | `941ffed988d6234e7d1e18d5f804367402024d3376115c1c22fb9232d832211f` |
| toolbox/tools/cc-local-llm.md | `6dea0a7abec61140c07d77a1683ab067cc180a458f041da9bccd85368de69bdf` |
| toolbox/evals/cc-local-llm.md | `5271b906a94860cf899bd4bd7c8be09134ffba5018658633aac0875bfa93b587` |
| toolbox/registry/cc-local-llm.md | `69b08b16ec0e85dd89c3f20b37b87025e0100e1de025d506579816d988178374` |
| wiki/tools/cc-local-llm.md | `33d3fc9fdda0fa9f0a8e9d98c0bac2372ac10b685f45e8cc4e2cc181e7660bf4` |

## SKILL.md sha256

`3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050`  `~/.codex/skills/cc-local-llm/SKILL.md`

## automation_allowed Invariant

`grep -rn "^automation_allowed: true" AKB2.0/toolbox/tools AKB2.0/wiki/tools` → **0 命中** ✓

## L1 Evidence Paths

- `ai-infra/akb2-toolbox-evals/p1c-cc-local-llm/run-smoke.sh`
- `ai-infra/akb2-toolbox-evals/p1c-cc-local-llm/fixtures/`
- `ai-infra/akb2-toolbox-evals/p1c-cc-local-llm/outputs/`
- `ai-infra/akb2-toolbox-evals/p1c-cc-local-llm/assertions.md`
- `ai-infra/akb2-toolbox-evals/p1c-cc-local-llm/case-register.md`
- `ai-infra/akb2-toolbox-evals/p1c-cc-local-llm/run-evidence.md`

## Drift Check

All five-file sha256 values match `ai-infra/akb2-toolbox-closure/2026-05-07/file-sha256.txt`
(primary baseline captured 2026-05-08T00:20:00Z). **No drift detected.**
