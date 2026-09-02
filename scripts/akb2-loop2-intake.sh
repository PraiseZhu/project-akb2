#!/usr/bin/env bash
# ===========================================================================
# AKB2 loop2 X intake 启动包装脚本
#
# 职责:
#   1. 加载网关 env（launchd 不继承交互 shell，grok 预筛需要）
#   2. Python 环境检测（≥ 3.11）
#   3. 调一次 intake_x.orchestrator（有界，无常驻锁）
#
# 用法:
#   bash scripts/akb2-loop2-intake.sh
#
# 部署:
#   由 launchd/com.akb2.loop2-intake.plist 管理（StartInterval=14400）
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="${AKB2_LOG_DIR:-$PROJECT_ROOT/logs}"
INTAKE_LOG="${AKB2_INTAKE_LOG:-$LOG_DIR/loop2-intake.log}"
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
export LUNA_MODEL_ID="${LUNA_MODEL_ID:-gpt-5.6-luna}"

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

# --- 日志 ---
mkdir -p "$LOG_DIR"
exec >> "$INTAKE_LOG" 2>&1

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] loop2 intake starting: PID=$$ PYTHON=$PYTHON_BIN ($PY_VERSION)"
if [ -f "$ENV_FILE" ]; then
    ENV_STATUS="loaded"
else
    ENV_STATUS="missing"
fi
if [ -n "${AI_GATEWAY_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; then
    KEY_STATUS="present"
else
    KEY_STATUS="missing"
fi
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] loop2 intake env: env_file=$ENV_STATUS openai_base=${OPENAI_BASE_URL:-<unset>} key=$KEY_STATUS"

if [ -n "${AKB2_VAULT_ROOT:-}" ]; then
    if [ ! -d "$AKB2_VAULT_ROOT" ]; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: AKB2_VAULT_ROOT 不是目录: $AKB2_VAULT_ROOT" >&2
        exit 1
    fi
    mkdir -p "$AKB2_VAULT_ROOT/raw-loop2"
fi

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
# Mini 首轮 remixicon.com：defuddle parse 本地 HTML 在 30s 被掐。loop1 默认
# 仍 30s；只抬本链路。非法值由 python 侧回落到 30。
export AKB2_DEFUDDLE_TIMEOUT_S="${AKB2_DEFUDDLE_TIMEOUT_S:-90}"

INTAKE_ARGS=()
if [ -n "${AKB2_VAULT_ROOT:-}" ]; then
    INTAKE_ARGS+=(--vault-root "$AKB2_VAULT_ROOT")
    INTAKE_ARGS+=(--output-dir "$AKB2_VAULT_ROOT/raw-loop2")
fi
INTAKE_ARGS+=(--sources-config "$PROJECT_ROOT/config/x_sources.yaml")
INTAKE_ARGS+=(--state-file "$PROJECT_ROOT/reports/intake/loop2-intake-state.jsonl")

exec "$PYTHON_BIN" -m akb2.quality.intake_x.orchestrator "${INTAKE_ARGS[@]}" "$@"
