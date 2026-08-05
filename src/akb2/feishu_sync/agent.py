"""LangGraph agent — discover → gate → render → send。"""

from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from . import blocked, config, gate, idempotency, notify, render
from . import discover as discover_mod
from .feishu.send import FeishuSendError, send_card, send_card_dm
from .state import SyncState


def _missing_reason(kind: str, date: str, vault_root: Path) -> str:
    reason = blocked.blocking_reason(kind, date) or "expected_briefing_missing"
    artifact = blocked.backstop_artifact_status(kind, date, vault_root)
    next_step = blocked.next_step_command(kind, date, vault_root)
    parts = [reason, f"backstop产物: {artifact}"]
    if next_step:
        parts.append(f"下一步: {next_step}")
    return "；".join(parts)


def _record_missing_pending(
    state: SyncState,
    missing_expected: list[tuple[str, str]],
    errors: list[str],
    log: list[str],
) -> tuple[list[str], list[str], bool]:
    dry_run = bool(state.get("dry_run", False))
    vault_root = Path(state["vault_root"])
    failed_persist = False
    state_path_value = state.get("state_path")
    state_path = Path(state_path_value) if state_path_value else None

    for kind, path in missing_expected:
        reason = _missing_reason(kind, state["date"], vault_root)
        msg = f"{kind} 缺失（期望路径: {path}）；真因: {reason}"
        errors.append(msg)
        log.append(msg)
        if dry_run or state_path is None:
            continue
        try:
            idempotency.mark_missing_expected(
                state_path,
                date=state["date"],
                kind=kind,
                target=state.get("target", "dm"),
                expected_path=path,
                reason=reason,
            )
        except Exception as exc:  # noqa: BLE001
            failed_persist = True
            persist_msg = f"{kind} missing_expected 状态落盘失败: {exc}"
            errors.append(persist_msg)
            log.append(f"{kind} fail-pending-write")
    return errors, log, failed_persist


def _filter_reconcile_candidates(
    state: SyncState,
    briefings,
    errors: list[str],
    log: list[str],
):
    state_path = Path(state["state_path"])
    pending = idempotency.pending_items(
        state_path,
        date=state["date"],
        target=state.get("target", "dm"),
        statuses=(idempotency.PENDING_MISSING_EXPECTED,),
    )
    pending_by_kind = {item.get("kind"): item for item in pending}
    if not pending_by_kind:
        log.append(f"reconcile-missing: {state['date']} 无待补发项")
        return [], errors, log

    kept = []
    found_kinds = set()
    for briefing in briefings:
        if briefing.kind in pending_by_kind:
            kept.append(briefing)
            found_kinds.add(briefing.kind)

    for kind, item in pending_by_kind.items():
        if kind not in found_kinds:
            expected_path = item.get("expected_path", "")
            log.append(f"{kind} reconcile-pending（仍缺失: {expected_path}）")
    return kept, errors, log


def discover_node(state: SyncState) -> dict:
    vault = Path(state["vault_root"])
    expected = state.get("expected")
    briefings, errors, missing_expected = discover_mod.discover(
        vault, state["date"], expected=expected
    )
    log = list(state.get("log", []))

    if state.get("reconcile_missing"):
        briefings, errors, log = _filter_reconcile_candidates(state, briefings, errors, log)
        return {"briefings": briefings, "errors": errors, "log": log}

    if state.get("strict") and missing_expected:
        errors, log, failed_persist = _record_missing_pending(state, missing_expected, errors, log)
        if state.get("missing_policy") == config.MISSING_POLICY_FAIL_ALL or failed_persist:
            briefings = []

    return {"briefings": briefings, "errors": errors, "log": log}


def gate_node(state: SyncState) -> dict:
    kept = []
    log = list(state.get("log", []))
    errors = list(state.get("errors", []))

    for briefing in state.get("briefings", []):
        passed, reason = gate.gate_decision(briefing)
        if passed:
            kept.append(briefing)
            continue
        if briefing.kind == "github":
            msg = f"github skip-gate（{reason}）"
            log.append(msg)
            errors.append(msg)
            continue
        log.append(f"{briefing.kind} skip-gate（{reason}）")

    return {"briefings": kept, "log": log, "errors": errors}


def render_node(state: SyncState) -> dict:
    briefings = state.get("briefings", [])
    for briefing in briefings:
        briefing.cards = render.render_cards(briefing)
    return {"briefings": briefings}


