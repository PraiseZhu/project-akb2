#!/usr/bin/env bash
# launchctl.sh — launchd 安装脚本共用的安全原语（bootout/teardown/bootstrap/守卫）。
#
# 为什么要有这个文件（2026-08-03，Lane 4）：
#   2026-07-28 16:34 `bootstrap_launchd.sh` 被跑了两次，两次都是
#   「bootout 成功 + bootstrap 失败 + 不回滚」——com.akb2.briefing（social 简报主跑）
#   被卸掉，5 天 22 小时无人发现。硬证据是 $TMPDIR 里两份内容完全相同的渲染残留
#   （`com.akb2.briefing.XXXXXX.plist.{ALyRNbRLBO,W1i0OsbLkP}`，均 1423B / Jul 28 16:34，
#   `cmp` 判定 IDENTICAL）：脚本在 `rm -f "$PLIST_TMP"` 之前就 `set -e` 退出了，
#   而它之前唯一能中断的语句就是 `launchctl bootstrap`。
#
#   同一竞态的正确修法在本仓 `scripts/deploy-mac-mini.sh:100-125,309-345` 里已经存在
#   （bootout → wait_for_launchctl_teardown → bootstrap，失败走 rollback()），
#   只是从未回灌到两个 bootstrap 脚本。本文件把那套范式提取成共用实现，
#   供 `bootstrap_launchd.sh` / `bootstrap_intake_launchd.sh` 引用
#   （deploy-mac-mini.sh 的去重留给后续，本轮不动它）。
#
# 用法：
#   . "$SCRIPT_DIR/lib/launchctl.sh"
#   akb2_assert_not_worktree "$PROJECT_ROOT"      || exit 2
#   akb2_assert_vault_root_sane "$VAULT_ROOT"     || exit 2
#   akb2_assert_render_clean "$PLIST_TMP"         || exit 2
#   akb2_launchctl_bootout "$(akb2_launchctl_domain)/$LABEL"
#   akb2_wait_for_launchctl_teardown "$(akb2_launchctl_domain)/$LABEL" || exit 2
#   akb2_launchctl_bootstrap "$(akb2_launchctl_domain)" "$DST"         || rollback
#
# 测试接缝（三个 env 变量，默认值即生产行为；测试必须靠它们，绝不真跑 launchctl）：
#   AKB2_LAUNCH_AGENTS_DIR       plist 安装目标目录，默认 $HOME/Library/LaunchAgents
#   AKB2_ALLOW_WORKTREE_INSTALL  =1 放行 worktree 守卫
#   AKB2_ALLOW_TMP_VAULT_ROOT    =1 放行 VAULT_ROOT 临时目录门

# --------------------------------------------------------------------------
# 具名常量（宪法 P4「预算显式」：重试上限/间隔不得用裸字面量）
# --------------------------------------------------------------------------
# teardown 轮询预算：与 deploy-mac-mini.sh:wait_for_launchctl_teardown 的 10×1s 同额度。
: "${AKB2_TEARDOWN_MAX_ATTEMPTS:=10}"
: "${AKB2_TEARDOWN_SLEEP_SEC:=1}"

# 已知临时目录前缀（VAULT_ROOT 门 3 判据）。$TMPDIR 在运行期追加。
AKB2_TMP_PATH_PREFIXES=(/var/folders /private/var/folders /tmp /private/tmp)

# --------------------------------------------------------------------------
# 基础
# --------------------------------------------------------------------------

# launchd 用户域（gui/<uid>）。
akb2_launchctl_domain() {
    printf 'gui/%s' "$(id -u)"
}

# plist 安装目标目录（可注入，默认 ~/Library/LaunchAgents）。
akb2_launch_agents_dir() {
    printf '%s' "${AKB2_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
}

# --------------------------------------------------------------------------
# 守卫：worktree
# --------------------------------------------------------------------------

