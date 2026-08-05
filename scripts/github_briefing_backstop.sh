#!/usr/bin/env bash
# github_briefing_backstop.sh — GitHub Briefing 补跑守卫（主跑 05:30 失败时兜底）。
#
# 由 com.akb2.github_briefing.backstop（07:30）触发。设计要点：
#   - 幂等：今日 GitHub Briefing 已写盘 → 直接跳过，绝不重复产出。
#   - 仅补失败：只有当日 briefing 文件不存在时才重跑 daily_github_briefing.sh。
#   - 不抢主跑：pgrep 软守卫 + fcntl.flock（pgrep 会扑空，见锁那一段的说明）。
#   - 静默：成功/跳过/失败都只写日志，不弹桌面通知。
#
# 日志：
#   logs/github-backstop.log              ← 主看：每次一行（时间 + 结果）
#   测试/手工调用经 --log-path / AKB2_GITHUB_BACKSTOP_LOG 隔离，绝不污染生产日志
#
# ── 退出码语义（与 briefing_backstop.sh 对齐）─────────────────────────────
#   124 = DEADLINE_EXIT_CODE：**只**保留给「pipeline 被 run_with_deadline.py 到点
#         终止」。124 也是 GNU timeout(1) 的超时约定码，通用监控会读成"子进程超时"。
#   125 = GIVE_UP_EXIT_CODE：backstop **自己**主动放弃（过 deadline 不启动补跑、
#         重试循环撞 deadline）。二者以前共用 124，launchd 层完全不可分辨。
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
# DEADLINE_EXIT_CODE 与 scripts/run_with_deadline.py 的 _DEADLINE_EXIT 对齐；
# GIVE_UP_EXIT_CODE 是本脚本主动放弃。两者必须不同值（语义见文件头）。
readonly DEADLINE_EXIT_CODE=124
readonly GIVE_UP_EXIT_CODE=125
# flock_exec.py 的协议码：busy 是正常让路；helper/setup 失败必须响亮 fail-closed。
readonly LOCK_BUSY_EXIT_CODE=73
readonly LOCK_HELPER_ERROR_EXIT_CODE=74
readonly LOCK_BUSY_MARKER="flock_exec: busy"
readonly LOCK_HELPER_ERROR_MARKER="flock_exec: error"
readonly BACKSTOP_PYTHON="${AKB2_BACKSTOP_PYTHON:-python3}"
readonly FLOCK_EXEC_HELPER="$SCRIPT_DIR/lib/flock_exec.py"

# Resolve the permanent lock inode before the first outer/inner branch. Re-entry is
# accepted only when the helper's inherited fd still names this exact inode.
# shellcheck source=scripts/lib/state-root.sh
. "$SCRIPT_DIR/lib/state-root.sh"
akb2_resolve_state_root
LOCK_FILE="${AKB2_GITHUB_BACKSTOP_LOCK_FILE:-$STATE_ROOT/locks/github-briefing-backstop.lock}"
BACKSTOP_FLOCK_HELD=0
FLOCK_FD_CANDIDATE="${AKB2_BACKSTOP_FLOCK_FD:-}"
if [[ "$FLOCK_FD_CANDIDATE" =~ ^[0-9]+$ ]] && \
  "$BACKSTOP_PYTHON" -c \
    'import fcntl, os, sys; fd = int(sys.argv[1]); actual = os.fstat(fd); expected = os.stat(sys.argv[2]); same = (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino); same and fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); raise SystemExit(0 if same else 1)' \
    "$FLOCK_FD_CANDIDATE" "$LOCK_FILE" >/dev/null 2>&1; then
  BACKSTOP_FLOCK_HELD=1
fi
unset AKB2_BACKSTOP_FLOCK_FD FLOCK_FD_CANDIDATE
readonly BACKSTOP_FLOCK_HELD

FLOCK_EXEC_ERR_FILE=""
cleanup() {
  [[ -n "$FLOCK_EXEC_ERR_FILE" ]] && rm -f "$FLOCK_EXEC_ERR_FILE"
  return 0
}
trap cleanup EXIT

