"""LangGraph agent — discover → gate → render → send 线性图。

图刻意保持最小，节点边界清晰，便于后续扩展（加简报源 / 重试分支 / 摘要节点）。
核心逻辑都在各模块的纯函数里，agent 只做编排，便于单测。
"""

from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from . import config, gate, idempotency, notify, render
from . import discover as discover_mod
from .feishu.send import send_card, send_card_dm
from .state import SyncState


def discover_node(state: SyncState) -> dict:
    vault = Path(state["vault_root"])
    briefings, errors = discover_mod.discover(vault, state["date"])
    return {"briefings": briefings, "errors": errors, "log": []}


def gate_node(state: SyncState) -> dict:
    kept = []
    log = list(state.get("log", []))
    for b in state.get("briefings", []):
        if gate.passes_gate(b):
            kept.append(b)
        else:
            log.append(f"{b.kind} skip-gate（audit/gate 未过）")
    return {"briefings": kept, "log": log}


def render_node(state: SyncState) -> dict:
    briefings = state.get("briefings", [])
    for b in briefings:
        b.cards = render.render_cards(b)
    return {"briefings": briefings}


def send_node(state: SyncState) -> dict:
    target = state.get("target", "dm")
    date = state["date"]
    state_path = Path(state["state_path"])
    log = list(state.get("log", []))
    errors = list(state.get("errors", []))

    for b in state.get("briefings", []):
        try:
            is_dup = idempotency.already_sent(state_path, date, b.kind, target)
        except idempotency.StateCorruptError as exc:
            # fail-closed：状态损坏时拒发，避免重复刷群（Codex P1）
            errors.append(f"{b.kind} 幂等状态损坏，拒发以防重复: {exc}")
            log.append(f"{b.kind} fail-state-corrupt")
            continue
        if is_dup:
            log.append(f"{b.kind} skip-dup（{date}/{target} 已发）")
            continue
        # 发送前预留状态（Codex P2）：先持久化再发送，消除"已发成功但状态没写→重跑重发"窗口
        try:
            idempotency.mark_sent(state_path, date, b.kind, target)
        except Exception as exc:  # noqa: BLE001 — 预留失败就不发（fail-closed）
            errors.append(f"{b.kind} 预留状态失败，未发送: {exc}")
            log.append(f"{b.kind} fail-reserve")
            continue
        try:
            for card in b.cards:
                if target == "group":
                    send_card(config.push_chat_id(), card)
                else:
                    send_card_dm(config.dm_open_id(), card)
            log.append(f"{b.kind} sent → {target}")
        except Exception as exc:  # noqa: BLE001 — 发送失败：回滚预留，使重跑可重试
            try:
                idempotency.unmark_sent(state_path, date, b.kind, target)
            except Exception:  # noqa: BLE001
                pass
            errors.append(f"{b.kind} 发送失败: {exc}")
            log.append(f"{b.kind} fail")

    return {"log": log, "errors": errors}


def build_agent():
    g = StateGraph(SyncState)
    g.add_node("discover", discover_node)
    g.add_node("gate", gate_node)
    g.add_node("render", render_node)
    g.add_node("send", send_node)
    g.add_edge(START, "discover")
    g.add_edge("discover", "gate")
    g.add_edge("gate", "render")
    g.add_edge("render", "send")
    g.add_edge("send", END)
    return g.compile()


def run_sync(*, date: str, vault_root: Path, target: str, state_path: Path) -> int:
    """运行同步，返回退出码（0=成功/静默无内容，1=有失败）。"""
    agent = build_agent()
    init: SyncState = {
        "date": date,
        "vault_root": str(vault_root),
        "target": target,
        "state_path": str(state_path),
        "briefings": [],
        "errors": [],
        "log": [],
    }
    final = agent.invoke(init)

    for line in final.get("log", []):
        notify.log_line(line)

    errors = final.get("errors", [])
    sent_any = any("sent →" in line for line in final.get("log", []))

    if not sent_any and not errors:
        notify.log_line(f"今日无可发布简报，静默退出 ({date}/{target})")

    if errors:
        notify.alert_failure(
            f"{date} 简报同步 {len(errors)} 项失败: " + "; ".join(errors)
        )
        return 1
    return 0
