"""append-only 状态文件的落盘权限（SC-R2-11②）。

缺陷：`quality-ledger.jsonl` 走普通 `open(path, "a+")`，umask 022 下落成 **0644**
（组/其他可读），而同链路的 `shared-budget-gate.json` / `.lock` 都是 0600。账本行会
带 provider 错误原文（`llm_gateway_429` 事件的 `message` 字段），属机器本地敏感状态。

修复点定在唯一咽喉 `loops.ledger.open_append_secure`，**不在调用点 chmod**：
`quality-ledger.jsonl` 曾有 4 个写者（quality/llm_router、quality/promote、
quality/backfill_requeue、synthesis_assembler；主链路删除后只剩 llm_router），逐点修会让权限退化成
"谁先创建谁决定"的非确定态。

断言绑定的可观测行为：
- umask 022 下**新建**的账本 mode == 0o600（旧代码 0o644）
- 已存在的 0644 账本（生产上早就建好的那份）在下一次追加时被就地收紧
- 幂等/追加语义不变：重复行仍返回 False 且不增行（守住 os.open 改写没打坏主行为）
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from akb2.loops.ledger import STATE_FILE_MODE, append_event


@pytest.fixture()
def _umask_022() -> Iterator[None]:
    """把 umask 钉在 022 —— 缺陷本身就是"默认 umask 下落成 0644"。

    不能靠继承 harness 的 umask：若某个上游 fixture 恰好设了 077，0644 缺陷会自己
    "消失"，测试就变成了不可能失败的绿灯。文件级 fixture（不进共享 conftest）。
    """
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _event(content_hash: str) -> dict:
    return {
        "loop_id": "loop1",
        "node": "llm_router",
        "event": "llm_gateway_429",
        "content_hash": content_hash,
        "ts": "2026-08-04T00:00:00Z",
    }


@pytest.mark.usefixtures("_umask_022")
def test_append_event_creates_mode_0600(tmp_path: Path) -> None:
    """新建账本首次创建即 0600（父目录尚不存在，走 mkdir + O_CREAT 全路径）。"""
    ledger = tmp_path / "ledgers" / "quality-ledger.jsonl"

    assert append_event(str(ledger), _event("h1")) is True

    assert ledger.exists()
    assert _mode(ledger) == 0o600, (
        f"账本落成 {oct(_mode(ledger))}；umask 022 下必须仍是 0600"
    )
    assert STATE_FILE_MODE == 0o600

    # 幂等与追加语义不变（os.open 改写不得打坏主行为）
    assert append_event(str(ledger), _event("h1")) is False
    assert append_event(str(ledger), _event("h2")) is True
    assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 2
    assert _mode(ledger) == 0o600


@pytest.mark.usefixtures("_umask_022")
def test_append_event_tightens_preexisting_0644_ledger(tmp_path: Path) -> None:
    """生产上早已 0644 的账本必须在下一次追加时收紧，否则修复对现网是空操作。"""
    ledger = tmp_path / "ledgers" / "quality-ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("", encoding="utf-8")
    os.chmod(ledger, 0o644)
    assert _mode(ledger) == 0o644

    assert append_event(str(ledger), _event("h1")) is True

    assert _mode(ledger) == 0o600
    assert ledger.read_text(encoding="utf-8").strip() != ""
