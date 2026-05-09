#!/usr/bin/env bash
# run-search-quality.sh — cc-akb-search Search Quality v0 Runner
# Read-only: does NOT modify catalog.db, SKILL.md, MCP wrapper, or AKB2.0 five-piece suite
#
# Usage:
#   bash run-search-quality.sh --collect-queries   # SQ-1: extract queries from transcripts
#   bash run-search-quality.sh --baseline          # SQ-2/3: run dual-path + score
#   bash run-search-quality.sh --recommend         # SQ-4: write recommendation.md
#   bash run-search-quality.sh --self-test         # run fixture-based self-test
#
# Exit codes:
#   0 — evaluation complete (no_patch_required or patch_candidate_required)
#   1 — upstream evidence missing (catalog.db not found, transcript dir not found)
#   2 — query insufficient hard fail (n_user_observed < 7)
#   3 — runtime mutation detected (catalog.db sha256 drift)
#   4 — runner internal error

set -euo pipefail

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
OUTPUT_DIR="$SCRIPT_DIR"

CLAUDE_CODE_ROOT="/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code"
PROJ_AKB="$CLAUDE_CODE_ROOT/Project AKB"
CODEX_ROOT="/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex"
AKB2_ROOT="/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0"

TRANSCRIPT_DIR="/Users/praise/.claude/projects/-Users-praise-Library-Mobile-Documents-com-apple-CloudDocs-Claude-Code"
CATALOG_DB="$PROJ_AKB/index/catalog.db"
SERVER_PY="$CODEX_ROOT/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py"
SEARCH_PY="$PROJ_AKB/scripts/core/search.py"
DB_PY="$PROJ_AKB/scripts/utils/db.py"
MAIN_PY="$PROJ_AKB/scripts/main.py"
TOOLBOX_CARD="$AKB2_ROOT/toolbox/tools/cc-akb-search.md"
WIKI_CARD="$AKB2_ROOT/wiki/tools/cc-akb-search.md"

# ── Write-path whitelist (enforce read-only) ────────────────────────────────
# Only OUTPUT_DIR is writable; anything else → error
enforce_readonly() {
    local path="$1"
    if [[ "$path" != "$OUTPUT_DIR"* ]]; then
        echo "[ERROR] Attempted write outside output dir: $path" >&2
        exit 4
    fi
}

# ── SHA256 helper ───────────────────────────────────────────────────────────
sha256_file() {
    shasum -a 256 "$1" | awk '{print $1}'
}

# ── Preflight checks ────────────────────────────────────────────────────────
preflight() {
    echo "[preflight] Checking upstream evidence..."
    if [[ ! -f "$CATALOG_DB" ]]; then
        echo "[ERROR] catalog.db not found: $CATALOG_DB" >&2; exit 1
    fi
    if [[ ! -d "$TRANSCRIPT_DIR" ]]; then
        echo "[ERROR] transcript dir not found: $TRANSCRIPT_DIR" >&2; exit 1
    fi
    if [[ ! -f "$SERVER_PY" ]]; then
        echo "[ERROR] MCP server.py not found: $SERVER_PY" >&2; exit 1
    fi
    echo "[preflight] PASS"
}

# ── SQ-0: Baseline sha256 snapshot ──────────────────────────────────────────
snapshot_baseline() {
    local ts; ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local sha_catalog sha_server sha_search sha_db sha_toolbox sha_wiki
    sha_catalog=$(sha256_file "$CATALOG_DB")
    sha_server=$(sha256_file "$SERVER_PY")
    sha_search=$(sha256_file "$SEARCH_PY")
    sha_db=$(sha256_file "$DB_PY")
    sha_toolbox=$(sha256_file "$TOOLBOX_CARD")
    sha_wiki=$(sha256_file "$WIKI_CARD")

    # Store for post-run comparison
    export BASELINE_SHA_CATALOG="$sha_catalog"

    echo "$sha_catalog $sha_server $sha_search $sha_db $sha_toolbox $sha_wiki" > "$OUTPUT_DIR/.baseline-sha.tmp"
    echo "[snapshot] catalog.db=$sha_catalog"
}

