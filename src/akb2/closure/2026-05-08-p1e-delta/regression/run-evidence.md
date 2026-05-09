# bad-delta Regression Run Evidence

**Date**: 2026-05-08
**Phase**: L2 Regression v1 — Step 1
**Purpose**: Verify merge-baselines.sh exits 2 when delta slug is outside allowed_slugs

## Input Fixture

`bad-delta-cc-review.json` — identical to `delta-manifest.json` except:
- `tools_changed[0].slug` changed from `"cc-akb-search"` to `"cc-review"`
- `allowed_slugs` unchanged: `["cc-akb-search"]`

## Command

```bash
bash merge-baselines.sh \
  --primary <Codex>/ai-infra/akb2-toolbox-closure/2026-05-07/closure-manifest.json \
  --delta regression/bad-delta-cc-review.json \
  --output $TMPDIR/effective-bad.json
```

## Output

**Exit code**: `2`

**stderr**:
```
ERROR: delta tools_changed[cc-review] is outside allowed_slugs scope
```

**stdout**: (empty)

**Effective manifest written**: no (`output_exists=no`)

## Verdict

PASS — `allowed_slugs` guard correctly blocks a delta that tries to modify `cc-review`
while `allowed_slugs` only permits `cc-akb-search`. Exit 2 as expected.

## Related Files

- `bad-delta-cc-review.json` — input fixture
- `exit-code.txt` — persisted exit code
- `../merge-baselines.sh` — script under test (v3, P1e-2026-05-08)
- `../delta-manifest.json` — canonical delta (allowed_slugs guard source)
