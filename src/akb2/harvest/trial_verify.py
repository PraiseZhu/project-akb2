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
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：读 trial_verify JSON → 对账 → fail-closed。

    JSON 格式：{"events": [...], "committed_records": [...]}

    Returns:
        exit code: 0 = 通过；2 = 任一校验失败。
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m akb2.harvest.trial_verify",
        description="试投对账判定（fail-closed）",
    )
    parser.add_argument("file", help="trial_verify JSON 文件路径")
    args = parser.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        print(f"trial_verify: 文件不可读: {args.file}", file=sys.stderr)
        return CLI_EXIT_INVALID
    except json.JSONDecodeError as exc:
        print(f"trial_verify: JSON 非法: {exc}", file=sys.stderr)
        return CLI_EXIT_INVALID

    events = data.get("events", [])
    committed_records = data.get("committed_records", [])

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