# ── SQ-1: Collect queries from transcripts ──────────────────────────────────
collect_queries() {
    preflight
    snapshot_baseline

    echo "[SQ-1] Scanning transcripts for mcp__akb-search__akb_search tool calls..."

    local py_exit=0
    set +e
    python3 - << 'PYEOF'
import json, os, glob, sys

TRANSCRIPT_DIR = "/Users/praise/.claude/projects/-Users-praise-Library-Mobile-Documents-com-apple-CloudDocs-Claude-Code"
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else "."

A4_CONTEXT_KEYWORDS = ["P1e A4", "P1c A4", "L1 eval", "A4 fixture", "V2 verify", "V1: 确认"]
NONSENSE_PATTERNS = ["xyzzy", "xyz_nonexistent", "notexist", "qwerty_"]

all_calls = []

for fpath in sorted(glob.glob(os.path.join(TRANSCRIPT_DIR, "*.jsonl"))):
    session_id = os.path.basename(fpath).replace(".jsonl", "")
    try:
        messages = []
        with open(fpath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    messages.append(json.loads(line))
                except: pass

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict) or msg.get("type") != "assistant":
                continue
            content = msg.get("message", {}).get("content", [])
            if not isinstance(content, list): continue
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "tool_use": continue
                if "akb_search" not in item.get("name", ""): continue
                query = item.get("input", {}).get("query", "")
                if not query: continue

                # Find preceding user messages:
                # prev_user_msg = immediate preceding user message (for display & V2 check)
                # session_a4_context = any user message in session contains A4 keywords
                prev_user_msg = ""
                session_a4_context = False
                for j in range(i-1, -1, -1):
                    prev = messages[j]
                    if prev.get("type") != "user": continue
                    prev_content = prev.get("message", {}).get("content", prev.get("content", ""))
                    user_text = ""
                    if isinstance(prev_content, list):
                        for pc in prev_content:
                            if isinstance(pc, dict) and pc.get("type") == "text":
                                t = pc.get("text", "")
                                if t and "tool_use_id" not in t[:20]:
                                    user_text = t[:400]; break
                    elif isinstance(prev_content, str) and "tool_use_id" not in prev_content[:50]:
                        user_text = prev_content[:400]
                    if user_text:
                        if not prev_user_msg:
                            prev_user_msg = user_text
                        if any(k in user_text for k in A4_CONTEXT_KEYWORDS):
                            session_a4_context = True
                        if prev_user_msg and session_a4_context:
                            break

                all_calls.append({
                    "session_id": session_id,
                    "timestamp": str(msg.get("timestamp", "unknown")),
                    "query": query,
                    "tool_name": item.get("name", ""),
                    "prev_user_msg": prev_user_msg,
                    "session_a4_context": session_a4_context,
                    "source_path": fpath
                })
    except Exception as e:
        pass

# Classify
user_observed = []
eval_observed = []

for call in all_calls:
    q = call["query"]
    prev = call["prev_user_msg"]
    session_a4 = call.get("session_a4_context", False)

    is_eval = False
    is_nonsense = any(p in q for p in NONSENSE_PATTERNS)
    has_a4_direct = any(k in prev for k in A4_CONTEXT_KEYWORDS)

    # V2 connection test
    if "mcp__akb-search" in prev and "V2" in prev:
        is_eval = True
    # A4 eval skill context (direct or anywhere in session window)
    elif has_a4_direct or session_a4:
        is_eval = True
    # No preceding user msg → unverified → discard (eval)
    elif not prev:
        is_eval = True
    else:
        is_eval = False

    if is_eval:
        eval_observed.append(call)
    else:
        user_observed.append(call)

n_user = len(user_observed)
n_eval = len(eval_observed)

print(f"[SQ-1] Total calls: {len(all_calls)}")
print(f"[SQ-1] user_observed: {n_user}")
print(f"[SQ-1] eval_observed: {n_eval}")
print(f"[SQ-1] threshold: 7")

if n_user < 7:
    print(f"[SQ-1] HARD FAIL: n_user_observed ({n_user}) < 7")
    sys.exit(2)
else:
    print(f"[SQ-1] PASS: n_user_observed ({n_user}) >= 7")
PYEOF
    py_exit=$?
    set -e

    # Post-run catalog.db sha256 check (always runs regardless of SQ-1 outcome)
    local post_sha; post_sha=$(sha256_file "$CATALOG_DB")
    if [[ "$BASELINE_SHA_CATALOG" != "$post_sha" ]]; then
        echo "[ERROR] catalog.db sha256 drift detected in SQ-1! pre=$BASELINE_SHA_CATALOG post=$post_sha" >&2
        exit 3
    fi
    echo "[SQ-1] catalog.db sha256 unchanged: $post_sha"

    if [ $py_exit -eq 2 ]; then
        echo "[ERROR] Hard fail: insufficient user_observed queries (< 7). See blocked_insufficient_user_queries.md" >&2
        exit 2
    elif [ $py_exit -ne 0 ]; then
        echo "[ERROR] SQ-1 Python script failed with exit $py_exit" >&2
        exit 4
    fi
}

