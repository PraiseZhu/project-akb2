"""真实 vault 隔离闸回归：非 integration 测试打开 /Volumes/AKB2 下文件必须立即失败。"""

from __future__ import annotations

import os

import pytest


def test_open_under_real_vault_is_blocked():
    with pytest.raises(PermissionError, match="真实 vault"):
        open("/Volumes/AKB2/Obsidian/does-not-matter.md", encoding="utf-8")


def test_listdir_real_vault_is_blocked():
    with pytest.raises(PermissionError, match="真实 vault"):
        os.listdir("/Volumes/AKB2")


def test_similar_prefix_is_not_blocked(tmp_path):
    # 只拦该卷本身，不误伤前缀相同的其他路径
    with pytest.raises(FileNotFoundError):
        open("/Volumes/AKB2-other/x.md", encoding="utf-8")
