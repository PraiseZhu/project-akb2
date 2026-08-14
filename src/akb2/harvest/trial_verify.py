"""试投判定唯一底座 — wave7/g11/sc-trial-core。

对账折叠（fold）引擎：将试投对账事件序列折叠为最终对账状态，并通过
纯函数校验器验证状态一致性。

转移表（三条合法边，其余 fail-closed）：
- 空前态 --reconcile_resolved--> 已解除（三项 expected_* 与唯一 committed 逐字相等且 hash 非空）
- blocked --resolved--> 已解除
- unverifiable --manual_attested--> 已解除

状态设置事件（非转移表裁定，恒合法）：
- blocked    → 空前态 → 阻塞态
- unverifiable → 空前态 → 不可验证态
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from enum import IntEnum


class ReconcilePhase(IntEnum):
    """试投对账阶段枚举。"""

    EMPTY = 1
    BLOCKED = 2
    UNVERIFIABLE = 3
    RESOLVED = 4


@dataclass(frozen=True)
class ReconcileState:
    """对账状态。"""

    phase: ReconcilePhase
    resolved_hash: str = ""
    resolved_details: str = ""


class ResolverError(ValueError):
    """对账失败：非法转移、结构不匹配等。"""


CLI_EXIT_OK = 0
CLI_EXIT_INVALID = 2

# 转移表：三条合法边（key = (current_phase, event_kind) → description）
LEGAL_TRANSITIONS: dict[tuple[ReconcilePhase, str], str] = {
    (ReconcilePhase.EMPTY, "reconcile_resolved"): "empty->resolved",
    (ReconcilePhase.BLOCKED, "resolved"): "blocked->resolved",
    (ReconcilePhase.UNVERIFIABLE, "manual_attested"): "unverifiable->resolved",
}

# 状态设置事件（恒合法，不属转移表裁定）
STATE_SETTING_EVENTS = ("blocked", "unverifiable")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _require_dict_list(value: object, field_name: str) -> list[dict]:
    """确保字段是 dict 列表，拒绝任何非 list 输入。"""
    if not isinstance(value, list):
        raise ResolverError(f"{field_name} 必须是 list")
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise ResolverError(f"{field_name}[{idx}] 必须是 dict")
    return value


def _match_expected(record: dict, expected_a: str, expected_b: str, expected_c: str) -> bool:
    """预期字段与 committed record 逐字匹配。"""
    return (
        record.get("a", "") == expected_a
        and record.get("b", "") == expected_b
        and record.get("c", "") == expected_c
    )


def _resolve_empty(event: dict, committed_records: list[dict]) -> ReconcileState:
    """空前态转移：reconcile_resolved → 已解除。"""
    if len(committed_records) != 1:
        raise ResolverError(
            f"空前态 reconcile_resolved 需要恰好 1 条 committed record,"
            f"实际 {len(committed_records)} 条"
        )
    record = committed_records[0]
    expected_a = event.get("expected_a", "")
    expected_b = event.get("expected_b", "")
    expected_c = event.get("expected_c", "")
    if not _match_expected(record, expected_a, expected_b, expected_c):
        raise ResolverError(
            "空前态 reconcile_resolved: expected_* 与 committed record 不匹配 "
            + json.dumps(
                {
                    "expected_a": expected_a,
                    "expected_b": expected_b,
                    "expected_c": expected_c,
                    "record_a": record.get("a", ""),
                    "record_b": record.get("b", ""),
                    "record_c": record.get("c", ""),
                },
                ensure_ascii=False,
            )
        )
    record_hash = record.get("hash", "")
    if not record_hash:
        raise ResolverError("空前态 reconcile_resolved: committed record hash 为空")
    return ReconcileState(
        phase=ReconcilePhase.RESOLVED,
        resolved_hash=record_hash,
        resolved_details=f"reconcile_resolved: expected_* 匹配, hash={record_hash}",
    )


def _resolve_blocked() -> ReconcileState:
    """阻塞态转移：resolved → 已解除。"""
    return ReconcileState(
        phase=ReconcilePhase.RESOLVED,
        resolved_hash="blocked_resolved",
        resolved_details="blocked --resolved--> resolved",
    )


def _resolve_unverifiable() -> ReconcileState:
    """不可验证态转移：manual_attested → 已解除。"""
    return ReconcileState(
        phase=ReconcilePhase.RESOLVED,
        resolved_hash="manual_attested",
        resolved_details="unverifiable --manual_attested--> resolved",
    )


def _format_legal_transitions() -> str:
    """格式化合法转移表（用于错误消息）。"""
    parts = []
    for (phase, kind), desc in LEGAL_TRANSITIONS.items():
        parts.append(f"{phase.name} --{kind}--> {desc}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# 纯函数 1: fold_reconcile
# ---------------------------------------------------------------------------


def fold_reconcile(events: list[dict], *, committed_records: list[dict]) -> ReconcileState:
    """对事件序列执行 fold，返回最终对账状态。

    Args:
        events: 对账事件序列（dict，每项含 kind 字段）。
        committed_records: 已落盘记录（具名必填；零条也必须显式传空列表）。

    Returns:
        最终对账状态。

    Raises:
        ResolverError: 非法转移或结构不匹配。
    """
    events = _require_dict_list(events, "events")
    committed_records = _require_dict_list(committed_records, "committed_records")
    state = ReconcileState(phase=ReconcilePhase.EMPTY)
    for event in events:
        kind = event.get("kind", "")

        # 状态设置事件（恒合法）
        if kind == "blocked":
            if state.phase != ReconcilePhase.EMPTY:
                raise ResolverError(
                    f"blocked 事件仅允许从空前态转出,"
                    f"当前为 {state.phase.name}"
                )
            state = ReconcileState(phase=ReconcilePhase.BLOCKED)
            continue

        if kind == "unverifiable":
            if state.phase != ReconcilePhase.EMPTY:
                raise ResolverError(
                    f"unverifiable 事件仅允许从空前态转出,"
                    f"当前为 {state.phase.name}"
                )
            state = ReconcileState(phase=ReconcilePhase.UNVERIFIABLE)
            continue

        # 转移表裁定
        key = (state.phase, kind)
        if key not in LEGAL_TRANSITIONS:
            raise ResolverError(
                f"非法转移: {state.phase.name} --{kind}--> ? "
                f"(合法转移: {_format_legal_transitions()})"
            )

        if state.phase == ReconcilePhase.EMPTY:
            state = _resolve_empty(event, committed_records)
        elif state.phase == ReconcilePhase.BLOCKED:
            state = _resolve_blocked()
        else:
            state = _resolve_unverifiable()

    return state


# ---------------------------------------------------------------------------
# 纯函数 2: validate_resolved
# ---------------------------------------------------------------------------


def validate_resolved(state: ReconcileState, committed_records: list[dict]) -> list[str]:
    """验证已解除状态的一致性。

    Args:
        state: 对账状态。
        committed_records: 已落盘记录（仅用于校验上下文）。

    Returns:
        违规清单（空 = 合规）。
    """
    violations: list[str] = []
    if state.phase != ReconcilePhase.RESOLVED:
        return violations
    if not state.resolved_hash:
        violations.append("resolved state: resolved_hash 为空")
    if not state.resolved_details:
        violations.append("resolved state: resolved_details 为空")
    return violations


# ---------------------------------------------------------------------------
# 纯函数 3: check_consistency
# ---------------------------------------------------------------------------


def check_consistency(events: list[dict], committed_records: list[dict]) -> list[str]:
    """检查事件与已落盘记录的一致性。

    Args:
        events: 对账事件序列。
        committed_records: 已落盘记录。

    Returns:
        违规清单（空 = 合规）。
    """
    violations: list[str] = []
    if not events and not committed_records:
        return violations
    if not events and committed_records:
        violations.append(
            f"一致性: 无事件但有 {len(committed_records)} 条 committed record"
        )
        return violations

    has_reconcile = any(e.get("kind") == "reconcile_resolved" for e in events)
    if has_reconcile and not committed_records:
        violations.append("一致性: 有 reconcile_resolved 事件但 committed_records 为空")
    if committed_records and not has_reconcile:
        violations.append(
            f"一致性: 有 {len(committed_records)} 条 committed record"
            f"但无 reconcile_resolved 事件"
        )
    return violations


# ---------------------------------------------------------------------------
# 纯函数 4: format_evidence
# ---------------------------------------------------------------------------


def format_evidence(state: ReconcileState) -> str:
    """格式化已解除状态的证据文本。

    Args:
        state: 对账状态。

    Returns:
        Evidence 文本；非 resolved 状态返回空字符串。
    """
    if state.phase != ReconcilePhase.RESOLVED:
        return ""
    return (
        f"resolved: phase={state.phase.name},"
        f" hash={state.resolved_hash},"
        f" details={state.resolved_details}"
    )


# ---------------------------------------------------------------------------
# Resolver: resolve_trial
# ---------------------------------------------------------------------------


def resolve_trial(
    events: list[dict], *, committed_records: list[dict]
) -> tuple[ReconcileState, list[str]]:
    """对账解析器：折叠事件 → 校验 → 一致性检查 → 结果。

    Args:
        events: 对账事件序列。
        committed_records: 已落盘记录（具名必填；零条显式传空列表）。

    Returns:
        (state, violations): 对账状态与违规清单。
    """
    violations: list[str] = []
    try:
        state = fold_reconcile(events, committed_records=committed_records)
    except ResolverError as exc:
        return (ReconcileState(phase=ReconcilePhase.EMPTY), [str(exc)])
    violations.extend(validate_resolved(state, committed_records))
    violations.extend(check_consistency(events, committed_records))
    return (state, violations)


# ---------------------------------------------------------------------------
# batch 级 check（--batch 模式；底座仍是 resolve_trial，不另起对账逻辑）
# ---------------------------------------------------------------------------


def check_batch_calibration(batch: dict) -> list[str]:
    """校验 batch.attempted 的对账状态一致性（sc-trial-run 校准）。

    每条 attempted 按 reconciliation_state 映射到 trial_verify 事件，
    经 resolve_trial（fold + validate + consistency）裁定：

    - "committed"：构造该条目自身唯一 committed record
      （hash = pipeline_content_hash），事件 reconcile_resolved；
      要求 final_state == "committed" 且折叠为 RESOLVED。
    - "blocked"：事件序列 [blocked, resolved]；要求 final_state 非 committed。
    - "not_required" / phase == "intent"：未到对账期，跳过。
    - 其它值：fail-closed。

    Returns:
        违规清单（空 = 合规）。
    """
    violations: list[str] = []
    attempted = batch.get("attempted")
    if not isinstance(attempted, list):
        return ["calibration: attempted 必须是 list"]
    for idx, entry in enumerate(attempted):
        label = f"attempted[{idx}]"
        if not isinstance(entry, dict):
            violations.append(f"{label}: 期望对象")
            continue
        phase = entry.get("phase", "")
        rec_state = entry.get("reconciliation_state")
        if phase == "intent" or rec_state == "not_required":
            continue

        if rec_state == "committed":
            if entry.get("final_state") != "committed":
                violations.append(
                    f"{label}: reconciliation_state=committed 但 "
                    f"final_state={entry.get('final_state')!r}"
                )
            hash_val = entry.get("pipeline_content_hash") or ""
            record = {
                "a": entry.get("session_id", ""),
                "b": entry.get("expected_slug", ""),
                "c": hash_val,
                "hash": hash_val,
            }
            event = {
                "kind": "reconcile_resolved",
                "expected_a": record["a"],
                "expected_b": record["b"],
                "expected_c": record["c"],
            }
            state, fold_violations = resolve_trial(
                [event], committed_records=[record]
            )
            violations.extend(f"{label}: {v}" for v in fold_violations)
            if state.phase != ReconcilePhase.RESOLVED:
                violations.append(
                    f"{label}: committed 未折叠为 RESOLVED "
                    f"(实际 {state.phase.name})"
                )
        elif rec_state == "blocked":
            if entry.get("final_state") == "committed":
                violations.append(
                    f"{label}: reconciliation_state=blocked 但 "
                    f"final_state='committed'"
                )
            state, fold_violations = resolve_trial(
                [{"kind": "blocked"}, {"kind": "resolved"}],
                committed_records=[],
            )
            violations.extend(f"{label}: {v}" for v in fold_violations)
            if state.phase != ReconcilePhase.RESOLVED:
                violations.append(
                    f"{label}: blocked 未折叠为 RESOLVED "
                    f"(实际 {state.phase.name})"
                )
        else:
            violations.append(
                f"{label}: 未知 reconciliation_state {rec_state!r}"
            )
    return violations


def check_batch_sourceurl(batch: dict) -> list[str]:
    """校验 batch.planned 的 source_url 身份同源（sc-trial-sourceurl）。

    source_url 策略 = ``cindy://session/<payload session_id>``（常量策略，
    不做变换、不生成新身份）。身份不漂移的充要条件是三元一致：

        entry.session_id == payload.session_id == payload.source_meta.session_id

    Returns:
        违规清单（空 = 合规）。
    """
    violations: list[str] = []
    planned = batch.get("planned")
    if not isinstance(planned, list):
        return ["sourceurl: planned 必须是 list"]
    for idx, item in enumerate(planned):
        label = f"planned[{idx}]"
        if not isinstance(item, dict):
            violations.append(f"{label}: 期望对象")
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            violations.append(f"{label}.payload: 期望对象")
            continue
        sid_entry = item.get("session_id", "")
        sid_payload = payload.get("session_id", "")
        meta = payload.get("source_meta")
        sid_meta = meta.get("session_id", "") if isinstance(meta, dict) else ""
        if not (sid_entry and sid_entry == sid_payload == sid_meta):
            violations.append(
                f"{label}: source_url 身份不一致 "
                f"(entry={sid_entry!r}, payload={sid_payload!r}, "
                f"source_meta={sid_meta!r})"
            )
    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：读 trial_verify JSON 或 trial-batch.json → 对账 → fail-closed。

    单条模式（file 位置参数）：JSON 格式
    {"events": [...], "committed_records": [...]}

    批量模式（--batch）：对 trial-batch.json 执行 --check-calibration
    与/或 --check-sourceurl。

    Returns:
        exit code: 0 = 通过；2 = 任一校验失败。
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m akb2.harvest.trial_verify",
        description="试投对账判定（fail-closed）",
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="trial_verify JSON 文件路径（单条对账模式）",
    )
    parser.add_argument(
        "--batch",
        help="trial-batch.json 文件路径（批量校验模式，与 file 互斥）",
    )
    parser.add_argument(
        "--check-calibration",
        action="store_true",
        help="校验 batch.attempted 对账状态一致性",
    )
    parser.add_argument(
        "--check-sourceurl",
        action="store_true",
        help="校验 batch.planned source_url 身份同源",
    )
    args = parser.parse_args(argv)

    if args.batch is not None:
        if args.file is not None:
            parser.error("file 与 --batch 互斥")
        if not (args.check_calibration or args.check_sourceurl):
            parser.error("--batch 需要至少一个 --check-calibration/--check-sourceurl")
        try:
            with open(args.batch, encoding="utf-8") as fh:
                batch = json.load(fh)
        except FileNotFoundError:
            print(f"trial_verify: 文件不可读: {args.batch}", file=sys.stderr)
            return CLI_EXIT_INVALID
        except json.JSONDecodeError as exc:
            print(f"trial_verify: JSON 非法: {exc}", file=sys.stderr)
            return CLI_EXIT_INVALID
        if not isinstance(batch, dict):
            print("trial_verify: batch JSON 顶层必须是对象", file=sys.stderr)
            return CLI_EXIT_INVALID

        violations: list[str] = []
        if args.check_calibration:
            violations.extend(check_batch_calibration(batch))
        if args.check_sourceurl:
            violations.extend(check_batch_sourceurl(batch))

        checks = [
            name
            for name, enabled in (
                ("calibration", args.check_calibration),
                ("sourceurl", args.check_sourceurl),
            )
            if enabled
        ]
        result = {"checks": checks, "violations": violations}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if violations:
            for v in violations:
                print(f"trial_verify: {v}", file=sys.stderr)
            print("trial_verify: 校验失败", file=sys.stderr)
            return CLI_EXIT_INVALID
        print("trial_verify: 校验通过", file=sys.stderr)
        return CLI_EXIT_OK

    if args.file is None:
        parser.error("需要 file 或 --batch")

    if args.check_calibration or args.check_sourceurl:
        parser.error("--check-* 仅在 --batch 模式下可用")

    try:
        with open(args.file, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        print(f"trial_verify: 文件不可读: {args.file}", file=sys.stderr)
        return CLI_EXIT_INVALID
    except json.JSONDecodeError as exc:
        print(f"trial_verify: JSON 非法: {exc}", file=sys.stderr)
        return CLI_EXIT_INVALID

    if not isinstance(data, dict):
        print("trial_verify: JSON 顶层必须是对象", file=sys.stderr)
        return CLI_EXIT_INVALID

    events = data.get("events")
    committed_records = data.get("committed_records")

    state, violations = resolve_trial(events, committed_records=committed_records)

    result = {
        "phase": state.phase.name,
        "resolved_hash": state.resolved_hash,
        "resolved_details": state.resolved_details,
        "violations": violations,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if violations:
        print("trial_verify: 校验失败", file=sys.stderr)
        return CLI_EXIT_INVALID

    print("trial_verify: 校验通过", file=sys.stderr)
    return CLI_EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
