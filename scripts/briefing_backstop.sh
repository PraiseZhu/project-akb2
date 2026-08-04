#!/usr/bin/env bash
# briefing_backstop.sh — 早 7:30 主跑失败后的补跑守卫。
#
# 由 com.akb2.briefing.backstop（8:00）触发。设计要点：
#   - 幂等：今日已发布到 Social → 直接跳过，绝不重复发。
#   - 仅补失败：只有当日 Social 文件未达 pass 时才重跑 daily_briefing.sh。
#   - 不抢主跑：pgrep 软守卫 + PID 锁（见下方"为什么 pgrep 不够"）。
#   - 静默告警（选项 C）：成功/跳过/失败都只写日志，不弹桌面通知。
#
# 日志：
#   logs/backstop.log              ← 主看：每次一行（时间 + 结果）
#   logs/backstop-{stdout,stderr}  ← 由 launchd 接管（重跑那条 pipeline 的原始输出）
#
# ── 退出码语义（L3-5：124 曾被重载，launchd 层不可分辨）─────────────────────
#   124 = DEADLINE_EXIT_CODE：**只**保留给「pipeline 被 run_with_deadline.py 到点
#         终止」这一种来源。124 同时是 GNU timeout(1) 的超时约定码，通用监控读到
#         它会理解成"某个子进程超时"—— 这个理解现在是对的。
#   125 = GIVE_UP_EXIT_CODE：backstop **自己**主动放弃（过 deadline 不再启动补跑、
#         重试循环撞上 deadline）。以前这两类共用 124，看 launchd 的
#         "last exit code 124" 完全分不出是"pipeline 跑超时"还是"守卫压根没启动"。
#   **不是** 任何 stage 超时：translate 的 AKB2_TRANSLATE_TIMEOUT(=300s) 是单次 HTTP
#   请求超时，不是 stage 预算，所以 translate 段耗时 442.9s 并不越界、也不产出 124。
#
# ── 终态语义（L3-2：failed 曾被过度断言）───────────────────────────────────
#   terminal=failed        断言的是「**当天没有简报**」—— 只有 status=missing 之类
#                          真能支持这个断言的状态才允许用。
#   terminal=indeterminate 「无法判定，需人工」。status=unreadable + 过 deadline 走
#                          这一档：读盘失败恰恰**不能**支持"当天没有简报"的断言。
#   backstop.log 全史 38/40 条 FAILED（95%）是假阳性，真缺文件只有 07-27/07-28
#   两天 —— 全部源于把 unreadable 一路断言成 failed。假阳性降到 0 之前不能接告警
#   通道，否则必然告警疲劳。
#
# ── 补跑时段与 UTC 预算日的耦合（已由 owner 决策并改模板，勿再私自改调度）────
#   为什么第二补跑时段必须早于 deadline，且必须落在 UTC 预算日重置之后：
#     · 本机时区 Asia/Shanghai = UTC+8。
#     · 05:30 主跑 = UTC 21:30、07:30/08:00 backstop = UTC 23:30/00:00 —— 都落在
#       **UTC 预算日的尾部**，日配额通常已被前 22 小时消耗殆尽（2026-08-01 实证：
#       `ExceededBudget: exceeded UTC daily cap. Date=2026-08-01, Spend=2701.23,
#       DailyCap=2700.0`，主跑与 backstop 双双 429）。
#     · 11:00 = UTC 03:00 —— UTC 预算日已重置 3 小时，额度充裕，且早于 12:00 deadline。
#
#   历史形态：该时段曾是 **12:05**，晚于默认 deadline 12:00 —— 于是"唯一有额度的补跑
#   窗口"被 deadline 结构性锁死，按设计只能巡检报告、永远无法真正补跑。
#   现状：owner 2026-08-03 确认，**仓库模板已改为 11:00**（launchd/*.backstop.plist）。
#   注意运行态与模板可能仍不一致：这两个 label 的 manifest installer 是
#   `manual_existing_runtime`，需 owner 人工重装才生效；未重装期间
#   scripts/verify-launchd-parity.sh 会报 installed_stale（方向指向运行态，不是仓库），
#   该残留已在 docs/allowed-residuals.md 登记。
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
# DEADLINE_EXIT_CODE / GIVE_UP_EXIT_CODE：语义见文件头。124 与
# scripts/run_with_deadline.py 的 _DEADLINE_EXIT 对齐（那是它的唯一合法来源）；
# 本脚本自己放弃时用 125，两者必须不同值，否则 launchd 层永远分不出来。
readonly DEADLINE_EXIT_CODE=124
readonly GIVE_UP_EXIT_CODE=125

# 读盘重试：外置 APFS 卷（/Volumes/AKB2）偶发**卷级瞬时 open() 失败**，单次失败不该
# 把健康的一天翻成 FAILED；重试次数有界，每次尝试都落日志。
# 4 次 × 5s = 20s 窗口：旧值（3 × 2s = 4s）盖不住卷级抖动的量级 —— 生产实测同一个
# awk 连续 3 次全失败，说明抖动持续时间超过 4s。但**加长窗口只是次要手段**，
# 主修法是下面的双读者交叉验证：同一个 awk 重试 N 次不构成独立观测。
readonly VERDICT_READ_ATTEMPT_BUDGET=4
readonly VERDICT_READ_RETRY_SLEEP_SECONDS="${AKB2_BACKSTOP_READ_RETRY_SLEEP:-5}"
# 纯 bash 回退读者的行数上限（宪法 P1：任何循环都要有界）。frontmatter 不会更长。
readonly NATIVE_READER_MAX_LINES=200

