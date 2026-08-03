#!/usr/bin/env bash
# daily_github_briefing.sh — GitHub Curator 日推主入口
# Usage: bash scripts/daily_github_briefing.sh [--vault-root <path>] [--mock] [--force-layer N]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

BUDGET_GATE_BLOCKED_STATUS=10
SHARED_BUDGET_BLOCKED_EXIT_CODE=65
GITHUB_BUDGET_CONSUMER="github_briefing_main"
MOCK_FLAG=""
FORCE_LAYER=""
VAULT_ROOT_ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vault-root) VAULT_ROOT_ARG="$2"; shift ;;
        --mock) MOCK_FLAG="--mock" ;;
        --force-layer) FORCE_LAYER="--force-layer $2"; shift ;;
        *) echo "Unknown arg: $1"; exit 2 ;;
    esac
    shift
done

if [[ -n "$VAULT_ROOT_ARG" ]]; then
    export VAULT_ROOT="$VAULT_ROOT_ARG"
fi

# VAULT_ROOT contract: must be set and absolute
if [ -z "${VAULT_ROOT:-}" ] || [[ "$VAULT_ROOT" != /* ]]; then
    cat > briefing-skipped.json <<EOF
{"status": "skipped", "reason": "vault_root_missing_or_relative", "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
    echo "briefing: SKIPPED (vault_root_missing_or_relative)"
    exit 1
fi

PYTHON_BIN="$(bash "$SCRIPT_DIR/find-python311.sh")"
RUN_DATE="$(TZ=Asia/Shanghai date +%Y-%m-%d)"
if [[ -n "${AKB2_GITHUB_BRIEFING_PIPELINE_BIN:-}" ]]; then
    PIPELINE_CMD=("${AKB2_GITHUB_BRIEFING_PIPELINE_BIN}")
else
    PIPELINE_CMD=("$PYTHON_BIN" -m akb2.briefing.github.pipeline)
fi
if [[ -n "$MOCK_FLAG" ]]; then
    PIPELINE_CMD+=("$MOCK_FLAG")
fi
if [[ -n "$FORCE_LAYER" ]]; then
    # shellcheck disable=SC2206
    FORCE_LAYER_PARTS=($FORCE_LAYER)
    PIPELINE_CMD+=("${FORCE_LAYER_PARTS[@]}")
fi

set +e
PRECHECK_OUTPUT="$(
    PYTHONPATH=src "$PYTHON_BIN" -m akb2.common.budget_gate preflight \
        --consumer "$GITHUB_BUDGET_CONSUMER" 2>&1
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
    PYTHONPATH=src exec "${PIPELINE_CMD[@]}"
fi

PYTHONPATH=src exec "$PYTHON_BIN" "$SCRIPT_DIR/run_with_deadline.py" \
    --name github-briefing \
    --date "$RUN_DATE" \
    --deadline-hour "${AKB2_GITHUB_DEADLINE_HOUR:-12}" \
    --deadline-minute "${AKB2_GITHUB_DEADLINE_MINUTE:-0}" \
    -- "${PIPELINE_CMD[@]}"
