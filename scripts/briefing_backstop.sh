#!/usr/bin/env bash
# briefing_backstop.sh — 早 7:30 主跑失败后的补跑守卫。
#
# 由 com.akb2.briefing.backstop（8:00）触发。设计要点：
#   - 幂等：今日已发布到 Social → 直接跳过，绝不重复发。
#   - 仅补失败：只有当日 Social 文件不存在时才重跑 daily_briefing.sh。
#   - 不抢主跑：若主跑进程仍在运行，跳过本次（让主跑先跑完）。
#   - 静默告警（选项 C）：成功/跳过/失败都只写日志，不弹桌面通知。
#
# 日志：
#   logs/backstop.log              ← 主看：每次一行（时间 + 结果）
#   logs/backstop-{stdout,stderr}  ← 由 launchd 接管（重跑那条 pipeline 的原始输出）
#
# 退出码 124 的唯一两个来源（launchd 的 "last exit code 124" 只可能是这两个）：
#   1. 本脚本 deadline_missed 判定 → DEADLINE_EXIT_CODE
#   2. 重跑时 run_with_deadline.py 到点终止 pipeline → 同一个 124
# **不是** 任何 stage 超时：translate 的 AKB2_TRANSLATE_TIMEOUT(=300s) 是单次 HTTP
# 请求超时，不是 stage 预算，所以 translate 段耗时 442.9s 并不越界、也不产出 124。
#
# 已知结构性约束（需人工决策，勿由 agent 私自改 launchd 调度）：
#   com.akb2.briefing.backstop 的第二个 StartCalendarInterval 是 12:05，而默认
#   deadline 是 12:00 —— 该时段调用按设计只能巡检报告、永远无法真正补跑。当日简报
#   已 pass 时它现在正常 exit 0（skip）；未达 pass 时它照实报 FAILED + 124。
#
# 用法：briefing_backstop.sh --vault-root <path> [--dry-run] [--log-path <path>]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_ROOT/logs"
DEFAULT_LOG="$LOG_DIR/backstop.log"

VAULT_ROOT=""
DRY_RUN=0
LOG_PATH=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --vault-root) VAULT_ROOT="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    --log-path)   LOG_PATH="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done
if [[ -z "$VAULT_ROOT" ]]; then
  echo "ERROR: --vault-root is required" >&2
  exit 1
fi

# 日志解析优先级：--log-path > AKB2_BACKSTOP_LOG > 默认生产日志
# 测试/手工调用可经 AKB2_BACKSTOP_LOG 隔离到 tmp，绝不污染仓库 logs/backstop.log
LOG="${LOG_PATH:-${AKB2_BACKSTOP_LOG:-$DEFAULT_LOG}}"
mkdir -p "$(dirname "$LOG")"

# ── 具名预算/常量（宪法 P1 有界自动化、P4 预算显式）────────────────────────
# DEADLINE_EXIT_CODE：与 scripts/run_with_deadline.py 的 _DEADLINE_EXIT 对齐。
# launchd 看到的 124 只可能来自本脚本的 deadline 判定或 run_with_deadline 的
# deadline 终止，**不是** 任何 stage 的超时（translate 用的 AKB2_TRANSLATE_TIMEOUT
# 是单次 HTTP 请求超时，不构成 stage 预算，超时不会产出 124）。
readonly DEADLINE_EXIT_CODE=124
# 读盘重试：外置 APFS 卷（/Volumes/AKB2）偶发瞬时读失败，单次失败不该把健康的
# 一天翻成 FAILED；重试次数有界，每次尝试都落日志。
readonly VERDICT_READ_ATTEMPT_BUDGET=3
readonly VERDICT_READ_RETRY_SLEEP_SECONDS="${AKB2_BACKSTOP_READ_RETRY_SLEEP:-2}"

