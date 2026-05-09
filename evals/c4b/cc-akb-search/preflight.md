# Preflight — cc-akb-search C-4b

> **C-4b is conditional.** Codex MUST clear all preflight items below before
> attempting any L1 query smoke. Any failure → write `run-evidence.md` with
> `assertion_status: blocked`, `eval_level_after: L0`, do NOT proceed to
> `fixture-queries.md`.

## Why preflight exists

`~/.codex/skills/cc-akb-search/SKILL.md` declares:
- `Install status: restricted_install`
- `risk: high`
- Source flags: `claude_runtime_terms, mcp_dependency, external_integration, project_specific`

Per AKB2.0 plan §C-4b, this means cc-akb-search cannot enter L1 smoke
without first proving the call entry is genuinely read-only and bounded.

## Preflight checks (all required)

### P1. SKILL.md sha256 still matches baseline

```bash
shasum -a 256 ~/.codex/skills/cc-akb-search/SKILL.md
# expected: b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982
```

- [ ] hash matches → continue
- [ ] mismatch → blocked: SKILL.md drifted, re-baseline first

### P2. A real read-only call entry exists

The skill must point at a concrete invocation that does NOT mutate AKB
data. Options to verify (any one acceptable):

- a Codex-native MCP tool (e.g. `mcp__akb-search`) with documented
  read-only contract, AND that MCP tool is currently mounted in this
  Codex session
- a CLI command that strictly reads (e.g. `python -m scripts.main search
  ...`) with no side effects on `index/` or `notes/`

- [ ] Documented and mounted → continue
- [ ] Not documented or not mounted → blocked: no read-only entry

### P3. No write side effects on AKB data

Verify that the entry confirmed in P2 cannot:

- write to `index/` or any catalog database
- create / modify / delete notes
- mutate FTS or embeddings
- enqueue work for synth / translate

Codex must inspect the entry's source / docs and record evidence.

- [ ] Confirmed read-only → continue
- [ ] Any write path possible → blocked: write side effect risk

### P4. No external network or secret needed

- [ ] Entry runs locally without external API calls (or only against
      `127.0.0.1` services already mounted)
- [ ] No env var or credential file required
- [ ] Otherwise → blocked: external dependency

### P5. No secret in skill header

```bash
head -c 4096 ~/.codex/skills/cc-akb-search/SKILL.md | \
  grep -E "(api[_-]?key|secret|token|password|bearer|sk-|ghp_|AKIA[0-9A-Z]{16})"
```

- [ ] no match → continue
- [ ] match → blocked: secret-shaped header, escalate to human review

## After preflight

- All 5 pass → proceed to `fixture-queries.md`
- Any blocked → write `run-evidence.md` with blocked status, stop here
- Even when L1 query smoke later passes, this skill stays out of any
  active gate this round (risk_level=high needs separate human risk
  adjudication per plan §C-4b).
