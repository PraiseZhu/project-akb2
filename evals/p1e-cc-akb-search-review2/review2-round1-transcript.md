# Review 2 Round 1 — code-reviewer subagent

**Channel**: Claude code-reviewer subagent (sonnet model)
**Domain**: ai_infra
**Focus**: readonly contract — 0 DB writes, 0 file writes, 0 network calls
**Verdict**: REQUIRES_CHANGES

## Files reviewed (initial diff)

1. `Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py` — `db = CatalogDB(readonly=True)`
2. `Project AKB/scripts/utils/db.py` — `CatalogDB.__init__(readonly=False)`, `connect()` branch
3. `Project AKB/scripts/core/search.py` — `cache=None when router is None`, `_search_toolbox` skip_init
4. `Project AKB/scripts/toolbox/db.py` — `ToolboxDB(skip_init=False)`

## Findings

### [P1] Curator.__init__ silently bypasses `cache=None` — KnowledgeCache created anyway

**File**: `scripts/pipeline/curator.py:68`

```python
self.cache = cache or KnowledgeCache()
```

The fix in `search.py` passes `cache=None` to `Curator(...)` when `router is None`, intending to suppress KnowledgeCache. But `None or KnowledgeCache()` evaluates the right-hand side unconditionally — `KnowledgeCache()` is always instantiated.

`KnowledgeCache.__init__` calls `self.db_path.parent.mkdir(...)` (filesystem write) and `self._init_db()` which executes `conn.executescript(_SCHEMA)` (DDL write to cache.db).

`Curator.search()` at line 365 calls `self.cache.set(...)` unconditionally after every cache miss. **Readonly contract violated on every search call not already cached.**

**Fix required**: Make `Curator` treat `cache=None` as no-op, with `is not None` guards before all `self.cache.get/set` calls.

### [P2a] ToolboxDB(skip_init=True).fts_search() opens read-write SQLite connection

**File**: `scripts/toolbox/db.py:77-89`

`ToolboxDB.connect()` always opens a writable connection with `PRAGMA journal_mode=WAL` (a write-type pragma that modifies DB header). Even with `skip_init=True`, `fts_search()` goes through the same `connect()` which:
1. Issues `PRAGMA journal_mode=WAL` — write op
2. Calls `conn.commit()` on exit even after read-only SELECT

`_DB_PATH.exists()` check in `search.py` is correct primary guard (no file creation). But if file exists, WAL pragma is a real write.

**Fix required**: Add readonly branch to `ToolboxDB.connect()` that uses `file:path?mode=ro` URI when `skip_init=True`.

### [P2b] cache=None guard coupled to router, not to deploy mode

**File**: `scripts/core/search.py:48-53`

```python
cache=KnowledgeCache() if router is not None else None,
```

Intent correct, but coupling "is this readonly deployment" to "does it have a router" is fragile. Future changes that decouple may silently re-enable cache writes.

**Fix (advisory)**: Add explicit `readonly: bool = False` to `search_knowledge()`. (This is a design smell, not a current bug — accepted as P3 in later rounds.)

### [P3] quote() URI safe-char note

`scripts/utils/db.py:374` — `_quote(str(self.db_path))` uses Python default `safe='/'` which works correctly. Cosmetic note only.

## Outcome

**Verdict: REQUIRES_CHANGES** — P1 must be fixed before readonly contract holds. P2a should be addressed for defense-in-depth.