def send_node(state: SyncState) -> dict:
    target = state.get("target", "dm")
    date = state["date"]
    state_path = Path(state["state_path"])
    dry_run = bool(state.get("dry_run", False))
    log = list(state.get("log", []))
    errors = list(state.get("errors", []))

    for briefing in state.get("briefings", []):
        if dry_run:
            log.append(f"{briefing.kind} dry-run → {target}")
            continue
        try:
            reservation = idempotency.reserve_if_unsent(
                state_path, date, briefing.kind, target
            )
        except idempotency.StateCorruptError as exc:
            errors.append(f"{briefing.kind} 幂等状态损坏，拒发以防重复: {exc}")
            log.append(f"{briefing.kind} fail-state-corrupt")
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{briefing.kind} 预留状态失败，未发送: {exc}")
            log.append(f"{briefing.kind} fail-reserve")
            continue
        if reservation is None:
            log.append(f"{briefing.kind} skip-dup-or-inflight（{date}/{target} 已发或发送中）")
            continue

        preserve_reservation = False
        try:
            sent_indexes = idempotency.sent_card_indexes(
                state_path, date, briefing.kind, target
            )
            total_cards = len(briefing.cards)
            for card_index, card in enumerate(briefing.cards):
                if card_index in sent_indexes:
                    log.append(f"{briefing.kind} skip-card-dup（card={card_index}）")
                    continue
                if target == "group":
                    send_card(config.push_chat_id(), card)
                else:
                    send_card_dm(config.dm_open_id(), card)
                try:
                    idempotency.mark_card_sent(
                        state_path,
                        date,
                        briefing.kind,
                        target,
                        card_index=card_index,
                        total_cards=total_cards,
                    )
                except Exception as exc:  # noqa: BLE001
                    # transport 已成功，若此时释放 CAS 会允许重发该卡；保留预留 fail-closed。
                    preserve_reservation = True
                    raise RuntimeError(
                        f"card={card_index} 已发送但进度状态落盘失败: {exc}"
                    ) from exc
            idempotency.mark_sent(state_path, date, briefing.kind, target)
            log.append(f"{briefing.kind} sent → {target}")
        except Exception as exc:  # noqa: BLE001
            if not preserve_reservation and not idempotency.release_reservation(reservation):
                log.append(f"{briefing.kind} fail-reserve-release")
            if isinstance(exc, FeishuSendError) and (exc.retryable or exc.exhausted):
                try:
                    idempotency.mark_send_failed_retryable(
                        state_path,
                        date=date,
                        kind=briefing.kind,
                        target=target,
                        reason=str(exc),
                        retry_after=exc.retry_after,
                    )
                except Exception:  # noqa: BLE001
                    log.append(f"{briefing.kind} fail-pending-write")
            errors.append(f"{briefing.kind} 发送失败: {exc}")
            log.append(f"{briefing.kind} fail")
        else:
            if not idempotency.release_reservation(reservation):
                log.append(f"{briefing.kind} reserve-release-skipped")

    return {"log": log, "errors": errors}


def build_agent():
    graph = StateGraph(SyncState)
    graph.add_node("discover", discover_node)
    graph.add_node("gate", gate_node)
    graph.add_node("render", render_node)
    graph.add_node("send", send_node)
    graph.add_edge(START, "discover")
    graph.add_edge("discover", "gate")
    graph.add_edge("gate", "render")
    graph.add_edge("render", "send")
    graph.add_edge("send", END)
    return graph.compile()


def run_sync(
    *,
    date: str,
    vault_root: Path,
    target: str,
    state_path: Path,
    dry_run: bool = False,
    expected: list[str] | None = None,
    strict: bool = False,
    reconcile_missing: bool = False,
    missing_policy: str | None = None,
) -> int:
    agent = build_agent()
    init: SyncState = {
        "date": date,
        "vault_root": str(vault_root),
        "target": target,
        "state_path": str(state_path),
        "dry_run": dry_run,
        "strict": strict,
        "reconcile_missing": reconcile_missing,
        "missing_policy": missing_policy or config.missing_policy(),
        "expected": expected or [],
        "briefings": [],
        "errors": [],
        "log": [],
    }
    final = agent.invoke(init)

    for line in final.get("log", []):
        notify.log_line(line)

    errors = final.get("errors", [])
    acted_any = any("sent →" in line or "dry-run →" in line for line in final.get("log", []))
    if not acted_any and not errors:
        notify.log_line(f"今日无可发布简报，静默退出 ({date}/{target})")

    if errors:
        msg = f"{date} 简报同步 {len(errors)} 项失败: " + "; ".join(errors)
        if dry_run:
            notify.log_line(f"FAILED — {msg}")
        else:
            notify.alert_failure(msg)
        return 1
    return 0
