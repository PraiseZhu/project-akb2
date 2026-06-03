"""路径解析 — 绝对路径，防 cwd 漂移（Codex P1）。"""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """AKB2 仓库根：src/akb2/feishu_sync/paths.py → parents[3]。"""
    return Path(__file__).resolve().parents[3]


def logs_dir() -> Path:
    d = repo_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_file() -> Path:
    return logs_dir() / "feishu-sync.log"


def alert_log_file() -> Path:
    return logs_dir() / "feishu-sync-ALERT.log"


def resolve_state_path(cli_arg: str | None) -> Path:
    """幂等状态文件的绝对路径。

    cli_arg 优先；否则 `<repo_root>/logs/feishu-sync-state.json`。
    **绝不**用 cwd 相对路径——手动跑与 launchd 工作目录不同会读写到不同文件，
    导致 group 重复发（Codex P1）。
    """
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    return logs_dir() / "feishu-sync-state.json"
