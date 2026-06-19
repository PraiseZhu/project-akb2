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
LOG_DIR="$PROJECT_ROOT/logs"
RUNNER_LOG="$LOG_DIR/akb2-quality-runner.log"
LOCK_FILE="$PROJECT_ROOT/staging/locks/runner.lock"

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

# --- 启动 runner ---
cd "$PROJECT_ROOT"

RUNNER_ARGS=("$@")
if [ ${#RUNNER_ARGS[@]} -eq 0 ]; then
    # 默认: 常驻模式
    RUNNER_ARGS=()
fi

exec "$PYTHON_BIN" -m src.akb2.quality.runner "${RUNNER_ARGS[@]}"