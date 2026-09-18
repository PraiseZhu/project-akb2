"""Cindy 会话树不得进 git。

`.stignore` 已排除 `.cindy-worktrees`（跨机同步），`.gitignore` 漏了同一目录。
2026-09-19 提交 #112 时 `git status` 把该目录算成未跟踪，commit-github dry-run
把它计进 12 个文件。会话树是本机隔离副本，不入库。
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_gitignore_excludes_cindy_worktrees() -> None:
    patterns = [
        line.strip()
        for line in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert ".cindy-worktrees/" in patterns or ".cindy-worktrees" in patterns, (
        ".gitignore 未排除 .cindy-worktrees/，git status 会把它当未跟踪文件"
    )
    assert not any(p.startswith("!.cindy-worktrees") for p in patterns)
