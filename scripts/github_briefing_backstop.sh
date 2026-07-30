#!/usr/bin/env bash
# github_briefing_backstop.sh — GitHub Briefing 补跑守卫（主跑 05:30 失败时兜底）。
#
# 由 com.akb2.github_briefing.backstop（07:30）触发。设计要点：
#   - 幂等：今日 GitHub Briefing 已写盘 → 直接跳过，绝不重复产出。
#   - 仅补失败：只有当日 briefing 文件不存在时才重跑 daily_github_briefing.sh。
#   - 不抢主跑：若 github pipeline 进程仍在运行，跳过（让主跑先跑完）。
#   - 静默：成功/跳过/失败都只写日志，不弹桌面通知。
#
# 日志：
#   logs/github-backstop.log              ← 主看：每次一行（时间 + 结果）
#   测试/手工调用经 --log-path / AKB2_GITHUB_BACKSTOP_LOG 隔离，绝不污染生产日志
#
# 用法：github_briefing_backstop.sh --vault-root <path> [--dry-run] [--log-path <path>]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_ROOT/logs"
DEFAULT_LOG="$LOG_DIR/github-backstop.log"

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

# 日志解析优先级：--log-path > AKB2_GITHUB_BACKSTOP_LOG > 默认生产日志
# 与 briefing_backstop.sh 对齐。缺这一口时测试直接写生产日志，把测试场景伪装成
# 线上重试轮次（生产日志里 408 条 `env file /dev/null 缺失` 全是测试留下的）。
LOG="${LOG_PATH:-${AKB2_GITHUB_BACKSTOP_LOG:-$DEFAULT_LOG}}"
mkdir -p "$(dirname "$LOG")"

# ── 具名常量（宪法 P4 预算/退出码显式）────────────────────────────────────
# 与 scripts/run_with_deadline.py 的 _DEADLINE_EXIT 对齐。
readonly DEADLINE_EXIT_CODE=124

# run_id/host：同一日志里区分多进程与多机器。~/AI-Agent 是 Syncthing 共享目录，
# logs/github-backstop.log 会被两台机器同时追加，不带归属字段就看不出
# "FAILED 与 recovered 交替" 其实是不同 run。
RUN_ID="$(TZ=Asia/Shanghai date +%Y%m%d%H%M%S)-$$"
SOURCE="${GITHUB_BACKSTOP_SOURCE:-schedule}"   # schedule / manual / test
HOST="$(hostname -s 2>/dev/null || echo unknown)"

