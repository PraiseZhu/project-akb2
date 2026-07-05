#!/usr/bin/env bash
# bootstrap_intake_launchd.sh — idempotent launchd bootstrap for com.akb2.intake
#
# Renders launchd/com.akb2.intake.plist placeholders → plutil -lint → bootout old →
# bootstrap new → launchctl print 验证。脚本只写不跑:本脚本由 lead 装载时执行
# (worker 不装载,工单 F21 红线:不自行 bootstrap/装载任何 plist)。
#
# Preflight(fail-closed):defuddle CLI + python import akb2.quality.intake.orchestrator;
# playwright 软依赖(缺失只 warn 不 fail)。
set -euo pipefail

PLIST_LABEL="com.akb2.intake"

echo "=== Bootstrapping $PLIST_LABEL ==="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VAULT_ROOT="${VAULT_ROOT:-/Volumes/AKB2/Obsidian}"
HOME_ROOT="${HOME:-/Users/praise}"
PLIST_SRC="$SCRIPT_DIR/../launchd/com.akb2.intake.plist"

# python3 解析:优先 pyenv(与 plist 同),fallback PATH 里的 python3
PYTHON_BIN="$HOME_ROOT/.pyenv/shims/python3"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3 || echo /usr/bin/python3)"
fi

# ---------------------------------------------------------------------------
# Preflight(fail-closed,工单 F21)
# ---------------------------------------------------------------------------
echo "=== Preflight ==="
if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: plist 源不存在: $PLIST_SRC" >&2
    exit 3
fi
if ! command -v defuddle >/dev/null 2>&1; then
    echo "ERROR: defuddle CLI 不可用(orchestrator 必需软依赖)" >&2
    echo "   装: pip install defuddle" >&2
    exit 3
fi
if ! PYTHONPATH="$PROJECT_ROOT/src" "$PYTHON_BIN" -c "import akb2.quality.intake.orchestrator" 2>/dev/null; then
    echo "ERROR: python 无法 import akb2.quality.intake.orchestrator" >&2
    echo "   检查 PYTHONPATH=$PROJECT_ROOT/src + 依赖(scrapling/pyyaml/httpx)" >&2
    exit 3
fi
echo "  OK: defuddle + orchestrator import 可用"
# playwright 软依赖(StealthyFetcher 用,缺失只 warn 不 fail)
if ! PYTHONPATH="$PROJECT_ROOT/src" "$PYTHON_BIN" -c "import playwright" 2>/dev/null; then
    echo "⚠️  playwright 未装(StealthyFetcher 软依赖,缺失时该 fetcher 不可用,装载阶段处理)"
else
    if ! PYTHONPATH="$PROJECT_ROOT/src" "$PYTHON_BIN" -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
        echo "⚠️  playwright 装但 chromium 未装(StealthyFetcher 需 chromium,装载阶段处理)"
    fi
fi

# ---------------------------------------------------------------------------
# Render placeholders
# ---------------------------------------------------------------------------
PLIST_TMP="$(mktemp -t com.akb2.intake.XXXXXX.plist)"
sed \
  -e "s|__AKB2_REPO_ROOT__|$PROJECT_ROOT|g" \
  -e "s|__AKB2_VAULT_ROOT__|$VAULT_ROOT|g" \
  -e "s|__AKB2_HOME__|$HOME_ROOT|g" \
  "$PLIST_SRC" > "$PLIST_TMP"
echo "  Generated plist: repo=$PROJECT_ROOT vault=$VAULT_ROOT home=$HOME_ROOT"

# plutil -lint(XML 合法性,工单 F21 测试要求)
if command -v plutil >/dev/null 2>&1; then
    if ! plutil -lint "$PLIST_TMP" >/dev/null 2>&1; then
        echo "ERROR: plutil -lint 失败——plist XML 不合法" >&2
        rm -f "$PLIST_TMP"
        exit 3
    fi
    echo "  OK: plutil -lint 通过"
fi

# ---------------------------------------------------------------------------
# Idempotent: bootout old (ignore errors)
# ---------------------------------------------------------------------------
launchctl bootout "gui/$(id -u)/$PLIST_LABEL" 2>/dev/null || true
echo "  Removed old registration (if any)"

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
launchctl bootstrap "gui/$(id -u)" "$PLIST_TMP"
echo "  Bootstrapped from $PLIST_TMP"

rm -f "$PLIST_TMP"

# ---------------------------------------------------------------------------
# Verify(断言绑本 plist 可观测特征:orchestrator + StartInterval 7200)
# ---------------------------------------------------------------------------
echo ""
echo "=== Verification ==="
launchctl print "gui/$(id -u)/$PLIST_LABEL" | grep -E 'intake\.orchestrator|run interval = 2h|7200' || {
    echo "ERROR: Verification failed — plist does not reference intake.orchestrator or interval != 7200" >&2
    exit 2
}
echo "  OK: intake.orchestrator detected + StartInterval=7200(每 2 小时)"

echo ""
echo "=== Done ==="
echo "  首跑由 lead 手动 kickstart 验证(RunAtLoad=false,不搞装载即自动跑):"
echo "    launchctl kickstart -k gui/$(id -u)/$PLIST_LABEL"
echo "  日志: tail -f $PROJECT_ROOT/logs/intake.log"
