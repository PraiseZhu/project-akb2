"""tests/loops 专用 fixtures。

AKB2_TEST_HOME 隔离：设置 HOME 为 tmp_path，避免接触真 vault。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def worktree_root() -> Path:
    """本 worktree 的工程根路径。"""
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_home(tmp_path: Path) -> Path:
    """提供一个隔离的 HOME 目录，内不含真 vault。"""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture
def schema_dir(worktree_root: Path) -> Path:
    """schema 目录路径。"""
    return worktree_root / "schema"


@pytest.fixture
def scripts_dir(worktree_root: Path) -> Path:
    """scripts 目录路径。"""
    return worktree_root / "scripts"
