"""feishu_sync 状态类型 — Briefing 数据 + LangGraph SyncState。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict


@dataclass
class Briefing:
    """一份已发现并解析的简报。"""

    kind: str  # "ai_trends" | "github"
    date: str
    path: Path
    title: str
    frontmatter: dict
    body: str  # 去掉 frontmatter 的正文 markdown
    json_data: dict | None = None  # 仅 github：结构化数据源
    cards: list[dict] = field(default_factory=list)  # render 阶段填充


class SyncState(TypedDict, total=False):
    """LangGraph 流转状态（节点返回 partial dict，默认 replace 语义）。"""

    date: str
    vault_root: str
    target: str  # "dm" | "group"
    state_path: str
    dry_run: bool
    strict: bool  # SC-7: strict 模式下期望简报缺失 → fail-visible
    reconcile_missing: bool
    missing_policy: str
    expected: list  # SC-7: 期望齐全的 kind 列表（["ai_trends","github"]）
    briefings: list  # list[Briefing]
    errors: list  # list[str]：解析/发送失败（非空 → exit 1 + 告警）
    log: list  # list[str]：每份简报一行结果（sent/skip-*/fail）
