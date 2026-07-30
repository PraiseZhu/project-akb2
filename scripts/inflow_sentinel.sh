#!/usr/bin/env bash
# inflow_sentinel.sh — Shell wrapper：入库断流哨兵（只读 quality-ledger → 飞书告警）。
#
# 为什么需要 wrapper：飞书凭证在 ~/.akb2-secrets.sh 里，launchd 不会 source 用户 shell
# 配置。范式与 scripts/feishu_sync.sh 一致（同一份 secrets、同一个 find-python311.sh）。
#
# Usage: inflow_sentinel.sh [--dry-run] [--force] [--window-hours N] [--ledger <abs>]
#   参数原样透传给 python3 -m akb2.quality.inflow_sentinel。
#
# 退出码：0 检查完成（健康 / 已告警 / 已抑制 / dry-run）；1 检查失败或告警发送失败。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

# Source secrets（含 FEISHU_*；绝不 log 值；文件须 chmod 600）
_SECRETS_FILE="${HOME:-}/.akb2-secrets.sh"
if [[ -n "${HOME:-}" ]] && [[ -f "$_SECRETS_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$_SECRETS_FILE"
fi

PYTHON_BIN="$(bash "${SCRIPT_DIR}/find-python311.sh")"

exec "$PYTHON_BIN" -m akb2.quality.inflow_sentinel "$@"
