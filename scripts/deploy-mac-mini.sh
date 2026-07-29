#!/usr/bin/env bash
# deploy-mac-mini.sh — AKB2 主链路(loop-1 入库 quality runner)Mac Mini 部署 + 验收
#
# 只部署 com.akb2.main-pipeline —— 24h 常驻 KeepAlive daemon(知识入库主链路)。
# 不含社媒日报 / github 日报 / 飞书同步(那是另外的产品链路,本脚本不碰)。
#
# 设计约束(对齐 docs/constitution.md):
#   - fail-closed:前置 + 加载后验收任一失败立即非 0 退出(原则 3)
#   - 失败可见:crash-loop / exec 后退出 / 缺 key / 未启动一律 fail(原则 4)
#   - secret 值绝不打印(只报 SET/MISSING)
#   - 事务化安装:render→lint→backup→原子 replace→bootstrap→验收→commit/rollback;
#     任一环节失败恢复上次 good plist(首装则移除新 plist),不留半安装态
#   - 验收以 launchd `launchctl print` 的 state/PID 为权威(日志只是辅助),
#     因为 wrapper 在 `exec python` 之前就打 runner starting/grader_key,日志出现 ≠ 进程存活
#   - preflight 用的 Python / env 文件 == daemon 实际用的同一个(消除 false-pass)
#   - sudo 项(防睡眠/断电自启/FileVault)只打印命令,不替用户执行
#
# 用法:
#   bash scripts/deploy-mac-mini.sh --dry-run     # 只跑前置检查 + 打印计划,不安装
#   bash scripts/deploy-mac-mini.sh               # 部署 + 加载 + fail-closed 验收(失败回滚)
#   bash scripts/deploy-mac-mini.sh --skip-verify # 跳过加载后验收
#
# 环境变量覆盖:
#   VAULT_ROOT   默认 /Volumes/AKB2/Obsidian(Obsidian vault 根,父目录含 "Praise Space/AKB2.0")
#
# 注:LLM key env 文件固定为 daemon 实际加载的 ~/.config/akb2/github-briefing.env;
#     Python 固定走 scripts/find-python311.sh(与 akb2-main-pipeline.sh 一致),不接受覆盖。

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────
# 0) 路径 & 参数
# ──────────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VAULT_ROOT="${VAULT_ROOT:-/Volumes/AKB2/Obsidian}"
ENV_FILE="$HOME/.config/akb2/github-briefing.env"   # 与 wrapper 默认一致(必改1)
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
LABEL="com.akb2.main-pipeline"
RUNNER_LOG="$REPO_ROOT/logs/akb2-quality-runner.log"
UID_N="$(id -u)"
# 机器本地状态根(锁/账本/游标):REPO_ROOT 在 ~/AI-Agent(Syncthing folder)内,
# 锁目录不能建在仓库里,否则两台机器互看/互删对方的锁。解析顺序与
# scripts/akb2-main-pipeline.sh、src/akb2/common/state_paths.get_state_root() 一致。
# shellcheck source=scripts/lib/state-root.sh
. "$(cd "$(dirname "$0")" && pwd)/lib/state-root.sh"
akb2_resolve_state_root

DRY_RUN=0
SKIP_VERIFY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)      DRY_RUN=1; shift ;;
    --skip-verify)  SKIP_VERIFY=1; shift ;;
    --vault-root)   VAULT_ROOT="${2:-}"; shift 2 ;;
    -h|--help)      grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数:$1(用 -h 看用法)" >&2; exit 2 ;;
  esac
done

AKB2_DIR="$VAULT_ROOT/Praise Space/AKB2.0"

# ──────────────────────────────────────────────────────────────────────────
# 输出助手 + 失败累计
# ──────────────────────────────────────────────────────────────────────────
FAIL_COUNT=0
WARN_COUNT=0
info() { printf '   %s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail() { printf '❌ %s\n' "$*" >&2; FAIL_COUNT=$((FAIL_COUNT+1)); }
hdr()  { printf '\n=== %s ===\n' "$*"; }

abort_if_failed() {
  if [[ "$FAIL_COUNT" -gt 0 ]]; then
    printf '\n❌ 有 %s 项失败,fail-closed 中止。修复后重跑。\n' "$FAIL_COUNT" >&2
    exit 1
  fi
}

# sed 替换值转义(& | \ 都可能破坏 sed 表达式)
sed_escape() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }

