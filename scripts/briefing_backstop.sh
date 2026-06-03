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
# 用法：briefing_backstop.sh --vault-root <path> [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_ROOT/logs"
LOG="$LOG_DIR/backstop.log"
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

DATE="$(TZ=Asia/Shanghai date +%Y-%m-%d)"
ts()  { TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(ts) [backstop] $*" | tee -a "$LOG" >&2; }

# 发布落点（与 pipeline.py: briefing_filename = "<date> Daily Briefing.md" 对齐）
SOCIAL_FILE="$VAULT_ROOT/Praise Space/AKB2.0/AI TRENDS/Social/$DATE Daily Briefing.md"

# 幂等守卫 ①：今日已发布 → 跳过
if [[ -f "$SOCIAL_FILE" ]]; then
  log "skip — 今日已发布，无需补跑 ($DATE)"
  exit 0
fi

# 守卫 ②：主跑进程仍在运行 → 让它先跑完，本次跳过
if pgrep -f "akb2.briefing.pipeline" >/dev/null 2>&1; then
  log "skip — 主跑仍在进行，本次不抢 ($DATE)"
  exit 0
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "dry-run — 今日未发布，将会触发补跑（实际未执行）($DATE)"
  exit 0
fi

log "run — 今日未发布，开始补跑 ($DATE)"
set +e
bash "$SCRIPT_DIR/daily_briefing.sh" --date "$DATE" --vault-root "$VAULT_ROOT"
rc=$?
set -e

case "$rc" in
  0) log "recovered — 重跑成功已入库 (exit 0, $DATE)" ;;
  1) log "FAILED — 重跑 exit 1 (gate reject)，当日无简报 ($DATE)" ;;
  2) log "FAILED — 重跑 exit 2 (audit fail)，当日无简报 ($DATE)" ;;
  *) log "FAILED — 重跑 exit $rc (异常)，当日无简报 ($DATE)" ;;
esac
exit "$rc"
