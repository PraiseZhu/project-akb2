#!/usr/bin/env bash
# launchd_wrapper.sh — launchd entry point
# Sources secrets from ~/.config/akb2/github-briefing.env (0600) then runs pipeline.
set -euo pipefail

ENV_FILE="$HOME/.config/akb2/github-briefing.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: missing env file $ENV_FILE — abort" >&2
    exit 2
fi
# shellcheck source=/dev/null
source "$ENV_FILE"

if [ -z "${VAULT_ROOT:-}" ] || [[ "$VAULT_ROOT" != /* ]]; then
    echo "ERROR: VAULT_ROOT must be an absolute path" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/daily_github_briefing.sh" --vault-root "$VAULT_ROOT"
