#!/usr/bin/env bash
# feishu_sync.sh — Shell wrapper：发现当天简报 → 飞书卡片 → PROJECT DOKI 发送。
# Usage: feishu_sync.sh --vault-root <path> [--date YYYY-MM-DD] [--target dm|group] [--state-path <abs>] [--dry-run]
# 默认 --target dm（防误发群）；prod launchd 显式传 --target group。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

# Source secrets（含 FEISHU_*；绝不 log 值；文件须 chmod 600）
_SECRETS_FILE="${HOME:-}/.akb2-secrets.sh"
SECRETS_STATUS="missing_file"
if [[ -n "${HOME:-}" ]] && [[ -f "$_SECRETS_FILE" ]]; then
    # shellcheck disable=SC1090
    set +e
    source "$_SECRETS_FILE" >/dev/null 2>&1
    source_rc=$?
    set -e
    if [[ "$source_rc" -eq 0 ]]; then
        SECRETS_STATUS="loaded"
    else
        echo "ERROR: failed to source $_SECRETS_FILE" >&2
        exit 1
    fi
elif [[ -z "${HOME:-}" ]]; then
    SECRETS_STATUS="home_missing"
fi
export AKB2_FEISHU_SECRETS_STATUS="$SECRETS_STATUS"
export AKB2_FEISHU_SECRETS_FILE="$_SECRETS_FILE"

PYTHON_BIN="$(bash "${SCRIPT_DIR}/find-python311.sh")"

DATE_ARG=""
VAULT_ROOT=""
TARGET="dm"
STATE_PATH=""
DRY_RUN=0
RECONCILE_MISSING=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date) DATE_ARG="$2"; shift 2 ;;
    --vault-root) VAULT_ROOT="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --state-path) STATE_PATH="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --reconcile-missing) RECONCILE_MISSING=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$DATE_ARG" ]]; then
  DATE_ARG=$(TZ=Asia/Shanghai date +%Y-%m-%d)
fi

if [[ -z "$VAULT_ROOT" ]]; then
  echo "ERROR: --vault-root is required" >&2
  exit 1
fi

CMD=("$PYTHON_BIN" -m akb2.feishu_sync.cli --date "$DATE_ARG" --vault-root "$VAULT_ROOT" --target "$TARGET")
if [[ -n "$STATE_PATH" ]]; then
  CMD+=(--state-path "$STATE_PATH")
fi
if [[ "$DRY_RUN" -eq 1 ]]; then
  CMD+=(--dry-run)
fi
if [[ "$RECONCILE_MISSING" -eq 1 ]]; then
  CMD+=(--reconcile-missing)
fi

exec "${CMD[@]}"
