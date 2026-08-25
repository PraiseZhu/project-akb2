"""账本单一来源：runner / promote / requeue / synthesis_assembler 必须同一份。

2026-07-28 背景：runner 的账本移出 Syncthing 同步范围后（`common/state_paths`），
promote / requeue / synthesis_assembler 仍各自拼 `<vault>/staging/quality-ledger.jsonl`
→ 两份账本分裂。后果不是"少记一条"，而是 runner 的 `_is_processed` 读不到
promote 写的 finalize 事件 → 判"未处理" → **同一篇重复走完整管线并重复入库**。

本文件锁住的可观测行为：四个模块解析出的账本路径逐字相同，且不在同步范围内。
"""

from __future__ import annotations

from pathlib import Path

from akb2.common.state_paths import LEDGER_SUBDIR, is_synced_path, resolve_state_file
from akb2.quality.promote import _resolve_ledger_path


def test_promote_resolves_to_same_path_as_runner(tmp_path, monkeypatch):
    """promote 与 runner 必须解析到同一个账本文件。

    runner 的解析在 `runner.py` main()：resolve_state_file(name, subdir=LEDGER_SUBDIR,
    legacy_path=<vault>/staging/<name>)。此处复现同一调用并逐字比对。
    """
    state_root = tmp_path / "state"
    vault = tmp_path / "vault"
    (vault / "staging").mkdir(parents=True)
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))

    runner_path = Path(
        resolve_state_file(
            "quality-ledger.jsonl",
            subdir=LEDGER_SUBDIR,
            legacy_path=vault / "staging" / "quality-ledger.jsonl",
        )
    )
    promote_path = _resolve_ledger_path(vault, "quality-ledger.jsonl")

    assert promote_path == runner_path, (
        f"账本分裂：promote={promote_path} runner={runner_path}"
    )
    # 不得回落到 vault（vault 在跨机同步范围内，会被 clobber）
    assert vault not in promote_path.parents


def test_failures_ledger_also_shared(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    vault = tmp_path / "vault"
    (vault / "staging").mkdir(parents=True)
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))

    runner_path = Path(
        resolve_state_file(
            "failures.jsonl",
            subdir=LEDGER_SUBDIR,
            legacy_path=vault / "staging" / "failures.jsonl",
        )
    )
    assert _resolve_ledger_path(vault, "failures.jsonl") == runner_path


def test_resolved_ledger_is_not_in_synced_range(tmp_path, monkeypatch):
    """账本必须落在机器本地状态根 —— 落进同步范围就会重演写入丢失。"""
    state_root = tmp_path / "state"
    vault = tmp_path / "vault"
    (vault / "staging").mkdir(parents=True)
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))

    path = _resolve_ledger_path(vault, "quality-ledger.jsonl")
    assert not is_synced_path(path), f"账本落在跨机同步范围内: {path}"


def test_legacy_ledger_content_is_inherited_not_dropped(tmp_path, monkeypatch):
    """旧 vault 账本有内容时必须被继承（否则本机把已入库文章全量重跑）。"""
    state_root = tmp_path / "state"
    vault = tmp_path / "vault"
    (vault / "staging").mkdir(parents=True)
    legacy = vault / "staging" / "quality-ledger.jsonl"
    legacy.write_text('{"event":"finalize","content_hash":"abc"}\n', encoding="utf-8")
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))

    path = _resolve_ledger_path(vault, "quality-ledger.jsonl")

    assert path.exists(), "旧账本存在时新位置应被继承创建"
    assert "abc" in path.read_text(encoding="utf-8")
    assert legacy.exists(), "继承是复制，旧文件必须保留不删"


def test_distill_lane_demotion_default_uses_shared_resolver(tmp_path, monkeypatch):
    """未设 AKB2_QUALITY_LEDGER 时，lane_demotion 必须写机器本地账本，不得写 vault staging。"""
    import json

    from akb2.quality.distill import _record_lane_demotion

    state_root = tmp_path / "state"
    vault = tmp_path / "vault"
    (vault / "staging").mkdir(parents=True)
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))
    monkeypatch.delenv("AKB2_QUALITY_LEDGER", raising=False)
    monkeypatch.setattr(
        "src.akb2.loops.paths.get_akb2_root",
        lambda: vault,
    )

    _record_lane_demotion(
        from_lane="concept",
        to_lane="pattern",
        reason="test shared ledger",
        raw_path="raw/demo.md",
        basis={"component_count": 0, "source_causal_edge_count": 0},
    )

    shared = resolve_state_file(
        "quality-ledger.jsonl",
        subdir=LEDGER_SUBDIR,
        legacy_path=vault / "staging" / "quality-ledger.jsonl",
    )
    legacy = vault / "staging" / "quality-ledger.jsonl"
    assert shared.exists(), "lane_demotion 应写入机器本地账本"
    events = [
        json.loads(line)
        for line in shared.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(ev.get("event") == "lane_demotion" for ev in events)
    assert not legacy.exists(), "不得再写 vault/staging 旧账本"


def test_requeue_and_synthesis_assembler_use_the_shared_resolver():
    """CLI / distill 不得再自行拼 `<vault>/staging/...`（回归防线）。

    断言源码里不存在旧拼法 —— 这类分裂只在生产跑起来才显形，源码级检查是唯一
    能在 CI 抓住它的手段。
    """
    root = Path(__file__).resolve().parents[2] / "src" / "akb2" / "quality"
    for name in ("requeue.py", "synthesis_assembler.py", "promote.py", "distill.py"):
        src = (root / name).read_text(encoding="utf-8")
        # 允许出现在注释/docstring 里（说明历史成因），只禁止真实路径拼接
        offenders = [
            ln.strip()
            for ln in src.splitlines()
            if '"staging"' in ln
            and "quality-ledger.jsonl" in ln
            or ('"staging"' in ln and "failures.jsonl" in ln)
        ]
        # legacy_path=... 是显式的继承来源，属正当用法
        offenders = [ln for ln in offenders if "legacy" not in ln.lower()]
        assert not offenders, f"{name} 仍在自行拼 staging 账本路径: {offenders}"