# 拒绝从 git worktree 安装生产 job。
# 依据：三个安装脚本的 PROJECT_ROOT 都从脚本自身位置推导（`dirname "$0"/..`），
# 在 .cindy-worktrees/<name>/ 下执行就会把生产 job 的 WorkingDirectory /
# ProgramArguments 指向一个随时会被 `git worktree remove` 删掉的目录。
# 返回 0 = 允许安装；返回 1 = 拒绝（调用方 fail-closed）。
akb2_assert_not_worktree() {
    local project_root="$1"

    if [ "${AKB2_ALLOW_WORKTREE_INSTALL:-0}" = "1" ]; then
        printf '⚠️  AKB2_ALLOW_WORKTREE_INSTALL=1：worktree 守卫已被显式放行（%s）\n' \
            "$project_root" >&2
        return 0
    fi

    # 判据 1：路径含 worktree 目录约定（即使 git 不可用也能拦住）
    case "$project_root" in
        */.cindy-worktrees/*|*/.cindy-worktrees)
            printf 'ERROR: 拒绝安装——PROJECT_ROOT 位于 worktree 目录下：%s\n' "$project_root" >&2
            printf '       worktree 随时会被删除，装上去的生产 job 会指向不存在的路径。\n' >&2
            printf '       请在主仓执行；确实要演练请显式设 AKB2_ALLOW_WORKTREE_INSTALL=1。\n' >&2
            return 1
            ;;
    esac

    # 判据 2：git --git-dir 与 --git-common-dir 不同 ⟹ 处于 linked worktree
    local common_dir git_dir common_abs git_abs
    common_dir="$(git -C "$project_root" rev-parse --git-common-dir 2>/dev/null || true)"
    git_dir="$(git -C "$project_root" rev-parse --git-dir 2>/dev/null || true)"
    if [ -n "$common_dir" ] && [ -n "$git_dir" ]; then
        # git 可能返回相对路径（主仓里两者都是 ".git"），先归一化到绝对路径再比较
        common_abs="$(cd "$project_root" && cd "$common_dir" 2>/dev/null && pwd || printf '%s' "$common_dir")"
        git_abs="$(cd "$project_root" && cd "$git_dir" 2>/dev/null && pwd || printf '%s' "$git_dir")"
        if [ "$common_abs" != "$git_abs" ]; then
            printf 'ERROR: 拒绝安装——当前处于 git worktree（git-dir=%s ≠ git-common-dir=%s）\n' \
                "$git_abs" "$common_abs" >&2
            printf '       请在主仓执行；确实要演练请显式设 AKB2_ALLOW_WORKTREE_INSTALL=1。\n' >&2
            return 1
        fi
    fi
    return 0
}

# --------------------------------------------------------------------------
# 守卫：VAULT_ROOT 三门（fail-closed）
# --------------------------------------------------------------------------