# 补跑重试：**该变的是模型族，不是次数**。2026-08-02 实证 —— pipeline 的 synthesis
# 阶段内部已有 3 次重试（`DeepSeek API failed after 3 retries`），主跑 05:59 用掉 3
# 次、backstop 07:30 又 3 次，**共 6 次同族尝试全部 429**。在同族上加第 7/8/9 次只
# 会多烧额度、把 backstop 运行时间推向 deadline，**反而降低补跑成功率**。
# 所以预算取 2 而不是 3，且第 2 轮**必须换模型族**（见 AKB2_LLM_FAMILY_FALLBACK）。
readonly RERUN_ATTEMPT_BUDGET="${AKB2_BRIEFING_RERUN_ATTEMPTS:-2}"
readonly RERUN_BACKOFF_SECONDS="${AKB2_BRIEFING_RERUN_BACKOFF:-600}"
# 换模型族**救不了**的退出码 → 不进第 2 轮。这批修复的主题就是「别在明知无望的地方
# 烧额度」，在重试循环里重犯等于换了个位置复发（宪法 P4 预算显式）。
# 取值语义的权威源：src/akb2/briefing/pipeline.py 的 EXIT_* 常量 +
# scripts/run_with_deadline.py 的 _DEADLINE_EXIT；那边新增码位时这里要跟着复核。
#   124 = pipeline 被 run_with_deadline 到点终止 —— 再跑一轮同样跑不完，
#         只会把 backstop 自己的运行时间推过 deadline
#     3 = intake 降级到测试 fixture、硬阻断 —— 确定性阻断，与模型族无关
#     5 = 跨日去重账本不可用 —— 基础设施故障，换族不解决
#    65 = 共享预算闸已短路（daily_briefing.sh 的 SHARED_BUDGET_BLOCKED_EXIT_CODE）——
#         闸门在 pipeline **启动之前**就拦下了，跟模型族毫无关系：
#         `AKB2_LLM_FAMILY_FALLBACK=1` 对它结构性无效。不拦这个码的后果是生产默认
#         `RERUN_BACKOFF=600` 明知无望仍空睡 600s，而这 600s 一直**占着补跑锁**
#         （正是 deadline_missed_after 要消掉的那种浪费，只是换了触发源）。
# 仍然重试的：1（通用未交付 / gate reject / vault 不可用）、2（audit fail）、
#   4（gate 不收敛）—— 这三类都可能由 LLM 输出质量驱动，换族确有机会救回。
readonly SHARED_BUDGET_BLOCKED_EXIT_CODE=65
readonly RERUN_NONRETRYABLE_EXIT_CODES="124 3 5 $SHARED_BUDGET_BLOCKED_EXIT_CODE"
if ! [[ "$RERUN_ATTEMPT_BUDGET" =~ ^[0-9]+$ ]] || (( RERUN_ATTEMPT_BUDGET < 1 )); then
  echo "ERROR: AKB2_BRIEFING_RERUN_ATTEMPTS 必须是 >=1 的整数，实际 '$RERUN_ATTEMPT_BUDGET'" >&2
  exit 1
