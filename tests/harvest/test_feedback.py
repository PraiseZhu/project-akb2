"""sc-fb-stats 验收：harvest 反馈统计脚本（wave9/g7）+ p2a 联结与复扫（wave1/g4）。

测试覆盖：
- 身份断言：fold_reconcile 与 trial_verify 是同一函数对象
- 三源必读：三个路径都被真实读取
- 正例：折叠后各状态分布
- 空输入：三源全空
- 异常：fold_reconcile 抛出 ResolverError
- 坏 JSONL：损坏行被静默跳过
- CLI 入口：argv 不足/正确
- p2a-sc1：按 harvest_run_id exact-key 联结 raw；缺 run_id 走 content_key
  二级映射；不可联结进 unmatched 不静默丢弃；前缀匹配变异转红
- p2a-sc2：CLI 输出复扫 redaction——伪凭证默认 Reject（exit 3 不回显
  原文）；--redact 含 [REDACTED] 不含原文；跳过复扫变异转红
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from harvest_feedback import (  # noqa: E402
    _read_jsonl,
    harvest_feedback,
    join_raw_by_run_id,
    main,
)

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
    raw_abspath: str = "/vault/raw/r1.json",
    content_key: str = "",
) -> dict:
    d: dict = {
        "event": "committed",
        "harvest_run_id": run_id,
        "a": a,
        "b": b,
        "c": c,
        "hash": hash_val,
        "content_fingerprint": "fp1",
        "session_id": "s1",
        "ts": 1000,
        "raw_abspath": raw_abspath,
        "pipeline_content_hash": hash_val,
    }
    if content_key:
        d["content_key"] = content_key
    return d


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


# ---------------------------------------------------------------------------
# p2a-sc1: run_id exact-key 联结
# ---------------------------------------------------------------------------


class TestRunIdJoin:
    """按 harvest_run_id exact-key 联结 raw（wave1/g4）。"""

    def test_joinable_via_run_id(self, tmp_path: Path) -> None:
        """可联结：trial/quality 行 run_id 与 committed 逐字相等，raw 钉死
        committed 行 raw_abspath。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [
            _committed_row(run_id="run-1", raw_abspath="/vault/raw/a.json"),
            _committed_row(run_id="run-2", raw_abspath="/vault/raw/b.json"),
        ])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [
            {"kind": "blocked", "harvest_run_id": "run-1", "content_hash": "ch1"},
            {"kind": "resolved", "harvest_run_id": "run-1"},
        ])
        ql_rows = [dict(_quality_row(content_hash="ch1"), harvest_run_id="run-2")]
        ql = _write_jsonl(tmp_path / "ql.jsonl", ql_rows)

        result = harvest_feedback(str(hl), str(tj), str(ql))

        rj = result["raw_join"]
        assert rj["by_run_id"]["run-1"]["raw_abspath"] == "/vault/raw/a.json"
        assert rj["by_run_id"]["run-2"]["raw_abspath"] == "/vault/raw/b.json"
        assert rj["by_run_id"]["run-1"]["trial_events_joined"] == 2
        assert rj["by_run_id"]["run-2"]["quality_entries_joined"] == 1
        assert rj["joined_via_run_id"] == 3
        assert rj["joined_via_content_key"] == 0
        assert rj["unmatched"]["trial_events"] == 0
        assert rj["unmatched"]["quality_entries"] == 0
        # 前缀不相等不联结：run-1x 不是 run-1
        result2 = join_raw_by_run_id(
            [{"event": "committed", "harvest_run_id": "run-1",
              "raw_abspath": "/vault/raw/a.json"}],
            [{"kind": "blocked", "harvest_run_id": "run-1x"}],
            [],
        )
        assert result2["unmatched"]["trial_events"] == 1
        assert result2["unmatched"]["trial_run_ids"] == ["run-1x"]
        assert result2["by_run_id"]["run-1"]["trial_events_joined"] == 0

    def test_secondary_join_via_content_key(self, tmp_path: Path) -> None:
        """缺 run_id 的 quality 行经 content_key 二级映射联结。"""
        committed = [
            _committed_row(run_id="run-A", raw_abspath="/vault/raw/a.json",
                           content_key="ck-a"),
        ]
        quality = [
            {"event": "finalize", "content_key": "ck-a"},      # 二级映射命中
            {"event": "finalize", "content_key": "ck-z"},      # 映射不中 → unmatched
            {"event": "intake_raw"},                            # 无键 → unmatched
        ]
        rj = join_raw_by_run_id(committed, [], quality)
        assert rj["joined_via_content_key"] == 1
        assert rj["by_run_id"]["run-A"]["quality_entries_joined"] == 1
        assert rj["unmatched"]["quality_entries"] == 2
        assert rj["unmatched"]["quality_run_ids"] == []  # 无自带 run_id 不列

    def test_mismatched_run_id_no_content_key_fallback(self) -> None:
        """行自带非空 run_id 且未逐字命中 committed → 不得走 content_key 兜底。

        否则 run-B 的反馈行会经共享 content_key 错记到 run-A 名下
        （reviewer major finding）：只允许缺 run_id 的行走二级映射。
        """
        committed = [
            _committed_row(run_id="run-A", raw_abspath="/vault/raw/a.json",
                           content_key="ck-a"),
        ]
        trial = [{"kind": "blocked", "harvest_run_id": "run-B",
                  "content_key": "ck-a"}]
        quality = [{"event": "finalize", "harvest_run_id": "run-B",
                    "content_key": "ck-a"}]
        rj = join_raw_by_run_id(committed, trial, quality)
        assert rj["joined_via_content_key"] == 0  # 不经 content_key 错记
        assert rj["joined_via_run_id"] == 0
        assert rj["by_run_id"]["run-A"]["trial_events_joined"] == 0
        assert rj["by_run_id"]["run-A"]["quality_entries_joined"] == 0
        assert rj["unmatched"]["trial_events"] == 1
        assert rj["unmatched"]["quality_entries"] == 1
        assert rj["unmatched"]["trial_run_ids"] == ["run-B"]
        assert rj["unmatched"]["quality_run_ids"] == ["run-B"]

    def test_missing_run_id_still_joins_via_content_key(self) -> None:
        """合法兜底保留：缺 run_id（空串/缺键）+ content_key 命中仍可联结。"""
        committed = [
            _committed_row(run_id="run-A", raw_abspath="/vault/raw/a.json",
                           content_key="ck-a"),
        ]
        trial = [{"kind": "blocked", "harvest_run_id": "",
                  "content_key": "ck-a"}]       # 空 run_id → 允许兜底
        quality = [{"event": "finalize",
                    "content_key": "ck-a"}]     # 缺键 → 允许兜底
        rj = join_raw_by_run_id(committed, trial, quality)
        assert rj["joined_via_content_key"] == 2
        assert rj["by_run_id"]["run-A"]["trial_events_joined"] == 1
        assert rj["by_run_id"]["run-A"]["quality_entries_joined"] == 1
        assert rj["unmatched"]["trial_events"] == 0
        assert rj["unmatched"]["quality_entries"] == 0

    @pytest.mark.parametrize("falsy_rid", [0, False])
    def test_falsy_run_id_is_present_no_content_key_fallback(self, falsy_rid) -> None:
        """非空 falsy run_id（0/False）视同自带 run_id，禁止 content_key 兜底。

        第二轮 GPT 单审 major finding：``or ""`` 会把 0/False 吞成缺
        run_id，再经共享 content_key 错记到 run-A 名下。修后 0/False →
        str(value) 做 exact-key（"0"/"False" 不命中 run-A）→ unmatched，
        且其 run_id 进 unmatched 清单供排障（与计数同一裁定语义）。
        """
        committed = [
            _committed_row(run_id="run-A", raw_abspath="/vault/raw/a.json",
                           content_key="ck-a"),
        ]
        trial = [{"kind": "blocked", "harvest_run_id": falsy_rid,
                  "content_key": "ck-a"}]
        quality = [{"event": "finalize", "harvest_run_id": falsy_rid,
                    "content_key": "ck-a"}]
        rj = join_raw_by_run_id(committed, trial, quality)
        assert rj["joined_via_content_key"] == 0  # 不经 content_key 错记
        assert rj["joined_via_run_id"] == 0
        assert rj["by_run_id"]["run-A"]["trial_events_joined"] == 0
        assert rj["by_run_id"]["run-A"]["quality_entries_joined"] == 0
        assert rj["unmatched"]["trial_events"] == 1
        assert rj["unmatched"]["quality_entries"] == 1
        # 自带 run_id 一并进清单（str(0)="0"、str(False)="False"）
        assert rj["unmatched"]["trial_run_ids"] == [str(falsy_rid)]
        assert rj["unmatched"]["quality_run_ids"] == [str(falsy_rid)]

    def test_missing_raw_goes_unmatched(self, tmp_path: Path) -> None:
        """查无 raw（run_id 未知/typo）进 unmatched，不静默丢弃。"""
        committed = [_committed_row(run_id="real-run")]
        trial = [
            {"kind": "blocked", "harvest_run_id": "typo-run"},
            {"kind": "resolved", "harvest_run_id": "typo-run"},
        ]
        rj = join_raw_by_run_id(committed, trial, [])
        assert rj["unmatched"]["trial_events"] == 2
        assert rj["unmatched"]["trial_run_ids"] == ["typo-run"]
        assert rj["by_run_id"]["real-run"]["trial_events_joined"] == 0

    def test_empty_ledger_all_unmatched(self, tmp_path: Path) -> None:
        """ledger 空（无 committed）→ 所有带 run_id 的行都 unmatched。"""
        trial = [{"kind": "unverifiable", "harvest_run_id": "r-x"}]
        quality = [{"event": "intake_raw", "harvest_run_id": "r-x"}]
        rj = join_raw_by_run_id([], trial, quality)
        assert rj["by_run_id"] == {}
        assert rj["unmatched"]["trial_events"] == 1
        assert rj["unmatched"]["quality_entries"] == 1
        assert rj["unmatched"]["trial_run_ids"] == ["r-x"]
        assert rj["unmatched"]["quality_run_ids"] == ["r-x"]

    def test_samples_match_unmatched_verdict(self) -> None:
        """unmatched.samples 与 unmatched 计数同一裁定路径：经 content_key
        联结成功的行（无 run_id）不得混进 samples。"""
        committed = [
            _committed_row(run_id="run-A", raw_abspath="/vault/raw/a.json",
                           content_key="ck-a"),
        ]
        trial = [
            {"kind": "unverifiable", "harvest_run_id": "run-ghost"},  # 不可联结
            {"kind": "manual_attested", "harvest_run_id": "run-ghost"},
        ]
        quality = [
            {"event": "finalize", "content_key": "ck-a"},  # content_key 联结成功
        ]
        rj = join_raw_by_run_id(committed, trial, quality)
        samples = rj["unmatched"]["samples"]
        assert len(samples) == 2  # 只含两条不可联结 trial
        assert all(s.get("harvest_run_id") == "run-ghost" for s in samples)
        # 联结成功的 quality 行不在 samples 里
        assert not any(s.get("event") == "finalize" for s in samples)

    def test_exactly_three_sources_no_extra_input(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """恰三源：真实读三个 ledger 之外，不 open 任何其他文件。"""
        import builtins

        hl = _write_jsonl(tmp_path / "hl.jsonl", [_committed_row()])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [_trial_event()])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [_quality_row()])

        opened: list[str] = []
        real_open = builtins.open

        def spy_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", spy_open)
        harvest_feedback(str(hl), str(tj), str(ql))

        allowed = {str(p) for p in (hl, tj, ql)}
        extra = [p for p in opened if p not in allowed]
        assert extra == []  # 除三源外零额外文件读取

    def test_mutation_prefix_match_turns_red(self) -> None:
        """反向变异：exact 改前缀匹配后此用例必须失败。

        若 join_raw_by_run_id 被改成前缀匹配（如 startswith），run-pre
        会错误联结到 run-prefix-run 的 committed 行，raw_abspath 断言转红。
        """
        committed = [
            _committed_row(run_id="run-prefix-run", raw_abspath="/vault/raw/real.json"),
        ]
        trial = [{"kind": "blocked", "harvest_run_id": "run-pre"}]
        rj = join_raw_by_run_id(committed, trial, [])
        # exact-key 下 run-pre 不可联结
        assert rj["unmatched"]["trial_events"] == 1
        assert rj["by_run_id"]["run-prefix-run"]["trial_events_joined"] == 0