# run_id：单次调用唯一标识（时间+pid），同日志里区分多进程/多调用，消除
# "同一轮自相矛盾" 的伪装（多个 backstop 进程写同一日志时 run_id 不同 = 不同轮）。
# host：~/AI-Agent 是 Syncthing 共享目录，logs/backstop.log 会被两台机器同时追加。
# 不带 host 时两机日志混排，看起来像"同一天先 recovered 又 FAILED"的自相矛盾。
RUN_ID="$(TZ=Asia/Shanghai date +%Y%m%d%H%M%S)-$$"
SOURCE="${BACKSTOP_SOURCE:-schedule}"   # schedule / manual / test
HOST="$(hostname -s 2>/dev/null || echo unknown)"

DATE="$(TZ=Asia/Shanghai date +%Y-%m-%d)"
ts()  { TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S'; }
log() {
  echo "$(ts) [backstop] run_id=$RUN_ID host=$HOST pid=$$ source=$SOURCE vault=$VAULT_ROOT $*" \
    | tee -a "$LOG" >&2
}
# 终态行：单次 run 只允许一条 terminal=<state>，供监控按 run_id 判定唯一结局。
log_terminal() { local state="$1"; shift; log "$* terminal=$state"; }
deadline_missed() {
  local now_hm deadline_h deadline_m deadline_hm
  now_hm="$(TZ=Asia/Shanghai date +%H%M)"
  deadline_h="${AKB2_BRIEFING_DEADLINE_HOUR:-12}"
  deadline_m="${AKB2_BRIEFING_DEADLINE_MINUTE:-0}"
  printf -v deadline_hm '%02d%02d' "$deadline_h" "$deadline_m"
  (( 10#$now_hm >= 10#$deadline_hm ))
}

# 发布落点（与 pipeline.py: briefing_filename = "<date> Daily Briefing.md" 对齐）
SOCIAL_FILE="$VAULT_ROOT/Praise Space/AKB2.0/AI TRENDS/Social/$DATE Daily Briefing.md"

# ── frontmatter audit_verdict 读取（单次 awk，容忍真实世界的格式抖动）────────
# 旧实现用 `sed -n '/^---$/,/^---$/p'`，只认严格 `^---$`：UTF-8 BOM、CRLF 行尾、
# fence 尾随空格任一出现，整段 frontmatter 就取不到 → 已入库的 pass 简报被判
# "无法解析" 并触发重跑。这里改成单次 awk：去 BOM、去 CR、容忍 fence 前后空白、
# 只读第一段 frontmatter（段外的同名键不采信）、剥引号与行尾 YAML 注释。
#
# 退出码 0 = 读盘成功（verdict 可能为空串 = 字段确实不存在）
# 退出码 非0 = 读盘失败（stderr 保留 awk 的真实错误，交给调用方落日志）
read_frontmatter_verdict() {
  # LC_ALL=C 强制 awk 走字节模式：frontmatter 的键名与取值都是 ASCII，而 BOM 必须
  # 按字节 \357\273\277 匹配才删得掉——在多字节 locale 下 BOM 是单个字符，
  # 上面的字节转义匹不中（实测 macOS awk：仅 LC_ALL=C 下 BOM 才被剥掉）。
  LC_ALL=C awk '
    NR == 1 { sub(/^\357\273\277/, "") }
    { sub(/\r$/, "") }
    !seen_open {
      if ($0 ~ /^[[:space:]]*---[[:space:]]*$/) { seen_open = 1; next }
      if ($0 ~ /^[[:space:]]*$/) { next }
      exit                                    # 首个非空行不是 fence → 无 frontmatter
    }
    $0 ~ /^[[:space:]]*(---|\.\.\.)[[:space:]]*$/ { exit }   # frontmatter 段结束
    /^[[:space:]]*audit_verdict[[:space:]]*:/ {
      line = $0
      sub(/^[[:space:]]*audit_verdict[[:space:]]*:[[:space:]]*/, "", line)
      sub(/[[:space:]]+#.*$/, "", line)       # 去行尾 YAML 注释
      sub(/[[:space:]]+$/, "", line)
      gsub(/^["'"'"']|["'"'"']$/, "", line)   # 去成对引号
      sub(/[[:space:]]+$/, "", line)
      print line
      exit
    }
  ' "$1"
}

# ── 单一已发布状态判定（消除"先判 pass 跳过、又判未发布重跑"的前后不一致）────
# 直接写全局，**禁止**放进 `$( )` 调用：历史缺陷正是把本函数塞进命令替换里，
# 子 shell 的赋值丢失，导致日志里的 audit_verdict 永远打印成空串（那个 `('')`
# 是无条件输出，不是真实解析结果）。
#
# PUBLISHED_STATUS ∈ pass | warn_or_fail | unknown_verdict | no_verdict | unreadable | missing
#   no_verdict  = 读盘成功但 frontmatter 里没有 audit_verdict 字段（真·解析不出）
#   unreadable  = 读盘失败（I/O、权限、卷掉线），与解析失败严格区分（宪法 P4）
PUBLISHED_STATUS=""
PUBLISHED_VERDICT=""
PUBLISHED_READ_ERROR=""
VERDICT_ERR_FILE=""
trap '[[ -n "$VERDICT_ERR_FILE" ]] && rm -f "$VERDICT_ERR_FILE"' EXIT
resolve_published_state() {
  local f="$1" attempt=1 out rc err_file
  PUBLISHED_STATUS=""; PUBLISHED_VERDICT=""; PUBLISHED_READ_ERROR=""

  if [[ ! -f "$f" ]]; then
    PUBLISHED_STATUS="missing"
    return 0
  fi

  err_file="$(mktemp -t akb2-backstop-read)"
  VERDICT_ERR_FILE="$err_file"      # 供 EXIT trap 兜底清理（中途 set -e 退出时）
  while (( attempt <= VERDICT_READ_ATTEMPT_BUDGET )); do
    set +e
    out="$(read_frontmatter_verdict "$f" 2>"$err_file")"
    rc=$?
    set -e
    if (( rc == 0 )); then
      PUBLISHED_VERDICT="$out"
      case "$out" in
        pass)       PUBLISHED_STATUS="pass" ;;
        warn|fail)  PUBLISHED_STATUS="warn_or_fail" ;;
        "")         PUBLISHED_STATUS="no_verdict" ;;
        *)          PUBLISHED_STATUS="unknown_verdict" ;;
      esac
      rm -f "$err_file"
      return 0
    fi
    PUBLISHED_READ_ERROR="$(tr '\n' ' ' < "$err_file" | sed -E 's/[[:space:]]+$//')"
    log "warn — 读取 audit_verdict 失败 attempt=$attempt/$VERDICT_READ_ATTEMPT_BUDGET rc=$rc err=\"$PUBLISHED_READ_ERROR\" ($DATE)"
    attempt=$(( attempt + 1 ))
    if (( attempt <= VERDICT_READ_ATTEMPT_BUDGET )); then
      sleep "$VERDICT_READ_RETRY_SLEEP_SECONDS"
    fi
  done
  PUBLISHED_STATUS="unreadable"
  rm -f "$err_file"
  return 0
}