fi
if ! [[ "$RERUN_BACKOFF_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: AKB2_BRIEFING_RERUN_BACKOFF 必须是非负整数，实际 '$RERUN_BACKOFF_SECONDS'" >&2
  exit 1
fi

# 补跑入口（测试经 AKB2_BRIEFING_RERUN_SCRIPT 注入 stub —— 缺这一口时"补跑是否真被
# 触发"无法被测试观测，SC「不得重跑」这类断言就只能靠读日志猜）
RERUN_SCRIPT="${AKB2_BRIEFING_RERUN_SCRIPT:-$SCRIPT_DIR/daily_briefing.sh}"

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
  dl_min=$(( 10#${AKB2_BRIEFING_DEADLINE_HOUR:-12} * 60 + 10#${AKB2_BRIEFING_DEADLINE_MINUTE:-0} ))
  projected=$(( now_min + (wait_s + 59) / 60 ))   # 向上取整到分钟，宁可保守
  (( projected >= 1440 )) && return 0             # 跨夜 = 必然过界（deadline 是当日）
  (( projected >= dl_min ))
}

# 发布落点（与 pipeline.py: briefing_filename = "<date> Daily Briefing.md" 对齐）
SOCIAL_FILE="$VAULT_ROOT/Praise Space/AKB2.0/AI TRENDS/Social/$DATE Daily Briefing.md"

# ── 主读者：frontmatter audit_verdict（单次 awk，容忍真实世界的格式抖动）──────
# 旧实现用 `sed -n '/^---$/,/^---$/p'`，只认严格 `^---$`：UTF-8 BOM、CRLF 行尾、
# fence 尾随空格任一出现，整段 frontmatter 就取不到 → 已入库的 pass 简报被判
# "无法解析" 并触发重跑。这里改成单次 awk：去 BOM、去 CR、容忍 fence 前后空白、
# 只读第一段 frontmatter（段外的同名键不采信）、剥引号与行尾 YAML 注释。
#
# 退出码 0 = 读盘成功（verdict 可能为空串 = 字段确实不存在）
# 退出码 非0 = 读盘失败（stderr 保留 awk 的真实错误，交给调用方落日志）
#
# 测试注入口 AKB2_BACKSTOP_READER：指向一个可执行文件替换本读者。为什么必须有它 ——
# unreadable 分支过去只能靠 `chmod 000` 构造，而生产卷是 **noowners 挂载**，同构
# 环境下 chmod 拦不住读取 → 测试静默 `pytest.skip`，在最需要它的环境里恰好不生效。
# 注入式构造彻底摆脱对挂载语义的依赖。生产不设该变量 → 走内建 awk。
BACKSTOP_READER="${AKB2_BACKSTOP_READER:-}"
read_frontmatter_verdict() {
  if [[ -n "$BACKSTOP_READER" ]]; then
    "$BACKSTOP_READER" "$1"
    return $?
  fi
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

# ── 第二读者：纯 bash 回退（L3-1 本 lane 核心修法）──────────────────────────
# 为什么必须是"另一个读者"而不是"awk 再重试几次"：
#   同一个 awk 连续读 3 次**不构成独立观测** —— 生产 3/3 全失败已实证。判据单点
#   造成同一个根因（卷级瞬时 open() 失败）的两种表现：08:00 判 unreadable →
#   fail-closed 重跑 → 38 天双跑；12:05 判 unreadable → 过 deadline → 95% 假 FAILED。
# 为什么纯 bash 是有效的第二观测：
#   1. 无外部进程（不 fork/exec），open() 由 bash 自己发起，语义与 awk 的不同；
#   2. bash 的重定向失败**带 strerror**（"No such file or directory" /
#      "Input/output error"），而 one-true-awk 的 `can't open file` 不带 ——
#      err= 里过去永远只有那句不带 errno 的话，宪法 P4「失败可见」在此失效。
#
# 关于 `awk: can't open file ... source line number 15`（不要再往解析方向修）：
#   `source line number 15` 是 one-true-awk 在**任何 FATAL 时无条件打印**的
#   curnode->lineno，与失败原因无关；报错里**没有** `input record number` 字段
#   ⇒ NR==0 ⇒ open() 在读到第一条记录前就失败 ⇒ 纯 open 失败，不是解析问题。
#   也**不是**路径含空格未加引号（引号本来就对，且已在 launchd 精确环境实测通过）。
#   errno 不可知（awk 不带 strerror）—— 所以修法对 errno 不可知保持鲁棒：
#   不猜错误类型，只换观测者 + 落真实探针。
#
# 正则放变量里再 =~：bash 3.2（macOS /bin/bash）下引号包裹的 RHS 会被当字面量，
# 用变量是两版通用的写法。
_RE_FENCE_OPEN='^[[:space:]]*---[[:space:]]*$'
_RE_FENCE_CLOSE='^[[:space:]]*(---|\.\.\.)[[:space:]]*$'
_RE_BLANK='^[[:space:]]*$'
_RE_VERDICT_KEY='^[[:space:]]*audit_verdict[[:space:]]*:'
# 循环体的**最后一条**语句一律写成 `if …; then break; fi` 而不是 `(( … )) && break`：
# `while` 的退出状态 = body 里最后执行的那条命令的状态，`(( … )) && break` 条件不成立
# 时留下状态 1，会让下面 `done < "$f" || rc=1` 把一次**正常读完**误判成读盘失败。
read_frontmatter_verdict_native() {
  local f="$1" line value n=0 seen_open=0 found=0 rc=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    n=$(( n + 1 ))
    if (( n == 1 )); then line="${line#$'\357\273\277'}"; fi   # 去 UTF-8 BOM
    line="${line%$'\r'}"                              # 去 CR（CRLF 行尾）
    if (( seen_open == 0 )); then
      if [[ "$line" =~ $_RE_FENCE_OPEN ]]; then seen_open=1
      elif [[ "$line" =~ $_RE_BLANK ]]; then :
      else break                                      # 首个非空行不是 fence → 无 frontmatter
      fi
    elif [[ "$line" =~ $_RE_FENCE_CLOSE ]]; then
      break                                           # frontmatter 段结束
    elif [[ "$line" =~ $_RE_VERDICT_KEY ]]; then
      value="${line#*:}"
      value="${value%%[[:space:]]\#*}"                 # 去行尾 YAML 注释
      while [[ "$value" == [[:space:]]* ]]; do value="${value#?}"; done
      while [[ "$value" == *[[:space:]] ]]; do value="${value%?}"; done
      value="${value#[\"\']}"                          # 去成对引号
      value="${value%[\"\']}"
      while [[ "$value" == *[[:space:]] ]]; do value="${value%?}"; done
      found=1
      break
    fi
    if (( n >= NATIVE_READER_MAX_LINES )); then break; fi   # 有界（宪法 P1）
  done < "$f" || rc=1
  if (( rc != 0 )); then return 1; fi
  if (( found == 1 )); then printf '%s\n' "$value"; fi
  return 0
}

# ── 单一已发布状态判定（消除"先判 pass 跳过、又判未发布重跑"的前后不一致）────
# 直接写全局，**禁止**放进 `$( )` 调用：历史缺陷正是把本函数塞进命令替换里，
# 子 shell 的赋值丢失，导致日志里的 audit_verdict 永远打印成空串（那个 `('')`
# 是无条件输出，不是真实解析结果）。
#
# PUBLISHED_STATUS ∈ pass | warn_or_fail | unknown_verdict | no_verdict | unreadable | missing
#   no_verdict  = 读盘成功但 frontmatter 里没有 audit_verdict 字段（真·解析不出）
#   unreadable  = **两个读者都失败**（I/O、权限、卷掉线），与解析失败严格区分（宪法 P4）
# PUBLISHED_READ_PATH ∈ awk_primary | native_fallback | both_failed | ""（文件不存在）
PUBLISHED_STATUS=""
PUBLISHED_VERDICT=""
PUBLISHED_READ_ERROR=""
PUBLISHED_READ_PATH=""
PUBLISHED_READ_ATTEMPTS=0   # 主读者本次实际尝试轮次（结构性不可用时为 1，不是预算值）
VERDICT_ERR_FILE=""
LOCK_FILE=""
LOCK_HELD=0
# 补跑后复查得到的产物状态。初值刻意不是 "pass"：若 rc=0 却没走过复查（结构性不可能，
# 但真发生时属编程错误），假 recovered 闸按 fail-closed 报 FAILED 而不是放行 recovered。
RERUN_POST_STATUS="unset"
# 释放锁：**删前重读**，内容 == 自己的 pid 才删（所有者校验）。
# 为什么 `LOCK_HELD=1` 不够：acquire 是 check-then-write，两个同刻实例可以都判定
# 「无锁/陈旧锁」并各自 `echo $$ >`，于是**双方 LOCK_HELD 都是 1**；后写者覆盖前者，
# 前者退出时无条件 rm 就把**后者**（当前真持有者）的锁删了 —— 第三个实例随即冲进来。
# 内容比对不能消除 acquire 侧的竞态窗口，但能保证「只有所有者释放锁」，
# 把「误删他人锁」这一半彻底关掉。
release_rerun_lock() {
  local current
  [[ "$LOCK_HELD" -eq 1 && -n "$LOCK_FILE" ]] || return 0
  LOCK_HELD=0
  current="$(cat "$LOCK_FILE" 2>/dev/null || echo '')"
  if [[ "$current" != "$$" ]]; then
    log "warn — 锁已被他人接管，不删除 lock_owner_now=${current:-none} lock_self=$$ lock_file=$LOCK_FILE (${DATE:-})" || true
    return 0
  fi
  rm -f "$LOCK_FILE"
  return 0
}
cleanup() {
  [[ -n "$VERDICT_ERR_FILE" ]] && rm -f "$VERDICT_ERR_FILE"
  release_rerun_lock
  return 0
}
trap cleanup EXIT

classify_verdict() {
  PUBLISHED_VERDICT="$1"
  case "$1" in
    pass)       PUBLISHED_STATUS="pass" ;;
    warn|fail)  PUBLISHED_STATUS="warn_or_fail" ;;
    "")         PUBLISHED_STATUS="no_verdict" ;;
    *)          PUBLISHED_STATUS="unknown_verdict" ;;
  esac
}

