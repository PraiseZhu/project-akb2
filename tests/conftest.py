"""Shared pytest fixtures for Project AKB2 tests.

Design:
- repo_root: pathlib.Path to the project root (resolved once per session)
- run_script: run a bash script with optional HOME override (AKB2_TEST_HOME pattern)
- sha256_file: compute SHA-256 of a single file
- tree_sha256: aggregate SHA-256 of all files in a directory (sorted, deterministic)
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 真实 vault 隔离闸
# ---------------------------------------------------------------------------
# 非 integration 测试不得打开真实 vault 卷里的文件。2026-09-24 事故：Argus 用从未获授权的
# node 跑单测，一条用例读 /Volumes/AKB2 触发 macOS「可移除宗卷」授权弹窗；锁屏无人应答，
# 该授权请求一直挂着，整机所有进程（早报 / intake / gate）访问该卷被挂起 03:13→10:45。
# 在进程内用审计钩子把这类访问变成立即失败，而不是去碰卷、赌授权状态。
_REAL_VAULT_VOLUME = "/Volumes/AKB2"
_real_vault_guard = {"active": False}


def _real_vault_audit_hook(event: str, args: tuple) -> None:
    if not _real_vault_guard["active"] or event not in ("open", "os.listdir", "os.scandir"):
        return
    target = args[0] if args else None
    if isinstance(target, int) or target is None:
        return
    try:
        path = os.fsdecode(target)
    except TypeError:
        return
    if path == _REAL_VAULT_VOLUME or path.startswith(_REAL_VAULT_VOLUME + "/"):
        raise PermissionError(
            f"测试不得访问真实 vault 卷 {path!r}（会触发 macOS 可移除宗卷授权弹窗、"
            "挂起整机对该卷的访问）；改用 tests/ 下的 fixture，或标记 integration"
        )


sys.addaudithook(_real_vault_audit_hook)


@pytest.fixture(autouse=True)
def _forbid_real_vault_volume(request):
    _real_vault_guard["active"] = request.node.get_closest_marker("integration") is None
    try:
        yield
    finally:
        _real_vault_guard["active"] = False


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the Project AKB2 root directory."""
    # conftest.py lives in tests/, so parent is the repo root
    return Path(__file__).parent.parent.resolve()


# ---------------------------------------------------------------------------
# Function-scoped helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def run_script(tmp_path):
    """Return a callable that runs a bash script with an optional isolated HOME.

    Usage::

        result = run_script("scripts/sync-skills-to-codex.sh", ["--write"],
                            home=tmp_path / "home")

    The script receives ``HOME=<tmp_path/home>`` in its environment, so all
    ``~/.codex/skills`` and ``~/Library/...`` paths redirect into the temp tree.
    Production paths on the real filesystem are never touched.

    ``env`` merges extra variables on top of the inherited environment — used by
    the backstop tests to redirect logs away from the repo ``logs/`` directory.
    """
    root = Path(__file__).parent.parent.resolve()

    def _run(
        script_rel: str,
        args: list[str] | None = None,
        home: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        script = root / script_rel
        run_env = os.environ.copy()
        if home is not None:
            run_env["HOME"] = str(home)
        if env:
            run_env.update(env)
        return subprocess.run(
            [str(script)] + (args or []),
            env=run_env,
            capture_output=True,
            text=True,
        )

    return _run


# ---------------------------------------------------------------------------
# SHA helpers (standalone functions — no fixture needed)
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """Return hex SHA-256 of a single file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_sha256(directory: Path) -> str:
    """Return aggregate hex SHA-256 of all files under *directory* (sorted, deterministic).

    Excludes .DS_Store, *.pyc, and __pycache__ paths — matching the snapshot
    logic in post-change-pipeline.sh.
    """
    h = hashlib.sha256()
    files = sorted(
        p for p in directory.rglob("*")
        if p.is_file()
        and p.name != ".DS_Store"
        and p.suffix != ".pyc"
        and "__pycache__" not in p.parts
    )
    for p in files:
        h.update(str(p.relative_to(directory)).encode())
        h.update(sha256_file(p).encode())
    return h.hexdigest()