# 补跑重试：**该变的是模型族，不是次数**。2026-08-02 实证 —— pipeline 的 synthesis
# 阶段内部已有 3 次重试（`DeepSeek API failed after 3 retries`），主跑 05:59 用掉 3
# 次、backstop 07:30 又 3 次，**共 6 次同族尝试全部 429**。在同族上加第 7/8/9 次只
# 会多烧额度、把 backstop 运行时间推向 deadline，**反而降低补跑成功率**。
# 所以预算取 2 而不是 3，且第 2 轮**必须换模型族**（见 AKB2_LLM_FAMILY_FALLBACK）。
readonly RERUN_ATTEMPT_BUDGET="${AKB2_GITHUB_RERUN_ATTEMPTS:-2}"
readonly RERUN_BACKOFF_SECONDS="${AKB2_GITHUB_RERUN_BACKOFF:-600}"
# 换模型族**救不了**的退出码 → 不进第 2 轮（宪法 P4 预算显式）。
#   124 = pipeline 被 run_with_deadline 到点终止 —— 再跑一轮同样跑不完，
#         只会把 backstop 自己推过 deadline。
#    65 = 共享预算闸已短路（daily_github_briefing.sh 的 SHARED_BUDGET_BLOCKED_EXIT_CODE）。
#         注意：这条码位**不是** pipeline 产的，是 wrapper 在 pipeline 启动**之前**
#         由预算闸拦下的 —— 此前这里的注释写「github pipeline 的码位只有 0/1，所以只需
#         拦 124」，那个前提已被同一提交推翻。它对 AKB2_LLM_FAMILY_FALLBACK 结构性无效
#         （闸门跟模型族无关），不拦的后果是明知无望仍空睡 RERUN_BACKOFF=600s，
#         而这 600s 一直占着补跑锁。
# 1 仍然重试（可能是 LLM 输出质量，换族有救）。
readonly SHARED_BUDGET_BLOCKED_EXIT_CODE=65
readonly RERUN_NONRETRYABLE_EXIT_CODES="124 $SHARED_BUDGET_BLOCKED_EXIT_CODE"

# is_nonretryable_rc <rc> → 0=该码换族救不了(别重试), 1=可以再试一轮
is_nonretryable_rc() {
  local code
  for code in $RERUN_NONRETRYABLE_EXIT_CODES; do
    [[ "$1" == "$code" ]] && return 0
  done
  return 1
}
if ! [[ "$RERUN_ATTEMPT_BUDGET" =~ ^[0-9]+$ ]] || (( RERUN_ATTEMPT_BUDGET < 1 )); then
  echo "ERROR: AKB2_GITHUB_RERUN_ATTEMPTS 必须是 >=1 的整数，实际 '$RERUN_ATTEMPT_BUDGET'" >&2
  exit 1