# primary_reader_available → 0=主读者可调用, 1=结构性不可用
# 所有判断都放进 if 条件里：裸 `[[ … ]]` 作为独立语句失败时会被 set -e 直接掀掉函数。
primary_reader_available() {
  local target="${BACKSTOP_READER:-}"
  if [[ -n "$target" ]]; then
    if [[ -x "$target" ]]; then return 0; fi
    return 1
  fi
  if command -v awk >/dev/null 2>&1; then return 0; fi
  return 1
}

resolve_published_state() {
  local f="$1" attempt=1 out rc err_file tmp_base probe_rc native_out native_rc
  local read_budget="$VERDICT_READ_ATTEMPT_BUDGET"
  PUBLISHED_STATUS=""; PUBLISHED_VERDICT=""; PUBLISHED_READ_ERROR=""; PUBLISHED_READ_PATH=""
  PUBLISHED_READ_ATTEMPTS=0

  if [[ ! -f "$f" ]]; then
    PUBLISHED_STATUS="missing"
    return 0
  fi

  # ── 主读者可用性预检（回退不得悄悄变成常态）─────────────────────────────
  # 回退读者只有作为**例外路径**才有价值。如果 awk 本身不可解析（launchd 给的是
  # 固定最小 PATH，见 launchd/com.akb2.briefing.backstop.plist 的 PATH 键；模板化
  # 或环境变动都可能把它弄坏），主读者会**每次**失败，native 回退将永久承担 100%
  # 的判据 —— 而 verdict 依然正确，于是这个结构性缺陷被回退悄悄掩盖，
  # `read_path=native_fallback` 也就此失去信号价值：它本该表示"今天卷抖了一下"，
  # 却变成了"永远如此"。把响亮的故障变成安静的长期降级，正是宪法 P4 要禁的。
  # 所以这里把两件事分开：
  #   · 结构性不可用 → 单独归因 reader_unavailable=，不与卷级瞬时失败混为一谈；
  #   · 且不再空跑 4×5s 的重试窗口 —— 对"二进制不存在"重试毫无意义，只是白等 20s
  #     并把 backstop 的运行时间往 deadline 推。
  if ! primary_reader_available; then
    read_budget=1
    log "warn — 主读者结构性不可用 reader_unavailable=${BACKSTOP_READER:-awk}（不是卷抖动；回退将承担全部判据，需人工修环境）跳过重试窗口 ($DATE)"
  fi

  # mktemp 必须用**纯模板参数**（模板自带 XXXXXX），两端通用：
  #   BSD/macOS  `mktemp -t <prefix>`  → 自动补随机后缀，模板可不带 X
  #   GNU/Linux  `mktemp -t <prefix>`  → 把它当模板，缺 XXXXXX 直接 rc=1
  #                                       "mktemp: too few X's in template"
  # CI（ubuntu-latest）实证过后者：err_file 取不到 → set -e 掀掉整个函数 →
  # tests/briefing 8 条连带失败。本机 macOS 全绿测不出这个差异。
  tmp_base="${TMPDIR:-/tmp}"
  err_file="$(mktemp "${tmp_base%/}/akb2-backstop-read.XXXXXX")"
  VERDICT_ERR_FILE="$err_file"      # 供 EXIT trap 兜底清理（中途 set -e 退出时）
  while (( attempt <= read_budget )); do
    set +e
    out="$(read_frontmatter_verdict "$f" 2>"$err_file")"
    rc=$?
    set -e
    PUBLISHED_READ_ATTEMPTS="$attempt"
    if (( rc == 0 )); then
      PUBLISHED_READ_PATH="awk_primary"
      classify_verdict "$out"
      rm -f "$err_file"; VERDICT_ERR_FILE=""
      return 0
    fi
    # 折成单行并把内嵌双引号换成单引号：err="..." 这个字段必须保持可机读，
    # 而各家 awk 的报错文案差异很大（BSD 裸路径 / gawk 反引号 / mawk 双引号包路径）。
    PUBLISHED_READ_ERROR="$(tr '\n"' " '" < "$err_file" | sed -E 's/[[:space:]]+$//')"
    log "warn — 读取 audit_verdict 失败 read_path=awk_primary attempt=$attempt/$read_budget rc=$rc err=\"$PUBLISHED_READ_ERROR\" ($DATE)"
    attempt=$(( attempt + 1 ))
    if (( attempt <= read_budget )); then
      sleep "$VERDICT_READ_RETRY_SLEEP_SECONDS"
    fi
  done

  # 主读者全轮失败 → 换第二观测者。先落一次 `cat` 探针把**真实 errno** 记进
  # err_file（awk 的 can't open file 不带 strerror，光看它永远不知道是 ENOENT /
  # EIO / EACCES 哪一种），再让纯 bash 读者独立尝试一次。
  set +e
  cat "$f" >/dev/null 2>>"$err_file"
  probe_rc=$?
  native_out="$(read_frontmatter_verdict_native "$f" 2>>"$err_file")"
  native_rc=$?
  set -e
  if (( native_rc == 0 )); then
    PUBLISHED_READ_PATH="native_fallback"
    classify_verdict "$native_out"
    log "warn — awk 主读者 $read_budget 轮全失败，纯 bash 回退读者成功 read_path=native_fallback cat_rc=$probe_rc status=$PUBLISHED_STATUS ($DATE)"
    rm -f "$err_file"; VERDICT_ERR_FILE=""
    return 0
  fi
  # 两个读者都失败才判 unreadable。err= 必须同时带上非 awk 来源的证据
  # （bash 重定向的 strerror + cat 的退出码），否则这行日志无法定位真因。
  PUBLISHED_READ_ERROR="$(tr '\n"' " '" < "$err_file" | sed -E 's/[[:space:]]+$//') cat_rc=$probe_rc"
  PUBLISHED_READ_PATH="both_failed"
  PUBLISHED_STATUS="unreadable"
  rm -f "$err_file"; VERDICT_ERR_FILE=""
  return 0
}

