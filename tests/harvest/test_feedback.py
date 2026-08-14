"""sc-fb-stats 验收：harvest 反馈统计脚本（wave9/g7）。

测试覆盖：
- 身份断言：fold_reconcile 与 trial_verify 是同一函数对象
- 三源必读：三个路径都被真实读取
- 正例：折叠后各状态分布
- 空输入：三源全空
- 异常：fold_reconcile 抛出 ResolverError
- 坏 JSONL：损坏行被静默跳过
- CLI 入口：argv 不足/正确
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from harvest_feedback import _read_jsonl, harvest_feedback, main  # noqa: E402

# ---------------------------------------------------------------------------
# 身份断言
# ---------------------------------------------------------------------------


def test_fold_reconcile_identity() -> None:
    """确保 harvest_feedback 引用的 fold_reconcile
    与 akb2.harvest.trial_verify.fold_reconcile 是同一函数对象。"""
    import harvest_feedback as fb_mod

    from akb2.harvest.trial_verify import fold_reconcile as tv_fr

    assert fb_mod.fold_reconcile is tv_fr


# ---------------------------------------------------------------------------
# 三源必读
# ---------------------------------------------------------------------------


def test_reads_all_three_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """三个路径都必须被真实读取（monkeypatch 计数反证）。"""
    paths_read: list[str] = []

    def tracking_read(path: str) -> tuple[list[dict], int]:
        paths_read.append(path)
        return [], 0

    monkeypatch.setattr("harvest_feedback._read_jsonl", tracking_read)
    harvest_feedback("/p/a", "/p/b", "/p/c")
    assert len(paths_read) == 3
    assert "/p/a" in paths_read
    assert "/p/b" in paths_read
    assert "/p/c" in paths_read


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    """写 JSONL 文件并返回路径。"""
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    return path


def _committed_row(
    run_id: str = "r1",
    a: str = "a1",
    b: str = "b1",
    c: str = "c1",
    hash_val: str = "h1",
) -> dict:
    return {
        "event": "committed",
        "harvest_run_id": run_id,
        "a": a,
        "b": b,
        "c": c,
        "hash": hash_val,
        "content_fingerprint": "fp1",
        "session_id": "s1",
        "ts": 1000,
    }


def _trial_event(
    kind: str = "reconcile_resolved",
    expected_a: str = "a1",
    expected_b: str = "b1",
    expected_c: str = "c1",
    content_hash: str = "",
) -> dict:
    d: dict = {
        "kind": kind,
        "expected_a": expected_a,
        "expected_b": expected_b,
        "expected_c": expected_c,
    }
    if content_hash:
        d["content_hash"] = content_hash
    return d


def _quality_row(
    event: str = "intake_raw",
    content_hash: str = "",
) -> dict:
    d: dict = {
        "loop_id": "loop1",
        "node": "runner",
        "event": event,
        "content_hash": content_hash,
        "ts": "2026-08-14T00:00:00Z",
        "details": {},
    }
    return d


# ---------------------------------------------------------------------------
# 正例
# ---------------------------------------------------------------------------


class TestHappyPath:
    """三源齐全，fold_reconcile 正常返回。"""

    def test_resolved_phase(self, tmp_path: Path) -> None:
        """空前态 --reconcile_resolved--> 已解除。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [_committed_row()])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [_trial_event()])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [_quality_row()])

        result = harvest_feedback(str(hl), str(tj), str(ql))

        assert result["fold_state"]["phase"] == "RESOLVED"
        assert result["fold_state"]["resolved_hash"] == "h1"
        assert result["counts"]["harvest_committed"] == 1
        assert result["counts"]["trial_events"] == 1
        assert result["counts"]["quality_entries"] == 1

    def test_blocked_phase(self, tmp_path: Path) -> None:
        """blocked 事件后保持 BLOCKED 直到 resolved。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [_committed_row()])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [
            _trial_event(kind="blocked"),
            _trial_event(kind="resolved"),
        ])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])

        result = harvest_feedback(str(hl), str(tj), str(ql))

        assert result["fold_state"]["phase"] == "RESOLVED"
        assert result["fold_state"]["resolved_hash"] == "blocked_resolved"

    def test_unverifiable_phase(self, tmp_path: Path) -> None:
        """unverifiable → manual_attested → RESOLVED。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [
            _trial_event(kind="unverifiable"),
            _trial_event(kind="manual_attested"),
        ])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])

        result = harvest_feedback(str(hl), str(tj), str(ql))

        assert result["fold_state"]["phase"] == "RESOLVED"
        assert result["fold_state"]["resolved_hash"] == "manual_attested"

    def test_quality_summary_by_event(self, tmp_path: Path) -> None:
        """quality 按 event 类型分组正确。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [
            _quality_row(event="intake_raw", content_hash="ch1"),
            _quality_row(event="intake_raw", content_hash="ch2"),
            _quality_row(event="finalize", content_hash="ch3"),
        ])

        result = harvest_feedback(str(hl), str(tj), str(ql))

        assert result["quality_summary"]["total"] == 3
        assert result["quality_summary"]["by_event"]["intake_raw"] == 2
        assert result["quality_summary"]["by_event"]["finalize"] == 1

    def test_cross_reference_overlap(self, tmp_path: Path) -> None:
        """trial 与 quality 的 content_hash 交叉引用统计。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [])
        # 使用 blocked→resolved（不依赖 committed_records）避免 ResolverError
        tj = _write_jsonl(tmp_path / "tj.jsonl", [
            _trial_event(kind="blocked", content_hash="ch1"),
            _trial_event(kind="resolved", content_hash="ch2"),
        ])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [
            _quality_row(content_hash="ch1"),
            _quality_row(content_hash="ch3"),
        ])

        result = harvest_feedback(str(hl), str(tj), str(ql))

        assert result["cross_reference"]["overlapped_content_hashes"] == 1
        assert result["cross_reference"]["trial_only_content_hashes"] == 1
        assert result["cross_reference"]["quality_only_content_hashes"] == 1


