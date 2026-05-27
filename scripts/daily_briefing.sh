#!/usr/bin/env bash
# daily_briefing.sh — Shell wrapper for AKB2.0 social-trends daily briefing pipeline.
# Usage: daily_briefing.sh [--date YYYY-MM-DD] --vault-root <path> [--dry-run-intake]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

DATE_ARG=""
VAULT_ROOT=""
DRY_RUN_INTAKE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)
      DATE_ARG="$2"
      shift 2
      ;;
    --vault-root)
      VAULT_ROOT="$2"
      shift 2
      ;;
    --dry-run-intake)
      DRY_RUN_INTAKE="--dry-run-intake"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$DATE_ARG" ]]; then
  DATE_ARG=$(TZ=Asia/Shanghai date +%Y-%m-%d)
  echo "date=$DATE_ARG"
fi

if [[ -z "$VAULT_ROOT" ]]; then
  echo "ERROR: --vault-root is required" >&2
  exit 1
fi

exec python3 -m akb2.briefing.pipeline \
  --date "$DATE_ARG" \
  --vault-root "$VAULT_ROOT" \
  $DRY_RUN_INTAKE
