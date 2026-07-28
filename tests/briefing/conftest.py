"""tests/briefing/conftest.py — briefing 测试的状态隔离。

social 链路新增了两处**进程外落盘**：
  - 跨日去重账本（`akb2.common.state_paths` 解析的机器本地状态根）
  - 降级诊断 JSON（`AKB2_BRIEFING_DIAG_DIR`）
默认位置是真机状态目录 / 仓库根，测试若不隔离会污染生产账本
（把 fixture id 标成"已入库"）并向仓库根写诊断文件。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_briefing_state(tmp_path_factory, monkeypatch):
    state_root = tmp_path_factory.mktemp("akb2-state")
    diag_dir = tmp_path_factory.mktemp("akb2-diag")
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))
    monkeypatch.setenv("AKB2_BRIEFING_DIAG_DIR", str(diag_dir))
    yield
