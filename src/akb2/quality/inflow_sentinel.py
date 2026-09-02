"""inflow_sentinel.py — 入库断流哨兵(只读账本 → 判定 → 飞书告警)。

背景(2026-07-24 → 07-30 生产事故):
    主质量管线连续 6 天零入库。上游 LLM API 报 400(`response_format type is
    unavailable`),准入四判定全部"调用失败",被账本记成 `admission failed` →
    `abort`——**故障伪装成质量拒绝**。管线自身各段都"正常"(有进料、有判定、有终态),
    没有任何一段的 fail-closed 会被触发,于是 6 天里零告警,最后靠人工询问才发现。

    结论:段内门控看不见"整条链路的产出为零"这件事。需要一条**独立于管线**的
    断流哨兵——只看账本的进出比,不关心每篇文章为什么被拒。

判定(二值,无连续分;宪法 P8):
    窗口 = 近 `--window-hours` 小时(默认 `DEFAULT_WINDOW_HOURS`)。

    | intake_raw | real finalize | verdict | 含义                              |
    |------------|--------------|-------------|---------------------------------------|
    | > 0        | == 0         | `stalled`   | **有进无出** = 入库断流(主告警)        |
    | == 0       | == 0         | `no_intake` | 全链零事件 = 采集停摆或 runner 死(降级)|
    | 任意       | > 0         | `healthy`   | 有真实入库,不告警                    |

    "有进无出才算断流"是刻意的:零 intake_raw 是**另一种**故障,排查现场不同——
    且 intake_raw 事件是 runner 落账的(runner 从 raw/ 取件时写),所以零 intake_raw
    既可能是上游采集停了、也可能是 runner daemon 死了,降级文案必须把两个现场都给出。

告警出口:
    复用 `feishu_sync.feishu.send.send_text_dm`(自带 9499/429 退避重试 + 凭证走
    `feishu_sync.config` 的 `~/.akb2-secrets.sh` env 机制)。**不复用**
    `feishu_sync.notify.alert_failure`:它按真因分级标题，默认前缀是"简报飞书同步失败",
    套在入库断流上属于错误归因(宪法 P4 要求失败可见,不是失败可见但说错地方)。
    飞书本身不可达时降级到 macOS 本地通知 + stderr + 退出码 1(不静默)。

幂等抑制 + 有界重提:
    "同一次断流"的身份 = `verdict + 参照事件时刻 + 窗口长度`(`episode_key`):
    `stalled` 取账本里**最后一次真实 finalize** 的时刻(= 流水线上次正常的时刻),
    `no_intake` 取最后一次 intake_raw 的时刻。只要没有新的真实 finalize 落账,就还是
    同一次断流;一旦恢复入库再断,参照时刻变了 → 算新的一次。

    同一次断流内的发送节奏(有界,宪法 P1):

        首报 1 条 → 每满 `REALERT_INTERVAL_HOURS` 重提 1 条 → 最多重提
        `REALERT_MAX` 次 → 封顶后彻底静默,直到本次断流结束(恢复入库或 verdict 切换)。

    为什么要重提:本次事故持续 7 天。只发一条的话,那条被漏看就退回静默——重提是
    哨兵存在意义的一部分。为什么要封顶:无上限重发就是告警轰炸,人会直接屏蔽通道。

    状态文件经 `common.state_paths` 落机器本地状态根(**不落 Syncthing 同步区**,
    否则两台机器互相覆盖抑制状态 → 要么重复轰炸要么双双静默),记录本次断流的
    首报时刻、上次发送时刻与已重提次数。

    `--force` 绕过抑制强制发送,但**不消耗重提配额、也不推移重提节奏**(它是人工
    验证通路用的,不该改变自动告警的时序)。

用法::

    # 只看判定不发送(排查/验收用)
    python3 -m akb2.quality.inflow_sentinel --dry-run

    # 真发送(launchd 每日 2 次;plist 在 launchd/com.akb2.inflow-sentinel.plist)
    python3 -m akb2.quality.inflow_sentinel

    # 自定义窗口 / 指定账本快照 / 忽略抑制强制重发
    python3 -m akb2.quality.inflow_sentinel --window-hours 72 --ledger /path/to.jsonl --force

退出码:
    0 = 检查完成(健康 / 已告警 / 已抑制 / dry-run)
    1 = 检查无法完成(账本不可读、状态根不可用)或告警发送失败(fail-visible)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 具名常量(宪法 P4:预算/阈值不得裸字面量)
# ---------------------------------------------------------------------------

# 判定窗口默认长度(小时)。48h 覆盖"周末无人值守 + 一个完整 intake 周期"。
DEFAULT_WINDOW_HOURS: int = 48

# 抑制状态文件(落机器本地状态根,不进同步区)
STATE_FILE_NAME: str = "inflow-sentinel-state.json"

# 状态文件里保留的历史告警条数上限(有界,防无限增长)
MAX_STATE_HISTORY: int = 20

# 同一次断流内两次发送的最小间隔(小时)。首报之后每满这个间隔重提一次。
REALERT_INTERVAL_HOURS: int = 72

# 同一次断流内最多**重提**次数(不含首报)。总发送量上限 = 1 + REALERT_MAX。
# 有界(宪法 P1):封顶后彻底静默,直到本次断流结束(恢复入库或 verdict 切换)。
REALERT_MAX: int = 3

# 发送决策类型
ALERT_FIRST: str = "first"
ALERT_REALERT: str = "realert"
ALERT_FORCED: str = "forced"
SUPPRESS_INTERVAL: str = "suppressed_interval"
SUPPRESS_MAX: str = "suppressed_max"

_SENDING_KINDS: frozenset[str] = frozenset({ALERT_FIRST, ALERT_REALERT, ALERT_FORCED})

# 告警文案里最多列出的中止原因种类数
MAX_ABORT_REASONS_REPORTED: int = 3

# 单条中止原因在文案里的截断长度(飞书文本消息不宜过长)
ABORT_REASON_MAX_CHARS: int = 120

# 账本损坏行最多列出的行号数
MAX_CORRUPT_LINES_REPORTED: int = 5

# macOS 本地通知超时(秒)
LOCAL_NOTIFY_TIMEOUT_SEC: int = 10

# 判定枚举
VERDICT_STALLED: str = "stalled"
VERDICT_NO_INTAKE: str = "no_intake"
VERDICT_HEALTHY: str = "healthy"

# 参与判定的两个事件(其余事件只作诊断上下文)。finalize 需为真实升格；
# loop2 shadow 的 finalize 仅是终态记账，不计入入库。
EVENT_INTAKE: str = "intake_raw"
EVENT_FINALIZE: str = "finalize"
EVENT_ABORT: str = "abort"


def _counts_as_real_finalize(entry: dict[str, Any]) -> bool:
    """finalize 是否代表真实写入 wiki。

    loop2 影子跑仍写终态 finalize，避免 raw 被重处理；
    details.mode=shadow 表示没写 active 页，不得把断流判成 healthy。
    """
    details = entry.get("details")
    return not (isinstance(details, dict) and details.get("mode") == "shadow")

# 账本 ts 的规范格式(runner._log_event 写入格式)
_TS_FORMAT: str = "%Y-%m-%dT%H:%M:%SZ"


# ---------------------------------------------------------------------------
# 判定结果
# ---------------------------------------------------------------------------


@dataclass
class InflowVerdict:
    """一次断流判定的完整结果(可直接序列化,便于 dry-run 打印与测试断言)。"""

    verdict: str
    window_hours: int
    window_start: datetime
    now: datetime
    # 窗口内各事件计数(event 名 → 条数)
    window_counts: dict[str, int] = field(default_factory=dict)
    # 全账本范围内最后一次 finalize / intake_raw 的时刻(None = 从未发生)
    last_finalize_ts: datetime | None = None
    last_intake_ts: datetime | None = None
    # 窗口内中止原因 top-N(reason, 次数)
    top_abort_reasons: list[tuple[str, int]] = field(default_factory=list)
    # 数据质量:损坏行 / ts 不可解析行 / ts 在未来的行(计入告警文案,不静默)
    corrupt_lines: int = 0
    unparseable_ts: int = 0
    future_ts: int = 0

    @property
    def intake_count(self) -> int:
        return self.window_counts.get(EVENT_INTAKE, 0)

    @property
    def finalize_count(self) -> int:
        return self.window_counts.get(EVENT_FINALIZE, 0)

    @property
    def is_alerting(self) -> bool:
        """本判定是否需要告警(healthy 之外都要)。"""
        return self.verdict != VERDICT_HEALTHY

    @property
    def episode_key(self) -> str:
        """本次断流"窗口"的幂等身份(见模块 docstring 的幂等抑制段)。"""
        if self.verdict == VERDICT_STALLED:
            anchor = _fmt_ts(self.last_finalize_ts) if self.last_finalize_ts else "never"
        elif self.verdict == VERDICT_NO_INTAKE:
            anchor = _fmt_ts(self.last_intake_ts) if self.last_intake_ts else "never"
        else:
            anchor = "n/a"
        return f"{self.verdict}:{anchor}:w{self.window_hours}"


# ---------------------------------------------------------------------------
# 时间与解析
# ---------------------------------------------------------------------------


def _fmt_ts(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime(_TS_FORMAT)


def _parse_ts(raw: object) -> datetime | None:
    """解析账本 ts。返回 None 表示不可解析(调用方必须计数,不得静默丢弃)。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        return datetime.strptime(text, _TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        pass
    # 容错:schema 只要求 ISO-8601 date-time,别的写入者可能带偏移或毫秒
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _abort_reason(entry: dict) -> str:
    """从 abort 事件里取人类可读的中止原因(缺失时给占位,不抛)。"""
    details = entry.get("details")
    if isinstance(details, dict):
        for key in ("reason", "lane_reason", "error"):
            value = details.get(key)
            if isinstance(value, str) and value.strip():
                text = " ".join(value.split())
                if len(text) > ABORT_REASON_MAX_CHARS:
                    text = text[:ABORT_REASON_MAX_CHARS] + "…"
                return text
    return "(未记录原因)"


# ---------------------------------------------------------------------------
# 核心判定(纯函数:只读账本,无副作用,便于测试)
# ---------------------------------------------------------------------------


def evaluate_ledger(
    ledger_path: str | Path,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    now: datetime | None = None,
    loop_id: str | None = None,
) -> InflowVerdict:
    """流式扫账本,产出断流判定。

    Args:
        ledger_path: quality-ledger.jsonl 路径(缺失视为"无任何事件")。
        window_hours: 判定窗口长度,必须 > 0。
        now: 判定基准时刻(默认 UTC 当下);测试注入用。

    Returns:
        InflowVerdict。窗口边界为**闭区间左端**:`ts >= now - window_hours` 计入。

    Raises:
        ValueError: window_hours <= 0(不接受空窗口——那会让哨兵永远判"无进料")。
        OSError: 账本存在但读取失败(fail-visible,不当成"没有事件")。
    """
    if window_hours <= 0:
        raise ValueError(f"--window-hours 必须为正整数,收到 {window_hours}")

    now = (now or datetime.now(UTC)).astimezone(UTC)
    window_start = now - timedelta(hours=window_hours)

    window_counts: Counter[str] = Counter()
    abort_reasons: Counter[str] = Counter()
    last_finalize_ts: datetime | None = None
    last_intake_ts: datetime | None = None
    corrupt: list[int] = []
    unparseable_ts = 0
    future_ts = 0

    path = Path(ledger_path)
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    corrupt.append(lineno)
                    continue
                if not isinstance(entry, dict):
                    corrupt.append(lineno)
                    continue

                event = entry.get("event")
                if loop_id is not None and entry.get("loop_id") != loop_id:
                    continue
                ts = _parse_ts(entry.get("ts"))
                if ts is None:
                    unparseable_ts += 1
                    continue

                if ts > now:
                    # 时钟偏移/手工编辑产生的未来事件:窗口是 [now-W, now],未来不在其中。
                    # 若把它算进窗口,一条偏移到未来的 finalize 就能把真断流盖成
                    # "healthy" —— 正是本哨兵要防的静默失败。计数后排除,不静默。
                    future_ts += 1
                    continue

                # 全账本范围:记录最后一次真实 finalize / intake(用于 episode_key 与文案)
                if event == EVENT_FINALIZE and _counts_as_real_finalize(entry) and (
                    last_finalize_ts is None or ts > last_finalize_ts
                ):
                    last_finalize_ts = ts
                if event == EVENT_INTAKE and (last_intake_ts is None or ts > last_intake_ts):
                    last_intake_ts = ts

                if ts < window_start:
                    continue
                if isinstance(event, str) and not (
                    event == EVENT_FINALIZE and not _counts_as_real_finalize(entry)
                ):
                    window_counts[event] += 1
                    if event == EVENT_ABORT:
                        abort_reasons[_abort_reason(entry)] += 1

    if corrupt:
        shown = ", ".join(str(n) for n in corrupt[:MAX_CORRUPT_LINES_REPORTED])
        more = "" if len(corrupt) <= MAX_CORRUPT_LINES_REPORTED else " ..."
        print(
            f"[inflow_sentinel] 警告: {path} 有 {len(corrupt)} 行 JSON 损坏"
            f"(行号 {shown}{more});这些行未参与断流判定",
            file=sys.stderr,
        )
    if unparseable_ts:
        print(
            f"[inflow_sentinel] 警告: {path} 有 {unparseable_ts} 行 ts 不可解析,"
            "未参与断流判定(判定可能偏保守)",
            file=sys.stderr,
        )
    if future_ts:
        print(
            f"[inflow_sentinel] 警告: {path} 有 {future_ts} 行 ts 在 {_fmt_ts(now)} 之后"
            "(疑似时钟偏移),已排除在窗口外",
            file=sys.stderr,
        )

    intake = window_counts.get(EVENT_INTAKE, 0)
    finalize = window_counts.get(EVENT_FINALIZE, 0)
    if finalize > 0:
        verdict = VERDICT_HEALTHY
    elif intake > 0:
        verdict = VERDICT_STALLED
    else:
        verdict = VERDICT_NO_INTAKE

    return InflowVerdict(
        verdict=verdict,
        window_hours=window_hours,
        window_start=window_start,
        now=now,
        window_counts=dict(window_counts),
        last_finalize_ts=last_finalize_ts,
        last_intake_ts=last_intake_ts,
        top_abort_reasons=abort_reasons.most_common(MAX_ABORT_REASONS_REPORTED),
        corrupt_lines=len(corrupt),
        unparseable_ts=unparseable_ts,
        future_ts=future_ts,
    )


# ---------------------------------------------------------------------------
# 告警文案
# ---------------------------------------------------------------------------


def _age_hours(verdict: InflowVerdict, ts: datetime | None) -> str:
    """把时刻渲染成"绝对时刻(距今 Xh)"。

    未来时刻不渲染成负数("距今 -317.0h" 是没人能读的垃圾),明确标成时钟偏移。
    """
    if ts is None:
        return "从未发生"
    hours = (verdict.now - ts).total_seconds() / 3600
    if hours < 0:
        return f"{_fmt_ts(ts)}(在未来 {-hours:.1f}h,疑似时钟偏移)"
    return f"{_fmt_ts(ts)}(距今 {hours:.1f}h)"


def build_alert_text(
    verdict: InflowVerdict, decision: AlertDecision | None = None
) -> str:
    """生成告警正文。healthy 判定不该走到这里(调用方先查 is_alerting)。

    Args:
        verdict: 断流判定。
        decision: 发送决策;非首报时用于标注「第 N 次重提 / 首报时间」——收到第 3 条
            同样内容的告警时,不标注就无法判断是新故障还是同一次的重提。
    """
    if verdict.verdict == VERDICT_HEALTHY:
        raise ValueError("healthy 判定无告警文案(调用方应先检查 is_alerting)")

    counts = verdict.window_counts
    counts_line = (
        f"进料 intake_raw={counts.get(EVENT_INTAKE, 0)} / "
        f"入库 finalize={counts.get(EVENT_FINALIZE, 0)} / "
        f"中止 abort={counts.get(EVENT_ABORT, 0)}"
    )
    lines: list[str] = []

    if verdict.verdict == VERDICT_STALLED:
        lines.append("🚨 AKB2 入库断流告警")
        lines.append(f"近 {verdict.window_hours}h 有进料但零入库(有进无出)")
    else:
        # 措辞不能写成"账本零事件":本判定只要求 intake_raw==0 且 finalize==0,
        # 窗口内完全可能还有在飞的 repair/grade 事件。说成"零事件"是假陈述。
        lines.append("⚠️ AKB2 取件停滞告警")
        lines.append(
            f"近 {verdict.window_hours}h 无新进料且零入库"
            "(上游采集停摆 或 runner daemon 未在取件)"
        )

    if decision is not None and decision.kind == ALERT_REALERT:
        lines.append(
            f"提醒: 第 {decision.realert_index}/{REALERT_MAX} 次重提"
            f"(每 {REALERT_INTERVAL_HOURS}h 一次,满配额后静默)"
        )
        if decision.first_alerted_at is not None:
            lines.append(f"首报时间: {_age_hours(verdict, decision.first_alerted_at)}")
    elif decision is not None and decision.kind == ALERT_FORCED:
        lines.append("提醒: 手动强制重发(--force,不计入重提配额)")
        if decision.first_alerted_at is not None:
            lines.append(f"首报时间: {_age_hours(verdict, decision.first_alerted_at)}")

    lines.append(f"窗口: {_fmt_ts(verdict.window_start)} → {_fmt_ts(verdict.now)}")
    lines.append(counts_line)
    lines.append(f"上次入库: {_age_hours(verdict, verdict.last_finalize_ts)}")
    lines.append(f"上次进料: {_age_hours(verdict, verdict.last_intake_ts)}")

    if verdict.top_abort_reasons:
        lines.append("主要中止原因:")
        for idx, (reason, count) in enumerate(verdict.top_abort_reasons, start=1):
            lines.append(f"  {idx}. ×{count} {reason}")

    if verdict.corrupt_lines or verdict.unparseable_ts or verdict.future_ts:
        lines.append(
            f"账本数据质量: 损坏行 {verdict.corrupt_lines} / "
            f"ts 不可解析 {verdict.unparseable_ts} / "
            f"ts 在未来 {verdict.future_ts}(均未参与判定)"
        )

    if verdict.verdict == VERDICT_STALLED:
        lines.append("排查: tail -100 logs/akb2-quality-runner.log")
        lines.append("      launchctl print gui/$(id -u)/com.akb2.main-pipeline | head -30")
    else:
        # intake_raw 事件是 **runner** 落账的(runner 从 raw/ 取件时写),
        # 所以"零 intake_raw"有两种成因,必须把两个现场都给出来,不能只指 intake。
        lines.append("排查: tail -100 logs/intake.log      # 上游是否还在落 raw")
        lines.append("      tail -100 logs/akb2-quality-runner.log  # runner 是否还活着")
        lines.append("      launchctl list | grep -E 'com.akb2.(intake|main-pipeline)'")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 抑制状态
# ---------------------------------------------------------------------------


def resolve_state_path() -> Path:
    """抑制状态文件路径(机器本地状态根,fail-closed 不落同步区)。"""
    from akb2.common.state_paths import resolve_state_file

    return resolve_state_file(STATE_FILE_NAME)


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # 状态损坏不能静默当"从未告警"(会重复轰炸),也不能当"已告警"(会漏报)。
        # 选择:明示 + 按"从未告警"继续(漏报比漏告警更危险 → 宁可多发一次)。
        print(
            f"[inflow_sentinel] 警告: 抑制状态 {path} 不可读({exc});"
            "按「从未告警」继续,本轮可能重复告警",
            file=sys.stderr,
        )
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


@dataclass
class AlertDecision:
    """本轮该不该发、发的是第几条(见模块 docstring 的「幂等抑制 + 有界重提」)。"""

    kind: str
    # 第几次**重提**(1..REALERT_MAX);首报为 0
    realert_index: int
    # 本次断流的首报时刻(首报时为 None——此刻就是首报)
    first_alerted_at: datetime | None
    # 人类可读的决策理由(进日志,宪法 P4 决策可见)
    detail: str

    @property
    def should_send(self) -> bool:
        return self.kind in _SENDING_KINDS


def _prior_episode(state: dict, verdict: InflowVerdict) -> dict | None:
    """取"同一次断流"的上次告警记录;episode 不同或无记录返回 None。"""
    last = state.get("last_alert")
    if not isinstance(last, dict):
        return None
    return last if last.get("episode_key") == verdict.episode_key else None


def decide_alert(
    state: dict, verdict: InflowVerdict, *, force: bool = False
) -> AlertDecision:
    """决定本轮发送行为(纯函数,不改 state、不发送)。

    - 新的一次断流 → 首报。
    - 同一次断流内:距上次发送 >= REALERT_INTERVAL_HOURS 且重提未封顶 → 重提;
      否则抑制(间隔未到 / 已封顶)。
    - `force` → 强制发送,但不消耗重提配额、不推移节奏(见模块 docstring)。
    """
    prior = _prior_episode(state, verdict)
    if prior is None:
        # force 落在新 episode 上就是首报本身,按首报正常记账
        return AlertDecision(ALERT_FIRST, 0, None, "新的一次断流,首报")

    first_at = _parse_ts(prior.get("first_alerted_at"))
    raw_count = prior.get("realert_count")
    count = raw_count if isinstance(raw_count, int) and raw_count >= 0 else 0
    # 兼容早期状态格式(只有 alerted_at)：读不到 last_alerted_at 时回退
    last_at = _parse_ts(prior.get("last_alerted_at")) or _parse_ts(prior.get("alerted_at"))

    if force:
        return AlertDecision(
            ALERT_FORCED, count, first_at, "--force 强制重发(不消耗重提配额、不推移节奏)"
        )

    if count >= REALERT_MAX:
        return AlertDecision(
            SUPPRESS_MAX,
            count,
            first_at,
            f"已重提 {count}/{REALERT_MAX} 次并封顶,静默至本次断流结束",
        )

    if last_at is None:
        # 上次发送时刻不可解析 → 宁可多发一次也不漏报(与损坏状态同口径)
        return AlertDecision(
            ALERT_REALERT, count + 1, first_at, "上次发送时刻不可解析,按需重提"
        )

    elapsed_h = (verdict.now - last_at).total_seconds() / 3600
    if elapsed_h >= REALERT_INTERVAL_HOURS:
        return AlertDecision(
            ALERT_REALERT,
            count + 1,
            first_at,
            f"距上次告警 {elapsed_h:.1f}h ≥ {REALERT_INTERVAL_HOURS}h,第 "
            f"{count + 1}/{REALERT_MAX} 次重提",
        )
    return AlertDecision(
        SUPPRESS_INTERVAL,
        count,
        first_at,
        f"距上次告警仅 {elapsed_h:.1f}h < {REALERT_INTERVAL_HOURS}h,抑制",
    )


def record_alert(
    state: dict, verdict: InflowVerdict, decision: AlertDecision
) -> dict:
    """把本次发送写入状态(返回新 state,history 有界)。

    `ALERT_FORCED` 刻意**不动** `last_alerted_at` 与 `realert_count`——否则手动
    验证一次就会把自动重提的节奏往后推、或白吃掉一次配额。
    """
    prior = _prior_episode(state, verdict) or {}
    now_ts = _fmt_ts(verdict.now)

    if decision.kind == ALERT_FORCED:
        entry = dict(prior)
        forced = entry.get("forced_sends")
        entry["forced_sends"] = (forced if isinstance(forced, int) else 0) + 1
    else:
        first_at = (
            _fmt_ts(decision.first_alerted_at) if decision.first_alerted_at else now_ts
        )
        entry = {
            "episode_key": verdict.episode_key,
            "verdict": verdict.verdict,
            "window_hours": verdict.window_hours,
            "first_alerted_at": first_at,
            "last_alerted_at": now_ts,
            "realert_count": decision.realert_index,
            "forced_sends": prior.get("forced_sends", 0)
            if isinstance(prior.get("forced_sends"), int)
            else 0,
        }
    entry["intake_count"] = verdict.intake_count
    entry["finalize_count"] = verdict.finalize_count

    history = state.get("history")
    history = list(history) if isinstance(history, list) else []
    history.append(
        {
            "episode_key": verdict.episode_key,
            "kind": decision.kind,
            "realert_index": decision.realert_index,
            "sent_at": now_ts,
        }
    )
    return {"last_alert": entry, "history": history[-MAX_STATE_HISTORY:]}


# ---------------------------------------------------------------------------
# 告警发送
# ---------------------------------------------------------------------------


def _local_notify(text: str) -> None:
    """macOS 本地通知 —— 飞书不可达时的独立降级通道(best-effort)。

    标题用中性的"AKB2 断流告警":同一函数服务 stalled / no_intake 两种判定,
    写死"入库断流"会把上游采集停摆的告警贴错标签(归因错误比没归因更费时)。
    """
    body = text.replace('"', "'").splitlines()
    summary = " / ".join(body[:2])[:200]
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{summary}" with title "AKB2 断流告警"',
            ],
            check=False,
            timeout=LOCAL_NOTIFY_TIMEOUT_SEC,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def send_feishu_alert(text: str) -> None:
    """经 feishu_sync 现有通道发私信。失败抛异常(由调用方降级 + 退出码 1)。"""
    from akb2.feishu_sync import config
    from akb2.feishu_sync.feishu.send import send_text_dm

    send_text_dm(config.dm_open_id(), text)


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


