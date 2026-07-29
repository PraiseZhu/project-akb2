#!/usr/bin/env bash
# bootstrap_launchd.sh — idempotent launchd bootstrap
# Removes old registration then bootstraps new one.
# Dynamically replaces plist placeholders with current PROJECT_ROOT.
#
# 用法:
#   bash scripts/bootstrap_launchd.sh                        # 渲染 + 注册 + 验证
#   bash scripts/bootstrap_launchd.sh --render-only <out>     # 只渲染到 <out>，不碰 launchctl
#   bash scripts/bootstrap_launchd.sh --plist-src <template>  # 换模板（默认 briefing）
#
# 占位符: __AKB2_REPO_ROOT__ / __AKB2_VAULT_ROOT__ / __AKB2_STATE_ROOT__
#   __AKB2_STATE_ROOT__ = 机器本地状态根，用于把**游标/账本类状态**（intake
#   --state-file、失败账本等）挪出仓库：PROJECT_ROOT 在 ~/AI-Agent(Syncthing folder)
#   内，状态放仓库会被跨机同步分叉（reports/intake/ 下已有两个
#   sweep-probe-result.sync-conflict-* 实证）。解析顺序与 python 侧
#   src/akb2/common/state_paths.get_state_root() 一致:
#   AKB2_STATE_ROOT > $HOME/Library/Application Support/akb2
set -euo pipefail

PLIST_LABEL="com.akb2.briefing"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VAULT_ROOT="${VAULT_ROOT:-/Volumes/AKB2/Obsidian}"
# shellcheck source=scripts/lib/state-root.sh
. "$SCRIPT_DIR/lib/state-root.sh"
akb2_resolve_state_root
PLIST_SRC="$SCRIPT_DIR/../launchd/com.akb2.briefing.plist"
RENDER_ONLY_OUT=""

# 参数解析必须在任何 launchctl 之前：早期版本没有解析，未知参数被静默忽略后脚本
# 仍会 bootout/bootstrap 真服务（实测把 com.akb2.briefing 卸掉过）。
while [ $# -gt 0 ]; do
    case "$1" in
        --render-only) RENDER_ONLY_OUT="${2:-}"; shift 2 ;;
        --plist-src)   PLIST_SRC="${2:-}"; shift 2 ;;
        -h|--help)     grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "ERROR: 未知参数: $1（用 --help 看用法）" >&2; exit 2 ;;
    esac
done

# fail-closed：状态根落在跨机同步范围内 → 拒绝渲染（与 python 侧同语义）
if akb2_state_root_is_synced; then
    echo "ERROR: AKB2_STATE_ROOT=$STATE_ROOT 在跨机同步范围内；状态放同步目录会被 Syncthing 分叉丢数据" >&2
    exit 2
fi

if [ -n "$RENDER_ONLY_OUT" ]; then
    echo "=== Rendering $PLIST_LABEL (render-only, 不注册) ==="
else
    echo "=== Bootstrapping $PLIST_LABEL ==="
fi

if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: plist 模板不存在: $PLIST_SRC" >&2
    exit 2
fi

# Generate temp plist with dynamic paths (avoid hardcoded paths)
PLIST_TMP="$(mktemp -t com.akb2.briefing.XXXXXX.plist)"
sed \
  -e "s|__AKB2_REPO_ROOT__|$PROJECT_ROOT|g" \
  -e "s|__AKB2_VAULT_ROOT__|$VAULT_ROOT|g" \
  -e "s|__AKB2_STATE_ROOT__|$STATE_ROOT|g" \
  "$PLIST_SRC" > "$PLIST_TMP"
echo "  Generated plist with repo root: $PROJECT_ROOT"
echo "  Vault root: $VAULT_ROOT"
echo "  State root: $STATE_ROOT"

# --render-only: 交付渲染结果后退出，绝不碰 launchctl（供人工核对将要注册的路径）
if [ -n "$RENDER_ONLY_OUT" ]; then
    mv "$PLIST_TMP" "$RENDER_ONLY_OUT"
    echo "  Rendered → $RENDER_ONLY_OUT (未注册任何服务)"
    exit 0
fi

# Idempotent: remove old registration (ignore errors)
launchctl bootout "gui/$(id -u)/$PLIST_LABEL" 2>/dev/null || true
echo "  Removed old registration (if any)"

# Bootstrap
launchctl bootstrap "gui/$(id -u)" "$PLIST_TMP"
echo "  Bootstrapped from $PLIST_TMP"

# Cleanup temp plist
rm -f "$PLIST_TMP"

# Verify
echo ""
echo "=== Verification ==="
launchctl print "gui/$(id -u)/$PLIST_LABEL" | grep -E 'daily_briefing\.sh|hour = 7' || {
    echo "ERROR: Verification failed — plist does not reference daily_briefing.sh or hour != 7" >&2
    exit 2
}
echo "  OK: daily_briefing.sh detected + hour = 7 (本地 7:30，与社媒 7:30/8:30 错峰)"

# Env 完整性检查
echo ""
echo "=== Env Check ==="
ENV_FILE="$HOME/.config/akb2/github-briefing.env"
if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE" 2>/dev/null || true
    MISSING=""
    [ -z "${GITHUB_PAT:-}" ] && MISSING="$MISSING GITHUB_PAT"
    [ -z "${VAULT_ROOT:-}" ] && MISSING="$MISSING VAULT_ROOT"
    [ -z "${AI_GATEWAY_API_KEY:-}" ] && MISSING="$MISSING AI_GATEWAY_API_KEY"
    if [ -n "$MISSING" ]; then
        echo "⚠️  env 文件缺少以下必填字段:$MISSING"
        echo "   请编辑 $ENV_FILE 后重跑 bootstrap"
    else
        echo "  OK: env 文件字段完整"
    fi
else
    echo "⚠️  env 文件不存在: $ENV_FILE"
    echo "   cp config/github-briefing.env.example $ENV_FILE"
    echo "   然后编辑填入实际值后重跑 bootstrap"
fi