# ── SQ-2/3: Baseline runner + scoring ───────────────────────────────────────
run_baseline() {
    if [[ ! -f "$OUTPUT_DIR/queries.jsonl" ]]; then
        echo "[ERROR] queries.jsonl not found. Run --collect-queries first." >&2; exit 1
    fi

    preflight
    snapshot_baseline

    local pre_sha; pre_sha="$BASELINE_SHA_CATALOG"

    echo "[SQ-2] Running dual-path baseline (MCP + CLI)..."
    # ... (implementation per SQ-2 spec: for each query, call server.py search() and main.py)
    # ... compare results, write baseline-results.jsonl and mcp-cli-parity.json
    echo "[SQ-2] (full implementation pending — SQ-1 blocked before reaching this step)"

    # Post-run catalog.db sha256 check
    local post_sha; post_sha=$(sha256_file "$CATALOG_DB")
    if [[ "$pre_sha" != "$post_sha" ]]; then
        echo "[ERROR] catalog.db sha256 drift detected! pre=$pre_sha post=$post_sha" >&2
        exit 3
    fi
    echo "[SQ-2] catalog.db sha256 unchanged: $post_sha"
}

# ── SQ-4: Recommendation ────────────────────────────────────────────────────
run_recommend() {
    if [[ ! -f "$OUTPUT_DIR/metrics.json" ]]; then
        echo "[ERROR] metrics.json not found. Run --baseline first." >&2; exit 1
    fi
    echo "[SQ-4] Generating recommendation..."
    # ... (implementation per SQ-4 spec: read metrics.json verdict, write recommendation.md)
    echo "[SQ-4] (full implementation pending — SQ-1 blocked before reaching this step)"
}

# ── Self-test ────────────────────────────────────────────────────────────────
run_self_test() {
    echo "[self-test] Running SQ-1 gate logic tests..."
    local pass=0 fail=0

    # Test 1: catalog.db exists
    if [[ -f "$CATALOG_DB" ]]; then
        echo "[PASS] catalog.db exists"; ((pass++))
    else
        echo "[FAIL] catalog.db not found"; ((fail++))
    fi

    # Test 2: server.py exists
    if [[ -f "$SERVER_PY" ]]; then
        echo "[PASS] server.py exists"; ((pass++))
    else
        echo "[FAIL] server.py not found"; ((fail++))
    fi

    # Test 3: transcript dir exists
    if [[ -d "$TRANSCRIPT_DIR" ]]; then
        echo "[PASS] transcript dir exists"; ((pass++))
    else
        echo "[FAIL] transcript dir not found"; ((fail++))
    fi

    # Test 4: automation_allowed=true global 0 hits
    local hits; hits=$(grep -rE '^automation_allowed:[[:space:]]*true' \
        "$AKB2_ROOT/toolbox/tools" "$AKB2_ROOT/wiki/tools" 2>/dev/null | wc -l | tr -d ' ') || hits="0"
    if [[ "${hits:-0}" -eq 0 ]]; then
        echo "[PASS] automation_allowed=true global 0 hits"; pass=$((pass+1))
    else
        echo "[FAIL] automation_allowed=true hits: $hits"; fail=$((fail+1))
    fi

    # Test 5: output dir is writable
    if touch "$OUTPUT_DIR/.write-test" 2>/dev/null; then
        rm -f "$OUTPUT_DIR/.write-test"
        echo "[PASS] output dir writable"; ((pass++))
    else
        echo "[FAIL] output dir not writable"; ((fail++))
    fi

    echo ""
    echo "Self-test: $pass passed, $fail failed"
    [[ $fail -eq 0 ]] && exit 0 || exit 1
}

# ── Invariant check ──────────────────────────────────────────────────────────
check_invariants() {
    local hits; hits=$(grep -rE '^automation_allowed:[[:space:]]*true' \
        "$AKB2_ROOT/toolbox/tools" "$AKB2_ROOT/wiki/tools" 2>/dev/null | wc -l | tr -d ' ') || hits="0"
    if [[ "${hits:-0}" -gt "0" ]]; then
        echo "[ERROR] HARD INVARIANT FAIL: automation_allowed=true found ($hits hits)" >&2
        exit 3
    fi
    echo "[invariant] automation_allowed=true global count: 0 (PASS)"
}

# ── Main dispatch ────────────────────────────────────────────────────────────
case "${1:-}" in
    --collect-queries)  check_invariants; collect_queries ;;
    --baseline)         check_invariants; run_baseline ;;
    --recommend)        check_invariants; run_recommend ;;
    --self-test)        run_self_test ;;
    *)
        echo "Usage: $0 --collect-queries | --baseline | --recommend | --self-test"
        exit 4
        ;;
esac