# should_rerun <status> → 0=rerun(需重跑), 1=skip(已发布且pass)
# fail-closed（宪法 P3）：除 pass 之外的一切状态都保守重跑，包括读盘失败。
# **这条保留不动** —— 它是对的。错的是把同一个判据又拿去当终态（见
# terminal_state_for_status）：重跑决策可以保守，终态断言不可以。
should_rerun() {
  case "$1" in
    pass) return 1 ;;
    *)    return 0 ;;
  esac
}

# terminal_state_for_status <status> → failed | indeterminate （L3-2）
# 终态判定与重跑决策**解耦**：failed 断言"当天没有简报"，而 unreadable 恰恰不能
# 支持这个断言。两者必须可区分，这是后续接告警通道的前置条件。
terminal_state_for_status() {
  case "$1" in
    unreadable) printf 'indeterminate' ;;
    *)          printf 'failed' ;;
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
      log "rerun — 今日已发布但 audit_verdict=${PUBLISHED_VERDICT}，触发质量重跑 status=$status read_path=$PUBLISHED_READ_PATH ($DATE)" ;;
    unknown_verdict)
      log "rerun — 今日已发布但 audit_verdict=$PUBLISHED_VERDICT 非已知取值，保守重跑 status=$status read_path=$PUBLISHED_READ_PATH ($DATE)" ;;
    no_verdict)
      log "rerun — 今日文件可读但 frontmatter 无 audit_verdict 字段，保守重跑 status=$status read_path=$PUBLISHED_READ_PATH ($DATE)" ;;
    unreadable)
      log "rerun — 今日文件存在但双读者均读盘失败（awk ${PUBLISHED_READ_ATTEMPTS} 轮 + 纯 bash 回退）err=\"$PUBLISHED_READ_ERROR\"，保守重跑 status=$status read_path=$PUBLISHED_READ_PATH ($DATE)" ;;
    missing)
      log "rerun — 今日未发布，触发补跑 status=$status ($DATE)" ;;
  esac
