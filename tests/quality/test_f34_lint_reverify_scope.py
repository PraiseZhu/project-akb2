"""test_f34_lint_reverify_scope.py — F34 finalize 复验作用域修正。

F33 后 replit 重审(2026-07-08T20:10 账本)死因:Bug-B 打通 pending 供水后,
concept-backlinks.jsonl(机器 JSONL)首次进 finalize touched,被 vault 笔记
lint 误判 1 P1 → 整体回滚。两刀:
  刀1 _lint_reverify 只 lint .md(机器文件不适用笔记 lint,修作用域非放宽:
      所有 .md 内容页复验行为零变化)
  刀2 pending 队列文件纳入 promote 快照 → 失败回滚连同还原追加行
      (复用 F33 Bug-A created_files 机制,消除 stale 供水残留)
"""

from __future__ import annotations

from pathlib import Path

from src.akb2.quality import promote
from src.akb2.quality.promote import (
    _lint_reverify,
    rollback_snapshot,
    snapshot_before_promote,
)


class TestLintReverifyMarkdownOnly:
    def _spy(self, monkeypatch) -> list[Path]:
        calls: list[Path] = []

        def fake_lint(fpath: Path, vault_root: Path) -> tuple[bool, str]:
            calls.append(fpath)
            return True, "lint: 0 P0/P1 findings (total: 0)"

        monkeypatch.setattr(promote, "_run_lint_check", fake_lint)
        return calls

    def test_jsonl_skipped_md_linted(self, tmp_path, monkeypatch) -> None:
        """复现死因:touched 含 .jsonl → 不进 lint;.md 照常复验。"""
        calls = self._spy(monkeypatch)
        md = tmp_path / "page.md"
        md.write_text("---\nstatus: active\n---\nbody\n", encoding="utf-8")
        jsonl = tmp_path / "concept-backlinks.jsonl"
        jsonl.write_text('{"a": 1}\n', encoding="utf-8")

        ok, detail = _lint_reverify([md, jsonl], tmp_path)
        assert ok is True
        assert calls == [md], "F34: 只有 .md 应进 lint 复验"

    def test_md_failure_still_blocks(self, tmp_path, monkeypatch) -> None:
        """严格性不降:.md 复验失败照旧整体 fail。"""

        def fake_lint(fpath: Path, vault_root: Path) -> tuple[bool, str]:
            return False, "lint: 0 P0, 1 P1 findings (exit code 1)"

        monkeypatch.setattr(promote, "_run_lint_check", fake_lint)
        md = tmp_path / "page.md"
        md.write_text("x\n", encoding="utf-8")
        ok, detail = _lint_reverify([md], tmp_path)
        assert ok is False and "page.md" in detail


class TestPendingQueueRollback:
    def test_appended_pending_lines_rolled_back(self, tmp_path, monkeypatch) -> None:
        """失败回滚后 pending 队列还原到 promote 前(stale 供水消除)。"""
        monkeypatch.setattr(promote, "_SNAPSHOTS_DIR", tmp_path / "snapshots")
        vault = tmp_path / "vault"
        pending = vault / "staging" / "pending" / "concept-backlinks.jsonl"
        pending.parent.mkdir(parents=True)
        pending.write_text('{"pre": "existing"}\n', encoding="utf-8")

        sid = snapshot_before_promote(vault, [pending])
        with pending.open("a", encoding="utf-8") as f:
            f.write('{"source_slug": "x", "concept_slug": "agent"}\n')

        assert rollback_snapshot(sid) is True
        assert pending.read_text(encoding="utf-8") == '{"pre": "existing"}\n'

    def test_pending_created_by_promote_deleted_on_rollback(
        self, tmp_path, monkeypatch
    ) -> None:
        """promote 前 pending 文件不存在 → created_files 语义删除(Bug-A 复用)。"""
        monkeypatch.setattr(promote, "_SNAPSHOTS_DIR", tmp_path / "snapshots")
        vault = tmp_path / "vault"
        (vault / "staging" / "pending").mkdir(parents=True)
        pending = vault / "staging" / "pending" / "concept-backlinks.jsonl"

        sid = snapshot_before_promote(vault, [pending])
        pending.write_text('{"source_slug": "x"}\n', encoding="utf-8")

        assert rollback_snapshot(sid) is True
        assert not pending.exists()

    def test_run_promote_snapshots_pending_path(self) -> None:
        """源码断言:auto-promote affected 组装包含 pending 队列文件(防回退)。"""
        import inspect

        src = inspect.getsource(promote)
        assert 'affected.append(vault_root / "staging" / "pending" / "concept-backlinks.jsonl")' in src
