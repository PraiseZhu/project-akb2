#!/usr/bin/env bash
# daily_github_briefing.sh — GitHub Curator 日推主入口
# Usage: bash scripts/daily_github_briefing.sh [--mock] [--force-layer N]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# VAULT_ROOT contract: must be set and absolute
if [ -z "${VAULT_ROOT:-}" ] || [[ "$VAULT_ROOT" != /* ]]; then
    cat > briefing-skipped.json <<EOF
{"status": "skipped", "reason": "vault_root_missing_or_relative", "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
    echo "briefing: SKIPPED (vault_root_missing_or_relative)"
    exit 0
fi

MOCK_FLAG=""
FORCE_LAYER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mock) MOCK_FLAG="--mock" ;;
        --force-layer) FORCE_LAYER="--force-layer $2"; shift ;;
        *) echo "Unknown arg: $1"; exit 2 ;;
    esac
    shift
done

PYTHONPATH=src python -m akb2.briefing.github.pipeline $MOCK_FLAG $FORCE_LAYER