# ---------------------------------------------------------------------------
# p2a-sc2: CLI 输出复扫 redaction
# ---------------------------------------------------------------------------


class TestOutputRescan:
    """CLI 输出前 json.dumps 全文复扫（wave1/g4）。"""

    def _write_credential_journal(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """三源中 journal 携带伪凭证(github token 形态)。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [
            {"kind": "unverifiable", "harvest_run_id": "r-cred",
             "details": "token ghp_AbCdEfGhIjKlMnOpQrStUvWxYz1234567890 leaked"},
        ])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])
        return hl, tj, ql

    def test_default_reject_exit_3_no_plaintext(self, tmp_path: Path, capsys) -> None:
        """默认 Reject：伪凭证 journal → exit 3，stdout 无输出、stderr 不含原文。"""
        hl, tj, ql = self._write_credential_journal(tmp_path)

        rc = main(["prog", str(hl), str(tj), str(ql)])
        captured = capsys.readouterr()

        assert rc == 3
        assert captured.out == ""  # 未落任何输出
        assert "ghp_AbCdEfGh" not in captured.err  # 不回显原文
        assert "github_token" in captured.err  # 点名模式类

    def test_redact_flag_replaces_and_outputs(self, tmp_path: Path, capsys) -> None:
        """--redact：输出含 [REDACTED] 且不含凭证原文。"""
        hl, tj, ql = self._write_credential_journal(tmp_path)

        rc = main(["prog", str(hl), str(tj), str(ql), "--redact"])
        captured = capsys.readouterr()

        assert rc == 0
        assert "ghp_AbCdEfGh" not in captured.out
        assert "[REDACTED]" in captured.out
        parsed = json.loads(captured.out)  # 输出仍是合法 JSON
        assert parsed["counts"]["trial_events"] == 1

    def test_clean_output_passes_untouched(self, tmp_path: Path, capsys) -> None:
        """无凭证输出原样通过（ Reject 不误伤正常统计输出）。"""
        hl = _write_jsonl(tmp_path / "hl.jsonl", [_committed_row()])
        tj = _write_jsonl(tmp_path / "tj.jsonl", [_trial_event()])
        ql = _write_jsonl(tmp_path / "ql.jsonl", [])

        rc = main(["prog", str(hl), str(tj), str(ql)])
        captured = capsys.readouterr()

        assert rc == 0
        assert "[REDACTED]" not in captured.out
        parsed = json.loads(captured.out)
        assert parsed["fold_state"]["phase"] == "RESOLVED"

    def test_scan_text_shares_rule_set_with_assembler(self) -> None:
        """复扫与 assembler 同一规则集：scan_text 是 redaction 模块的薄封装，
        不复制 pattern 表（identity 断言：模块内只有一处 _PATTERNS 定义）。"""
        import inspect

        import akb2.harvest.redaction as red_mod
        from akb2.harvest import assembler as asm_mod

        # assembler 的落盘扫描与 scan_text 用同一底层实现
        src = inspect.getsource(red_mod.scan_text)
        assert "_scan_markdown" in src  # 复用同一编译模式族
        assert "_PATTERNS" not in src.replace("_scan_markdown", "")  # 零模式定义
        # pattern 表全仓只此一处定义
        harvest_dir = Path(red_mod.__file__).parent
        pattern_defs = []
        for py in harvest_dir.glob("*.py"):
            text = py.read_text(encoding="utf-8")
            if re.search(r"^_PATTERNS\s*[:=]", text, re.MULTILINE):
                pattern_defs.append(py.name)
        assert pattern_defs == ["redaction.py"]
        # assembler 确实经 redaction 模块复用(不是自己的副本)
        assert asm_mod.scan_and_redact is red_mod.scan_and_redact

    def test_mutation_skip_rescan_turns_red(self, tmp_path: Path, capsys) -> None:
        """反向变异：跳过复扫后两个用例必须失败。

        monkeypatch main 里的 scan_text 为直通函数,模拟"复扫被跳过"——
        默认 Reject 用例(期望 exit 3)与 --redact 用例(期望 [REDACTED])
        在直通下都会转红。
        """
        import harvest_feedback as fb_mod

        def skip_scan(text, *, policy):  # type: ignore[no-untyped-def]
            return text, []

        hl, tj, ql = self._write_credential_journal(tmp_path)
        # 直通变异下:默认 Reject 用例的 rc=3 期望必然失守(rc 变 0),
        # 且输出必含原文 —— 两个锚点任一都可检出"复扫被跳过"。
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(fb_mod, "scan_text", skip_scan)
            rc = main(["prog", str(hl), str(tj), str(ql)])
            leaked = capsys.readouterr().out
        assert rc == 0  # 直通下不再拒绝(与原实现的 rc=3 相悖)
        assert "ghp_AbCdEfGh" in leaked  # 跳过复扫 = 原文泄漏(变异检测锚点)
