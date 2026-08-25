#!/usr/bin/env bash
# akb2-retrieval-http.sh — Mini 上给同事 Cindy 插件用的只读检索 HTTP 包装。
#
# 职责:
#   1. Python ≥ 3.11
#   2. 从 ~/.config/akb2/retrieval.env 加载 token / bind（不打印 secret）
#   3. token 缺失 fail-closed
#   4. exec python -m akb2.retrieval.cli serve
#
# 部署: launchd/com.akb2.retrieval-http.plist（KeepAlive）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${AKB2_RETRIEVAL_ENV_FILE:-$HOME/.config/akb2/retrieval.env}"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
fi

if [[ -z "${AKB2_RETRIEVAL_TOKEN:-}" ]]; then
    echo "FATAL: AKB2_RETRIEVAL_TOKEN unset (env file $ENV_FILE)" >&2
    exit 2
fi

PYTHON_BIN="$(bash "$SCRIPT_DIR/find-python311.sh")"

cd "$PROJECT_ROOT"
export PYTHONPATH="${PYTHONPATH:-$PROJECT_ROOT/src}"
exec "$PYTHON_BIN" -m akb2.retrieval.cli serve
