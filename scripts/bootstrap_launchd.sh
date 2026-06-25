#!/usr/bin/env bash
# bootstrap_launchd.sh — idempotent launchd bootstrap
# Removes old registration then bootstraps new one.
# Dynamically replaces plist placeholders with current PROJECT_ROOT.
set -euo pipefail

PLIST_LABEL="com.akb2.briefing"

echo "=== Bootstrapping $PLIST_LABEL ==="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VAULT_ROOT="${VAULT_ROOT:-/Volumes/AKB2/Obsidian}"
PLIST_SRC="$SCRIPT_DIR/../launchd/com.akb2.briefing.plist"

# Generate temp plist with dynamic paths (avoid hardcoded paths)
PLIST_TMP="$(mktemp -t com.akb2.briefing.XXXXXX.plist)"
sed \
  -e "s|__AKB2_REPO_ROOT__|$PROJECT_ROOT|g" \
  -e "s|__AKB2_VAULT_ROOT__|$VAULT_ROOT|g" \
  "$PLIST_SRC" > "$PLIST_TMP"
echo "  Generated plist with repo root: $PROJECT_ROOT"
echo "  Vault root: $VAULT_ROOT"

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
launchctl print "gui/$(id -u)/$PLIST_LABEL" | grep -E 'daily_briefing\.sh|hour = 7' || {
    echo "ERROR: Verification failed — plist does not reference daily_briefing.sh or hour != 7" >&2
    exit 2
}
echo "  OK: daily_briefing.sh detected + hour = 7 (本地 7:30，与社媒 7:30/8:30 错峰)"

# Env 完整性检查
echo ""
echo "=== Env Check ==="
ENV_FILE="$HOME/.config/akb2/github-briefing.env"
if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE" 2>/dev/null || true
    MISSING=""
    [ -z "${GITHUB_PAT:-}" ] && MISSING="$MISSING GITHUB_PAT"
    [ -z "${VAULT_ROOT:-}" ] && MISSING="$MISSING VAULT_ROOT"
    [ -z "${AI_GATEWAY_API_KEY:-}" ] && MISSING="$MISSING AI_GATEWAY_API_KEY"
    if [ -n "$MISSING" ]; then
        echo "⚠️  env 文件缺少以下必填字段:$MISSING"
        echo "   请编辑 $ENV_FILE 后重跑 bootstrap"
    else
        echo "  OK: env 文件字段完整"
    fi
else
    echo "⚠️  env 文件不存在: $ENV_FILE"
    echo "   cp config/github-briefing.env.example $ENV_FILE"
    echo "   然后编辑填入实际值后重跑 bootstrap"
fi
