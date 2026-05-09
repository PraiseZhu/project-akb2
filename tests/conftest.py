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
from pathlib import Path

import pytest


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
    """
    root = Path(__file__).parent.parent.resolve()

    def _run(script_rel: str, args: list[str] | None = None, home: Path | None = None) -> subprocess.CompletedProcess:
        script = root / script_rel
        env = os.environ.copy()
        if home is not None:
            env["HOME"] = str(home)
        return subprocess.run(
            [str(script)] + (args or []),
            env=env,
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
