"""视频链路测试的状态隔离（autouse fixture 供 `tests/test_video_*.py` 导入）。

为什么需要：`VideoConfig()` 的 `dedup_db_path` 默认走 `common/state_paths` 解析到
**真机状态根**（`~/Library/Application Support/akb2/ledgers/video-dedup.sqlite`）。
测试里任何一处构造裸 `VideoConfig()` 并调 collector/store 方法，就会把 fixture 的
假视频 id 写进**生产去重账本**——那些 id 从此被永久标记"已入库"。
`tests/test_video_config.py` 与 `tests/test_video_collector.py` 里已经存在裸
`VideoConfig()`，只是当下恰好没调用写端，没有任何护栏挡住下一个人。

social 侧早就有 `tests/briefing/conftest.py` 的 autouse `AKB2_STATE_ROOT` 隔离，
视频侧靠每个 fixture 手写 —— 这里补上对称的护栏。

视频测试是 `tests/` 下的平铺模块（没有自己的目录），拿不到目录级 conftest，
所以用"导入 fixture 名"的方式启用：

    from tests.video_state_isolation import isolate_video_state  # noqa: F401
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_video_state(tmp_path_factory, monkeypatch):
    """把 `AKB2_STATE_ROOT` 指到临时目录，杜绝测试写进生产去重账本。"""
    state_root = tmp_path_factory.mktemp("akb2-video-state")
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))
    yield
