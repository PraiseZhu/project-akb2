#!/usr/bin/env bash
# dry_run_github_e2e.sh — E2E dry run with mock intake
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export VAULT_ROOT="${VAULT_ROOT:-$PROJECT_ROOT/tests/fixtures/vault-stub}"
bash "$SCRIPT_DIR/daily_github_briefing.sh" --mock