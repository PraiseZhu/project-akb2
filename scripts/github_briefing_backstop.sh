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
#
# 用法：github_briefing_backstop.sh --vault-root <path> [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_ROOT/logs"
LOG="$LOG_DIR/github-backstop.log"
mkdir -p "$LOG_DIR"

VAULT_ROOT=""
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --vault-root) VAULT_ROOT="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done
if [[ -z "$VAULT_ROOT" ]]; then
  echo "ERROR: --vault-root is required" >&2
  exit 1
fi

ts()  { TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(ts) [github-backstop] $*" | tee -a "$LOG" >&2; }
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

# 幂等守卫 ①：今日 briefing 已产出 → 跳过
if [[ -f "$GITHUB_FILE" ]]; then
  log "skip — GitHub briefing 已产出，无需补跑 ($DATE_TODAY)"
  exit 0
fi

# 守卫 ②：主跑进程仍在运行 → 让它先跑完
if pgrep -f "akb2.briefing.github.pipeline" >/dev/null 2>&1; then
  if deadline_missed; then
    log "FAILED — 已超过 12:00 deadline，github pipeline 仍在运行，不能继续静默等待 ($DATE_TODAY)"
    exit 124
  fi
  log "skip — github pipeline 仍在运行，本次不抢 ($DATE_TODAY)"
  exit 0
fi

if deadline_missed; then
  log "FAILED — 已超过 12:00 deadline 且 GitHub briefing 缺失，不再启动补跑 ($DATE_TODAY)"
  exit 124
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "dry-run — 今日 GitHub briefing 缺失，将会触发补跑（实际未执行）($DATE_TODAY)"
  exit 0
fi

log "run — 今日 GitHub briefing 缺失，开始补跑 ($DATE_TODAY)"
set +e
bash "$SCRIPT_DIR/daily_github_briefing.sh" --vault-root "$VAULT_ROOT"
rc=$?
set -e

case "$rc" in
  0) log "recovered — 补跑成功 (exit 0, $DATE_TODAY)" ;;
  *) log "FAILED — 补跑 exit $rc，当日 GitHub briefing 无产出 ($DATE_TODAY)" ;;
esac
exit "$rc"
