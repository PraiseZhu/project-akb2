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

# 单次 launchctl 调用的 wall-clock 上限（秒）。
# 为什么必须有：`AKB2_TEARDOWN_MAX_ATTEMPTS × SLEEP` 限制的是**循环次数**，不是单次
# 调用的墙钟 —— 一次挂死的 `launchctl print` 就能让脚本永久悬挂在
# 「bootout 已生效 / bootstrap 还没做」这个**破坏性中间态**上（2026-07-28 事故正是
# 停在这个态，只是触发源是 bootstrap 失败而非悬挂）。超时按失败处理 → 进回滚。
: "${AKB2_LAUNCHCTL_TIMEOUT_SEC:=30}"
# 超时返回码：与 GNU timeout(1) / run_with_deadline.py 的约定一致，便于统一识别。
AKB2_BOUNDED_TIMEOUT_RC=124
# 轮询粒度（秒）：0.2 让"超时"在测试里 1s 内就能观测到，生产上开销可忽略。
AKB2_BOUNDED_POLL_INTERVAL_SEC="0.2"
AKB2_BOUNDED_TICKS_PER_SEC=5
AKB2_BOUNDED_OUTPUT=""

# 已知临时目录前缀（VAULT_ROOT 门 3 判据）。$TMPDIR 在运行期追加。
AKB2_TMP_PATH_PREFIXES=(/var/folders /private/var/folders /tmp /private/tmp)

# --------------------------------------------------------------------------
# 基础
# --------------------------------------------------------------------------

# launchd 用户域（gui/<uid>）。
akb2_launchctl_domain() {
    printf 'gui/%s' "$(id -u)"
}

# HOME fail-closed。为什么单独一道门：HOME 在 launchd / `env -i` / `sudo` 下确实
# 会缺失（scripts/find-python311.sh 自己就注释了这一点）。旧写法是
# `HOME_ROOT="${HOME:-<owner 的家目录字面量>}"` —— 静默替换成**别人的家目录**，
# 于是渲染出的 plist 通过双门（占位符已替换 + XML 合法）后被装进 LaunchAgents，
# PATH 指向一个不属于当前用户的 .pyenv，运行期才炸。本 PR 给 VAULT_ROOT / state root
# / worktree 都加了 fail-closed，HOME 不该是唯一的 fail-open 路径变量。
# 返回 0 = HOME 可用；返回 1 = 拒绝（调用方 fail-closed）。
akb2_assert_home_set() {
    if [ -z "${HOME:-}" ]; then
        printf 'ERROR: HOME 未设置——拒绝渲染/安装（fail-closed）\n' >&2
        printf '       launchd / env -i / sudo 下 HOME 会缺失；静默替换成任何字面家目录\n' >&2
        printf '       都会把 plist 的 PATH 指向别人的 .pyenv，运行期才炸。\n' >&2
        printf '       请显式提供 HOME=<当前用户家目录> 后重跑。\n' >&2
        return 1
    fi
    return 0
}

# plist 安装目标目录（可注入，默认 ~/Library/LaunchAgents）。
akb2_launch_agents_dir() {
    printf '%s' "${AKB2_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
}

# --------------------------------------------------------------------------
# 有界外部调用（wall-clock 上限）
# --------------------------------------------------------------------------

# akb2_run_bounded <秒> <命令…> —— 在 wall-clock 上限内跑命令。
# 输出（stdout+stderr 合并）落 AKB2_BOUNDED_OUTPUT；返回命令的退出码，
# 超时返回 AKB2_BOUNDED_TIMEOUT_RC。
# 不用 timeout(1)：GNU 专用，macOS 默认没有（会踩 tests/test_shell_portability.py
# 的跨平台守卫）。纯 bash 轮询 + TERM→KILL 两段收尾，两个平台同语义。
akb2_run_bounded() {
    local limit="$1"; shift
    local tmp_out pid rc=0 ticks=0 max_ticks
    tmp_out="$(mktemp -t akb2-bounded.XXXXXX)"
    AKB2_BOUNDED_OUTPUT=""
    max_ticks=$(( limit * AKB2_BOUNDED_TICKS_PER_SEC ))
    "$@" >"$tmp_out" 2>&1 &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$ticks" -ge "$max_ticks" ]; then
            kill -TERM "$pid" 2>/dev/null || true
            sleep "$AKB2_BOUNDED_POLL_INTERVAL_SEC"
            kill -KILL "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
            AKB2_BOUNDED_OUTPUT="$(cat "$tmp_out" 2>/dev/null || true)"
            rm -f "$tmp_out"
            return "$AKB2_BOUNDED_TIMEOUT_RC"
        fi
        sleep "$AKB2_BOUNDED_POLL_INTERVAL_SEC"
        ticks=$(( ticks + 1 ))
    done
    wait "$pid" || rc=$?
    AKB2_BOUNDED_OUTPUT="$(cat "$tmp_out" 2>/dev/null || true)"
    rm -f "$tmp_out"
    return "$rc"
}

