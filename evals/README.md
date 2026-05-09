# AKB2.0 Toolbox Evals — C-4 L1 Smoke Fixtures

> **Read-only test specs prepared by Claude (coordination role).** Codex must
> execute the actual skill calls and write `run-evidence.md` files into the
> matching slug directory. **Without real Codex run-evidence, AKB2.0 evals
> stay at L0.**

## Layout

```
akb2-toolbox-evals/
├── c4a/                          # MUST run (low-risk skills)
│   ├── cc-executable-plan/
│   │   ├── fixture.md            # input spec (Claude prepared)
│   │   ├── assertions.md         # pass/fail criteria (Claude prepared)
│   │   └── run-evidence.md       # ← Codex must produce
│   └── cc-review/
│       ├── fixture-positive.md   # input: clean plan capsule
│       ├── fixture-negative.md   # input: capsule with deliberate P2
│       ├── assertions.md
│       └── run-evidence.md       # ← Codex must produce
└── c4b/                          # CONDITIONAL (read-only preflight first)
    └── cc-akb-search/
        ├── preflight.md          # read-only-entry check (Claude prepared)
        ├── fixture-queries.md    # 5 baseline queries (Claude prepared)
        ├── assertions.md
        └── run-evidence.md       # ← Codex must produce, only after preflight pass
```

## Hard rules

1. Claude does NOT execute skills. All `run-evidence.md` files must be
   produced by a Codex execution session calling the skill itself.
2. AKB2.0 `toolbox/evals/<slug>.md` upgrades from L0→L1 only after the
   matching `run-evidence.md` exists AND its assertions check out.
3. `~/.codex/skills/` is read-only this round. Never write fixtures there.
4. Even if everything passes, no skill becomes `automation_allowed: true`
   and no skill becomes `status: active` in this round.