ts()  { TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S'; }
log() {
  echo "$(ts) [github-backstop] run_id=$RUN_ID host=$HOST pid=$$ source=$SOURCE $*" \
    | tee -a "$LOG" >&2
}
# 终态行：单次 run 只允许一条 terminal=<state>。
log_terminal() { local state="$1"; shift; log "$* terminal=$state"; }
deadline_missed() {
  local now_hm deadline_h deadline_m deadline_hm
  now_hm="$(TZ=Asia/Shanghai date +%H%M)"
  deadline_h="${AKB2_GITHUB_DEADLINE_HOUR:-12}"
  deadline_m="${AKB2_GITHUB_DEADLINE_MINUTE:-0}"
  printf -v deadline_hm '%02d%02d' "$deadline_h" "$deadline_m"
  (( 10#$now_hm >= 10#$deadline_hm ))
}

# GitHub briefing 文件名对齐 pipeline.py：today = 抓取日（去掉旧的 +1 偏移）
DATE_TODAY="$(TZ=Asia/Shanghai date +%Y-%m-%d)"
GITHUB_FILE="$VAULT_ROOT/Praise Space/AKB2.0/AI TRENDS/GitHub/$DATE_TODAY GitHub Briefing.md"

# deadline 文案：自证"这个界是谁定的"
DEADLINE_LABEL="$(printf '%02d:%02d' "${AKB2_GITHUB_DEADLINE_HOUR:-12}" "${AKB2_GITHUB_DEADLINE_MINUTE:-0}")"

# 幂等守卫 ①：今日 briefing 已产出 → 跳过
if [[ -f "$GITHUB_FILE" ]]; then
  log_terminal skip "skip — GitHub briefing 已产出，无需补跑 ($DATE_TODAY)"
  exit 0
fi

# 守卫 ②：主跑进程仍在运行 → 让它先跑完
if pgrep -f "akb2.briefing.github.pipeline" >/dev/null 2>&1; then
  if deadline_missed; then
    log_terminal failed "FAILED — 已超过 $DEADLINE_LABEL deadline，github pipeline 仍在运行，不能继续静默等待 reason=deadline_missed exit_code=$DEADLINE_EXIT_CODE ($DATE_TODAY)"
    exit "$DEADLINE_EXIT_CODE"
  fi
  log_terminal skip "skip — github pipeline 仍在运行，本次不抢 ($DATE_TODAY)"
  exit 0
fi

if deadline_missed; then
  log_terminal failed "FAILED — 已超过 $DEADLINE_LABEL deadline 且 GitHub briefing 缺失，不再启动补跑 reason=deadline_missed exit_code=$DEADLINE_EXIT_CODE ($DATE_TODAY)"
  exit "$DEADLINE_EXIT_CODE"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  log_terminal dry_run "dry-run — 今日 GitHub briefing 缺失，将会触发补跑（实际未执行）($DATE_TODAY)"
  exit 0
fi

# SC-5: 补跑复用主管线环境 — source 同一 env（GITHUB_PAT/AI_GATEWAY_API_KEY/VAULT_ROOT），
# 与 launchd_wrapper.sh 一致；否则补跑无凭证 → enrich 全失败 → 假成功 exit 0。
#
# /dev/null 与空串是"显式不加载 env"的约定（测试用它跳过凭证注入）。旧实现把它
# 当成"配置缺失"记 warn，生产日志里堆了 408 条误导性告警 —— 真缺失才该 warn。
GITHUB_ENV_FILE="${AKB2_GITHUB_ENV_FILE:-$HOME/.config/akb2/github-briefing.env}"
if [[ -z "$GITHUB_ENV_FILE" || "$GITHUB_ENV_FILE" == "/dev/null" ]]; then
    log "info — env 加载按配置显式跳过 env_load=skipped_explicit path=${GITHUB_ENV_FILE:-<empty>} ($DATE_TODAY)"
elif [[ -f "$GITHUB_ENV_FILE" ]]; then
    set +e
    # shellcheck source=/dev/null disable=SC1090
    source "$GITHUB_ENV_FILE"
    set -e
    log "info — env 已加载 env_load=loaded path=$GITHUB_ENV_FILE ($DATE_TODAY)"
else
    log "warn — env file $GITHUB_ENV_FILE 缺失，补跑可能因无凭证失败 env_load=missing ($DATE_TODAY)"
fi

# 补跑入口（测试可经 AKB2_GITHUB_RERUN_SCRIPT 注入 stub）
RERUN_SCRIPT="${AKB2_GITHUB_RERUN_SCRIPT:-$SCRIPT_DIR/daily_github_briefing.sh}"
export GITHUB_FILE  # 供 rerun/stub 复查落点

# 本脚本单次调用只补跑一轮；attempt 显式标注轮次，避免与其他 run 的行混读成"多轮重试"。
log "run — 今日 GitHub briefing 缺失，开始补跑 attempt=1/1 ($DATE_TODAY)"
set +e
bash "$RERUN_SCRIPT" --vault-root "$VAULT_ROOT"
rc=$?
set -e

# SC-5: recovered 必须复查文件 — rc==0 但当日文件未写出 = 假成功 → 仍 fail-visible
if [[ "$rc" -eq 0 && -f "$GITHUB_FILE" ]]; then
    log_terminal recovered "recovered — 补跑成功且文件已落盘 (exit 0, $DATE_TODAY)"
    exit 0
fi
file_state="missing"
[[ -f "$GITHUB_FILE" ]] && file_state="present"
log_terminal failed "FAILED — 补跑未交付 attempt=1/1 (rc=$rc, file=$file_state, exit_code=$rc, $DATE_TODAY)"
if [[ "$rc" -ne 0 ]]; then
    exit "$rc"
fi
exit 1