# ---------------------------------------------------------------------------
# 空输入
# ---------------------------------------------------------------------------


class TestEmptyInput:
    """三源全空或部分空。"""

    def test_all_empty(self, tmp_path: Path) -> None:
        """三源全空，返回 EMPTY phase。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])

        result = harvest_feedback(str(hl), str(tj), str(ql))

        assert result["fold_state"]["phase"] == "EMPTY"
        assert result["counts"]["harvest_committed"] == 0
        assert result["counts"]["trial_events"] == 0
        assert result["counts"]["quality_entries"] == 0

    def test_missing_files(self, tmp_path: Path) -> None:
        """不存在的文件按空处理。"""
        result = harvest_feedback(
            str(tmp_path / "no-hl.jsonl"),
            str(tmp_path / "no-tj.jsonl"),
            str(tmp_path / "no-ql.jsonl"),
        )

        assert result["fold_state"]["phase"] == "EMPTY"

    def test_harvest_ledger_no_committed(self, tmp_path: Path) -> None:
        """harvest-ledger 有行但无 committed 记录。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [
            {"event": "pending", "harvest_run_id": "r1"},
        ])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])

        result = harvest_feedback(str(hl), str(tj), str(ql))

        assert result["counts"]["harvest_committed"] == 0
        assert result["fold_state"]["phase"] == "EMPTY"