else
  log_terminal skip "skip — 今日已发布且 audit_verdict=pass，无需补跑 status=$status read_path=$PUBLISHED_READ_PATH ($DATE)"
  exit 0
fi

# deadline 文案：DEADLINE_LABEL 供日志自证"12:xx 这个界是谁定的"
DEADLINE_LABEL="$(printf '%02d:%02d' "${AKB2_BRIEFING_DEADLINE_HOUR:-12}" "${AKB2_BRIEFING_DEADLINE_MINUTE:-0}")"

# 过 deadline 时的终态与人读标签由 status 单点决定（不是两处各写一遍 if）：
#   unreadable → indeterminate / INDETERMINATE（无法判定，需人工）
#   其余       → failed / FAILED（真的没交付）
GIVE_UP_TERMINAL="$(terminal_state_for_status "$status")"
if [[ "$GIVE_UP_TERMINAL" == "indeterminate" ]]; then
  GIVE_UP_LABEL="INDETERMINATE"
  GIVE_UP_DETAIL="；当日文件存在但双读者均失败，无法判定是否已交付（需人工核实）err=\"$PUBLISHED_READ_ERROR\""
else
  GIVE_UP_LABEL="FAILED"
  GIVE_UP_DETAIL=""
fi

# 守卫 ②：主跑进程仍在运行 → 让它先跑完，本次跳过
#
# 探针模式做成具名 + 可覆写：`pgrep -f` 是**全机器范围**的模糊匹配，同机器上任何一条
# 命令行里出现 `akb2.briefing.pipeline` 都会让本守卫命中。这对生产是正确行为（就是要
# 让路），但对测试是不可控外因 —— 实测并行开发时另一条链路跑
# `python3 -m akb2.briefing.pipeline` 就让本脚本的用例集体走进 skip 分支。
# 测试把它指向一个必然不命中的模式来取得确定性；生产不设该变量，行为不变。
readonly MAIN_RUN_PGREP_PATTERN="${AKB2_BACKSTOP_PGREP_PATTERN:-akb2.briefing.pipeline}"
if pgrep -f "$MAIN_RUN_PGREP_PATTERN" >/dev/null 2>&1; then
  if deadline_missed; then
    log_terminal "$GIVE_UP_TERMINAL" \
      "$GIVE_UP_LABEL — 已超过 $DEADLINE_LABEL deadline，主跑仍在进行，不能继续静默等待${GIVE_UP_DETAIL} status=$status read_path=$PUBLISHED_READ_PATH reason=deadline_missed exit_code=$GIVE_UP_EXIT_CODE ($DATE)"
    exit "$GIVE_UP_EXIT_CODE"
  fi
  log_terminal skip "skip — 主跑仍在进行，本次不抢 status=$status ($DATE)"
  exit 0
fi

if deadline_missed; then
  # 这里的 status 可能是 missing / warn_or_fail / no_verdict / unreadable，一律照实报：
  #   · 不得像旧实现那样在文件已存在时仍然谎报"今日未发布"；
  #   · 也不得像旧实现那样把 unreadable 一路断言成 failed（38 天假 FAILED 的唯一来源）。
  log_terminal "$GIVE_UP_TERMINAL" \
    "$GIVE_UP_LABEL — 已超过 $DEADLINE_LABEL deadline，不再启动补跑${GIVE_UP_DETAIL} status=$status read_path=$PUBLISHED_READ_PATH reason=deadline_missed exit_code=$GIVE_UP_EXIT_CODE ($DATE)"
  exit "$GIVE_UP_EXIT_CODE"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  log_terminal dry_run "dry-run — 当日简报未达 pass，将会触发补跑（实际未执行）status=$status ($DATE)"
  exit 0
fi

# ── 补跑互斥锁（L3-4）──────────────────────────────────────────────────────
# 为什么 pgrep 结构性救不了双跑：social 主跑调度 07:30、backstop 08:00，间隔 30
# 分钟 > 主跑实际耗时（历史 8–16 分钟）—— 08:00 时 pgrep **必然扑空**。这正是
# 38 天双跑（06-09~06-18、07-01~07-26、07-29~07-30）的成型条件。pgrep 守卫保留
# （便宜的早退），但真正的互斥必须落在锁上。另有两处 TOCTOU 也只有锁能收：
#   pgrep 判定 → 启动 pipeline 之间无原子性；`[[ -f ]]` → 读文件之间可被替换。
#
# 锁实现直接复用 scripts/akb2-main-pipeline.sh 已验证的范式（PID 文件 +
# `kill -0` 存活校验 + 陈旧锁清理）—— 宪法「约定优于新奇」，不引入第二种锁。
#
# 锁**位置**上刻意偏离 lane 计划里写的 `staging/locks/`，理由是仓库已经踩过并修好
# 这个坑：REPO_ROOT 在 ~/AI-Agent（Syncthing folder，`.stfolder` 实证）内，锁放仓库
# 会被跨机同步 → 两台机器互看/互删对方的 PID 文件，`kill -0` 对另一台的 PID 毫无
# 意义（akb2-main-pipeline.sh:26-30 已把锁从 staging/locks 迁到机器本地状态根）。
# 把 backstop 的锁放回 staging/locks 等于重新引入那个已修的缺陷。
# STATE_ROOT 解析复用 scripts/lib/state-root.sh（shell 侧唯一实现，与 python 侧
# state_paths.get_state_root() 有 parity 断言）。
# shellcheck source=scripts/lib/state-root.sh
. "$SCRIPT_DIR/lib/state-root.sh"
akb2_resolve_state_root
LOCK_FILE="${AKB2_BACKSTOP_LOCK_FILE:-$STATE_ROOT/locks/briefing-backstop.lock}"
if akb2_state_root_is_synced "$(dirname "$LOCK_FILE")"; then
  # 不 fail-closed 退出：拒跑等于把一次可能成功的补跑直接判死，比锁降级更糟。
  # 但必须明示降级（宪法 P4 失败可见），否则"为什么又双跑了"查不出来。
  log "warn — 锁目录在跨机同步范围内，互斥保证降级 lock_sync_risk=1 lock_file=$LOCK_FILE ($DATE)"
