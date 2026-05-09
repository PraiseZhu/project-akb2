# cc-local-llm L2 Run Evidence

**Date**: 2026-05-08T12:13:24Z
**Verdict**: blocked
**Reason**: backend 127.0.0.1:8080 unreachable (connection refused)

## Backend Probe

```
curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8080/v1/models
HTTP status: 000 (connection refused)
```

## Actions Taken

- Backend probe: FAIL (connection refused)
- LLM calls: skipped (not attempted)
- Output files: none written (no calls made)
- SKILL.md sha256: verified (no drift)
- automation_allowed=true: 0 hits (invariant preserved)

## Recommendation

See `recommendation.json` → `verdict: "blocked"`

Backend must be running at 127.0.0.1:8080 before re-running L2 regression.