# 门 1 绝对路径 / 门 2 目录存在 / 门 3 不在临时目录下。
# 门 3 的实证依据：2026-07-28 残留 temp 里 `--vault-root` 的值是
# `/var/folders/9m/.../T/tmp.MAVoliqX9M`——本仓标准测试命令就是
# `VAULT_ROOT="$(mktemp -d)" python3 -m pytest -q`，同一 shell 会话跑完测试再跑
# bootstrap 就会把 vault 指向已删临时目录的 job 装上去。
# 返回 0 = 合规；返回 1 = 拒绝。
akb2_assert_vault_root_sane() {
    local vault_root="$1" prefix

    if [ -z "$vault_root" ]; then
        printf 'ERROR: VAULT_ROOT 为空——拒绝渲染/安装\n' >&2
        return 1
    fi
    case "$vault_root" in
        /*) ;;
        *)
            printf 'ERROR: VAULT_ROOT 必须是绝对路径，实际：%s\n' "$vault_root" >&2
            return 1
            ;;
    esac
    if [ ! -d "$vault_root" ]; then
        printf 'ERROR: VAULT_ROOT 目录不存在：%s\n' "$vault_root" >&2
        return 1
    fi

    if [ "${AKB2_ALLOW_TMP_VAULT_ROOT:-0}" = "1" ]; then
        printf '⚠️  AKB2_ALLOW_TMP_VAULT_ROOT=1：VAULT_ROOT 临时目录门已被显式放行（%s）\n' \
            "$vault_root" >&2
        return 0
    fi

    local prefixes=("${AKB2_TMP_PATH_PREFIXES[@]}")
    if [ -n "${TMPDIR:-}" ]; then
        prefixes+=("${TMPDIR%/}")
    fi
    for prefix in "${prefixes[@]}"; do
        [ -n "$prefix" ] || continue
        case "$vault_root" in
            "$prefix"|"$prefix"/*)
                printf 'ERROR: VAULT_ROOT 落在临时目录下（前缀 %s）：%s\n' "$prefix" "$vault_root" >&2
                printf '       临时目录会被系统清掉，装上去的 job 会指向不存在的 vault。\n' >&2
                printf '       这正是 2026-07-28 的实际现场（跑完 VAULT_ROOT=$(mktemp -d) pytest 后接着跑 bootstrap）。\n' >&2
                return 1
                ;;
        esac
    done
    return 0
}

# --------------------------------------------------------------------------
# 守卫：渲染产物双门（占位符残留 + plist 合法性）
# --------------------------------------------------------------------------

# 抄 deploy-mac-mini.sh:286-293 的两道门。lint 优先 plutil（macOS），
# 无 plutil 时退到 python plistlib（等价校验，tests/test_intake_launchd.py 已认这一等价）；
# 两者都没有则 fail-closed，不静默跳过（宪法 P4「失败可见」）。
# 返回 0 = 双门通过；返回 1 = 拒绝。
akb2_assert_render_clean() {
    local rendered="$1" leftovers python_bin

    if [ ! -f "$rendered" ]; then
        printf 'ERROR: 渲染产物不存在：%s\n' "$rendered" >&2
        return 1
    fi

    if grep -q '__AKB2_' "$rendered"; then
        leftovers="$(grep -o '__AKB2_[A-Za-z0-9_]*__' "$rendered" | sort -u | tr '\n' ' ')"
        printf 'ERROR: 渲染后的 plist 仍有未替换占位符：%s\n' "${leftovers% }" >&2
        printf '       放弃安装（既有 plist 未动）。\n' >&2
        return 1
    fi

    if command -v plutil >/dev/null 2>&1; then
        if ! plutil -lint "$rendered" >/dev/null 2>&1; then
            printf 'ERROR: plutil -lint 失败——渲染后的 plist XML 不合法：%s\n' "$rendered" >&2
            return 1
        fi
        return 0
    fi

    python_bin="$(command -v python3 2>/dev/null || true)"
    if [ -n "$python_bin" ]; then
        if ! "$python_bin" -c \
            'import plistlib,sys; plistlib.load(open(sys.argv[1],"rb"))' "$rendered" >/dev/null 2>&1; then
            printf 'ERROR: plistlib 解析失败——渲染后的 plist XML 不合法：%s\n' "$rendered" >&2
            return 1
        fi
        return 0
    fi

    printf 'ERROR: 既无 plutil 也无 python3，无法校验 plist 合法性——fail-closed 拒绝安装\n' >&2
    return 1
}

# --------------------------------------------------------------------------
# launchctl 包装：stderr 与退出码全部留痕（不再被 2>/dev/null + set -e 吞掉）
# --------------------------------------------------------------------------

# bootout 旧注册。"未加载"不是错误（幂等安装的正常分支），但 stderr 必须打出来，
# 不能像旧版 `2>/dev/null || true` 那样把真实失败一起吞了。返回值恒为 0。
akb2_launchctl_bootout() {
    local domain_label="$1" err="" code=0
    err="$(launchctl bootout "$domain_label" 2>&1 >/dev/null)" || code=$?
    if [ "$code" -eq 0 ]; then
        printf '  bootout 成功：%s\n' "$domain_label"
    else
        printf '  bootout 未生效（exit=%s，通常是服务本来没加载）：%s\n' \
            "$code" "${err:-<no stderr>}"
    fi
    return 0
}

# bootout 后等待 launchd 真正 teardown，避免 `Bootstrap failed: 37: Operation
# already in progress` 竞态。范式来自 deploy-mac-mini.sh:109-123。
# 返回 0 = 已 teardown；返回 1 = 超时（调用方 fail-closed / 回滚）。
akb2_wait_for_launchctl_teardown() {
    local domain_label="$1" last_err="" attempt=1
    while [ "$attempt" -le "$AKB2_TEARDOWN_MAX_ATTEMPTS" ]; do
        last_err="$(launchctl print "$domain_label" 2>&1 >/dev/null || true)"
        if printf '%s\n' "$last_err" | grep -qiE 'Could not find service|not found'; then
            printf '  旧服务已 teardown：%s（第 %s 次探测）\n' "$domain_label" "$attempt"
            return 0
        fi
        sleep "$AKB2_TEARDOWN_SLEEP_SEC"
        attempt=$((attempt + 1))
    done
    printf 'ERROR: bootout 后等待 %s teardown 超时（%s 次 × %ss）；最后 launchctl print stderr：%s\n' \
        "$domain_label" "$AKB2_TEARDOWN_MAX_ATTEMPTS" "$AKB2_TEARDOWN_SLEEP_SEC" \
        "${last_err:-<empty>}" >&2
    return 1
}

# bootstrap 一个 plist。失败时把 launchctl 的 exit code 与 stderr 全部打出来
# （2026-07-28 之所以查不到死因，就是因为这两样当时都没留痕）。
# 返回 0 = 成功；返回 1 = 失败。
akb2_launchctl_bootstrap() {
    local domain="$1" plist="$2" err="" code=0
    err="$(launchctl bootstrap "$domain" "$plist" 2>&1 >/dev/null)" || code=$?
    if [ "$code" -eq 0 ]; then
        printf '  bootstrap 成功：%s ← %s\n' "$domain" "$plist"
        return 0
    fi
    printf 'ERROR: launchctl bootstrap 失败（exit=%s，domain=%s，plist=%s）：%s\n' \
        "$code" "$domain" "$plist" "${err:-<no stderr>}" >&2
    return 1
}

# --------------------------------------------------------------------------
# 原子安装 plist 到 LaunchAgents
# --------------------------------------------------------------------------

# 把渲染产物 $1 原子替换到 $2（渲染到临时文件 + mv 替换）。既有文件先备份，
# 备份路径写到全局 AKB2_PLIST_BACKUP（供 rollback 恢复）；无既有文件则置空。
# 旧行为是「从 mktemp 路径 bootstrap 后 rm」——装了什么零留痕，出问题只能靠
# $TMPDIR 残留考古（2026-07-28 就是这么查出来的）。
akb2_install_plist_atomic() {
    local rendered="$1" dst="$2" dst_dir
    AKB2_PLIST_BACKUP=""
    dst_dir="$(dirname "$dst")"
    mkdir -p "$dst_dir"
    if [ -f "$dst" ]; then
        AKB2_PLIST_BACKUP="$(mktemp "$dst_dir/.$(basename "$dst").bak.XXXXXX")"
        cp -p "$dst" "$AKB2_PLIST_BACKUP"
        printf '  已备份既有 plist → %s\n' "$AKB2_PLIST_BACKUP"
    fi
    mv -f "$rendered" "$dst"
    printf '  plist 原子写入：%s\n' "$dst"
}
