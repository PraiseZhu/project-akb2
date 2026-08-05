#!/usr/bin/env bash
# bootstrap_launchd.sh — idempotent launchd bootstrap
# Removes old registration then bootstraps new one.
# Dynamically replaces plist placeholders with current PROJECT_ROOT.
#
# 用法:
#   bash scripts/bootstrap_launchd.sh                        # 渲染 + 原子安装 + 注册 + 验证
#   bash scripts/bootstrap_launchd.sh --render-only <out>     # 只渲染到 <out>，不碰 launchctl
#   bash scripts/bootstrap_launchd.sh --plist-src <template>  # 换模板（默认 briefing）
#
# 占位符: __AKB2_REPO_ROOT__ / __AKB2_VAULT_ROOT__ / __AKB2_STATE_ROOT__ / __AKB2_HOME__
#   __AKB2_STATE_ROOT__ = 机器本地状态根，用于把**游标/账本类状态**（intake
#   --state-file、失败账本等）挪出仓库：PROJECT_ROOT 在 ~/AI-Agent(Syncthing folder)
#   内，状态放仓库会被跨机同步分叉（reports/intake/ 下已有两个
#   sweep-probe-result.sync-conflict-* 实证）。解析顺序与 python 侧
#   src/akb2/common/state_paths.get_state_root() 一致:
#   AKB2_STATE_ROOT > $HOME/Library/Application Support/akb2
#
# 安全语义（2026-08-03 Lane 4，事故驱动）：
#   - plist 先**原子写入** ~/Library/LaunchAgents/<label>.plist 再从该真实路径
#     bootstrap（旧版从 mktemp 路径 bootstrap 后 rm，装了什么零留痕）
#   - bootout → **等待 teardown** → bootstrap；bootstrap 失败**回滚**并把 launchctl
#     的 exit code + stderr 全部打出来（2026-07-28 事故：bootout 成功 + bootstrap
#     失败 + 不回滚 → com.akb2.briefing 被卸掉 5 天 22 小时无人发现）
#   - VAULT_ROOT 三门 fail-closed（绝对路径 / 目录存在 / 不在临时目录，按真实路径判定）
#   - worktree 守卫 fail-closed（git 判定失败也拒绝，不当主仓）
#   - HOME 缺失 fail-closed（不静默替换成任何字面家目录）
#   - **身份从模板的 Label 键派生**：`--plist-src` 换模板时，Label / 目标文件 /
#     launchctl 域标签 / 验证断言四者同一身份。旧版这些量全硬编码 com.akb2.briefing，
#     于是 INSTALL-MANIFEST 里那条「--plist-src 装 github_briefing」的命令实际会
#     覆写并 bounce 掉 social 主跑，而目标 job 一次也没装上。
#   - 后置验证失败 → **回滚**（不留「新版已装未验证」态）
#   共用实现见 scripts/lib/launchctl.sh。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VAULT_ROOT="${VAULT_ROOT:-/Volumes/AKB2/Obsidian}"
# shellcheck source=scripts/lib/state-root.sh
. "$SCRIPT_DIR/lib/state-root.sh"
# shellcheck source=scripts/lib/launchctl.sh
. "$SCRIPT_DIR/lib/launchctl.sh"
# HOME fail-closed 必须在任何用到 $HOME 的地方之前（state root / LaunchAgents 都用它）
akb2_assert_home_set || exit 2
HOME_ROOT="$HOME"
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

# fail-closed 守卫（在渲染之前——render-only 也要过，否则 --render-only 就成了
# 绕过 VAULT_ROOT 三门的后门，而它渲染出的产物是给人核对"将要注册的路径"的）
akb2_assert_not_worktree "$PROJECT_ROOT" || exit 2
akb2_assert_vault_root_sane "$VAULT_ROOT" || exit 2

if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: plist 模板不存在: $PLIST_SRC" >&2
    exit 2
fi

# 身份派生：Label 是这次安装的唯一真相来源（--plist-src 换模板时随之变）。
# 从**模板**（而不是运行态）取，符合 parity 的信任方向：可信期望来自仓库配置。
akb2_plist_identity "$PLIST_SRC" || exit 2
PLIST_LABEL="$AKB2_PLIST_LABEL"
VERIFY_PROGRAM_TOKEN="$AKB2_PLIST_PROGRAM_TOKEN"
VERIFY_SCHEDULE_TOKEN="$AKB2_PLIST_SCHEDULE_TOKEN"

if [ -n "$RENDER_ONLY_OUT" ]; then
    echo "=== Rendering $PLIST_LABEL (render-only, 不注册) ==="
else
    echo "=== Bootstrapping $PLIST_LABEL ==="
fi
echo "  Label（自模板 $PLIST_SRC 派生）: $PLIST_LABEL"

