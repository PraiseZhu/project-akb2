"""append-only 状态文件的落盘权限（SC-R2-11②）。

缺陷：`quality-ledger.jsonl` 走普通 `open(path, "a+")`，umask 022 下落成 **0644**
（组/其他可读），而同链路的 `shared-budget-gate.json` / `.lock` 都是 0600。账本行会
带 provider 错误原文（`llm_gateway_429` 事件的 `message` 字段），属机器本地敏感状态。

修复点定在唯一咽喉 `loops.ledger.open_append_secure`，**不在调用点 chmod**：
`quality-ledger.jsonl` 至少有 4 个写者（quality/llm_router、quality/promote、
quality/backfill_requeue、briefing 侧 synthesis_assembler），逐点修会让权限退化成
"谁先创建谁决定"的非确定态。`loops/signatures.py` 的 `failures.jsonl` 是同目录姊妹
状态文件，经同一咽喉。

断言绑定的可观测行为：
- umask 022 下**新建**的账本 mode == 0o600（旧代码 0o644）
- 已存在的 0644 账本（生产上早就建好的那份）在下一次追加时被就地收紧
- failures.jsonl 同样 0600（审计连带面，避免"改了账本没改姊妹文件"）
- 幂等/追加语义不变：重复行仍返回 False 且不增行（守住 os.open 改写没打坏主行为）
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from akb2.loops.ledger import STATE_FILE_MODE, append_event
from akb2.loops.signatures import record_failure


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


@pytest.mark.usefixtures("_umask_022")
def test_record_failure_creates_mode_0600(tmp_path: Path) -> None:
    """failures.jsonl 与账本同目录同口径（node 字段会带入库源 URL）。"""
    failures = tmp_path / "ledgers" / "failures.jsonl"

    result = record_failure(str(failures), "fetch_fail", "intake.extract.https://x/y")

    assert result["count"] == 1
    assert _mode(failures) == 0o600


@pytest.mark.usefixtures("_umask_022")
def test_record_failure_escalation_line_keeps_0600(tmp_path: Path) -> None:
    """升级标记行走第二个打开点，权限不得因此漂回 0644。"""
    failures = tmp_path / "ledgers" / "failures.jsonl"

    for _ in range(3):
        result = record_failure(str(failures), "repair_route_failed", "repair_loop")

    assert result["escalated"] is True
    assert _mode(failures) == 0o600
    # 3 条签名行 + 1 条 escalation 行
    assert len(failures.read_text(encoding="utf-8").strip().splitlines()) == 4


# ---------------------------------------------------------------------------
# SC-R4-03 —— 不变量的**脱敏**那一半
# ---------------------------------------------------------------------------
# R2 轮把「node 会带入库源 URL，URL 可能含 query 凭证」写进了 signatures.py 的
# docstring，然后只修了 0600（权限）。不变量要求的是「统一**脱敏** *且* 按 0600 创建」
# 两半 —— 脱敏那半从未落地：node 从没经过 redactor，含 `?access_token=…` 的 URL 原样
# 写进 failures.jsonl 的三处（签名行 / escalation 行 / 返回的 signature）。
#
# 哨兵同样不含已知前缀：只有 redactor 的**键名**规则能救它。若哪天有人把 signatures.py
# 的 redact 调用去掉、或把键名规则改回不认引号，本测试三条断言会同时红。
_NODE_SENTINEL = "opaque-node-sentinel-7b1c4e05"
_NODE_BASE = "intake.extract.https://example.com/a?access_token="


@pytest.mark.usefixtures("_umask_022")
def test_failures_node_field_is_redacted(tmp_path: Path) -> None:
    """node 的凭证样式 query 参数必须在落盘前脱敏，且去重逻辑本身不受影响。"""
    failures = tmp_path / "ledgers" / "failures.jsonl"

    # 同一 URL、仅 token 不同的三次失败：脱敏后归并为同一签名 → 计到 3 → 触发升级。
    # 这既证明脱敏生效，也证明**去重/计数逻辑仍然工作**（脱敏改了签名身份，
    # 但比对与累计仍在同一个值上进行）。
    for suffix in ("", "-rotated", "-again"):
        result = record_failure(
            str(failures), "fetch_fail", f"{_NODE_BASE}{_NODE_SENTINEL}{suffix}&x=1"
        )

    body = failures.read_text(encoding="utf-8")

    # ① 落盘文件（含 escalation 行）不得出现哨兵原文
    assert _NODE_SENTINEL not in body, f"failures.jsonl 仍带 node 凭证原文:\n{body}"
    # ② 返回给调用方的 signature 也不得带原文（它会进日志/异常文本）
    assert _NODE_SENTINEL not in result["signature"]
    assert "<redacted>" in result["signature"], result["signature"]
    # ③ 去重仍然工作：三次同签名累计到 3 并升级，行数 = 3 签名行 + 1 escalation 行
    assert result["count"] == 3, "脱敏后同签名未归并，去重/升级计数被打坏"
    assert result["escalated"] is True
    assert len(body.strip().splitlines()) == 4
    # 权限那一半不得因本次改动回退
    assert _mode(failures) == 0o600

    # ④ 脱敏**不是**把所有 node 抹成同一个值：不同来源仍是不同签名，各自从 1 起算。
    other = record_failure(str(failures), "fetch_fail", "intake.extract.https://other.test/z")
    assert other["count"] == 1, "不同来源被误并成同一签名 —— 脱敏过度，升级判据失效"


# ---------------------------------------------------------------------------
# SC-R5-04 —— 复合键在**真实可达路径**上的脱敏
# ---------------------------------------------------------------------------
# R4 只覆盖了 `?access_token=`（单下划线前缀恰好也是复合键，但当时命中靠的是
# `access[_-]?token` 这个**整体列了复合形态**的分支）。真正暴露前导 `\b` 缺口的是
# `?refresh_token=` / `?client_secret=`：规则里只有词尾的 `token` / `secret`，前导 `\b`
# 遇到 `_` 直接失配 → 原文写进 failures.jsonl。可达路径是
# `quality/intake/extract.py:_record_fetch_fail` 的 `node=f"intake.extract.{url[:80]}"`。
_COMPOUND_NODE_SENTINEL = "opaque-node-compound-8d2f61b4"


@pytest.mark.usefixtures("_umask_022")
def test_failures_node_compound_key_redacted(tmp_path: Path) -> None:
    """URL query 里的下划线复合凭证键必须在落盘前脱敏。"""
    failures = tmp_path / "ledgers" / "failures.jsonl"

    # 前提：哨兵不含任何已知前缀 —— 只有键名规则能救它（防掩盖闸，同 R4 口径）。
    from akb2.common.http_retry import redact_provider_text

    assert redact_provider_text(_COMPOUND_NODE_SENTINEL) == _COMPOUND_NODE_SENTINEL, (
        "哨兵被前缀规则命中 —— 无法证明键名规则是否工作，请换一个不透明哨兵"
    )

    results = [
        record_failure(
            str(failures),
            "fetch_fail",
            f"intake.extract.https://ex.test/a?{key}={_COMPOUND_NODE_SENTINEL}",
        )
        for key in ("refresh_token", "client_secret")
    ]

    body = failures.read_text(encoding="utf-8")
    assert _COMPOUND_NODE_SENTINEL not in body, (
        f"failures.jsonl 仍带复合键凭证原文:\n{body}"
    )
    for result in results:
        assert _COMPOUND_NODE_SENTINEL not in result["signature"], result["signature"]
        assert "<redacted>" in result["signature"], result["signature"]
    # 两个不同的键名 → 仍是两个不同签名，各自从 1 起算（脱敏没有把它们并成一个）
    assert [r["count"] for r in results] == [1, 1]
    # 权限那一半不得回退
    assert _mode(failures) == 0o600


# ---------------------------------------------------------------------------
# SC-R6-04 —— 驼峰复合键在**真实可达路径**上的脱敏
# ---------------------------------------------------------------------------
# R5 补了下划线轴（?client_secret=），驼峰轴（?clientSecret=）当时仍穿透：旧规则按
# regex 词表匹配，`clientSecret` 里的 Secret 前面是词字符 t，任何词边界写法都够不到。
# token 分解后驼峰边界与分隔符同权切开，本测试钉住这条轴在 record_failure 落盘路径生效。
_CAMEL_NODE_SENTINEL = "opaque-node-camel-3f9a72c1"


@pytest.mark.usefixtures("_umask_022")
def test_failures_node_camelcase_key_redacted(tmp_path: Path) -> None:
    """URL query 里的驼峰复合凭证键必须在落盘前脱敏。"""
    failures = tmp_path / "ledgers" / "failures.jsonl"

    # 前提：哨兵不含任何已知前缀 —— 只有键名规则能救它（防掩盖闸，同 R4/R5 口径）。
    from akb2.common.http_retry import redact_provider_text

    assert redact_provider_text(_CAMEL_NODE_SENTINEL) == _CAMEL_NODE_SENTINEL, (
        "哨兵被前缀规则命中 —— 无法证明键名规则是否工作，请换一个不透明哨兵"
    )

    results = [
        record_failure(
            str(failures),
            "fetch_fail",
            f"intake.extract.https://ex.test/a?{key}={_CAMEL_NODE_SENTINEL}",
        )
        for key in ("clientSecret", "refreshToken")
    ]

    body = failures.read_text(encoding="utf-8")
    assert _CAMEL_NODE_SENTINEL not in body, (
        f"failures.jsonl 仍带驼峰键凭证原文:\n{body}"
    )
    for result in results:
        assert _CAMEL_NODE_SENTINEL not in result["signature"], result["signature"]
        assert "<redacted>" in result["signature"], result["signature"]
    # 两个不同的键名 → 仍是两个不同签名，各自从 1 起算（脱敏没有把它们并成一个）
    assert [r["count"] for r in results] == [1, 1]
    # 权限那一半不得回退
    assert _mode(failures) == 0o600