# --------------------------------------------------------------------------
# plist 身份派生（--plist-src 的唯一真相来源）
# --------------------------------------------------------------------------

# python3 解析（plistlib 是本文件所有 plist 判定的唯一实现，见 akb2_assert_render_clean）。
akb2_python_bin() {
    local python_bin
    python_bin="$(command -v python3 2>/dev/null || true)"
    if [ -z "$python_bin" ]; then
        printf 'ERROR: 找不到 python3——plist 解析/校验无法进行，fail-closed\n' >&2
        return 1
    fi
    printf '%s' "$python_bin"
}

# akb2_plist_identity <plist> —— 从 plist 派生**这次安装的身份**，写三个全局：
#   AKB2_PLIST_LABEL           Label 键（决定目标文件名 / launchctl 域标签 / 验证对象）
#   AKB2_PLIST_PROGRAM_TOKEN   验证用的程序锚（`-m <module>` 优先，否则脚本 basename）
#   AKB2_PLIST_SCHEDULE_TOKEN  验证用的调度锚（`hour = N` 或 StartInterval 秒数）
#
# 为什么必须派生而不是硬编码：`--plist-src` 是本轮新增参数，而 Label / 目标文件 /
# bootout 域标签 / 验证断言全都写死成 com.akb2.briefing —— 于是 INSTALL-MANIFEST 里
# 那条「用 --plist-src 装 github_briefing」的命令实际会**卸掉 social 主跑并把 github
# 的内容写进 com.akb2.briefing.plist**，再按 social 的判据验证（必然失败）→ 回滚。
# 净效果是把生产 job bounce 一遍而目标 job 一次也没装上。同一个脚本 2026-07-28 刚把
# com.akb2.briefing 卸掉 5 天 22 小时。
# 返回 0 = 派生成功；返回 1 = 拒绝（Label 缺失/不可解析 → fail-closed）。
akb2_plist_identity() {
    local plist="$1" python_bin raw
    AKB2_PLIST_LABEL=""
    AKB2_PLIST_PROGRAM_TOKEN=""
    AKB2_PLIST_SCHEDULE_TOKEN=""
    python_bin="$(akb2_python_bin)" || return 1
    if ! raw="$("$python_bin" - "$plist" <<'PY'
import os
import plistlib
import sys

with open(sys.argv[1], "rb") as handle:
    data = plistlib.load(handle)
if not isinstance(data, dict):
    sys.exit(3)
label = str(data.get("Label") or "").strip()
if not label:
    sys.exit(3)

args = [a for a in (data.get("ProgramArguments") or []) if isinstance(a, str)]
program = ""
if "-m" in args:
    index = args.index("-m")
    if index + 1 < len(args):
        program = args[index + 1]
if not program:
    for item in args:
        base = os.path.basename(item)
        if base.endswith((".sh", ".py")):
            program = base
            break

schedule = ""
calendar = data.get("StartCalendarInterval")
if isinstance(calendar, dict) and "Hour" in calendar:
    schedule = "hour = %s" % calendar["Hour"]
elif isinstance(calendar, list) and calendar and isinstance(calendar[0], dict) \
        and "Hour" in calendar[0]:
    schedule = "hour = %s" % calendar[0]["Hour"]
elif isinstance(data.get("StartInterval"), int):
    schedule = str(data["StartInterval"])

print(label)
print(program)
print(schedule)
PY
    )"; then
        printf 'ERROR: 无法从 plist 解析 Label（模板不合法或缺 Label 键）：%s\n' "$plist" >&2
        return 1
    fi
    AKB2_PLIST_LABEL="$(printf '%s\n' "$raw" | sed -n '1p')"
    AKB2_PLIST_PROGRAM_TOKEN="$(printf '%s\n' "$raw" | sed -n '2p')"
    AKB2_PLIST_SCHEDULE_TOKEN="$(printf '%s\n' "$raw" | sed -n '3p')"
    if [ -z "$AKB2_PLIST_LABEL" ]; then
        printf 'ERROR: plist 的 Label 为空：%s\n' "$plist" >&2
        return 1
    fi
    return 0
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
    #
    # **判定失败即拒绝（fail-closed）**：旧写法两次 `rev-parse` 都带 `|| true`，
    # 空结果一路走到 `return 0` —— 于是 git 不可用 / `dubious ownership` /
    # 只吐出一半输出 / 压根不是 git 仓，全都被当成「这是主仓，放行」。
    # 守卫的语义是「确认不在 worktree」，无法确认时不能推断为确认（加固清单第 9 类：
    # 保证等级不得超过证据）。要在非 git 环境演练就显式设 AKB2_ALLOW_WORKTREE_INSTALL=1。
    local common_dir git_dir common_abs git_abs git_rc=0
    if ! command -v git >/dev/null 2>&1; then
        printf 'ERROR: 拒绝安装——git 不可用，无法判定是否处于 worktree（fail-closed）\n' >&2
        printf '       确实要在无 git 环境演练请显式设 AKB2_ALLOW_WORKTREE_INSTALL=1。\n' >&2
        return 1
    fi
    common_dir="$(git -C "$project_root" rev-parse --git-common-dir 2>&1)" || git_rc=$?
    if [ "$git_rc" -ne 0 ] || [ -z "$common_dir" ]; then
        printf 'ERROR: 拒绝安装——git rev-parse --git-common-dir 失败（exit=%s）：%s\n' \
            "$git_rc" "${common_dir:-<empty>}" >&2
        printf '       无法确认不在 worktree 就不能安装生产 job（fail-closed）。\n' >&2
        printf '       确实要演练请显式设 AKB2_ALLOW_WORKTREE_INSTALL=1。\n' >&2
        return 1
    fi
    git_rc=0
    git_dir="$(git -C "$project_root" rev-parse --git-dir 2>&1)" || git_rc=$?
    if [ "$git_rc" -ne 0 ] || [ -z "$git_dir" ]; then
        printf 'ERROR: 拒绝安装——git rev-parse --git-dir 失败（exit=%s）：%s\n' \
            "$git_rc" "${git_dir:-<empty>}" >&2
        printf '       无法确认不在 worktree 就不能安装生产 job（fail-closed）。\n' >&2
        return 1
    fi
    # git 可能返回相对路径（主仓里两者都是 ".git"），先归一化到绝对路径再比较
    common_abs="$(cd "$project_root" && cd "$common_dir" 2>/dev/null && pwd -P || printf '%s' "$common_dir")"
    git_abs="$(cd "$project_root" && cd "$git_dir" 2>/dev/null && pwd -P || printf '%s' "$git_dir")"
    if [ "$common_abs" != "$git_abs" ]; then
        printf 'ERROR: 拒绝安装——当前处于 git worktree（git-dir=%s ≠ git-common-dir=%s）\n' \
            "$git_abs" "$common_abs" >&2
        printf '       请在主仓执行；确实要演练请显式设 AKB2_ALLOW_WORKTREE_INSTALL=1。\n' >&2
        return 1
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
    local vault_root="$1" prefix real_root

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

    # 门 3 必须按**真实路径**判定：旧实现先跟随 symlink 做 `-d`（门 2），再拿**原始
    # 字符串**比前缀（门 3）—— 于是 `~/vault-link → /var/folders/…/T/real-vault`
    # 这类 symlink 一路放行（R1 实测 guard_rc=0），`/var/../tmp/x` 同样能绕。
    # `pwd -P` 而不是 `readlink -f`：后者旧版 macOS 没有，且被跨平台守卫禁用。
    real_root="$(cd "$vault_root" 2>/dev/null && pwd -P)" || real_root=""
    if [ -z "$real_root" ]; then
        printf 'ERROR: VAULT_ROOT 无法解析为真实路径（不可进入）：%s\n' "$vault_root" >&2
        return 1
    fi

    local prefixes=("${AKB2_TMP_PATH_PREFIXES[@]}")
    if [ -n "${TMPDIR:-}" ]; then
        prefixes+=("${TMPDIR%/}")
        # $TMPDIR 自身常是 symlink（macOS 的 /var/folders → /private/var/folders）
        prefixes+=("$(cd "${TMPDIR%/}" 2>/dev/null && pwd -P || true)")
    fi
    for prefix in "${prefixes[@]}"; do
        [ -n "$prefix" ] || continue
        case "$real_root" in
            "$prefix"|"$prefix"/*)
                printf 'ERROR: VAULT_ROOT 落在临时目录下（前缀 %s，真实路径 %s）：%s\n' \
                    "$prefix" "$real_root" "$vault_root" >&2
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

# 抄 deploy-mac-mini.sh:286-293 的两道门。
#
# ── lint 的两个实现**不等价**，所以判定权只给 plistlib（R1 审查 B2）──────────────
# 实测（同一个裸片段 `<string>a</string>\n<string>b</string>`）：
#     plutil -lint frag.plist  → "OK" (exit 0)      ← 无 XML 声明、无 DOCTYPE、无 <plist> 根
#     python3 plistlib.load    → InvalidFileException (exit 1)
# 也就是说旧实现「优先 plutil，无 plutil 才退 plistlib」让**生产平台（macOS）上这道
# fail-closed 门放行了根本不是 plist 的内容**，而无 plutil 的主机（CI ubuntu）反而更严
# —— 门的强度随主机而变，且声称的强度 > 实际强度。
# 现在：plistlib 是唯一判定权（两个平台同强度），plutil 存在时**叠加**跑一遍作补充
# （它能报出 plistlib 不查的一些结构问题）。缺 python3 → fail-closed，不退化到弱实现。
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

    python_bin="$(akb2_python_bin)" || return 1
    if ! "$python_bin" -c \
        'import plistlib,sys; plistlib.load(open(sys.argv[1],"rb"))' "$rendered" >/dev/null 2>&1; then
        printf 'ERROR: plistlib 解析失败——渲染后的 plist XML 不合法：%s\n' "$rendered" >&2
        return 1
    fi

    # 叠加校验（仅 macOS 有 plutil）：plistlib 已放行的内容再过一道，只会更严不会更松
    if command -v plutil >/dev/null 2>&1; then
        if ! plutil -lint "$rendered" >/dev/null 2>&1; then
            printf 'ERROR: plutil -lint 失败——渲染后的 plist XML 不合法：%s\n' "$rendered" >&2
            return 1
        fi
    fi
    return 0
}

# --------------------------------------------------------------------------
# launchctl 包装：stderr 与退出码全部留痕（不再被 2>/dev/null + set -e 吞掉）
# --------------------------------------------------------------------------

# bootout 旧注册。"未加载"不是错误（幂等安装的正常分支），但 stderr 必须打出来，
# 不能像旧版 `2>/dev/null || true` 那样把真实失败一起吞了。返回值恒为 0。
akb2_launchctl_bootout() {
    local domain_label="$1" code=0
    akb2_run_bounded "$AKB2_LAUNCHCTL_TIMEOUT_SEC" launchctl bootout "$domain_label" || code=$?
    if [ "$code" -eq 0 ]; then
        printf '  bootout 成功：%s\n' "$domain_label"
    elif [ "$code" -eq "$AKB2_BOUNDED_TIMEOUT_RC" ]; then
        # 超时 = 状态未知。不当成"本来没加载"：后面的 teardown 探测会因为同样悬挂
        # 而失败 → 走回滚路径（宪法 P3 fail-closed）。
        printf 'ERROR: launchctl bootout 超时（>%ss）launchctl_timeout=1：%s\n' \
            "$AKB2_LAUNCHCTL_TIMEOUT_SEC" "$domain_label" >&2
    else
        printf '  bootout 未生效（exit=%s，通常是服务本来没加载）：%s\n' \
            "$code" "${AKB2_BOUNDED_OUTPUT:-<no stderr>}"
    fi
    return 0
}

# bootout 后等待 launchd 真正 teardown，避免 `Bootstrap failed: 37: Operation
# already in progress` 竞态。范式来自 deploy-mac-mini.sh:109-123。
# 返回 0 = 已 teardown；返回 1 = 超时（调用方 fail-closed / 回滚）。
akb2_wait_for_launchctl_teardown() {
    local domain_label="$1" last_err="" attempt=1 probe_rc=0 timed_out=0
    while [ "$attempt" -le "$AKB2_TEARDOWN_MAX_ATTEMPTS" ]; do
        probe_rc=0
        akb2_run_bounded "$AKB2_LAUNCHCTL_TIMEOUT_SEC" launchctl print "$domain_label" \
            || probe_rc=$?
        last_err="$AKB2_BOUNDED_OUTPUT"
        if [ "$probe_rc" -eq "$AKB2_BOUNDED_TIMEOUT_RC" ]; then
            # 探针悬挂：不能把"问不出来"当成"已 teardown"（那正是要防的竞态）
            timed_out=1
            printf 'ERROR: launchctl print 探针超时（>%ss）launchctl_timeout=1：%s（第 %s 次探测）\n' \
                "$AKB2_LAUNCHCTL_TIMEOUT_SEC" "$domain_label" "$attempt" >&2
            break
        fi
        # 只锚定 `Could not find service`：旧判据额外收了裸 `not found`，
        # 而 `launchctl: command not found` / `Could not find domain` 等无关 stderr
        # 都含它 → 把"问不出来"误判成"已 teardown"后直接进 bootstrap。
        if printf '%s\n' "$last_err" | grep -qi 'Could not find service'; then
            printf '  旧服务已 teardown：%s（第 %s 次探测）\n' "$domain_label" "$attempt"
            return 0
        fi
        sleep "$AKB2_TEARDOWN_SLEEP_SEC"
        attempt=$((attempt + 1))
    done
    if [ "$timed_out" -eq 1 ]; then
        printf 'ERROR: bootout 后 teardown 探针 wall-clock 超时 → 按失败处理（进回滚/reconcile）：%s\n' \
            "$domain_label" >&2
        return 1
    fi
    printf 'ERROR: bootout 后等待 %s teardown 超时（%s 次 × %ss）；最后 launchctl print stderr：%s\n' \
        "$domain_label" "$AKB2_TEARDOWN_MAX_ATTEMPTS" "$AKB2_TEARDOWN_SLEEP_SEC" \
        "${last_err:-<empty>}" >&2
    return 1
}

# 有界 launchctl print：给验证段用。输出落 AKB2_BOUNDED_OUTPUT。
# 返回 0 = 拿到输出；1 = 失败；AKB2_BOUNDED_TIMEOUT_RC = 超时。
akb2_launchctl_print_bounded() {
    local domain_label="$1" code=0
    akb2_run_bounded "$AKB2_LAUNCHCTL_TIMEOUT_SEC" launchctl print "$domain_label" || code=$?
    if [ "$code" -eq "$AKB2_BOUNDED_TIMEOUT_RC" ]; then
        printf 'ERROR: launchctl print 超时（>%ss）launchctl_timeout=1：%s\n' \
            "$AKB2_LAUNCHCTL_TIMEOUT_SEC" "$domain_label" >&2
    fi
    return "$code"
}

# bootstrap 一个 plist。失败时把 launchctl 的 exit code 与 stderr 全部打出来
# （2026-07-28 之所以查不到死因，就是因为这两样当时都没留痕）。
# 返回 0 = 成功；返回 1 = 失败。
akb2_launchctl_bootstrap() {
    local domain="$1" plist="$2" code=0
    akb2_run_bounded "$AKB2_LAUNCHCTL_TIMEOUT_SEC" launchctl bootstrap "$domain" "$plist" \
        || code=$?
    if [ "$code" -eq 0 ]; then
        printf '  bootstrap 成功：%s ← %s\n' "$domain" "$plist"
        return 0
    fi
    if [ "$code" -eq "$AKB2_BOUNDED_TIMEOUT_RC" ]; then
        printf 'ERROR: launchctl bootstrap 超时（>%ss）launchctl_timeout=1（domain=%s，plist=%s）\n' \
            "$AKB2_LAUNCHCTL_TIMEOUT_SEC" "$domain" "$plist" >&2
        return 1
    fi
    printf 'ERROR: launchctl bootstrap 失败（exit=%s，domain=%s，plist=%s）：%s\n' \
        "$code" "$domain" "$plist" "${AKB2_BOUNDED_OUTPUT:-<no stderr>}" >&2
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

# --------------------------------------------------------------------------
# 安装事务回滚（两个 bootstrap 脚本共用，避免两份会各自漂移的实现）
# --------------------------------------------------------------------------

# akb2_rollback_plist <domain> <label> <dst> <backup_var_name>
# 恢复执行前状态：bootout 当前注册 → 恢复备份 plist（无备份则移除新 plist）→ 重新
# bootstrap。成功恢复备份后把调用方的备份变量清空（避免 EXIT trap 再把它删掉）。
#
# 关键差异（R1 审查 F19 / 加固清单第 1 类第 ⑤ 支「补偿失败必须显式留痕」）：
# 旧实现是裸 `mv -f "$BACKUP_PLIST" "$PLIST_DST"`。在 `set -e` 下 mv 失败（目标目录
# 不可写 / 磁盘满 / SIP）会**整函数中断**：下面的「手动补装」提示一行都不打印，
# 随后 EXIT trap 因为备份变量仍非空而把**唯一的旧 plist 副本 rm 掉**。
# 留下的态是「新 plist 在册位 + 无 job 在册 + 旧 plist 副本已被删」——
# 恰好落在 lane 4 要修的那个事故（com.akb2.briefing 被卸 5 天 22 小时）的补偿路径上。
# 现在：mv 失败 → 捕获、打完整手动恢复指引（含备份真实路径）、**保住备份**（把调用方
# 的备份变量置空表示"别删"由本函数控制；失败路径下反而要求调用方保留）。
akb2_rollback_plist() {
    local domain="$1" label="$2" dst="$3" backup_var="$4"
    local backup="${!backup_var:-}" domain_label="$domain/$label"

    akb2_launchctl_bootout "$domain_label"
    akb2_wait_for_launchctl_teardown "$domain_label" || true

    if [ -n "$backup" ] && [ -f "$backup" ]; then
        if ! mv -f "$backup" "$dst"; then
            printf 'ERROR: 回滚补偿失败——无法把备份移回安装位（mv 失败）\n' >&2
            printf '       备份仍在（**已保住，不会被清理 trap 删掉**）：%s\n' "$backup" >&2
            printf '       目标安装位：%s\n' "$dst" >&2
            printf '       手动恢复：cp -p %s %s && launchctl bootstrap %s %s\n' \
                "$backup" "$dst" "$domain" "$dst" >&2
            printf '       当前态：新 plist 可能仍在安装位，且无 job 在册 —— 需人工确认。\n' >&2
            # 置 1 = 让调用方的 EXIT trap 跳过删除（备份是唯一的旧 good 副本）
            AKB2_ROLLBACK_PRESERVE_BACKUP=1
            return 1
        fi
        printf -v "$backup_var" '%s' ''
        if akb2_launchctl_bootstrap "$domain" "$dst"; then
            printf '↩️  已恢复旧 plist 并重新 bootstrap 成功：%s\n' "$dst" >&2
        else
            printf '↩️  已恢复旧 plist，但重新 bootstrap 失败——服务当前无 job 在册！\n' >&2
            printf '    手动补装：launchctl bootstrap %s %s\n' "$domain" "$dst" >&2
        fi
    else
        rm -f "$dst"
        printf '↩️  此前无旧 plist，已移除新 plist（不留半安装态）：%s\n' "$dst" >&2
        printf '    服务当前无 job 在册（与执行前一致）。\n' >&2
    fi
    return 0
}
