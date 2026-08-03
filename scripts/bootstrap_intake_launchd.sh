#!/usr/bin/env bash
# bootstrap_intake_launchd.sh — idempotent launchd bootstrap for com.akb2.intake
#
# Renders launchd/com.akb2.intake.plist placeholders → 双门(占位符残留 + plist 合法性)
# → 原子写入 ~/Library/LaunchAgents → bootout old → 等待 teardown → bootstrap new
# → launchctl print 验证(AND 语义)。脚本只写不跑:本脚本由 lead 装载时执行
# (worker 不装载,工单 F21 红线:不自行 bootstrap/装载任何 plist)。
#
# Preflight(fail-closed):worktree 守卫 + VAULT_ROOT 三门 + defuddle CLI +
# python import akb2.quality.intake.orchestrator;playwright 软依赖(缺失只 warn 不 fail)。
#
# 安全语义(2026-08-03 Lane 4):与 bootstrap_launchd.sh 同源,共用实现在
# scripts/lib/launchctl.sh。事故背景:2026-07-28 同型脚本「bootout 成功 +
# bootstrap 失败 + 不回滚」把 com.akb2.briefing 卸掉 5 天 22 小时无人发现。
set -euo pipefail

PLIST_LABEL="com.akb2.intake"

echo "=== Bootstrapping $PLIST_LABEL ==="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VAULT_ROOT="${VAULT_ROOT:-/Volumes/AKB2/Obsidian}"
HOME_ROOT="${HOME:-/Users/praise}"
PLIST_SRC="$SCRIPT_DIR/../launchd/com.akb2.intake.plist"
# shellcheck source=scripts/lib/launchctl.sh
. "$SCRIPT_DIR/lib/launchctl.sh"

# python3 解析:优先 pyenv(与 plist 同),fallback PATH 里的 python3
PYTHON_BIN="$HOME_ROOT/.pyenv/shims/python3"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3 || echo /usr/bin/python3)"
fi

# ---------------------------------------------------------------------------
# Preflight(fail-closed,工单 F21 + Lane 4 守卫)
# ---------------------------------------------------------------------------
echo "=== Preflight ==="
# 安装位置与 vault 路径的守卫排在最前:它们判定的是"这次安装本身合不合法",
# 不该等到装依赖检查完才发现装错了地方。
akb2_assert_not_worktree "$PROJECT_ROOT" || exit 3
akb2_assert_vault_root_sane "$VAULT_ROOT" || exit 3
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
BACKUP_PLIST=""
trap 'rm -f "${PLIST_TMP:-}" "${BACKUP_PLIST:-}" 2>/dev/null || true' EXIT
sed \
  -e "s|__AKB2_REPO_ROOT__|$PROJECT_ROOT|g" \
  -e "s|__AKB2_VAULT_ROOT__|$VAULT_ROOT|g" \
  -e "s|__AKB2_HOME__|$HOME_ROOT|g" \
  "$PLIST_SRC" > "$PLIST_TMP"
echo "  Generated plist: repo=$PROJECT_ROOT vault=$VAULT_ROOT home=$HOME_ROOT"

# 双门:占位符残留 + plist 合法性(此刻既有 plist 完全未动)
akb2_assert_render_clean "$PLIST_TMP" || exit 3
echo "  OK: 占位符残留检查 + plist 合法性检查通过"

# ---------------------------------------------------------------------------
# 原子写入真实安装路径,再从该路径 bootstrap(不再从 mktemp 路径装完就 rm)
# ---------------------------------------------------------------------------
LAUNCH_AGENTS_DIR="$(akb2_launch_agents_dir)"
PLIST_DST="$LAUNCH_AGENTS_DIR/$PLIST_LABEL.plist"
DOMAIN="$(akb2_launchctl_domain)"
DOMAIN_LABEL="$DOMAIN/$PLIST_LABEL"

akb2_install_plist_atomic "$PLIST_TMP" "$PLIST_DST"
PLIST_TMP=""
BACKUP_PLIST="$AKB2_PLIST_BACKUP"

# 回滚:恢复上次 good(首装则移除新 plist,不留半安装态)。
# 范式来自 scripts/deploy-mac-mini.sh:309-324。
rollback() {
    akb2_launchctl_bootout "$DOMAIN_LABEL"
    akb2_wait_for_launchctl_teardown "$DOMAIN_LABEL" || true
    if [ -n "$BACKUP_PLIST" ] && [ -f "$BACKUP_PLIST" ]; then
        mv -f "$BACKUP_PLIST" "$PLIST_DST"
        BACKUP_PLIST=""
        if akb2_launchctl_bootstrap "$DOMAIN" "$PLIST_DST"; then
            echo "↩️  已恢复旧 plist 并重新 bootstrap 成功:$PLIST_DST" >&2
        else
            echo "↩️  已恢复旧 plist,但重新 bootstrap 失败——服务当前无 job 在册!" >&2
            echo "    手动补装:launchctl bootstrap $DOMAIN $PLIST_DST" >&2
        fi
    else
        rm -f "$PLIST_DST"
        echo "↩️  此前无旧 plist,已移除新 plist(不留半安装态):$PLIST_DST" >&2
        echo "    服务当前无 job 在册(与执行前一致)。" >&2
    fi
}

# ---------------------------------------------------------------------------
# bootout 旧 + 等待 teardown + bootstrap 新(stderr 与退出码全部留痕)
# ---------------------------------------------------------------------------
akb2_launchctl_bootout "$DOMAIN_LABEL"
if ! akb2_wait_for_launchctl_teardown "$DOMAIN_LABEL"; then
    echo "ERROR: teardown 超时 → 回滚" >&2
    rollback
    exit 2
fi
if ! akb2_launchctl_bootstrap "$DOMAIN" "$PLIST_DST"; then
    echo "ERROR: $PLIST_LABEL bootstrap 失败 → 回滚" >&2
    rollback
    exit 2
fi

# ---------------------------------------------------------------------------
# Verify — AND 语义:orchestrator 引用 + StartInterval=900 分别断言。
# 旧版把两者用 `grep -E 'a|b|c'` 拼成 OR,且里面写的间隔值(每 2 小时那版)早在
# commit 115554f 就被改成 900 → 调度断言实际已死,只剩"引用了 orchestrator"在起作用。
# ---------------------------------------------------------------------------
echo ""
echo "=== Verification ==="
PRINT_OUT="$(launchctl print "$DOMAIN_LABEL" 2>&1 || true)"
if ! printf '%s\n' "$PRINT_OUT" | grep -q 'intake\.orchestrator'; then
    echo "ERROR: Verification failed — 注册的 job 未引用 intake.orchestrator" >&2
    printf '%s\n' "$PRINT_OUT" >&2
    exit 2
fi
if ! printf '%s\n' "$PRINT_OUT" | grep -q '900'; then
    echo "ERROR: Verification failed — 注册的 job 调度不是 StartInterval=900(每 15 分钟)" >&2
    printf '%s\n' "$PRINT_OUT" >&2
    exit 2
fi
echo "  OK: intake.orchestrator detected + StartInterval=900(每 15 分钟)"

echo ""
echo "=== Done ==="
echo "  已安装 plist:$PLIST_DST"
echo "  首跑由 lead 手动 kickstart 验证(RunAtLoad=false,不搞装载即自动跑):"
echo "    launchctl kickstart -k $DOMAIN_LABEL"
echo "  日志: tail -f $PROJECT_ROOT/logs/intake.log"
