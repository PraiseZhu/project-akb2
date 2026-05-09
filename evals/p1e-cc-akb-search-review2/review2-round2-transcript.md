# Review 2 Round 2 — code-reviewer subagent

**Channel**: Claude code-reviewer subagent (sonnet model)
**Domain**: ai_infra
**Focus**: re-verify R1 P1/P2a fixes; check for new regressions
**Verdict**: REQUIRES_CHANGES

## Changes since Round 1

1. `Curator.__init__`: `self.cache = cache` (no `or` default)
2. `Curator.search()`: 2 `is not None` guards (read at line 341, write at line 366)
3. `ToolboxDB.connect()`: split into readonly/writable branches via `self._readonly = skip_init`
4. `_search_toolbox()`: existence check + `skip_init=True`

## Verification

### [P1 RESOLVED] Curator cache bypass

`search.py:52` constructs cache conditionally. `curator.py:341-344` and `366-367` guard `self.cache.get/set`. In readonly server `router=None` so `cache=None` and neither call fires.

### [P2a RESOLVED] ToolboxDB WAL pragma on readonly path

`toolbox/db.py:80-88` readonly branch uses `mode=ro` URI, no WAL pragma, no commit.

## NEW Findings

### [HIGH] run_full_curation() calls self.cache.purge_expired() without None guard

**File**: `scripts/pipeline/curator.py:1114`

```python
purged = self.cache.purge_expired()  # AttributeError when cache=None
```

The Round 1 fix added None semantics for `Curator(cache=None)` but didn't update `run_full_curation()`. Line 1114 is wrapped in `try/except Exception` so the AttributeError is silently swallowed with misleading warning `Cache purge failed: ...`.

**Impact**: `scripts/commands/curator.py` (CLI curator) constructs `Curator(db=, llm=, router=)` without explicit cache. Previously got `KnowledgeCache()` via `or` default. Now gets `None`, hits AttributeError, silently logs, skips purge.

**Fix required**:
```python
if self.cache is not None:
    purged = self.cache.purge_expired()
    ...
```

### [P1 unverifiable] server.py init_tables() call

The diff snippet only showed `db = CatalogDB(readonly=True)` line. Need to verify `init_tables()` is NOT called immediately after, otherwise readonly connection will raise `sqlite3.OperationalError: attempt to write a readonly database` on startup.

**Verification required**: confirm absence of `db.init_tables()` in server.py.

## Outcome

**Verdict: REQUIRES_CHANGES** — fix HIGH (`run_full_curation` None guard) + verify server.py init_tables() absence.