# Generate temp plist with dynamic paths (avoid hardcoded paths)
PLIST_TMP="$(mktemp -t akb2-bootstrap-plist.XXXXXX)"
BACKUP_PLIST=""
AKB2_ROLLBACK_PRESERVE_BACKUP=0
# 任何退出路径都清临时件与备份件（2026-07-28 之所以能考古到死点，正是因为旧版
# 在 set -e 半途退出时把 mktemp 产物留在了 $TMPDIR；留残留不该是取证手段）。
# 例外：回滚补偿失败时备份是**唯一的旧 good 副本**，必须保住（见 akb2_rollback_plist）。
cleanup_artifacts() {
    rm -f "${PLIST_TMP:-}" 2>/dev/null || true
    if [ "${AKB2_ROLLBACK_PRESERVE_BACKUP:-0}" != "1" ]; then
        rm -f "${BACKUP_PLIST:-}" 2>/dev/null || true
    fi
    return 0
}
trap cleanup_artifacts EXIT
# __AKB2_HOME__ 也必须渲染：2026-08-03 briefing plist 的 PATH 从硬编码
# 个人家目录 .pyenv/... 改成 __AKB2_HOME__/.pyenv/...，本脚本当时没跟着加这条
# 替换 —— 若照旧装上去，job 的 PATH 会是字面量 "__AKB2_HOME__/.pyenv/shims:…"，
# pyenv python3 解析不到，主跑运行期才炸。由渲染双门在安装前拦下。
sed \
  -e "s|__AKB2_REPO_ROOT__|$PROJECT_ROOT|g" \
  -e "s|__AKB2_VAULT_ROOT__|$VAULT_ROOT|g" \
  -e "s|__AKB2_STATE_ROOT__|$STATE_ROOT|g" \
  -e "s|__AKB2_HOME__|$HOME_ROOT|g" \
  "$PLIST_SRC" > "$PLIST_TMP"
echo "  Generated plist with repo root: $PROJECT_ROOT"
echo "  Vault root: $VAULT_ROOT"
echo "  State root: $STATE_ROOT"
echo "  Home root: $HOME_ROOT"

# 渲染产物双门：占位符残留 + plist 合法性（此刻既有 plist 完全未动）
akb2_assert_render_clean "$PLIST_TMP" || exit 2
echo "  OK: 占位符残留检查 + plist 合法性检查通过"

# --render-only: 交付渲染结果后退出，绝不碰 launchctl（供人工核对将要注册的路径）
if [ -n "$RENDER_ONLY_OUT" ]; then
    mv "$PLIST_TMP" "$RENDER_ONLY_OUT"
    PLIST_TMP=""
    echo "  Rendered → $RENDER_ONLY_OUT (未注册任何服务)"
    exit 0
fi

# 原子写入真实安装路径，再从该路径 bootstrap（不再从 mktemp 路径装）
LAUNCH_AGENTS_DIR="$(akb2_launch_agents_dir)"
PLIST_DST="$LAUNCH_AGENTS_DIR/$PLIST_LABEL.plist"
DOMAIN="$(akb2_launchctl_domain)"
DOMAIN_LABEL="$DOMAIN/$PLIST_LABEL"

akb2_install_plist_atomic "$PLIST_TMP" "$PLIST_DST"
PLIST_TMP=""
BACKUP_PLIST="$AKB2_PLIST_BACKUP"

# 回滚：恢复上次 good（首装则移除新 plist，不留半安装态）。共用实现在 lib
# （两份各自漂移过一次就够了）。补偿失败时它会保住备份并打手动恢复指引。
rollback() {
    akb2_rollback_plist "$DOMAIN" "$PLIST_LABEL" "$PLIST_DST" BACKUP_PLIST || true
}

# bootout 旧 + 等待 teardown + bootstrap 新（stderr 与退出码全部留痕）
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

# Verify — AND 语义：两条断言分别 grep，任一不满足即失败。
# 旧版是 `grep -E 'daily_briefing\.sh|hour = 7'`（OR），只要引用了脚本就算过，
# 调度断言实际从未生效；且两个 token 都硬编码成 social 的值，`--plist-src` 换模板后
# 必然验证失败。现在两个 token 都从**模板**派生（见 akb2_plist_identity）。
#
# 验证失败 → **回滚**（SC-R1-17）。旧版是直接 `exit 2`：报告了失败，却把
# 已知有问题的新 job 留在册，同时 EXIT trap 把 last-good 备份删掉 ——
# 「新版已装未验证」正是本 lane 要消掉的那类中间态。
echo ""
echo "=== Verification ==="
verify_failed() {
    echo "ERROR: Verification failed — $1 → 回滚到执行前状态" >&2
    printf '%s\n' "${AKB2_BOUNDED_OUTPUT:-<no output>}" >&2
    rollback
    exit 2
}
if ! akb2_launchctl_print_bounded "$DOMAIN_LABEL"; then
    verify_failed "launchctl print 未能读到 ${DOMAIN_LABEL}（含超时）"
fi
PRINT_OUT="$AKB2_BOUNDED_OUTPUT"
if [ -z "$VERIFY_PROGRAM_TOKEN" ]; then
    verify_failed "模板未能派生出程序锚（ProgramArguments 缺脚本/模块）"
fi
if ! printf '%s\n' "$PRINT_OUT" | grep -qF "$VERIFY_PROGRAM_TOKEN"; then
    verify_failed "注册的 job 未引用 $VERIFY_PROGRAM_TOKEN"
fi
if [ -z "$VERIFY_SCHEDULE_TOKEN" ]; then
    verify_failed "模板未能派生出调度锚（既无 StartCalendarInterval.Hour 也无 StartInterval）"
fi
if ! printf '%s\n' "$PRINT_OUT" | grep -qF "$VERIFY_SCHEDULE_TOKEN"; then
    verify_failed "注册的 job 调度不是 ${VERIFY_SCHEDULE_TOKEN}"
fi
echo "  OK: $VERIFY_PROGRAM_TOKEN detected + ${VERIFY_SCHEDULE_TOKEN}（与模板一致）"

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