# 文件权限位(八进制,如 600)。BSD(macOS)与 GNU(Linux/CI)stat 参数不同,且不能靠
# `stat -f … || stat -c …` 短路:GNU 的 `-f` 是"文件系统状态",遇到未知格式符 %Lp 只打
# '?' 却仍 exit 0 → 永远走不到 GNU 分支,拿垃圾值把 chmod 600 的文件误判成宽权限
# (Linux CI 上实证)。按平台分派(与 scripts/akb2-watchdog.sh:file_age_sec 同一写法),
# 并校验输出确实是八进制;探测不出就回报 '?' 让调用方 fail-closed,不静默当成 600。
file_mode_octal() {
  local _f="$1" _mode
  if [[ "$(uname -s)" == "Darwin" ]]; then
    _mode="$(stat -f '%Lp' "$_f" 2>/dev/null || true)"
  else
    _mode="$(stat -c '%a' "$_f" 2>/dev/null || true)"
  fi
  if [[ ! "$_mode" =~ ^[0-7]{3,4}$ ]]; then
    printf '?'
    return 0
  fi
  # 4 位形式去前导 0(0600 → 600);setuid/setgid 等 4xxx 原样返回 → 与 600 不等 → 判失败
  if [[ ${#_mode} -eq 4 && "${_mode:0:1}" == "0" ]]; then
    _mode="${_mode#0}"
  fi
  printf '%s' "$_mode"
}

launchctl_domain_label() { printf 'gui/%s/%s' "$UID_N" "$LABEL"; }

wait_for_launchctl_teardown() {
  _domain_label="$(launchctl_domain_label)"
  _last_err=""
  for _attempt in 1 2 3 4 5 6 7 8 9 10; do
    _last_err="$(launchctl print "$_domain_label" 2>&1 >/dev/null || true)"
    if printf '%s\n' "$_last_err" | grep -qiE 'Could not find service|not found'; then
      ok "旧服务已 teardown:${_domain_label}"
      return 0
    fi
    sleep 1
  done
  fail "bootout 后等待 ${_domain_label} teardown 超时;最后 launchctl print stderr:${_last_err:-<empty>}"
  return 1
}

bootstrap_launchagent() {
  if _bootstrap_err="$(launchctl bootstrap "gui/$UID_N" "$DST" 2>&1 >/dev/null)"; then
    return 0
  fi
  fail "$LABEL bootstrap 失败:${_bootstrap_err:-<no stderr>}"
  return 1
}

# ──────────────────────────────────────────────────────────────────────────
# 1) 前置检查
# ──────────────────────────────────────────────────────────────────────────
hdr "前置检查"

# B1 HOME
info "B1 HOME = $HOME"
if [[ "$HOME" == "/Users/praise" ]]; then
  ok "B1 HOME 符合预期"
else
  warn "B1 HOME 非 /Users/praise:plist 用占位符已规避硬编码(PATH/pyenv 指向 $HOME),确认无误"
fi

# B2 Python 3.11+(固定走 find-python311.sh,与 daemon 同源)
if [[ -x "$SCRIPT_DIR/find-python311.sh" ]]; then
  PY="$(bash "$SCRIPT_DIR/find-python311.sh" 2>/dev/null || true)"
else
  PY="$(command -v python3 || true)"
fi
if [[ -z "$PY" ]] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) else 1)' 2>/dev/null; then
  fail "B2 find-python311.sh 未找到 Python 3.11+(探测值:'${PY:-none}');装 pyenv 3.11+"
else
  ok "B2 Python 3.11+:$("$PY" --version 2>&1) ($PY) —— 与 daemon 同源"
fi

# B3 git:main + 干净 + ff-pull
if [[ "$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)" != "main" ]]; then
  fail "B3 当前不在 main 分支(部署应从 main)"
elif [[ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]]; then
  fail "B3 工作区有未提交改动,先提交/还原"
elif [[ "$DRY_RUN" -eq 1 ]]; then
  ok "B3 在 main 且工作区干净(dry-run 跳过 pull)"
elif git -C "$REPO_ROOT" pull origin main --ff-only >/dev/null 2>&1; then
  ok "B3 已 ff-only 同步 main → $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
else
  fail "B3 git pull --ff-only 失败(本地 main 与 origin 分叉?)"
fi

# B4 时区(免 sudo)
TZ_NOW="$(readlink /etc/localtime 2>/dev/null | sed 's#.*/zoneinfo/##' || true)"
if [[ "$TZ_NOW" == "Asia/Shanghai" ]]; then
  ok "B4 时区 = Asia/Shanghai"
else
  warn "B4 时区 = '${TZ_NOW:-未知}'(非 Asia/Shanghai,避让窗口/时间相关逻辑会偏)"
fi

# B5/B6 AKB2 盘挂载 + vault 可写 + AKB2.0/raw 存在
if [[ ! -d "$VAULT_ROOT" ]]; then
  fail "B5 VAULT_ROOT 不存在:$VAULT_ROOT(AKB2 磁盘未挂载?)"
elif [[ ! -w "$VAULT_ROOT" ]]; then
  fail "B6 VAULT_ROOT 不可写:$VAULT_ROOT"
elif [[ ! -d "$AKB2_DIR" ]]; then
  fail "B6 找不到 AKB2.0 目录:$AKB2_DIR(Syncthing 未同步 / 路径不符?)"
elif [[ ! -d "$AKB2_DIR/raw" ]]; then
  fail "B6 vault 下缺 raw/ 队列目录:$AKB2_DIR/raw(runner 取不到队列会静默空跑)"
else
  ok "B5/B6 vault 挂载且可写,raw/ 在:$AKB2_DIR"
fi

# B7 LLM key env 文件:存在 + chmod 600 + 含可用 key(不打印值)
if [[ ! -f "$ENV_FILE" ]]; then
  fail "B7 LLM env 文件缺失:$ENV_FILE(daemon wrapper 从此加载 grader/maker key)"
else
  PERM="$(file_mode_octal "$ENV_FILE")"
  if [[ "$PERM" == "600" ]]; then
    ok "B7 env 文件存在且 chmod 600"
  else
    fail "B7 env 文件权限 = $PERM,必须 chmod 600(宽权限 = 本机密钥泄露风险)"
  fi
  # env 文件路径用参数传递,不内插进 bash -c(避免路径含引号时注入/语法错);只报 SET/MISSING
  # shellcheck disable=SC1090
  if env -i HOME="$HOME" PATH="/usr/bin:/bin" bash -c \
      'source "$1" 2>/dev/null; [[ -n "${AI_GATEWAY_API_KEY:-}${OPENROUTER_API_KEY:-}${TAPTAP_API_KEY:-}${DEEPSEEK_API_KEY:-}" ]]' \
      _ "$ENV_FILE"; then
    ok "B7 LLM key SET(grader/maker 可用)"
  else
    fail "B7 env 文件无任何 LLM key(AI_GATEWAY/OPENROUTER/TAPTAP/DEEPSEEK 全空)→ grader 会降级 fallback"
  fi
fi

# B-dep 依赖(硬门=runner 真实运行时依赖;scrapling/intake 依赖降为软 warn)
if [[ "$FAIL_COUNT" -eq 0 && -n "${PY:-}" ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "B-dep dry-run:跳过 pip install -e ."
  elif "$PY" -m pip install -e "$REPO_ROOT" >/dev/null 2>&1; then
    ok "B-dep pip install -e . 成功"
  else
    fail "B-dep pip install -e . 失败"
  fi
  if "$PY" -c "import sys; sys.path.insert(0,'$REPO_ROOT/src'); import akb2.quality.runner, openai, jsonschema, yaml" 2>/dev/null; then
    ok "B-dep runner 硬依赖可 import(akb2.quality.runner + openai + jsonschema + yaml)"
  else
    fail "B-dep runner 硬依赖 import 失败(akb2.quality.runner / openai / jsonschema / pyyaml 之一缺失)"
  fi
  if "$PY" -c "import scrapling" 2>/dev/null; then
    info "B-dep(软)scrapling 在(intake 抓取用)"
  else
    warn "B-dep(软)scrapling 缺失:仅影响 intake 抓取,runner 处理既有 raw 队列不受影响"
  fi
  if command -v defuddle >/dev/null 2>&1 && defuddle parse --help >/dev/null 2>&1; then
    ok "B-dep defuddle CLI 可用:$(command -v defuddle)"
  else
    fail "B-dep defuddle CLI 缺失或不可执行:请安装 defuddle(推荐 npm install -g defuddle；当前兼容 defuddle-cli@0.7.0)"
  fi
  if "$PY" -c "import playwright" 2>/dev/null; then
    info "B-dep(软)playwright 在(StealthyFetcher;还需 $PY -m playwright install chromium)"
  else
    warn "B-dep(软)playwright 缺失:仅影响 StealthyFetcher 抓取"
  fi
fi

# B9 logs + 机器本地锁目录(不在仓库内,见 STATE_ROOT 说明)
if akb2_state_root_is_synced; then
  fail "B9 AKB2_STATE_ROOT=$STATE_ROOT 在跨机同步范围内(状态会被 Syncthing 分叉丢失)"
fi
if [[ "$DRY_RUN" -eq 0 ]]; then
  mkdir -p "$REPO_ROOT/logs" "$STATE_ROOT/locks" && ok "B9 logs/ + $STATE_ROOT/locks/ 就绪"
else
  info "B9 dry-run:将创建 logs/ + $STATE_ROOT/locks/"
fi

abort_if_failed

# ──────────────────────────────────────────────────────────────────────────
# 2) 事务化安装(render→lint→backup→replace→bootstrap→验收→commit/rollback)
# ──────────────────────────────────────────────────────────────────────────
SRC="$REPO_ROOT/launchd/$LABEL.plist"
DST="$LAUNCH_AGENTS/$LABEL.plist"
[[ -f "$SRC" ]] || { fail "plist 源缺失:$SRC"; abort_if_failed; }

if [[ "$DRY_RUN" -eq 1 ]]; then
  hdr "部署计划(dry-run)"
  info "将安装:$LABEL(KeepAlive 常驻 daemon)"
  info "  home  → $HOME"
  info "  repo  → $REPO_ROOT"
  info "  vault → $AKB2_DIR (AKB2_VAULT_ROOT)"
  info "  log   → $RUNNER_LOG"
  printf '\n✅ dry-run 完成:前置全过,可去掉 --dry-run 正式部署。\n'
  exit 0
fi

hdr "事务化安装"
mkdir -p "$LAUNCH_AGENTS"
TMP_PLIST="$(mktemp "$LAUNCH_AGENTS/.${LABEL}.XXXXXX.plist")"
BACKUP=""
HAD_OLD=0
# 任何退出都清理 temp + backup 残留
trap 'rm -f "${TMP_PLIST:-}" "${BACKUP:-}" 2>/dev/null || true' EXIT

# render + 转义
sed -e "s|__AKB2_REPO_ROOT__|$(sed_escape "$REPO_ROOT")|g" \
    -e "s|__AKB2_VAULT_ROOT__|$(sed_escape "$VAULT_ROOT")|g" \
    -e "s|__AKB2_HOME__|$(sed_escape "$HOME")|g" \
    "$SRC" > "$TMP_PLIST"
# lint(此刻旧 DST 完全未动)
if grep -q "__AKB2_" "$TMP_PLIST"; then
  fail "生成的 plist 仍有未替换占位符,放弃(旧 plist 未动)"; abort_if_failed
fi
if ! plutil -lint "$TMP_PLIST" >/dev/null 2>&1; then
  fail "生成的 plist XML 不合法,放弃(旧 plist 未动)"; abort_if_failed
fi
ok "render + lint 通过"

# 基线日志行数(必改3:在动服务之前记录,避免 RunAtLoad 拉起后误判)
PRE_LINES="$(wc -l < "$RUNNER_LOG" 2>/dev/null | tr -d ' ' || true)"
[[ "$PRE_LINES" =~ ^[0-9]+$ ]] || PRE_LINES=0

# backup 旧 plist(必改1)
if [[ -f "$DST" ]]; then
  HAD_OLD=1
  BACKUP="$(mktemp "$LAUNCH_AGENTS/.${LABEL}.bak.XXXXXX.plist")"
  cp -p "$DST" "$BACKUP"
  info "已备份旧 plist → $BACKUP"
fi

# 回滚:恢复上次 good(或首装则移除新 plist)
rollback() {
  launchctl bootout "$(launchctl_domain_label)" 2>/dev/null || true
  wait_for_launchctl_teardown || true
  if [[ "$HAD_OLD" -eq 1 && -n "$BACKUP" && -f "$BACKUP" ]]; then
    mv -f "$BACKUP" "$DST"; BACKUP=""
    # 诚实报告旧服务是否真的重新加载成功(RunAtLoad=true,bootstrap 即拉起)
    if bootstrap_launchagent; then
      printf '↩️  已恢复旧 plist 并重新加载成功\n' >&2
    else
      printf '↩️  已恢复旧 plist,但 launchctl 重新加载失败,需手动 bootstrap 旧服务\n' >&2
    fi
  else
    rm -f "$DST"
    printf '↩️  已移除新 plist(此前无旧 plist,未留半安装态)\n' >&2
  fi
}

# 原子 replace
mv -f "$TMP_PLIST" "$DST"; TMP_PLIST=""
ok "plist 原子写入:$DST"

# bootout 旧 + 等待 teardown + bootstrap 新
if launchctl print "$(launchctl_domain_label)" >/dev/null 2>&1; then
  if launchctl bootout "$(launchctl_domain_label)"; then
    if ! wait_for_launchctl_teardown; then
      rollback
      abort_if_failed
    fi
  else
    fail "$LABEL bootout 失败"
    rollback
    abort_if_failed
  fi
else
  info "旧服务未加载,跳过 bootout"
fi
if ! bootstrap_launchagent; then
  fail "$LABEL bootstrap 失败 → 回滚"
  rollback
  abort_if_failed
fi
ok "bootstrap 成功(RunAtLoad=true + KeepAlive 已拉起,无需 kickstart)"
# 不再 kickstart:RunAtLoad=true 时 bootstrap 已启动一次;额外 kickstart -k 会再产生一次
# "runner starting",与下方 crash-loop 检测(START_COUNT>1)冲突 → 健康部署被误判回滚。
# 健康部署应恰好 1 次启动;只有反复退出重启才是 crash-loop。

# ──────────────────────────────────────────────────────────────────────────
# 3) 验收(fail-closed;launchd state/PID 为权威 + 必需日志信号 + crash 检测)
# ──────────────────────────────────────────────────────────────────────────
if [[ "$SKIP_VERIFY" -eq 0 ]]; then
  hdr "验收(fail-closed)"
  # bounded wait ~24s:等三个必需信号都出现再 break(消除"看到 runner starting 就 break、
  # 但 grader_key/vault gate 尚未写入"的竞态);超时则进入下方逐项 fail 判定。
  for _ in 1 2 3 4 5 6; do
    sleep 4
    if [[ -f "$RUNNER_LOG" ]]; then
      _SEG="$(tail -n "+$((PRE_LINES+1))" "$RUNNER_LOG" 2>/dev/null || true)"
      if printf '%s\n' "$_SEG" | grep -q "runner starting" \
         && printf '%s\n' "$_SEG" | grep -q "grader_key=present" \
         && printf '%s\n' "$_SEG" | grep -q "vault gate: ok"; then
        break
      fi
    fi
  done
  NEWLOG="$(tail -n "+$((PRE_LINES+1))" "$RUNNER_LOG" 2>/dev/null || true)"
  START_COUNT="$(printf '%s\n' "$NEWLOG" | grep -c "runner starting" || true)"

  # 权威健康:重新取 launchctl print(此时 exec 后若已崩溃会现形)
  LP="$(launchctl print "gui/$UID_N/$LABEL" 2>/dev/null || true)"
  STATE="$(printf '%s\n' "$LP" | grep -iE 'state =' | head -1 | sed 's/.*= *//' | tr -d ' ' || true)"
  PID_V="$(printf '%s\n' "$LP" | grep -iE 'pid =' | head -1 | sed 's/.*= *//' | tr -d ' ' || true)"
  LASTEXIT="$(printf '%s\n' "$LP" | grep -iE 'last exit (code|status)' | head -1 | sed 's/.*= *//' | tr -d ' ' || true)"
  info "launchd: state=${STATE:-?} pid=${PID_V:-none} last_exit=${LASTEXIT:-?} | 本次 runner starting x${START_COUNT}"

  # 逐项 fail-closed 判定(全用 if/else,避免 set -e 与 &&|| 误判)
  if [[ "$START_COUNT" -ge 1 ]]; then :; else fail "验收:未见本次新 runner starting(未拉起/早退)"; fi
  if [[ "$START_COUNT" -le 1 ]]; then :; else fail "验收:runner starting x$START_COUNT = crash-loop(反复重启)"; fi
  if printf '%s\n' "$NEWLOG" | grep -q "vault gate: ok"; then :; else fail "验收:未见 'vault gate: ok'(vault 门未过/wrapper 异常)"; fi
  if printf '%s\n' "$NEWLOG" | grep -q "grader_key=present"; then :; else fail "验收:grader_key=missing(LLM key 未加载)"; fi
  if printf '%s\n' "$NEWLOG" | grep -qiE "Traceback|FATAL|\] ERROR"; then fail "验收:新日志出现 Traceback/FATAL/ERROR(查 $RUNNER_LOG)"; fi
  if [[ "$STATE" == "running" && -n "$PID_V" && "$PID_V" != "0" ]]; then
    ok "验收:launchd state=running pid=$PID_V(runner exec 后仍存活)"
  else
    fail "验收:launchd state 非 running / 无 PID(runner exec 后退出/throttle 回退;state=${STATE:-?})"
  fi

  if [[ "$FAIL_COUNT" -gt 0 ]]; then
    warn "验收失败 → 回滚到上次 good plist"
    rollback
    abort_if_failed
  fi
fi

# 成功:清理 backup(trap 兜底)
if [[ -n "${BACKUP:-}" ]]; then rm -f "$BACKUP"; BACKUP=""; fi

# ──────────────────────────────────────────────────────────────────────────
# 4) 7x24 加固清单(检查 + 打印,sudo 项不替你执行)
# ──────────────────────────────────────────────────────────────────────────
hdr "7x24 加固(需你手动执行,多数要 sudo)"
SLEEP_NOW="$(pmset -g 2>/dev/null | awk '/ sleep/{print $2; exit}' || echo '?')"
FV_STATUS="$(fdesetup status 2>/dev/null | head -1 || echo '未知')"
info "当前睡眠设置:sleep=$SLEEP_NOW(非 0 = 会睡,daemon 被挂起)"
info "当前 FileVault:$FV_STATUS"
cat <<EOF
   1) 防睡眠(关键,否则 mac mini 一睡 daemon 停):
      sudo pmset -a sleep 0 disablesleep 1 displaysleep 0
   2) 断电后自动开机:
      sudo pmset -a autorestart 1
      sudo systemsetup -setrestartpowerfailure on
   3) 自动登录(关键):LaunchAgents 属 gui/ 域,需 GUI 登录会话才加载;
      headless 重启若无人登录 → daemon 不会自动起。
      系统设置 → 用户与群组 → 自动登录 → 选 $(id -un) 用户。
   4) FileVault(关键):若上面显示 FileVault is On,断电重启会停在预启动解锁界面,
      自动登录不发生 → LaunchAgent 不起。7x24 headless 场景建议关闭 FileVault:
      sudo fdesetup disable    （或确保有人工解锁/MDM bootstrap token 方案）
   5) AKB2 外置卷:确认重启后能自动挂载到 $VAULT_ROOT(否则 wrapper 的 vault 门会 FATAL 重试)。
