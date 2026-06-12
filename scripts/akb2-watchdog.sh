#!/usr/bin/env bash
# akb2-watchdog.sh — 循环 7 最外层兜底，无外部 API 依赖
# 检查：心跳活性 / 账本最近写入 / 隔离区深度。超限输出非 0。
# --self-test 用临时目录自检三项检查逻辑。
# 宪法约束：纯 shell ≤80 行 / Bash 3.2 兼容 / 无语言模型调用
set -euo pipefail

HEARTBEAT_MAX_AGE_SEC=${AKB2_WATCHDOG_HEARTBEAT_MAX_AGE:-3600}
LEDGER_MAX_AGE_SEC=${AKB2_WATCHDOG_LEDGER_MAX_AGE:-7200}
QUARANTINE_MAX_FILES=${AKB2_WATCHDOG_QUARANTINE_MAX:-100}

file_age_sec() {
    local f="$1"
    if [ ! -f "$f" ]; then echo "-1"; return; fi
    local mtime
    if [ "$(uname -s)" = "Darwin" ]; then
        mtime=$(stat -f %m "$f" 2>/dev/null || echo "0")
    else
        mtime=$(stat -c %Y "$f" 2>/dev/null || echo "0")
    fi
    echo $(( $(date +%s) - mtime ))
}

check_heartbeat() {
    local age; age=$(file_age_sec "$1")
    if [ "$age" -eq -1 ]; then echo "WATCHDOG: heartbeat missing: $1" >&2; return 1; fi
    if [ "$age" -gt "$HEARTBEAT_MAX_AGE_SEC" ]; then echo "WATCHDOG: heartbeat stale: $1 age=${age}s" >&2; return 2; fi
}

check_ledger() {
    local age; age=$(file_age_sec "$1")
    if [ "$age" -eq -1 ]; then echo "WATCHDOG: ledger missing: $1" >&2; return 1; fi
    if [ "$age" -gt "$LEDGER_MAX_AGE_SEC" ]; then echo "WATCHDOG: ledger stale: $1 age=${age}s" >&2; return 2; fi
}

check_quarantine() {
    if [ ! -d "$1" ]; then return 0; fi
    local count; count=$(find "$1" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')
    if [ "$count" -gt "$QUARANTINE_MAX_FILES" ]; then echo "WATCHDOG: quarantine overflow: $count files > max=$QUARANTINE_MAX_FILES" >&2; return 3; fi
}

self_test() {
    local tmp; tmp=$(mktemp -d /tmp/akb2-watchdog-self-test.XXXXXX)
    # shellcheck disable=SC2064
    trap "rm -rf \"$tmp\"" EXIT

    local hb="$tmp/heartbeat.txt"; touch "$hb"
    check_heartbeat "$hb" || { echo "SELF_TEST_FAIL: fresh heartbeat"; exit 1; }

    touch -t 202001010000 "$hb" 2>/dev/null || true
    if check_heartbeat "$hb"; then echo "SELF_TEST_FAIL: stale heartbeat not caught"; exit 1; fi

    local lb="$tmp/ledger.jsonl"; echo '{}' > "$lb"
    check_ledger "$lb" || { echo "SELF_TEST_FAIL: fresh ledger"; exit 1; }

    local qd="$tmp/quarantine"; mkdir -p "$qd"
    check_quarantine "$qd" || { echo "SELF_TEST_FAIL: empty quarantine"; exit 1; }

    local i; for i in $(seq 1 105); do touch "$qd/file_$i"; done
    if check_quarantine "$qd"; then echo "SELF_TEST_FAIL: overflow not caught"; exit 1; fi

    echo "SELF_TEST_PASS"
}

if [ "${1:-}" = "--self-test" ]; then self_test; exit 0; fi

HB_FILE="${1:-}"; LEDGER_FILE="${2:-}"; QUARANTINE_DIR="${3:-}"
if [ -z "$HB_FILE" ] || [ -z "$LEDGER_FILE" ] || [ -z "$QUARANTINE_DIR" ]; then
    echo "Usage: $0 <heartbeat_file> <ledger_file> <quarantine_dir>" >&2
    echo "       $0 --self-test" >&2; exit 4
fi

failed=0
check_heartbeat "$HB_FILE"    || failed=1
check_ledger "$LEDGER_FILE"   || failed=1
check_quarantine "$QUARANTINE_DIR" || failed=1
exit $failed