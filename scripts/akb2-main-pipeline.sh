#!/usr/bin/env bash
# ===========================================================================
# AKB2 主链路 quality runner 启动包装脚本
#
# 职责:
#   1. Python 环境检测（≥ 3.11）
#   2. 单实例锁（防止双跑）
#   3. 日志重定向
#   4. 启动 runner daemon
#
# 用法:
#   bash scripts/akb2-main-pipeline.sh [--once] [--spot-check]
#
# 部署:
#   由 launchd/com.akb2.main-pipeline.plist 管理（KeepAlive 常驻型）
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="${AKB2_LOG_DIR:-$PROJECT_ROOT/logs}"
RUNNER_LOG="${AKB2_RUNNER_LOG:-$LOG_DIR/akb2-quality-runner.log}"
LOCK_FILE="${AKB2_LOCK_FILE:-$PROJECT_ROOT/staging/locks/runner.lock}"
ENV_FILE="${AKB2_ENV_FILE:-$HOME/.config/akb2/github-briefing.env}"

# --- Env 加载 / 网关变量归一化 ---
# launchd 不继承交互 shell 环境；runner 自己加载只含 key/base/model 的用户 env。
# 不打印 secret，只把历史变量名归一到 quality llm_router 能识别的 OpenAI-compatible 命名。
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
fi

if [ -n "${OPENROUTER_BASE_URL:-}" ] && [ -z "${AI_GATEWAY_BASE_URL:-}" ]; then
    export AI_GATEWAY_BASE_URL="$OPENROUTER_BASE_URL"
fi
if [ -n "${TAPTAP_BASE_URL:-}" ] && [ -z "${AI_GATEWAY_BASE_URL:-}" ]; then
    export AI_GATEWAY_BASE_URL="$TAPTAP_BASE_URL"
fi
if [ -n "${AI_GATEWAY_API_KEY:-}" ]; then
    :
elif [ -n "${OPENROUTER_API_KEY:-}" ]; then
    export AI_GATEWAY_API_KEY="$OPENROUTER_API_KEY"
elif [ -n "${TAPTAP_API_KEY:-}" ]; then
    export AI_GATEWAY_API_KEY="$TAPTAP_API_KEY"
elif [ -n "${DEEPSEEK_API_KEY:-}" ]; then
    export AI_GATEWAY_API_KEY="$DEEPSEEK_API_KEY"
fi
if [ -z "${AI_GATEWAY_BASE_URL:-}" ] && [ -n "${AI_GATEWAY_API_KEY:-}" ]; then
    export AI_GATEWAY_BASE_URL="https://llm-proxy.tapsvc.com"
fi
if [ -n "${AI_GATEWAY_BASE_URL:-}" ] && [ -z "${OPENAI_BASE_URL:-}" ]; then
    GATEWAY_BASE_CLEAN="${AI_GATEWAY_BASE_URL%/}"
    if [[ "$GATEWAY_BASE_CLEAN" == */v1 || "$GATEWAY_BASE_CLEAN" == */v1beta/openai ]]; then
        export OPENAI_BASE_URL="$GATEWAY_BASE_CLEAN"
    else
        export OPENAI_BASE_URL="$GATEWAY_BASE_CLEAN/v1"
    fi
fi
export OPENAI_MODEL_ID="${OPENAI_MODEL_ID:-gpt-5.4}"
export DEEPSEEK_MODEL_ID="${DEEPSEEK_MODEL_ID:-deepseek/deepseek-v4-pro}"

# --- Python 环境检测 ---
PYTHON_BIN="${AKB2_PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    # 优先用 scripts/find-python311.sh
    if [ -x "$PROJECT_ROOT/scripts/find-python311.sh" ]; then
        PYTHON_BIN="$("$PROJECT_ROOT/scripts/find-python311.sh")"
    else
        PYTHON_BIN="$(command -v python3 || echo '')"
    fi
fi

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: Python ≥ 3.11 not found" >&2
    exit 1
fi

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION##*.}"
if [ "$PY_MAJOR" -lt 3 ] || [ "$PY_MAJOR" -eq 3 -a "$PY_MINOR" -lt 11 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: Python $PY_VERSION < 3.11" >&2
    exit 1
fi

# --- 单实例锁 ---
mkdir -p "$(dirname "$LOCK_FILE")"

if [ -f "$LOCK_FILE" ]; then
    LOCK_PID="$(cat "$LOCK_FILE" 2>/dev/null || echo '')"
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARN: runner already running (PID=$LOCK_PID), exiting" >&2
        exit 0
    fi
    # 锁文件残留，清理
    rm -f "$LOCK_FILE"
fi

echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# --- 日志 ---
mkdir -p "$LOG_DIR"
exec >> "$RUNNER_LOG" 2>&1

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] runner starting: PID=$$ PYTHON=$PYTHON_BIN ($PY_VERSION)"
if [ -f "$ENV_FILE" ]; then
    ENV_STATUS="loaded"
else
    ENV_STATUS="missing"
fi
if [ -n "${AI_GATEWAY_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; then
    GRADER_KEY_STATUS="present"
else
    GRADER_KEY_STATUS="missing"
fi
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] runner env: env_file=$ENV_STATUS openai_base=${OPENAI_BASE_URL:-<unset>} openai_model=${OPENAI_MODEL_ID:-<unset>} grader_key=$GRADER_KEY_STATUS"

# --- 运行时 vault 门(fail-closed)---
# 重启后外置盘/Syncthing 未就绪时,若不拦截,runner 会对不存在的 raw_dir 返回空队列、
# 静默空跑(daemon 看着活着却不处理)。这里 FATAL 退出非 0,让 KeepAlive 按 ThrottleInterval
# 重试且日志可见,而不是静默 idle。仅当 plist 显式设了 AKB2_VAULT_ROOT 时校验。
if [ -n "${AKB2_VAULT_ROOT:-}" ]; then
    if [ ! -d "$AKB2_VAULT_ROOT" ]; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: AKB2_VAULT_ROOT 不是目录: $AKB2_VAULT_ROOT (磁盘未挂载?)" >&2
        exit 1
    fi
    if [ ! -d "$AKB2_VAULT_ROOT/raw" ]; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: vault 下缺 raw/ 目录: $AKB2_VAULT_ROOT/raw" >&2
        exit 1
    fi
    if [ -d "$AKB2_VAULT_ROOT/staging" ]; then
        if [ ! -w "$AKB2_VAULT_ROOT/staging" ]; then
            echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: vault staging/ 不可写: $AKB2_VAULT_ROOT/staging" >&2
            exit 1
        fi
    elif [ ! -w "$AKB2_VAULT_ROOT" ]; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: vault 根不可写(无法建 staging/): $AKB2_VAULT_ROOT" >&2
        exit 1
    fi
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] vault gate: ok ($AKB2_VAULT_ROOT)"
fi

# --- 启动 runner ---
cd "$PROJECT_ROOT"

RUNNER_ARGS=("$@")
if [ ${#RUNNER_ARGS[@]} -eq 0 ]; then
    # 默认: 常驻模式
    RUNNER_ARGS=()
fi

exec "$PYTHON_BIN" -m src.akb2.quality.runner "${RUNNER_ARGS[@]}"