def run_check(
    *,
    ledger_path: str | Path,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    dry_run: bool = False,
    force: bool = False,
    state_path: Path | None = None,
    now: datetime | None = None,
    sender=None,
    loop_id: str | None = None,
) -> int:
    """执行一次断流检查(判定 → 抑制 → 发送)。返回进程退出码。

    Args:
        ledger_path: quality-ledger.jsonl 路径。
        window_hours: 判定窗口。
        dry_run: 只打印文案,不发送、不读写抑制状态。
        force: 绕过抑制强制发送(手动验证用);不消耗重提配额、不推移重提节奏。
        state_path: 抑制状态文件;None → resolve_state_path()。
        now: 判定基准时刻(测试注入)。
        sender: 发送函数 `(text: str) -> None`;None → send_feishu_alert。

    Returns:
        0 检查完成;1 检查无法完成或发送失败(fail-visible)。
    """
    try:
        verdict = evaluate_ledger(
            ledger_path, window_hours=window_hours, now=now, loop_id=loop_id
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: 断流判定失败: {exc}", file=sys.stderr)
        return 1

    print(
        f"[inflow_sentinel] verdict={verdict.verdict} window={verdict.window_hours}h "
        f"intake_raw={verdict.intake_count} finalize={verdict.finalize_count} "
        f"ledger={ledger_path}"
    )

    if not verdict.is_alerting:
        print("[inflow_sentinel] 入库正常,不告警")
        return 0

    if dry_run:
        # dry-run 不读写抑制状态:它是排查工具,不该因"看一眼"就改变自动告警节奏。
        print("[inflow_sentinel] --dry-run:以下文案不会发送")
        print(build_alert_text(verdict))
        print(f"[inflow_sentinel] episode_key={verdict.episode_key}")
        return 0

    if state_path is None:
        try:
            state_path = resolve_state_path()
        except (RuntimeError, OSError) as exc:
            # fail-closed:状态根不可用时不退回同步目录(会让抑制状态跨机互相覆盖)
            print(f"ERROR: 无法解析机器本地状态根: {exc}", file=sys.stderr)
            return 1

    state = _load_state(state_path)
    decision = decide_alert(state, verdict, force=force)
    if not decision.should_send:
        print(
            f"[inflow_sentinel] 抑制本次发送:{decision.detail}"
            f"(episode_key={verdict.episode_key});--force 可强制重发"
        )
        return 0

    text = build_alert_text(verdict, decision)
    send = sender or send_feishu_alert
    try:
        send(text)
    except Exception as exc:  # noqa: BLE001 — 任何发送失败都要降级 + 可见,不吞
        print(
            f"ERROR: 飞书告警发送失败: {exc};已降级到本地通知(告警内容见下)",
            file=sys.stderr,
        )
        print(text, file=sys.stderr)
        _local_notify(text)
        return 1

    print(
        f"[inflow_sentinel] 已发送飞书告警(kind={decision.kind} "
        f"episode_key={verdict.episode_key}):{decision.detail}"
    )
    print(text)

    try:
        _save_state(state_path, record_alert(state, verdict, decision))
    except OSError as exc:
        # 告警已发出但抑制状态没落盘 → 下次会重复告警。明示,不静默。
        print(
            f"[inflow_sentinel] 警告: 抑制状态写入失败({exc});下次检查会重复告警",
            file=sys.stderr,
        )
    return 0


def _default_ledger_path() -> str:
    """默认账本路径(机器本地状态根 / ledgers / quality-ledger.jsonl)。"""
    from akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file

    # 不传 legacy_path:哨兵是只读的,不该有"继承旧账本"的复制副作用。
    return str(resolve_state_file("quality-ledger.jsonl", subdir=LEDGER_SUBDIR))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AKB2 入库断流哨兵(48h 零 finalize → 飞书告警)"
    )
    parser.add_argument(
        "--ledger",
        default=None,
        help="quality-ledger.jsonl 路径(默认经 common.state_paths 解析机器本地状态根)",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=DEFAULT_WINDOW_HOURS,
        help=f"判定窗口小时数(默认 {DEFAULT_WINDOW_HOURS})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印判定与文案,不发送、不写抑制状态"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="绕过抑制强制发送(手动验证用;不消耗重提配额、不推移重提节奏)",
    )
    parser.add_argument(
        "--state-file", default=None, help="抑制状态文件路径(默认经 common.state_paths 解析)"
    )
    parser.add_argument(
        "--loop-id",
        default=None,
        help="只统计该 loop_id 的账本事件(默认全账本；loop2 影子跑传 loop2)",
    )
    args = parser.parse_args(argv)

    ledger_path = args.ledger
    if not ledger_path:
        try:
            ledger_path = _default_ledger_path()
        except (RuntimeError, OSError) as exc:
            print(f"ERROR: 无法解析账本路径: {exc}", file=sys.stderr)
            return 1

    return run_check(
        ledger_path=ledger_path,
        window_hours=args.window_hours,
        dry_run=args.dry_run,
        force=args.force,
        state_path=Path(args.state_file) if args.state_file else None,
        loop_id=args.loop_id,
    )


if __name__ == "__main__":
    sys.exit(main())
