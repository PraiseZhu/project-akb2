"""test_requeue.py — F26 有界 re-enqueue 行为测试。

覆盖:_is_processed requeue 语义(终态→True;终态+requeue→False;
requeue 后新终态→True)/ CLI 有界拒超额 / 白名单防路径穿越 /
absent·in-flight 不追加 / 幂等 / vault-root fail-closed。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.akb2.quality.promote import _resolve_ledger_path
from src.akb2.quality.requeue import REQUEUE_MAX_PER_RUN
from src.akb2.quality.requeue import main as requeue_main
from src.akb2.quality.runner import _compute_raw_hash, _is_processed


def _write_ledger(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")


def _ev(event: str, h: str) -> dict:
    return {"loop_id": "loop1", "node": event, "event": event,
            "content_hash": h, "ts": "2026-07-06T00:00:00Z", "details": {}}


class TestIsProcessedRequeueSemantics:
    def test_terminal_is_processed(self, tmp_path: Path) -> None:
        led = tmp_path / "l.jsonl"
        _write_ledger(led, [_ev("intake_raw", "h1"), _ev("abort", "h1")])
        assert _is_processed("h1", str(led)) is True

    def test_requeue_after_terminal_reopens(self, tmp_path: Path) -> None:
        """终态后 requeue → 重新可处理(F26 核心语义)。"""
        led = tmp_path / "l.jsonl"
        _write_ledger(led, [_ev("abort", "h1"), _ev("requeue", "h1")])
        assert _is_processed("h1", str(led)) is False

    def test_new_terminal_after_requeue_closes_again(self, tmp_path: Path) -> None:
        """重审后的新终态再次生效(无自动循环)。"""
        led = tmp_path / "l.jsonl"
        _write_ledger(led, [_ev("abort", "h1"), _ev("requeue", "h1"), _ev("abort", "h1")])
        assert _is_processed("h1", str(led)) is True

    def test_other_hash_unaffected(self, tmp_path: Path) -> None:
        led = tmp_path / "l.jsonl"
        _write_ledger(led, [_ev("abort", "h1"), _ev("requeue", "h1")])
        assert _is_processed("h2", str(led)) is False  # h2 无记录=未处理

    def test_no_terminal_never_processed(self, tmp_path: Path) -> None:
        led = tmp_path / "l.jsonl"
        _write_ledger(led, [_ev("intake_raw", "h1"), _ev("admission", "h1")])
        assert _is_processed("h1", str(led)) is False


def _mk_vault(tmp_path: Path, raw_name: str, ledger_events: list[dict]) -> Path:
    vault = tmp_path / "vault"
    (vault / "raw").mkdir(parents=True)
    (vault / "staging").mkdir(parents=True)
    (vault / "raw" / raw_name).write_text("# t\n", encoding="utf-8")
    _write_ledger(vault / "staging" / "quality-ledger.jsonl", ledger_events)
    return vault


class TestRequeueCli:
    def test_happy_path_appends_requeue_event(self, tmp_path: Path, capsys) -> None:
        name = "src--aaaa--raw--2026-07-01.md"
        vault = _mk_vault(tmp_path, name, [])
        h = _compute_raw_hash(str(vault / "raw" / name))
        _write_ledger(vault / "staging" / "quality-ledger.jsonl", [_ev("abort", h)])

        rc = requeue_main([
            "--raw-file", name, "--reason", "test", "--vault-root", str(vault),
        ])
        assert rc == 0
        # 2026-07-28：requeue 改写 runner 共用的那份账本（机器本地状态根，见
        # promote._resolve_ledger_path）。写 vault 旧账本 → runner 看不到 requeue
        # 事件 → 手动重排队静默失效。这里必须按解析后的路径断言，否则测的是
        # 一条 runner 永远读不到的账本。AKB2_STATE_ROOT 由 conftest autouse 隔离。
        resolved = _resolve_ledger_path(vault, "quality-ledger.jsonl")
        assert resolved != vault / "staging" / "quality-ledger.jsonl", (
            "requeue 不应再写 vault 旧账本"
        )
        lines = [json.loads(x) for x in resolved.read_text().splitlines()]
        assert lines[-1]["event"] == "requeue"
        assert lines[-1]["content_hash"] == h
        assert lines[-1]["details"]["reason"] == "test"
        # 与 runner 语义闭环:现在应重新可处理
        assert _is_processed(h, str(resolved)) is False

    def test_bounded_rejects_over_cap(self, tmp_path: Path) -> None:
        """超 REQUEUE_MAX_PER_RUN 拒绝整个调用(有界,宪法 P1)。"""
        name = "src--aaaa--raw--2026-07-01.md"
        vault = _mk_vault(tmp_path, name, [])
        files: list[str] = []
        for i in range(REQUEUE_MAX_PER_RUN + 1):
            files += ["--raw-file", f"s--{i:04d}--raw--2026-07-01.md"]
        rc = requeue_main(files + ["--reason", "t", "--vault-root", str(vault)])
        assert rc == 1

    def test_path_traversal_stripped_and_missing_fails(self, tmp_path: Path) -> None:
        """路径穿越只取 basename;不在 raw 目录 → 该篇 fail,退出码 1。"""
        name = "src--aaaa--raw--2026-07-01.md"
        vault = _mk_vault(tmp_path, name, [])
        rc = requeue_main([
            "--raw-file", "../../etc/passwd--raw--x.md",
            "--reason", "t", "--vault-root", str(vault),
        ])
        assert rc == 1

    def test_absent_hash_skips_without_append(self, tmp_path: Path) -> None:
        """账本无记录(本就在队列)→ 跳过不追加,退出码 0。"""
        name = "src--aaaa--raw--2026-07-01.md"
        vault = _mk_vault(tmp_path, name, [])
        rc = requeue_main([
            "--raw-file", name, "--reason", "t", "--vault-root", str(vault),
        ])
        assert rc == 0
        content = (vault / "staging" / "quality-ledger.jsonl").read_text()
        assert "requeue" not in content

    def test_pending_requeue_not_duplicated(self, tmp_path: Path) -> None:
        """终态后已有 requeue → 不重复追加(幂等)。"""
        name = "src--aaaa--raw--2026-07-01.md"
        vault = _mk_vault(tmp_path, name, [])
        h = _compute_raw_hash(str(vault / "raw" / name))
        _write_ledger(vault / "staging" / "quality-ledger.jsonl",
                      [_ev("abort", h), _ev("requeue", h)])
        rc = requeue_main([
            "--raw-file", name, "--reason", "t", "--vault-root", str(vault),
        ])
        assert rc == 0
        lines = (vault / "staging" / "quality-ledger.jsonl").read_text().splitlines()
        assert sum(1 for x in lines if json.loads(x)["event"] == "requeue") == 1

    def test_missing_vault_root_fail_closed(self, monkeypatch) -> None:
        monkeypatch.delenv("AKB2_VAULT_ROOT", raising=False)
        rc = requeue_main(["--raw-file", "a--raw--x.md", "--reason", "t",
                           "--vault-root", ""])
        assert rc == 1

    def test_loop2_raw_subdir_requeues(self, tmp_path: Path) -> None:
        """loop2 raw 在 raw-loop2，默认 raw/ 找不到会假失败。"""
        vault = tmp_path / "vault"
        name = "x-home-feed--aaaa--raw--2026-09-01.md"
        (vault / "raw-loop2").mkdir(parents=True)
        (vault / "raw").mkdir(parents=True)
        (vault / "staging").mkdir(parents=True)
        (vault / "raw-loop2" / name).write_text("# t\n", encoding="utf-8")
        h = _compute_raw_hash(str(vault / "raw-loop2" / name))
        _write_ledger(vault / "staging" / "quality-ledger.jsonl", [_ev("abort", h)])
        rc = requeue_main([
            "--raw-file", name,
            "--reason", "loop2 short-anchor fix",
            "--vault-root", str(vault),
            "--loop-id", "loop2",
            "--raw-subdir", "raw-loop2",
        ])
        assert rc == 0
        resolved = _resolve_ledger_path(vault, "quality-ledger.jsonl")
        lines = [json.loads(x) for x in resolved.read_text().splitlines()]
        assert lines[-1]["event"] == "requeue"
        assert lines[-1]["loop_id"] == "loop2"
        assert lines[-1]["content_hash"] == h
        assert _is_processed(h, str(resolved)) is False