# should_rerun <status> → 0=rerun(需重跑), 1=skip(已发布且pass)
# fail-closed（宪法 P3）：除 pass 之外的一切状态都保守重跑，包括读盘失败。
should_rerun() {
  case "$1" in
    pass) return 1 ;;
    *)    return 0 ;;
  esac
}

resolve_published_state "$SOCIAL_FILE"
status="$PUBLISHED_STATUS"
# 文案约定：变量后紧跟中文标点时**必须**写成 ${VAR}。launchd 环境不带 LANG，
# bash 3.2 退化成单字节 locale，`$VAR，` 的 0xEF 会被并进变量名 → set -u 直接
# "unbound variable" 崩掉整个 backstop（此前 warn/fail 分支一直踩在这颗雷上）。
if should_rerun "$status"; then
  case "$status" in
    warn_or_fail)
      log "rerun — 今日已发布但 audit_verdict=${PUBLISHED_VERDICT}，触发质量重跑 status=$status ($DATE)" ;;
    unknown_verdict)
      log "rerun — 今日已发布但 audit_verdict=$PUBLISHED_VERDICT 非已知取值，保守重跑 status=$status ($DATE)" ;;
    no_verdict)
      log "rerun — 今日文件可读但 frontmatter 无 audit_verdict 字段，保守重跑 status=$status ($DATE)" ;;
    unreadable)
      log "rerun — 今日文件存在但读盘失败（已重试 $VERDICT_READ_ATTEMPT_BUDGET 次）err=\"$PUBLISHED_READ_ERROR\"，保守重跑 status=$status ($DATE)" ;;
    missing)
      log "rerun — 今日未发布，触发补跑 status=$status ($DATE)" ;;
  esac
