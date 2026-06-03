#!/usr/bin/env bash
# bootstrap_launchd.sh — idempotent launchd bootstrap
# Removes old registration then bootstraps new one.
# Dynamically replaces plist wrapper path with current PROJECT_ROOT.
set -euo pipefail

PLIST_LABEL="com.akb2.github_briefing"

echo "=== Bootstrapping $PLIST_LABEL ==="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_SRC="$SCRIPT_DIR/../launchd/com.akb2.github_briefing.plist"

# Generate temp plist with dynamic wrapper path (avoid hardcoded path)
PLIST_TMP="$(mktemp -t com.akb2.github_briefing.XXXXXX.plist)"
sed "s|/Users/praise/AI-Agent/Claude/projects/Project AKB2/scripts/launchd_wrapper.sh|$PROJECT_ROOT/scripts/launchd_wrapper.sh|g" "$PLIST_SRC" > "$PLIST_TMP"
echo "  Generated plist with wrapper path: $PROJECT_ROOT/scripts/launchd_wrapper.sh"

# Idempotent: remove old registration (ignore errors)
launchctl bootout "gui/$(id -u)/$PLIST_LABEL" 2>/dev/null || true
echo "  Removed old registration (if any)"

# Bootstrap
launchctl bootstrap "gui/$(id -u)" "$PLIST_TMP"
echo "  Bootstrapped from $PLIST_TMP"

# Cleanup temp plist
rm -f "$PLIST_TMP"

# Verify
echo ""
echo "=== Verification ==="
launchctl print "gui/$(id -u)/$PLIST_LABEL" | grep -E 'launchd_wrapper\.sh|hour = 5' || {
    echo "ERROR: Verification failed — plist does not reference wrapper or hour != 5" >&2
    exit 2
}
echo "  OK: wrapper script detected + hour = 5 (本地 5:30，与社媒 7:30/8:30 错峰)"