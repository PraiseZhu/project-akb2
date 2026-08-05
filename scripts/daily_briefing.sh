#!/usr/bin/env bash
# daily_briefing.sh — Shell wrapper for AKB2.0 social-trends daily briefing pipeline.
# Usage: daily_briefing.sh [--date YYYY-MM-DD] --vault-root <path> [--dry-run-intake]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

SOCIAL_BUDGET_CONSUMER="social_briefing_main"
BUDGET_GATE_BLOCKED_STATUS=10
SHARED_BUDGET_BLOCKED_EXIT_CODE=65

# Source API secrets if present (never log secret values; secrets file must be chmod 600)
_SECRETS_FILE="${HOME:-}/.akb2-secrets.sh"
if [[ -n "${HOME:-}" ]] && [[ -f "$_SECRETS_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$_SECRETS_FILE"
fi

# Resolve Python 3.11+ (launchd-safe: searches pyenv + versioned binaries)
PYTHON_BIN="$(bash "${SCRIPT_DIR}/find-python311.sh")"

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

PIPELINE_CMD=(
  "$PYTHON_BIN" -m akb2.briefing.pipeline
  --date "$DATE_ARG"
  --vault-root "$VAULT_ROOT"
)
if [[ -n "${AKB2_SOCIAL_BRIEFING_PIPELINE_BIN:-}" ]]; then
  PIPELINE_CMD=("${AKB2_SOCIAL_BRIEFING_PIPELINE_BIN}")
fi
if [[ -n "$DRY_RUN_INTAKE" ]]; then
  PIPELINE_CMD+=("$DRY_RUN_INTAKE")
fi

set +e
PRECHECK_OUTPUT="$(
  PYTHONPATH="${REPO_ROOT}/src" "$PYTHON_BIN" -m akb2.common.budget_gate preflight \
    --consumer "$SOCIAL_BUDGET_CONSUMER" 2>&1
)"
PRECHECK_STATUS=$?
set -e
if [[ $PRECHECK_STATUS -eq $BUDGET_GATE_BLOCKED_STATUS ]]; then
  echo "$PRECHECK_OUTPUT" >&2
  exit $SHARED_BUDGET_BLOCKED_EXIT_CODE
fi
if [[ $PRECHECK_STATUS -ne 0 ]]; then
  echo "$PRECHECK_OUTPUT" >&2
  exit $PRECHECK_STATUS
fi

if [[ "${AKB2_DISABLE_DEADLINE:-0}" == "1" ]]; then
  exec "${PIPELINE_CMD[@]}"
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/run_with_deadline.py" \
  --name social-briefing \
  --date "$DATE_ARG" \
  --deadline-hour "${AKB2_BRIEFING_DEADLINE_HOUR:-12}" \
  --deadline-minute "${AKB2_BRIEFING_DEADLINE_MINUTE:-0}" \
  -- "${PIPELINE_CMD[@]}"