EOF

# ──────────────────────────────────────────────────────────────────────────
# 5) 汇总
# ──────────────────────────────────────────────────────────────────────────
hdr "汇总"
printf '链路:%s(KeepAlive 7x24 常驻)\n' "$LABEL"
printf '警告:%s 项 | 失败:%s 项\n' "$WARN_COUNT" "$FAIL_COUNT"
[[ "$FAIL_COUNT" -gt 0 ]] && { printf '❌ 有失败项,检查上方输出。\n' >&2; exit 1; }
echo
# --skip-verify 时不得谎称"验收通过":如实标注未验收 + 给手动核验命令
if [[ "$SKIP_VERIFY" -eq 1 ]]; then
  printf '⚠️ 主链路已部署 + 加载,但已跳过验收(--skip-verify)——未确认 runner 是否健康。\n'
  printf '   手动核验:launchctl print gui/%s/%s(看 state=running + pid)+ tail "%s"\n' "$UID_N" "$LABEL" "$RUNNER_LOG"
  printf '            (确认 runner starting / grader_key=present / vault gate: ok / 无 Traceback)\n'
else
  printf '✅ 主链路已部署 + 加载 + 验收通过(launchd state=running)。\n'
fi
cat <<EOF
注意:
   - 当前为 proposal 安全模式(AKB2_AUTO_PROMOTE=0):只写 proposal manifest,不自动升格 active 知识。
     确认产出质量后,要真入库再把 plist 的 AKB2_AUTO_PROMOTE 改 1 并重跑本脚本。
   - 完成上方「7x24 加固」5 步,daemon 才能真正跨睡眠/重启/FileVault 7x24 运行。
   - 实时日志:tail -f "$RUNNER_LOG"
EOF
