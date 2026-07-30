"""backfill_requeue.py — 上游 400 故障误拒内容的批量 requeue CLI(F26 语义)。

背景(2026-07-23 起实证):DeepSeek `response_format` 不可用导致准入四判定全部
调用失败,内容被 `abort` 判死。这些 abort **不是质量结论,是基础设施误杀**——
账本 `details` 里留有指纹 `This response_format type is unavailable now`。
runner 的 `_is_processed` 把 abort 视为终态,故障修复后这批内容也永不重审。

与 `requeue.py`(F26 单篇白名单 CLI)的分工:
    requeue.py          —— 按 **raw 文件名** 白名单,单次上限 10 篇,人工点名重审。
    backfill_requeue.py —— 按 **账本指纹** 批量筛选历史误拒(数百篇量级),
                           人工无法逐个点名,故以"指纹 + 幂等 + 有界"替代白名单。

语义与 requeue.py 完全一致:向账本追加 `requeue` 事件,终态之后出现 requeue
→ daemon 下轮扫描视为未处理并重审。**不改任何判定标准**,重审仍走全套
fail-closed 闸门(admission/地板/评分/五闸原样)。

安全性质:

- **手动触发**:不注册任何自动化触发器(宪法:新增触发器须人工确认)。
- **默认 dry-run**:不带 `--apply` 只输出清单,零写入。
- **有界**(宪法 P1):每个 content_hash **最多一条** backfill requeue(账本里
  已有本 CLI 写入的 requeue → 永久排除);`--limit N` 可再压单次上限。
- **幂等**:重复执行零新增(排除规则基于账本自身状态,不依赖行 SHA 去重——
  事件 `ts` 每次不同,`append_event` 的行去重救不了重复追加)。
- **fail-visible**(宪法 P4):排除原因分类计数、损坏行数、`--limit` 截断量
  全部打印;账本缺失/不可读 → 非 0 退出,不静默当作"零候选"。

用法(在仓库根执行)::

    # 默认 dry-run:只看清单
    python3 -m src.akb2.quality.backfill_requeue

    # 显式指定账本(daemon 旧代码仍在写 vault staging 时用)
    python3 -m src.akb2.quality.backfill_requeue \\
        --ledger "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/staging/quality-ledger.jsonl"

    # 真写入
    python3 -m src.akb2.quality.backfill_requeue --apply [--limit 200]

装了包(`pip install -e .`)或设了 `PYTHONPATH=src` 时 `python3 -m
akb2.quality.backfill_requeue` 同样可用。

账本默认位置由 `common.state_paths.resolve_state_file` 解析(机器本地状态根,
不在跨机同步范围内)。**不传 legacy_path**:那会把旧账本复制继承过来——一个
默认只读的 CLI 不该产生状态迁移副作用。默认位置不存在时 fail-closed 退出并
提示用 `--ledger` 显式指定。

⚠ 本机当前**存在两处账本**:daemon 旧代码仍在写 vault staging,state_paths
默认位置是较旧的快照。报告首行打印账本路径与 **mtime**,选错会漏掉最近几天
的误拒——按 mtime 确认后再 `--apply`。
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 具名常量(宪法 P4:预算/口径显式)
# ---------------------------------------------------------------------------

# 默认故障指纹:DeepSeek json_schema 不可用时 litellm 透传的 400 报文特征串
DEFAULT_PATTERN = "response_format type is unavailable"

# requeue reason 前缀:标记本 CLI 的写入,同时充当"该 hash 已 backfill"的幂等键
BACKFILL_REASON_PREFIX = "backfill_response_format_400"

# 事件 details 里携带的原错误摘要上限(账本行不应因单条错误报文膨胀)
ERROR_EXCERPT_MAX_CHARS = 200

# dry-run 明细默认预览条数(0 = 全部);截断量会打印,不静默省略
DEFAULT_DETAIL_LIMIT = 20

# 损坏行最多列出的行号数
MAX_CORRUPT_LINES_REPORTED = 5

# --apply 每写入多少条打印一次进度（append_event 全量重扫 → 单条也可能上百毫秒）
APPLY_PROGRESS_INTERVAL = 100

_TERMINAL_EVENTS = frozenset({"finalize", "abort"})

# 排除原因(用于分类计数与报告)
SKIP_FINALIZE_AFTER = "finalize_after"
SKIP_BACKFILL_EXISTS = "backfill_requeue_exists"
SKIP_ALREADY_PENDING = "already_pending"


@dataclass(frozen=True)
class Candidate:
    """一条待 requeue 的误拒记录。"""

    content_hash: str
    first_hit_ts: str
    hit_count: int
    error_excerpt: str
    raw_filename: str


@dataclass
class ScanResult:
    """账本扫描结果。"""

    candidates: list[Candidate]
    skipped: dict[str, int]
    corrupt_lines: list[int]
    total_lines: int


@dataclass
class ApplyResult:
    """写入结果（interrupted=True 表示只写了一部分，宪法 P4 部分失败可见）。"""

    written: int
    deduped: int
    interrupted: bool


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------


def _details_text(entry: dict) -> str:
    """把 details 序列化成可搜索文本(指纹可能藏在任意嵌套字段的 reason 里)。"""
    return json.dumps(entry.get("details", {}), ensure_ascii=False)


def _extract_excerpt(details_text: str, pattern: str) -> str:
    """截取指纹命中处附近的原错误片段(审计用,宪法 P4 携带原因)。"""
    idx = details_text.find(pattern)
    if idx < 0:
        return details_text[:ERROR_EXCERPT_MAX_CHARS]
    start = max(0, idx - ERROR_EXCERPT_MAX_CHARS // 2)
    return details_text[start : start + ERROR_EXCERPT_MAX_CHARS]


def _is_backfill_requeue(entry: dict) -> bool:
    """该事件是否为本 CLI 写入的 requeue(按 reason 前缀识别)。"""
    if entry.get("event") != "requeue":
        return False
    reason = entry.get("details", {})
    if not isinstance(reason, dict):
        return False
    return str(reason.get("reason", "")).startswith(BACKFILL_REASON_PREFIX)


def _is_processed(events: list[tuple[int, str, bool]]) -> bool:
    """复刻 runner._is_processed 的三态判定(终态/驻留 → 已处理;requeue → 重开)。"""
    processed = False
    for _idx, event, _backfill in events:
        if event in _TERMINAL_EVENTS:
            processed = True
        elif event == "requeue":
            processed = False
        elif event == "needs_repair":
            processed = True
    return processed


def scan_ledger(ledger_path: str | Path, pattern: str = DEFAULT_PATTERN) -> ScanResult:
    """扫描账本,筛出被指纹命中的 abort,并按幂等/有界规则排除。

    Args:
        ledger_path: jsonl 账本路径。
        pattern: 故障指纹(在 abort 事件的 `details` 文本里做子串匹配)。

    Returns:
        ScanResult —— candidates 按首次误拒时间升序。

    Raises:
        OSError: 账本不可读(fail-closed,不静默当作零候选)。
    """
    # 每个 hash 的紧凑事件序列:(行序, event, 是否本 CLI 写的 requeue)
    events: dict[str, list[tuple[int, str, bool]]] = collections.defaultdict(list)
    # 每个 hash 的命中记录:(行序, ts, 原错误摘要)
    hits: dict[str, list[tuple[int, str, str]]] = collections.defaultdict(list)
    raw_names: dict[str, str] = {}
    corrupt_lines: list[int] = []
    total_lines = 0

    with open(ledger_path, encoding="utf-8") as fh:
        for lineno, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            total_lines += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                corrupt_lines.append(lineno)
                continue
            if not isinstance(entry, dict):
                corrupt_lines.append(lineno)
                continue
            content_hash = entry.get("content_hash")
            if not content_hash or not isinstance(content_hash, str):
                continue
            event = str(entry.get("event", ""))
            events[content_hash].append((lineno, event, _is_backfill_requeue(entry)))

            if event == "intake_raw":
                raw_path = str(entry.get("details", {}).get("raw_path", ""))
                if raw_path:
                    raw_names[content_hash] = Path(raw_path).name
            elif event == "abort":
                text = _details_text(entry)
                if pattern in text:
                    hits[content_hash].append(
                        (lineno, str(entry.get("ts", "")), _extract_excerpt(text, pattern))
                    )

    candidates: list[Candidate] = []
    skipped: dict[str, int] = {
        SKIP_FINALIZE_AFTER: 0,
        SKIP_BACKFILL_EXISTS: 0,
        SKIP_ALREADY_PENDING: 0,
    }

    for content_hash, hit_list in hits.items():
        seq = events[content_hash]
        last_hit_line = hit_list[-1][0]

        # 排除 1:最近一次误拒之后已 finalize —— 内容已入库,重审是浪费
        if any(line > last_hit_line and ev == "finalize" for line, ev, _b in seq):
            skipped[SKIP_FINALIZE_AFTER] += 1
            continue
        # 排除 2:已有本 CLI 写入的 requeue —— 每 hash 一生一条(有界 + 幂等)
        if any(line > last_hit_line and backfill for line, _ev, backfill in seq):
            skipped[SKIP_BACKFILL_EXISTS] += 1
            continue
        # 排除 3:当前已是"未处理"态(队列里等着重审)—— 再追加 requeue 是空操作
        if not _is_processed(seq):
            skipped[SKIP_ALREADY_PENDING] += 1
            continue

        candidates.append(
            Candidate(
                content_hash=content_hash,
                first_hit_ts=hit_list[0][1],
                hit_count=len(hit_list),
                error_excerpt=hit_list[-1][2],
                raw_filename=raw_names.get(content_hash, ""),
            )
        )

    candidates.sort(key=lambda c: (c.first_hit_ts, c.content_hash))
    return ScanResult(
        candidates=candidates,
        skipped=skipped,
        corrupt_lines=corrupt_lines,
        total_lines=total_lines,
    )


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def build_requeue_event(candidate: Candidate, ts: str) -> dict:
    """构造 requeue 事件(与 runner._log_event / requeue.py 同 schema)。"""
    return {
        "loop_id": "loop1",
        "node": "requeue",
        "event": "requeue",
        "content_hash": candidate.content_hash,
        "ts": ts,
        "details": {
            "reason": f"{BACKFILL_REASON_PREFIX}: {candidate.error_excerpt}",
            "by": "backfill-requeue-cli",
            "first_rejected_at": candidate.first_hit_ts,
            "infra_abort_count": candidate.hit_count,
            "raw_filename": candidate.raw_filename,
        },
    }


def apply_requeue(ledger_path: str | Path, candidates: list[Candidate]) -> ApplyResult:
    """向账本追加 requeue 事件（逐条走 `ledger.append_event`，幂等 + 持锁）。

    性能特征（实测 26k 行账本 / 639 条写入约 2 分 20 秒）：`append_event` 每次
    追加都全量重扫账本做行去重，故总耗时 O(候选数 × 账本行数)。因此**必须**
    有进度输出——否则操作者会把正常运行当成卡死并 Ctrl-C，留下写了一半的账本
    且不知道写到哪。中断时打印已写入条数（宪法 P4 部分失败可见）。
    """
    from src.akb2.loops.ledger import append_event

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = len(candidates)
    written = 0
    deduped = 0
    try:
        for index, candidate in enumerate(candidates, start=1):
            event = build_requeue_event(candidate, ts)
            if append_event(str(ledger_path), event):
                written += 1
            else:
                deduped += 1
            if index % APPLY_PROGRESS_INTERVAL == 0 and index < total:
                print(f"[apply] 进度 {index}/{total}（已写入 {written}）", flush=True)
    except KeyboardInterrupt:
        print(
            f"[apply] 已中断：{written}/{total} 条已写入账本（已写入的部分有效，"
            "重跑本 CLI 会补齐剩余部分，不会重复写）",
            file=sys.stderr,
        )
        return ApplyResult(written=written, deduped=deduped, interrupted=True)
    return ApplyResult(written=written, deduped=deduped, interrupted=False)


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def resolve_ledger_path(explicit: str | None) -> Path:
    """解析账本路径:`--ledger` 显式优先,否则走机器本地状态根。

    Raises:
        RuntimeError: 状态根不可用(落在同步范围内等),或解析出的账本不存在。
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise RuntimeError(f"--ledger 指定的账本不存在: {path}")
        return path

    from src.akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file

    # 不传 legacy_path:只读 CLI 不产生"复制继承旧账本"的状态迁移副作用
    path = resolve_state_file("quality-ledger.jsonl", subdir=LEDGER_SUBDIR)
    if not path.is_file():
        raise RuntimeError(
            f"默认账本不存在: {path}；若 daemon 仍在写旧位置"
            "(vault staging),请用 --ledger 显式指定该文件"
        )
    return path


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def _print_report(
    result: ScanResult,
    ledger_path: Path,
    pattern: str,
    selected: list[Candidate],
    detail_limit: int,
) -> None:
    """打印扫描报告(dry-run 与 --apply 共用同一份清单口径)。"""
    print(f"账本: {ledger_path}")
    # 打印 mtime：本机存在两处账本（state_paths 默认位置 与 vault staging 旧位置），
    # 选错一处会漏掉最近几天的误拒。把更新时间摊在报告里，操作者一眼可辨（宪法 P4）。
    try:
        mtime = datetime.fromtimestamp(ledger_path.stat().st_mtime, UTC)
        print(f"账本更新时间: {mtime.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    except OSError as exc:
        print(f"账本更新时间: 未知（{exc}）", file=sys.stderr)
    print(f"指纹: {pattern!r}")
    print(f"账本有效行: {result.total_lines}")
    print(f"命中候选(可 requeue): {len(result.candidates)}")

    by_day: collections.Counter[str] = collections.Counter(
        c.first_hit_ts[:10] or "(无 ts)" for c in result.candidates
    )
    if by_day:
        print("按日分布(首次误拒日):")
        for day, count in sorted(by_day.items()):
            print(f"  {day}: {count}")

    if len(selected) != len(result.candidates):
        print(f"本次处理: {len(selected)}（--limit 截断，剩余 "
              f"{len(result.candidates) - len(selected)} 条本次不处理）")

    shown = selected if detail_limit <= 0 else selected[:detail_limit]
    if shown:
        print("明细(content_hash / 首次误拒时间 / 误拒次数 / raw 文件名):")
        for c in shown:
            print(
                f"  {c.content_hash}  {c.first_hit_ts}  x{c.hit_count}  "
                f"{c.raw_filename or '(账本无 intake_raw 记录)'}"
            )
        if len(shown) < len(selected):
            print(f"  ...（仅显示前 {len(shown)} 条，共 {len(selected)} 条；"
                  f"--detail-limit 0 显示全部）")

    print(
        "排除: "
        + ", ".join(f"{k}={v}" for k, v in sorted(result.skipped.items()))
    )
    if result.corrupt_lines:
        head = ", ".join(str(n) for n in result.corrupt_lines[:MAX_CORRUPT_LINES_REPORTED])
        more = "" if len(result.corrupt_lines) <= MAX_CORRUPT_LINES_REPORTED else " ..."
        print(
            f"警告: 账本有 {len(result.corrupt_lines)} 行无法解析"
            f"（行号 {head}{more}）；这些行未参与筛选",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run_backfill(
    ledger_path: str | Path,
    pattern: str = DEFAULT_PATTERN,
    *,
    apply: bool = False,
    limit: int | None = None,
    detail_limit: int = DEFAULT_DETAIL_LIMIT,
) -> int:
    """扫描 + 可选写入。返回退出码(0 成功;1 失败)。"""
    try:
        result = scan_ledger(ledger_path, pattern)
    except OSError as exc:
        print(f"ERROR: 账本不可读: {exc}", file=sys.stderr)
        return 1

    selected = result.candidates if limit is None else result.candidates[:limit]
    _print_report(result, Path(ledger_path), pattern, selected, detail_limit)

    if not apply:
        print("[dry-run] 未写入任何事件；确认清单无误后加 --apply 执行")
        return 0

    if not selected:
        print("[apply] 无候选，未写入任何事件")
        return 0

    print(f"[apply] 开始写入 {len(selected)} 条 requeue 事件"
          f"（逐条持锁去重，26k 行账本约 0.2 秒/条，请勿中断）")
    try:
        applied = apply_requeue(ledger_path, selected)
    except OSError as exc:
        print(f"ERROR: 写入账本失败: {exc}", file=sys.stderr)
        return 1

    dedup_note = f"（行去重跳过 {applied.deduped} 条）" if applied.deduped else ""
    print(f"[apply] 已写入 requeue 事件 {applied.written} 条{dedup_note}")
    if applied.interrupted:
        return 1
    print("这些内容将在 daemon 下一轮扫描时重审（重审仍走全套 fail-closed 闸门）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AKB2 批量 backfill requeue：把被上游 400 故障误拒的内容排回质量管线"
    )
    parser.add_argument(
        "--ledger",
        default=None,
        help="账本路径（默认走 state_paths 解析的机器本地状态根）",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help=f"故障指纹，在 abort 的 details 文本里做子串匹配（默认 {DEFAULT_PATTERN!r}）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真写入 requeue 事件（缺省为 dry-run，只输出清单）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="本次最多处理的候选数（有界，默认不限）",
    )
    parser.add_argument(
        "--detail-limit",
        type=int,
        default=DEFAULT_DETAIL_LIMIT,
        help=f"明细预览条数，0=全部（默认 {DEFAULT_DETAIL_LIMIT}）",
    )
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit <= 0:
        print("ERROR: --limit 须为正整数", file=sys.stderr)
        return 1
    if not args.pattern.strip():
        print("ERROR: --pattern 不可为空（空串会命中全部 abort）", file=sys.stderr)
        return 1

    try:
        ledger_path = resolve_ledger_path(args.ledger)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return run_backfill(
        ledger_path,
        args.pattern,
        apply=args.apply,
        limit=args.limit,
        detail_limit=args.detail_limit,
    )


if __name__ == "__main__":
    sys.exit(main())