# ---------------------------------------------------------------------------
# 异常处理
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """fold_reconcile 异常被捕获为 fold_state.error。"""

    def test_fold_reconcile_illegal_transition(self, tmp_path: Path) -> None:
        """非法转移被捕获为 fold_state.error。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [])
        # 从 EMPTY 直接 resolved 没有 committed_records → ResolverError
        tj = _write_jsonl(tmp_path / "tj.jsonl", [
            _trial_event(kind="resolved"),
        ])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])

        result = harvest_feedback(str(hl), str(tj), str(ql))

        assert "error" in result["fold_state"]
        # counts 仍被填充
        assert result["counts"]["harvest_committed"] == 0
        assert result["counts"]["trial_events"] == 1

    def test_expected_mismatch(self, tmp_path: Path) -> None:
        """expected_* 与 committed record 不匹配 → ResolverError。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [_committed_row(a="a1")])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [
            _trial_event(expected_a="WRONG"),
        ])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])

        result = harvest_feedback(str(hl), str(tj), str(ql))

        assert "error" in result["fold_state"]

    def test_unexpected_fold_error_propagates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 ResolverError 不应被吞掉，否则程序错误会被伪装成正常统计输出。"""
        import harvest_feedback as fb_mod

        hl = _write_jsonl(tmp_path / "hl.jsonl", [])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])

        def broken_fold(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        monkeypatch.setattr(fb_mod, "fold_reconcile", broken_fold)

        with pytest.raises(RuntimeError, match="boom"):
            harvest_feedback(str(hl), str(tj), str(ql))


# ---------------------------------------------------------------------------
# 坏行
# ---------------------------------------------------------------------------


class TestCorruptedLines:
    """JSONL 损坏行被静默跳过。"""

    def test_corrupted_jsonl(self, tmp_path: Path) -> None:
        """损坏行不影响读取其他行，且被显式计数（不静默丢弃）。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [_committed_row()])
        # 写损坏行 + 合法行
        tj_path = tmp_path / "tj.jsonl"
        with open(tj_path, "w", encoding="utf-8") as fh:
            fh.write("not json\n")
            fh.write(json.dumps(_trial_event()) + "\n")
            fh.write("also not json\n")
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])

        result = harvest_feedback(str(hl), str(tj_path), str(ql))

        assert result["counts"]["trial_events"] == 1  # 仅合法行被计入
        assert result["counts"]["corrupted_lines"]["trial_journal"] == 2
        assert result["counts"]["corrupted_lines"]["harvest_ledger"] == 0
        assert result["counts"]["corrupted_lines"]["quality_ledger"] == 0
        assert result["fold_state"]["phase"] == "RESOLVED"

    def test_non_dict_json_value_counted_as_corrupted(self, tmp_path: Path) -> None:
        """合法 JSON 但非 dict 也属于结构损坏，不能在后续 .get() 处炸掉。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [_committed_row()])
        tj_path = tmp_path / "tj.jsonl"
        with open(tj_path, "w", encoding="utf-8") as fh:
            fh.write("[]\n")
            fh.write("42\n")
            fh.write(json.dumps(_trial_event()) + "\n")
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])

        result = harvest_feedback(str(hl), str(tj_path), str(ql))

        assert result["counts"]["trial_events"] == 1
        assert result["counts"]["corrupted_lines"]["trial_journal"] == 2
        assert result["fold_state"]["phase"] == "RESOLVED"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    """CLI 入口 argv 校验。"""

    def test_cli_help(self) -> None:
        """argv 不足时返回 2。"""
        assert main(["prog"]) == 2

    def test_cli_too_few(self) -> None:
        """argv 缺路径时返回 2。"""
        assert main(["prog", "/a", "/b"]) == 2

    def test_cli_too_many(self) -> None:
        """argv 多出路径时返回 2。"""
        assert main(["prog", "/a", "/b", "/c", "/d"]) == 2

    def test_cli_success(self, tmp_path: Path, capsys) -> None:
        """三路径齐全，CLI 正常输出 JSON 到 stdout。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [_committed_row()])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [_trial_event()])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])

        rc = main(["prog", str(hl), str(tj), str(ql)])
        captured = capsys.readouterr()

        assert rc == 0
        parsed = json.loads(captured.out)
        assert parsed["fold_state"]["phase"] == "RESOLVED"


# ---------------------------------------------------------------------------
# _read_jsonl
# ---------------------------------------------------------------------------


class TestReadJsonl:
    """_read_jsonl 辅助函数。"""

    def test_reads_valid_jsonl(self, tmp_path: Path) -> None:
        p = _write_jsonl(tmp_path / "f.jsonl", [{"a": 1}, {"b": 2}])
        rows, corrupted = _read_jsonl(str(p))
        assert len(rows) == 2
        assert rows[0] == {"a": 1}
        assert rows[1] == {"b": 2}
        assert corrupted == 0

    def test_skips_corrupted(self, tmp_path: Path) -> None:
        """损坏行被跳过但显式计数，不静默丢弃。"""
        p = tmp_path / "f.jsonl"
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('{"a": 1}\n')
            fh.write("not json\n")
            fh.write('{"b": 2}\n')
        rows, corrupted = _read_jsonl(str(p))
        assert len(rows) == 2
        assert corrupted == 1

    def test_skips_empty_lines(self, tmp_path: Path) -> None:
        """空行不计入损坏行计数（不是损坏，是留白）。"""
        p = tmp_path / "f.jsonl"
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('{"a": 1}\n')
            fh.write("\n")
            fh.write('{"b": 2}\n')
        rows, corrupted = _read_jsonl(str(p))
        assert len(rows) == 2
        assert corrupted == 0

    def test_missing_file(self, tmp_path: Path) -> None:
        rows, corrupted = _read_jsonl(str(tmp_path / "nope.jsonl"))
        assert rows == []
        assert corrupted == 0