fi

LOCK_PID_HELD_BY=""
LOCK_SETUP_ERROR=""
# 返回值三态（不是两态）：0=拿到锁 / 1=别人持锁 / 2=**建不了锁**。
# 建不了锁必须与「别人持锁」分开：后者是正常让路（exit 0），前者是**无法建立互斥**，
# 此时继续跑就是以互斥语义启动一次付费补跑而实际上没有互斥（宪法 P3 fail-closed）。
acquire_rerun_lock() {
  local lock_pid lock_dir
  lock_dir="$(dirname "$LOCK_FILE")"
  if ! mkdir -p "$lock_dir" 2>/dev/null; then
    LOCK_SETUP_ERROR="锁目录不可创建: $lock_dir"
    return 2
  fi
  if [[ -f "$LOCK_FILE" ]]; then
    lock_pid="$(cat "$LOCK_FILE" 2>/dev/null || echo '')"
    if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" 2>/dev/null; then
      LOCK_PID_HELD_BY="$lock_pid"
      return 1
    fi
    rm -f "$LOCK_FILE"      # 陈旧锁：持有者已死，接管
  fi
  if ! echo $$ > "$LOCK_FILE" 2>/dev/null; then
    LOCK_SETUP_ERROR="锁文件不可写: $LOCK_FILE"
    return 2
  fi
  LOCK_HELD=1
  return 0
}

set +e
acquire_rerun_lock
lock_rc=$?
set -e
if (( lock_rc == 2 )); then
  # fail-closed：无法建立机器本地互斥 → 不启动付费补跑。裸跑的后果是双跑烧双份额度
  # （38 天双跑事故的成型条件），而"少跑一次"可由下一个 backstop 时段兜。
  log_terminal failed \
    "FAILED — 无法建立补跑互斥锁（${LOCK_SETUP_ERROR}），拒绝以互斥语义启动付费补跑 lock_setup=failed status=$status exit_code=1 ($DATE)"
  exit 1
fi
if (( lock_rc != 0 )); then
  log_terminal skip "skip — already-running: 另一个 backstop 补跑正持锁 lock_pid=$LOCK_PID_HELD_BY lock_file=$LOCK_FILE status=$status ($DATE)"
  exit 0
fi

# is_nonretryable_rc <rc> → 0=该码换族救不了(别重试), 1=可以再试一轮
is_nonretryable_rc() {
  local code
  for code in $RERUN_NONRETRYABLE_EXIT_CODES; do
    [[ "$1" == "$code" ]] && return 0
  done
  return 1
}

# ── 补跑循环（有界 + 每轮换族 + 每轮前查 deadline）─────────────────────────
attempt=1
rc=0
while (( attempt <= RERUN_ATTEMPT_BUDGET )); do
  # 每轮开始前查 deadline：补跑本身要耗时，不能开一轮就把运行时间推过界（宪法 P1）
  if deadline_missed; then
    log_terminal give_up \
      "GIVE-UP — 已超过 $DEADLINE_LABEL deadline，停止补跑重试 attempt=$attempt/$RERUN_ATTEMPT_BUDGET status=$status reason=deadline_missed exit_code=$GIVE_UP_EXIT_CODE ($DATE)"
    exit "$GIVE_UP_EXIT_CODE"
  fi
  # 第 2 轮起换模型族（COMMON.md 契约 1，消费端由 synthesis 实现）。
  # 同族已被证明连续 6 次 429 —— 换的必须是**族**，不是次数。
  family_fallback=0
  (( attempt >= 2 )) && family_fallback=1
  log "run — 当日简报未达 pass，开始补跑 attempt=$attempt/$RERUN_ATTEMPT_BUDGET status=$status llm_family_fallback=$family_fallback ($DATE)"
  set +e
  AKB2_LLM_FAMILY_FALLBACK="$family_fallback" \
    bash "$RERUN_SCRIPT" --date "$DATE" --vault-root "$VAULT_ROOT"
  rc=$?
  set -e
  # recovered 必须由**产物状态**证明，不得只凭子进程退出码（抄 github 侧 SC-5 的形状）。
  # social 侧的正确判据不是"文件存在"而是 `resolve_published_state` 回到 pass ——
  # 触发补跑的条件本来就包含 warn_or_fail / no_verdict / unreadable，
  # 光判文件存在会把"重跑完还是 audit fail"也叫成 recovered。
  # 本文件头部花了 8 行论证「假阳性降到 0 之前不能接告警通道」，指的是假 FAILED；
  # 这里关掉的是同族的**假 recovered**，而 terminal= 正是告警通道要读的那个键。
  if (( rc == 0 )); then
    resolve_published_state "$SOCIAL_FILE"
    RERUN_POST_STATUS="$PUBLISHED_STATUS"
    if [[ "$RERUN_POST_STATUS" == "pass" ]]; then
      break
    fi
    # rc=0 却没达 pass = pipeline 的「未交付即非零」不变式被破，属真异常，值得再试一轮
    log "warn — 补跑 rc=0 但产物未达 pass（假成功，pipeline 不变式被破）post_status=$RERUN_POST_STATUS read_path=$PUBLISHED_READ_PATH attempt=$attempt/$RERUN_ATTEMPT_BUDGET ($DATE)"
  elif is_nonretryable_rc "$rc"; then
    log "warn — 补跑未成功 attempt=$attempt/$RERUN_ATTEMPT_BUDGET rc=$rc rerun_stop=nonretryable_exit（该退出码换模型族救不了，不再重试）($DATE)"
    break
  else
    log "warn — 补跑未成功 attempt=$attempt/$RERUN_ATTEMPT_BUDGET rc=$rc ($DATE)"
  fi
  attempt=$(( attempt + 1 ))
  if (( attempt <= RERUN_ATTEMPT_BUDGET )); then
    backoff=$(( RERUN_BACKOFF_SECONDS * (2 ** (attempt - 2)) ))
    if deadline_missed_after "$backoff"; then
      log_terminal give_up \
        "GIVE-UP — 退避 ${backoff}s 之后即已超过 $DEADLINE_LABEL deadline，不再空等（白睡且占着补跑锁）next_attempt=$attempt/$RERUN_ATTEMPT_BUDGET reason=deadline_would_pass exit_code=$GIVE_UP_EXIT_CODE ($DATE)"
      exit "$GIVE_UP_EXIT_CODE"
    fi
    log "info — 退避 ${backoff}s 后换模型族重试 next_attempt=$attempt/$RERUN_ATTEMPT_BUDGET ($DATE)"
    sleep "$backoff"
  fi