else
  log_terminal skip "skip — 今日已发布且 audit_verdict=pass，无需补跑 ($DATE)"
  exit 0
fi

# deadline 文案：DEADLINE_LABEL 供日志自证"12:xx 这个界是谁定的"
DEADLINE_LABEL="$(printf '%02d:%02d' "${AKB2_BRIEFING_DEADLINE_HOUR:-12}" "${AKB2_BRIEFING_DEADLINE_MINUTE:-0}")"

# 守卫 ②：主跑进程仍在运行 → 让它先跑完，本次跳过
if pgrep -f "akb2.briefing.pipeline" >/dev/null 2>&1; then
  if deadline_missed; then
    log_terminal failed "FAILED — 已超过 $DEADLINE_LABEL deadline，主跑仍在进行，不能继续静默等待 status=$status reason=deadline_missed exit_code=$DEADLINE_EXIT_CODE ($DATE)"
    exit "$DEADLINE_EXIT_CODE"
  fi
  log_terminal skip "skip — 主跑仍在进行，本次不抢 status=$status ($DATE)"
  exit 0
fi

if deadline_missed; then
  # 注意：这里的 status 可能是 missing / warn_or_fail / no_verdict / unreadable，
  # 一律照实报，不得像旧实现那样在文件已存在时仍然谎报"今日未发布"。
  log_terminal failed "FAILED — 已超过 $DEADLINE_LABEL deadline，不再启动补跑 status=$status reason=deadline_missed exit_code=$DEADLINE_EXIT_CODE ($DATE)"
  exit "$DEADLINE_EXIT_CODE"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  log_terminal dry_run "dry-run — 当日简报未达 pass，将会触发补跑（实际未执行）status=$status ($DATE)"
  exit 0
fi

log "run — 当日简报未达 pass，开始补跑 attempt=1/1 status=$status ($DATE)"
set +e
bash "$SCRIPT_DIR/daily_briefing.sh" --date "$DATE" --vault-root "$VAULT_ROOT"
rc=$?
set -e

case "$rc" in
  0)  log_terminal recovered "recovered — 重跑成功已入库 (exit 0, $DATE)" ;;
  1)  log_terminal failed "FAILED — 重跑 exit 1 (gate reject)，当日无简报 exit_code=1 ($DATE)" ;;
  2)  log_terminal failed "FAILED — 重跑 exit 2 (audit fail)，当日无简报 exit_code=2 ($DATE)" ;;
  "$DEADLINE_EXIT_CODE")
      log_terminal failed "FAILED — 重跑被 $DEADLINE_LABEL deadline 终止 (run_with_deadline)，当日无简报 reason=deadline_missed exit_code=$DEADLINE_EXIT_CODE ($DATE)" ;;
  *)  log_terminal failed "FAILED — 重跑 exit $rc (异常)，当日无简报 exit_code=$rc ($DATE)" ;;
esac
exit "$rc"