fi
if ! [[ "$RERUN_BACKOFF_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: AKB2_GITHUB_RERUN_BACKOFF 必须是非负整数，实际 '$RERUN_BACKOFF_SECONDS'" >&2
  exit 1
fi

# run_id/host：同一日志里区分多进程与多机器。~/AI-Agent 是 Syncthing 共享目录，
# logs/github-backstop.log 会被两台机器同时追加，不带归属字段就看不出
# "FAILED 与 recovered 交替" 其实是不同 run。
RUN_ID="${AKB2_GITHUB_BACKSTOP_RUN_ID:-$(TZ=Asia/Shanghai date +%Y%m%d%H%M%S)-$$}"
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

# deadline_missed_after <seconds> → 0=睡完就已过界（别睡，直接放弃）, 1=还有余量
# 为什么需要它：退避 sleep 发生在**下一轮的 deadline 检查之前**，所以原来会出现
# "白睡 600s，醒来才发现已过界然后放弃" —— 不但浪费，而且睡着的这段时间一直**占着
# 补跑锁**，把随后那次 backstop（12:05 那趟）也一起挡在门外。已经能算出的结论就别
# 靠睡出来（宪法 P1 有界自动化 / P4 预算显式）。
# 实现刻意只用 HHMM 分钟运算，不用 `date -v`（BSD-only）或 `date -d @`（GNU-only）——
# 两者任一都会踩 tests/test_shell_portability.py 的跨平台守卫。
deadline_missed_after() {
  local wait_s="$1" now_hm now_min dl_min projected
  now_hm="$(TZ=Asia/Shanghai date +%H%M)"
  now_min=$(( 10#${now_hm:0:2} * 60 + 10#${now_hm:2:2} ))
  dl_min=$(( 10#${AKB2_GITHUB_DEADLINE_HOUR:-12} * 60 + 10#${AKB2_GITHUB_DEADLINE_MINUTE:-0} ))
  projected=$(( now_min + (wait_s + 59) / 60 ))   # 向上取整到分钟，宁可保守
  (( projected >= 1440 )) && return 0             # 跨夜 = 必然过界（deadline 是当日）
  (( projected >= dl_min ))
}

# GitHub briefing 文件名对齐 pipeline.py：today = 抓取日（去掉旧的 +1 偏移）
DATE_TODAY="$(TZ=Asia/Shanghai date +%Y-%m-%d)"
GITHUB_FILE="$VAULT_ROOT/Praise Space/AKB2.0/AI TRENDS/GitHub/$DATE_TODAY GitHub Briefing.md"

# deadline 文案：自证"这个界是谁定的"
DEADLINE_LABEL="$(printf '%02d:%02d' "${AKB2_GITHUB_DEADLINE_HOUR:-12}" "${AKB2_GITHUB_DEADLINE_MINUTE:-0}")"

# 幂等守卫 ①：今日 briefing 已产出 → 跳过
if [[ -f "$GITHUB_FILE" ]]; then
  if [[ "$BACKSTOP_FLOCK_HELD" == "1" ]]; then
    log_terminal skip "skip — 拿锁后重检发现 GitHub briefing 已产出，不启动补跑 lock_artifact_recheck=pass ($DATE_TODAY)"
  else
    log_terminal skip "skip — GitHub briefing 已产出，无需补跑 ($DATE_TODAY)"
  fi
  exit 0
fi

# 外层做便宜的进程/deadline/dry-run 预检；helper exec 回来的内层已经持有 flock，
# 只做产物重检并进入补跑，避免重复读取时钟改变既有 deadline 语义。
if [[ "$BACKSTOP_FLOCK_HELD" != "1" ]]; then
# 守卫 ②：主跑进程仍在运行 → 让它先跑完
#
# 探针模式做成具名 + 可覆写：`pgrep -f` 是**全机器范围**的模糊匹配，同机器上任何一条
# 命令行里出现 `akb2.briefing.github.pipeline` 都会让本守卫命中。这对生产是正确行为
# （就是要让路），但对测试是不可控外因 —— 实测并行开发时另一条链路跑
# `python3 -m akb2.briefing.github.pipeline` 就让本脚本的用例集体走进 skip 分支。
# 测试把它指向一个必然不命中的模式来取得确定性；生产不设该变量，行为不变。
readonly MAIN_RUN_PGREP_PATTERN="${AKB2_GITHUB_BACKSTOP_PGREP_PATTERN:-akb2.briefing.github.pipeline}"
if pgrep -f "$MAIN_RUN_PGREP_PATTERN" >/dev/null 2>&1; then
  if deadline_missed; then
    log_terminal failed "FAILED — 已超过 $DEADLINE_LABEL deadline，github pipeline 仍在运行，不能继续静默等待 reason=deadline_missed exit_code=$GIVE_UP_EXIT_CODE ($DATE_TODAY)"
    exit "$GIVE_UP_EXIT_CODE"
  fi
  log_terminal skip "skip — github pipeline 仍在运行，本次不抢 ($DATE_TODAY)"
  exit 0
fi

if deadline_missed; then
  log_terminal failed "FAILED — 已超过 $DEADLINE_LABEL deadline 且 GitHub briefing 缺失，不再启动补跑 reason=deadline_missed exit_code=$GIVE_UP_EXIT_CODE ($DATE_TODAY)"
  exit "$GIVE_UP_EXIT_CODE"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  log_terminal dry_run "dry-run — 今日 GitHub briefing 缺失，将会触发补跑（实际未执行）($DATE_TODAY)"
  exit 0
fi
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

# ── 补跑互斥锁（L3-4）──────────────────────────────────────────────────────
# pgrep 结构性救不了双跑：主跑与 backstop 的调度间隔（social 侧 07:30 → 08:00 共
# 30 分钟）大于主跑实际耗时（历史 8–16 分钟），到 backstop 触发时 pgrep **必然扑
# 空**。pgrep 守卫保留（便宜的早退），真正的互斥必须落在锁上。
#
# 锁实现使用仓库既有的 `fcntl.flock` 共享 inode 范式。锁文件只作为永久 inode，
# 不承载 PID、不删除、不做陈旧接管；内核把锁绑定到继承的 fd，并在最后一个持 fd 的
# 执行进程退出时释放。
# macOS 没有 flock(1)，所以由 python helper 完成 LOCK_EX|LOCK_NB + exec。
# 锁**位置**用机器本地状态根而不是仓库 staging/locks/：REPO_ROOT 在 ~/AI-Agent
# （Syncthing folder）内，锁放仓库会被跨机同步，而 `fcntl.flock` 只在本机内核生效，
# 不能协调另一台机器上的同路径（该坑 akb2-main-pipeline.sh:26-30 已修过）。
if akb2_state_root_is_synced "$(dirname "$LOCK_FILE")"; then
  # 不 fail-closed 退出：拒跑等于把一次可能成功的补跑直接判死，比锁降级更糟。
  # 但必须明示降级（宪法 P4 失败可见），否则"为什么又双跑了"查不出来。
  log "warn — 锁目录在跨机同步范围内，互斥保证降级 lock_sync_risk=1 lock_file=$LOCK_FILE ($DATE_TODAY)"
fi

if [[ "$BACKSTOP_FLOCK_HELD" != "1" ]]; then
  if ! command -v "$BACKSTOP_PYTHON" >/dev/null 2>&1; then
    log_terminal failed "FAILED — python3 不可用，无法建立 fcntl.flock 互斥，拒绝无锁补跑 lock_setup=failed exit_code=1 ($DATE_TODAY)"
    exit 1
  fi
  if ! FLOCK_EXEC_ERR_FILE="$(mktemp "${TMPDIR:-/tmp}/akb2-flock-exec.XXXXXX")"; then
    log_terminal failed "FAILED — 无法建立 flock helper stderr framing，拒绝无锁补跑 lock_setup=failed exit_code=1 ($DATE_TODAY)"
    exit 1
  fi
  set +e
  AKB2_GITHUB_BACKSTOP_RUN_ID="$RUN_ID" \
    "$BACKSTOP_PYTHON" "$FLOCK_EXEC_HELPER" \
    --lock-file "$LOCK_FILE" \
    --busy-exit-code "$LOCK_BUSY_EXIT_CODE" \
    --error-exit-code "$LOCK_HELPER_ERROR_EXIT_CODE" \
    -- /bin/bash "$0" --vault-root "$VAULT_ROOT" --log-path "$LOG" \
    2>"$FLOCK_EXEC_ERR_FILE"
  lock_rc=$?
  set -e
  lock_busy_marker=0
  lock_error_marker=0
  if grep -Fxq "$LOCK_BUSY_MARKER" "$FLOCK_EXEC_ERR_FILE"; then lock_busy_marker=1; fi
  if grep -Fxq "$LOCK_HELPER_ERROR_MARKER" "$FLOCK_EXEC_ERR_FILE"; then lock_error_marker=1; fi
  cat "$FLOCK_EXEC_ERR_FILE" >&2
  rm -f "$FLOCK_EXEC_ERR_FILE"
  FLOCK_EXEC_ERR_FILE=""
  if (( lock_rc == LOCK_BUSY_EXIT_CODE && lock_busy_marker == 1 )); then
    log_terminal skip "skip — already-running: 另一个 github backstop 补跑正持 flock lock_file=$LOCK_FILE ($DATE_TODAY)"
    exit 0
  fi
  if (( lock_rc == LOCK_HELPER_ERROR_EXIT_CODE && lock_error_marker == 1 )); then
    log_terminal failed "FAILED — 无法建立 fcntl.flock 互斥，拒绝无锁补跑 lock_setup=failed exit_code=1 ($DATE_TODAY)"
    exit 1
  fi
  exit "$lock_rc"
fi

# 初次产物判断与拿锁之间，主跑可能刚好完成。成为唯一锁 owner 后必须重检，避免对
# 已交付的产物再启动一次付费补跑。
if [[ -f "$GITHUB_FILE" ]]; then
  log_terminal skip "skip — 拿锁后重检发现 GitHub briefing 已产出，不启动补跑 lock_artifact_recheck=pass ($DATE_TODAY)"
  exit 0
fi

# ── 补跑循环（有界 + 每轮换族 + 每轮前查 deadline）─────────────────────────
attempt=1
rc=0
while (( attempt <= RERUN_ATTEMPT_BUDGET )); do
  # 每轮开始前查 deadline：补跑本身要耗时，不能开一轮就把运行时间推过界（宪法 P1）
  if deadline_missed; then
    log_terminal give_up \
      "GIVE-UP — 已超过 $DEADLINE_LABEL deadline，停止补跑重试 attempt=$attempt/$RERUN_ATTEMPT_BUDGET reason=deadline_missed exit_code=$GIVE_UP_EXIT_CODE ($DATE_TODAY)"
    exit "$GIVE_UP_EXIT_CODE"
  fi
  # 第 2 轮起换模型族（COMMON.md 契约 1，消费端由 synthesis 实现）。
  # 同族已被证明连续 6 次 429 —— 换的必须是**族**，不是次数。
  family_fallback=0
  (( attempt >= 2 )) && family_fallback=1
  log "run — 今日 GitHub briefing 缺失，开始补跑 attempt=$attempt/$RERUN_ATTEMPT_BUDGET llm_family_fallback=$family_fallback ($DATE_TODAY)"
  set +e
  AKB2_LLM_FAMILY_FALLBACK="$family_fallback" \
    bash "$RERUN_SCRIPT" --vault-root "$VAULT_ROOT"
  rc=$?
  set -e
  # SC-5: recovered 必须复查文件 — rc==0 但当日文件未写出 = 假成功，不算成功
  if (( rc == 0 )) && [[ -f "$GITHUB_FILE" ]]; then
    log_terminal recovered "recovered — 补跑成功且文件已落盘 attempt=$attempt/$RERUN_ATTEMPT_BUDGET (exit 0, $DATE_TODAY)"
    exit 0
  fi
  if (( rc == 0 )); then
    # rc=0 却没文件 = pipeline 的「未交付即非零」不变式被破了，属真异常，值得再试一轮
    log "warn — 补跑 rc=0 但当日文件未落盘（假成功，pipeline 不变式被破）attempt=$attempt/$RERUN_ATTEMPT_BUDGET ($DATE_TODAY)"
  elif is_nonretryable_rc "$rc"; then
    log "warn — 补跑未成功 attempt=$attempt/$RERUN_ATTEMPT_BUDGET rc=$rc rerun_stop=nonretryable_exit（该退出码换模型族救不了，不再重试）($DATE_TODAY)"
    break
  else
    log "warn — 补跑未成功 attempt=$attempt/$RERUN_ATTEMPT_BUDGET rc=$rc ($DATE_TODAY)"
  fi
  attempt=$(( attempt + 1 ))
  if (( attempt <= RERUN_ATTEMPT_BUDGET )); then
    backoff=$(( RERUN_BACKOFF_SECONDS * (2 ** (attempt - 2)) ))
    if deadline_missed_after "$backoff"; then
      log_terminal give_up \
        "GIVE-UP — 退避 ${backoff}s 之后即已超过 $DEADLINE_LABEL deadline，不再空等（白睡且占着补跑锁）next_attempt=$attempt/$RERUN_ATTEMPT_BUDGET reason=deadline_would_pass exit_code=$GIVE_UP_EXIT_CODE ($DATE_TODAY)"
      exit "$GIVE_UP_EXIT_CODE"
    fi
    log "info — 退避 ${backoff}s 后换模型族重试 next_attempt=$attempt/$RERUN_ATTEMPT_BUDGET ($DATE_TODAY)"
    sleep "$backoff"
  fi
done

file_state="missing"
[[ -f "$GITHUB_FILE" ]] && file_state="present"
# 报实际用掉的轮次，不是预算值：非重试码会提前 break，此时写死 BUDGET/BUDGET
# 等于谎报"两轮都试过了"。
attempts_used=$(( attempt > RERUN_ATTEMPT_BUDGET ? RERUN_ATTEMPT_BUDGET : attempt ))
# exit_code 报的必须是**本脚本真正的退出码**：rc=0 却没交付时脚本退 1，
# 旧实现在这一行打印 exit_code=0 然后 exit 1 —— 日志与实际退出码不一致，
# 监控按日志判定就会把一次未交付读成成功（宪法 P4 失败可见）。
final_exit="$rc"
(( final_exit == 0 )) && final_exit=1
log_terminal failed "FAILED — 补跑未交付 attempt=$attempts_used/$RERUN_ATTEMPT_BUDGET (rc=$rc, file=$file_state, exit_code=$final_exit, $DATE_TODAY)"
exit "$final_exit"