done

attempts_used=$(( attempt > RERUN_ATTEMPT_BUDGET ? RERUN_ATTEMPT_BUDGET : attempt ))

# 假 recovered 闸：rc=0 但产物没回到 pass → 终态**不得**是 recovered。
# 退出码仍用 1 而不另开码位：pipeline.py 的 EXIT_* 注释已论证过「码位不该按分支扩散，
# 分支语义由日志/诊断字段承载」；这里靠 reason=false_recovery 让归因自证。
if (( rc == 0 )) && [[ "$RERUN_POST_STATUS" != "pass" ]]; then
  log_terminal failed \
    "FAILED — 补跑子进程 exit 0 但产物未达 pass，拒绝报 recovered attempt=$attempts_used/$RERUN_ATTEMPT_BUDGET post_status=$RERUN_POST_STATUS read_path=$PUBLISHED_READ_PATH reason=false_recovery exit_code=1 ($DATE)"
  exit 1
fi
# 文案与 src/akb2/briefing/pipeline.py 的 EXIT_* 常量逐条对齐。旧实现把 1 一律叫
# "gate reject"（实际 1 还覆盖上游硬失败与 vault 不可用），并把 3/4/5 一起丢进
# "异常" —— 它们是 pipeline **已定义**的结局，不是异常。误标的归因会把排查带偏
# （宪法 P4：失败要可见，且不得误导）。
case "$rc" in
  0)  log_terminal recovered "recovered — 重跑成功已入库 attempt=$attempts_used/$RERUN_ATTEMPT_BUDGET (exit 0, $DATE)" ;;
  1)  log_terminal failed "FAILED — 重跑 exit 1 (未交付：上游硬失败 / gate reject / vault 不可用)，当日无简报 attempt=$attempts_used/$RERUN_ATTEMPT_BUDGET exit_code=1 ($DATE)" ;;
  2)  log_terminal failed "FAILED — 重跑 exit 2 (audit 判 fail，产出留 staging 未入库)，当日无简报 attempt=$attempts_used/$RERUN_ATTEMPT_BUDGET exit_code=2 ($DATE)" ;;
  3)  log_terminal failed "FAILED — 重跑 exit 3 (intake 降级到测试 fixture，硬阻断)，当日无简报 attempt=$attempts_used/$RERUN_ATTEMPT_BUDGET exit_code=3 ($DATE)" ;;
  4)  log_terminal failed "FAILED — 重跑 exit 4 (gate 在最终文件上不收敛)，当日无简报 attempt=$attempts_used/$RERUN_ATTEMPT_BUDGET exit_code=4 ($DATE)" ;;
  5)  log_terminal failed "FAILED — 重跑 exit 5 (跨日去重账本不可用，不敢放行)，当日无简报 attempt=$attempts_used/$RERUN_ATTEMPT_BUDGET exit_code=5 ($DATE)" ;;
  "$DEADLINE_EXIT_CODE")
      log_terminal failed "FAILED — 重跑被 $DEADLINE_LABEL deadline 终止 (run_with_deadline)，当日无简报 attempt=$attempts_used/$RERUN_ATTEMPT_BUDGET reason=deadline_missed exit_code=$DEADLINE_EXIT_CODE ($DATE)" ;;
  *)  log_terminal failed "FAILED — 重跑 exit $rc (未知码，不在 pipeline 已定义集合 0-5/124 内)，当日无简报 attempt=$attempts_used/$RERUN_ATTEMPT_BUDGET exit_code=$rc ($DATE)" ;;
esac
exit "$rc"
