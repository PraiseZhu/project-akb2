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

    | intake_raw | finalize | verdict     | 含义                                  |
    |------------|----------|-------------|---------------------------------------|
    | > 0        | == 0     | `stalled`   | **有进无出** = 入库断流(主告警)        |
    | == 0       | == 0     | `no_intake` | 全链零事件 = 采集停摆或 runner 死(降级)|
    | 任意       | > 0      | `healthy`   | 有入库,不告警                         |

    "有进无出才算断流"是刻意的:零 intake_raw 是**另一种**故障,排查现场不同——
    且 intake_raw 事件是 runner 落账的(runner 从 raw/ 取件时写),所以零 intake_raw
    既可能是上游采集停了、也可能是 runner daemon 死了,降级文案必须把两个现场都给出。

告警出口:
    复用 `feishu_sync.feishu.send.send_text_dm`(自带 9499/429 退避重试 + 凭证走
    `feishu_sync.config` 的 `~/.akb2-secrets.sh` env 机制)。**不复用**
    `feishu_sync.notify.alert_failure`:它的固定标题/前缀是"简报飞书同步失败",
    套在入库断流上属于错误归因(宪法 P4 要求失败可见,不是失败可见但说错地方)。
    飞书本身不可达时降级到 macOS 本地通知 + stderr + 退出码 1(不静默)。

幂等抑制:
    同一断流窗口只告警一次。"同一窗口"的身份 = `verdict + 参照事件时刻 + 窗口长度`
    (`episode_key`):`stalled` 取账本里**最后一次 finalize** 的时刻(= 流水线上次
    正常的时刻),`no_intake` 取最后一次 intake_raw 的时刻。只要没有新的 finalize
    落账,就还是同一次断流 → 抑制;一旦恢复入库再断,参照时刻变了 → 重新告警。
    状态文件经 `common.state_paths` 落机器本地状态根(**不落 Syncthing 同步区**,
    否则两台机器互相覆盖抑制状态 → 要么重复轰炸要么双双静默)。

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

# ---------------------------------------------------------------------------
# 具名常量(宪法 P4:预算/阈值不得裸字面量)
# ---------------------------------------------------------------------------

# 判定窗口默认长度(小时)。48h 覆盖"周末无人值守 + 一个完整 intake 周期"。
DEFAULT_WINDOW_HOURS: int = 48

# 抑制状态文件(落机器本地状态根,不进同步区)
STATE_FILE_NAME: str = "inflow-sentinel-state.json"

# 状态文件里保留的历史告警条数上限(有界,防无限增长)
MAX_STATE_HISTORY: int = 20

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

# 参与判定的两个事件(其余事件只作诊断上下文)
EVENT_INTAKE: str = "intake_raw"
EVENT_FINALIZE: str = "finalize"
EVENT_ABORT: str = "abort"

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
    # 数据质量:损坏行 / ts 不可解析行(计入告警文案,不静默)
    corrupt_lines: int = 0
    unparseable_ts: int = 0

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
                ts = _parse_ts(entry.get("ts"))
                if ts is None:
                    unparseable_ts += 1
                    continue

                # 全账本范围:记录最后一次 finalize / intake(用于 episode_key 与文案)
                if event == EVENT_FINALIZE and (
                    last_finalize_ts is None or ts > last_finalize_ts
                ):
                    last_finalize_ts = ts
                if event == EVENT_INTAKE and (last_intake_ts is None or ts > last_intake_ts):
                    last_intake_ts = ts

                if ts < window_start:
                    continue
                if isinstance(event, str):
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
    )


# ---------------------------------------------------------------------------
# 告警文案
# ---------------------------------------------------------------------------


def _age_hours(verdict: InflowVerdict, ts: datetime | None) -> str:
    if ts is None:
        return "从未发生"
    hours = (verdict.now - ts).total_seconds() / 3600
    return f"{_fmt_ts(ts)}(距今 {hours:.1f}h)"


def build_alert_text(verdict: InflowVerdict) -> str:
    """生成告警正文。healthy 判定不该走到这里(调用方先查 is_alerting)。"""
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
        lines.append("⚠️ AKB2 全链无活动告警")
        lines.append(
            f"近 {verdict.window_hours}h 账本零事件"
            "(上游采集停摆 或 runner daemon 未在消化队列)"
        )

    lines.append(f"窗口: {_fmt_ts(verdict.window_start)} → {_fmt_ts(verdict.now)}")
    lines.append(counts_line)
    lines.append(f"上次入库: {_age_hours(verdict, verdict.last_finalize_ts)}")
    lines.append(f"上次进料: {_age_hours(verdict, verdict.last_intake_ts)}")

    if verdict.top_abort_reasons:
        lines.append("主要中止原因:")
        for idx, (reason, count) in enumerate(verdict.top_abort_reasons, start=1):
            lines.append(f"  {idx}. ×{count} {reason}")

    if verdict.corrupt_lines or verdict.unparseable_ts:
        lines.append(
            f"账本数据质量: 损坏行 {verdict.corrupt_lines} / "
            f"ts 不可解析 {verdict.unparseable_ts}(未参与判定)"
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


def is_suppressed(state: dict, verdict: InflowVerdict) -> bool:
    """同一断流窗口是否已告警过。"""
    last = state.get("last_alert")
    if not isinstance(last, dict):
        return False
    return last.get("episode_key") == verdict.episode_key


def record_alert(state: dict, verdict: InflowVerdict) -> dict:
    """把本次告警写入状态(返回新 state,history 有界)。"""
    entry = {
        "episode_key": verdict.episode_key,
        "verdict": verdict.verdict,
        "window_hours": verdict.window_hours,
        "alerted_at": _fmt_ts(verdict.now),
        "intake_count": verdict.intake_count,
        "finalize_count": verdict.finalize_count,
    }
    history = state.get("history")
    history = list(history) if isinstance(history, list) else []
    history.append(entry)
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
) -> int:
    """执行一次断流检查(判定 → 抑制 → 发送)。返回进程退出码。

    Args:
        ledger_path: quality-ledger.jsonl 路径。
        window_hours: 判定窗口。
        dry_run: 只打印文案,不发送、不写抑制状态。
        force: 忽略幂等抑制强制发送(手动验证用)。
        state_path: 抑制状态文件;None → resolve_state_path()。
        now: 判定基准时刻(测试注入)。
        sender: 发送函数 `(text: str) -> None`;None → send_feishu_alert。

    Returns:
        0 检查完成;1 检查无法完成或发送失败(fail-visible)。
    """
    try:
        verdict = evaluate_ledger(ledger_path, window_hours=window_hours, now=now)
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

    text = build_alert_text(verdict)

    if dry_run:
        print("[inflow_sentinel] --dry-run:以下文案不会发送")
        print(text)
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
    if not force and is_suppressed(state, verdict):
        print(
            f"[inflow_sentinel] 同一断流窗口已告警过,抑制本次"
            f"(episode_key={verdict.episode_key});--force 可强制重发"
        )
        return 0

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

    print(f"[inflow_sentinel] 已发送飞书告警(episode_key={verdict.episode_key})")
    print(text)

    try:
        _save_state(state_path, record_alert(state, verdict))
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
        "--force", action="store_true", help="忽略幂等抑制强制发送(手动验证用)"
    )
    parser.add_argument(
        "--state-file", default=None, help="抑制状态文件路径(默认经 common.state_paths 解析)"
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
    )


if __name__ == "__main__":
    sys.exit(main())
