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
ENV_FILE="${AKB2_ENV_FILE:-$HOME/.config/akb2/github-briefing.env}"

# --- 机器本地状态根 ---
# daemon 单实例锁必须落机器本地状态根，不能落仓库:PROJECT_ROOT 在 ~/AI-Agent
# (Syncthing folder,~/AI-Agent/.stfolder 实证)内,锁放 $PROJECT_ROOT/staging/locks
# 会被跨机同步 → 两台机器互看/互删对方的锁 PID 文件,`kill -0` 判活对另一台的 PID
# 毫无意义。解析顺序与 python 侧 src/akb2/common/state_paths.get_state_root() 一致。
STATE_ROOT="${AKB2_STATE_ROOT:-$HOME/Library/Application Support/akb2}"
case "$STATE_ROOT" in
    "$HOME/AI-Agent"|"$HOME/AI-Agent"/*|"$HOME/Library/Mobile Documents"/*|/Volumes/AKB2/*)
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: AKB2_STATE_ROOT=$STATE_ROOT 在跨机同步范围内(状态会被 Syncthing 分叉丢失)" >&2
        exit 1
        ;;
esac
# 迁移期兜底:锁位置从仓库换到状态根的那一刻,旧位置可能还有一个活着的 daemon
# 持锁(实测 2026-07-25 起的常驻进程锁就在 staging/locks/runner.lock)。新位置查不到
# 它 → 会再起第二个 daemon。所以旧锁仍要判活一次,活着就让位。
# 显式给了 AKB2_LOCK_FILE = 调用方自己指定了锁位置,不套用默认迁移语义。
if [ -n "${AKB2_LOCK_FILE:-}" ]; then
    LOCK_FILE="$AKB2_LOCK_FILE"
    LEGACY_LOCK_FILE=""
else
    LOCK_FILE="$STATE_ROOT/locks/runner.lock"
    LEGACY_LOCK_FILE="${AKB2_LEGACY_LOCK_FILE:-$PROJECT_ROOT/staging/locks/runner.lock}"
fi

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

# 迁移期:旧位置(仓库内)若还有活着的 daemon,让位并明示(不删旧锁,那是它的)
if [ -n "$LEGACY_LOCK_FILE" ] && [ "$LEGACY_LOCK_FILE" != "$LOCK_FILE" ] && [ -f "$LEGACY_LOCK_FILE" ]; then
    LEGACY_PID="$(cat "$LEGACY_LOCK_FILE" 2>/dev/null || echo '')"
    if [ -n "$LEGACY_PID" ] && kill -0 "$LEGACY_PID" 2>/dev/null; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARN: runner 仍持有迁移前的旧锁 $LEGACY_LOCK_FILE (PID=$LEGACY_PID), exiting" >&2
        exit 0
    fi
fi

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
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] runner lock: $LOCK_FILE (state_root=$STATE_ROOT)"
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

# macOS /bin/bash 3.2 下 set -u + "${arr[@]}" 空数组展开会报 unbound variable;
# launchd 不传参 → 数组恒空 → exec 行必挂在 python 启动前。直接用 "$@":
# 空参数在 bash 3.2 set -u 下恒安全(无参=常驻模式,有参透传)。
exec "$PYTHON_BIN" -m src.akb2.quality.runner "$@"
