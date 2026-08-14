"""sc-trial-core 验收：试投判定唯一底座状态转移。

测试覆盖：
- 三条合法边：空前态→已解除、blocked→已解除、unverifiable→已解除
- 非法转移：所有非转移表组合 fail-closed
- 同 events 不同 committed_records 返回不同状态
- 状态设置事件（blocked / unverifiable）
- 纯函数与 CLI 一致性
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from akb2.harvest.trial_verify import (  # noqa: E402
    CLI_EXIT_INVALID,
    CLI_EXIT_OK,
    ReconcilePhase,
    ReconcileState,
    ResolverError,
    check_consistency,
    fold_reconcile,
    format_evidence,
    resolve_trial,
    validate_resolved,
)

CLI_ENV = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------


def _record(a: str = "a1", b: str = "b1", c: str = "c1", hash_val: str = "h1") -> dict:
    return {"a": a, "b": b, "c": c, "hash": hash_val}


def _reconcile_event(
    expected_a: str = "a1",
    expected_b: str = "b1",
    expected_c: str = "c1",
) -> dict:
    return {
        "kind": "reconcile_resolved",
        "expected_a": expected_a,
        "expected_b": expected_b,
        "expected_c": expected_c,
    }


def _blocked_event() -> dict:
    return {"kind": "blocked"}


def _unverifiable_event() -> dict:
    return {"kind": "unverifiable"}


def _resolved_event() -> dict:
    return {"kind": "resolved"}


def _manual_attested_event() -> dict:
    return {"kind": "manual_attested"}


# ---------------------------------------------------------------------------
# 正例：三条合法转移边
# ---------------------------------------------------------------------------


class TestLegalTransitions:
    """三条合法转移边，每条都可观察 phase 与 resolved 字段。"""

    def test_empty_to_resolved_via_reconcile_resolved(self) -> None:
        """空前态 --reconcile_resolved--> 已解除（expected_* 匹配 + hash 非空）。"""
        state = fold_reconcile(
            [_reconcile_event()],
            committed_records=[_record()],
        )
        assert state.phase == ReconcilePhase.RESOLVED
        assert state.resolved_hash == "h1"
        assert "reconcile_resolved" in state.resolved_details

    def test_blocked_to_resolved_via_resolved(self) -> None:
        """blocked --resolved--> 已解除。"""
        state = fold_reconcile(
            [_blocked_event(), _resolved_event()],
            committed_records=[],
        )
        assert state.phase == ReconcilePhase.RESOLVED
        assert state.resolved_hash == "blocked_resolved"
        assert "blocked" in state.resolved_details

    def test_unverifiable_to_resolved_via_manual_attested(self) -> None:
        """unverifiable --manual_attested--> 已解除。"""
        state = fold_reconcile(
            [_unverifiable_event(), _manual_attested_event()],
            committed_records=[],
        )
        assert state.phase == ReconcilePhase.RESOLVED
        assert state.resolved_hash == "manual_attested"
        assert "unverifiable" in state.resolved_details


# ---------------------------------------------------------------------------
# 潘多拉盒：同 events 一有一无必须返回不同状态
# ---------------------------------------------------------------------------


class TestPandora:
    """同 events、committed_records 一有一无必须返回不同状态。"""

    def test_same_events_different_records(self) -> None:
        """有 committed_records 时 reconcile_resolved 通过，无记录时报错。"""
        events = [_reconcile_event()]

        s1 = fold_reconcile(events, committed_records=[_record()])
        assert s1.phase == ReconcilePhase.RESOLVED

        with pytest.raises(ResolverError, match="需要恰好 1 条"):
            fold_reconcile(events, committed_records=[])

    def test_match_mismatch_different_phases(self) -> None:
        """expected_* 匹配不匹配产生不同状态。"""
        events = [_reconcile_event(expected_a="x", expected_b="y", expected_c="z")]

        s1 = fold_reconcile(events, committed_records=[_record(a="x", b="y", c="z", hash_val="h2")])
        assert s1.phase == ReconcilePhase.RESOLVED

        with pytest.raises(ResolverError, match="不匹配"):
            fold_reconcile(events, committed_records=[_record(a="a1", b="b1", c="c1")])


# ---------------------------------------------------------------------------
# 负样例：非法转移 fail-closed
# ---------------------------------------------------------------------------


class TestIllegalTransitions:
    """非转移表组合一律 fail-closed。"""

    def test_empty_resolved_illegal(self) -> None:
        with pytest.raises(ResolverError, match="非法转移"):
            fold_reconcile([_resolved_event()], committed_records=[])

    def test_empty_manual_attested_illegal(self) -> None:
        with pytest.raises(ResolverError, match="非法转移"):
            fold_reconcile([_manual_attested_event()], committed_records=[])

    def test_blocked_reconcile_resolved_illegal(self) -> None:
        with pytest.raises(ResolverError, match="非法转移"):
            fold_reconcile(
                [_blocked_event(), _reconcile_event()],
                committed_records=[],
            )

    def test_blocked_manual_attested_illegal(self) -> None:
        with pytest.raises(ResolverError, match="非法转移"):
            fold_reconcile(
                [_blocked_event(), _manual_attested_event()],
                committed_records=[],
            )

    def test_unverifiable_reconcile_resolved_illegal(self) -> None:
        with pytest.raises(ResolverError, match="非法转移"):
            fold_reconcile(
                [_unverifiable_event(), _reconcile_event()],
                committed_records=[],
            )

    def test_unverifiable_resolved_illegal(self) -> None:
        with pytest.raises(ResolverError, match="非法转移"):
            fold_reconcile(
                [_unverifiable_event(), _resolved_event()],
                committed_records=[],
            )

    def test_unknown_event_kind_illegal(self) -> None:
        with pytest.raises(ResolverError, match="非法转移"):
            fold_reconcile([{"kind": "unknown_event"}], committed_records=[])

    def test_empty_event_no_kind_illegal(self) -> None:
        with pytest.raises(ResolverError, match="非法转移"):
            fold_reconcile([{"foo": "bar"}], committed_records=[])

    def test_blocked_from_non_empty_illegal(self) -> None:
        with pytest.raises(ResolverError, match="仅允许从空前态"):
            fold_reconcile(
                [_blocked_event(), _blocked_event()],
                committed_records=[],
            )

    def test_unverifiable_from_non_empty_illegal(self) -> None:
        with pytest.raises(ResolverError, match="仅允许从空前态"):
            fold_reconcile(
                [_blocked_event(), _unverifiable_event()],
                committed_records=[],
            )


# ---------------------------------------------------------------------------
# 空前态 reconcile_resolved 条件校验
# ---------------------------------------------------------------------------


class TestEmptyReconcileConditions:
    """空前态 --reconcile_resolved 的条件校验。"""

    def test_zero_records_fails(self) -> None:
        with pytest.raises(ResolverError, match="需要恰好 1 条"):
            fold_reconcile([_reconcile_event()], committed_records=[])

    def test_multi_records_fails(self) -> None:
        with pytest.raises(ResolverError, match="需要恰好 1 条"):
            fold_reconcile(
                [_reconcile_event()],
                committed_records=[_record(), _record(a="a2")],
            )

    def test_empty_hash_fails(self) -> None:
        with pytest.raises(ResolverError, match="hash 为空"):
            fold_reconcile(
                [_reconcile_event()],
                committed_records=[_record(hash_val="")],
            )

    def test_expected_a_mismatch_fails(self) -> None:
        with pytest.raises(ResolverError, match="不匹配"):
            fold_reconcile(
                [_reconcile_event(expected_a="wrong")],
                committed_records=[_record()],
            )

    def test_expected_b_mismatch_fails(self) -> None:
        with pytest.raises(ResolverError, match="不匹配"):
            fold_reconcile(
                [_reconcile_event(expected_b="wrong")],
                committed_records=[_record()],
            )

    def test_expected_c_mismatch_fails(self) -> None:
        with pytest.raises(ResolverError, match="不匹配"):
            fold_reconcile(
                [_reconcile_event(expected_c="wrong")],
                committed_records=[_record()],
            )

    def test_all_fields_match_enough(self) -> None:
        """三字段全部匹配 + 非空 hash 即可通过。"""
        state = fold_reconcile(
            [_reconcile_event(expected_a="x", expected_b="y", expected_c="z")],
            committed_records=[_record(a="x", b="y", c="z", hash_val="h2")],
        )
        assert state.phase == ReconcilePhase.RESOLVED
        assert state.resolved_hash == "h2"


# ---------------------------------------------------------------------------
# 纯函数 validate_resolved
# ---------------------------------------------------------------------------


class TestValidateResolved:
    def test_resolved_state_ok(self) -> None:
        state = ReconcileState(
            phase=ReconcilePhase.RESOLVED,
            resolved_hash="h1",
            resolved_details="ok",
        )
        assert validate_resolved(state, []) == []

    def test_resolved_state_missing_hash(self) -> None:
        state = ReconcileState(
            phase=ReconcilePhase.RESOLVED,
            resolved_hash="",
            resolved_details="ok",
        )
        vs = validate_resolved(state, [])
        assert vs
        assert "hash" in vs[0]

    def test_resolved_state_missing_details(self) -> None:
        state = ReconcileState(
            phase=ReconcilePhase.RESOLVED,
            resolved_hash="h1",
            resolved_details="",
        )
        vs = validate_resolved(state, [])
        assert vs
        assert "details" in vs[0]

    def test_non_resolved_state_skipped(self) -> None:
        for phase in (ReconcilePhase.EMPTY, ReconcilePhase.BLOCKED, ReconcilePhase.UNVERIFIABLE):
            state = ReconcileState(phase=phase)
            assert validate_resolved(state, []) == []


# ---------------------------------------------------------------------------
# 纯函数 check_consistency
# ---------------------------------------------------------------------------


class TestCheckConsistency:
    def test_empty_events_empty_records_ok(self) -> None:
        assert check_consistency([], []) == []

    def test_no_events_but_has_records(self) -> None:
        vs = check_consistency([], [_record()])
        assert vs
        assert "一致性" in vs[0]

    def test_has_reconcile_no_records(self) -> None:
        vs = check_consistency([_reconcile_event()], [])
        assert vs
        assert "committed_records 为空" in vs[0]

    def test_has_records_no_reconcile(self) -> None:
        vs = check_consistency([_blocked_event()], [_record()])
        assert vs
        assert "无 reconcile_resolved 事件" in vs[0]

    def test_has_reconcile_and_records_ok(self) -> None:
        vs = check_consistency([_reconcile_event()], [_record()])
        assert vs == []


# ---------------------------------------------------------------------------
# 纯函数 format_evidence
# ---------------------------------------------------------------------------


class TestFormatEvidence:
    def test_resolved_state_returns_text(self) -> None:
        state = ReconcileState(
            phase=ReconcilePhase.RESOLVED,
            resolved_hash="h1",
            resolved_details="some details",
        )
        text = format_evidence(state)
        assert text
        assert "resolved" in text
        assert "h1" in text

    def test_non_resolved_returns_empty(self) -> None:
        for phase in (ReconcilePhase.EMPTY, ReconcilePhase.BLOCKED, ReconcilePhase.UNVERIFIABLE):
            state = ReconcileState(phase=phase)
            assert format_evidence(state) == ""


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class TestResolveTrial:
    def test_valid_chain(self) -> None:
        state, violations = resolve_trial(
            [_reconcile_event()],
            committed_records=[_record()],
        )
        assert state.phase == ReconcilePhase.RESOLVED
        assert violations == []

    def test_illegal_transition(self) -> None:
        state, violations = resolve_trial(
            [_resolved_event()],
            committed_records=[],
        )
        assert state.phase == ReconcilePhase.EMPTY
        assert violations
        assert "非法转移" in violations[0]

    def test_blocked_to_resolved(self) -> None:
        state, violations = resolve_trial(
            [_blocked_event(), _resolved_event()],
            committed_records=[],
        )
        assert state.phase == ReconcilePhase.RESOLVED
        assert violations == []

    def test_unverifiable_to_resolved(self) -> None:
        state, violations = resolve_trial(
            [_unverifiable_event(), _manual_attested_event()],
            committed_records=[],
        )
        assert state.phase == ReconcilePhase.RESOLVED
        assert violations == []


# ---------------------------------------------------------------------------
# CLI 双入口一致性
# ---------------------------------------------------------------------------


class TestCli:
    """CLI 入口：与函数入口同等 fail-closed。"""

    @pytest.fixture()
    def cli_target(self) -> list[str]:
        return ["python3", "-m", "akb2.harvest.trial_verify"]

    def _run_cli(
        self, tmp_path: Path, data: dict, cli_target: list[str]
    ) -> subprocess.CompletedProcess:
        src = tmp_path / "trial_verify.json"
        src.write_text(json.dumps(data, ensure_ascii=False))
        proc = subprocess.run(
            cli_target + [str(src)],
            capture_output=True,
            text=True,
            env=CLI_ENV,
            cwd=REPO_ROOT,
        )
        return proc

    def test_valid_chain_exit_zero(self, tmp_path, cli_target) -> None:
        data = {
            "events": [_reconcile_event()],
            "committed_records": [_record()],
        }
        proc = self._run_cli(tmp_path, data, cli_target=cli_target)
        assert proc.returncode == CLI_EXIT_OK, proc.stderr
        assert "校验通过" in proc.stderr

    def test_illegal_exit_2(self, tmp_path, cli_target) -> None:
        data = {"events": [_resolved_event()], "committed_records": []}
        proc = self._run_cli(tmp_path, data, cli_target=cli_target)
        assert proc.returncode == CLI_EXIT_INVALID, proc.stderr

    def test_missing_file_exit_2(self, tmp_path, cli_target) -> None:
        proc = subprocess.run(
            cli_target + [str(tmp_path / "nope.json")],
            capture_output=True,
            text=True,
            env=CLI_ENV,
            cwd=REPO_ROOT,
        )
        assert proc.returncode == CLI_EXIT_INVALID

    def test_invalid_json_exit_2(self, tmp_path, cli_target) -> None:
        src = tmp_path / "bad.json"
        src.write_text("{not json")
        proc = subprocess.run(
            cli_target + [str(src)],
            capture_output=True,
            text=True,
            env=CLI_ENV,
            cwd=REPO_ROOT,
        )
        assert proc.returncode == CLI_EXIT_INVALID

    @pytest.mark.parametrize(
        ("data", "field"),
        [
            ({"events": {}, "committed_records": []}, "events"),
            ({"events": [], "committed_records": {}}, "committed_records"),
        ],
    )
    def test_non_list_payload_exit_2(self, tmp_path, cli_target, data, field) -> None:
        proc = self._run_cli(tmp_path, data, cli_target=cli_target)
        assert proc.returncode == CLI_EXIT_INVALID, proc.stderr
        output = json.loads(proc.stdout)
        assert output["violations"]
        assert field in output["violations"][0]

    def test_output_has_phase_and_violations(self, tmp_path, cli_target) -> None:
        data = {"events": [_reconcile_event()], "committed_records": [_record()]}
        proc = self._run_cli(tmp_path, data, cli_target=cli_target)
        output = json.loads(proc.stdout)
        assert "phase" in output
        assert output["phase"] == "RESOLVED"
        assert "violations" in output
        assert output["violations"] == []


class TestFunctionCliConsistency:
    """函数与 CLI 对同一输入判定一致。"""

    @pytest.mark.parametrize(
        ("events", "records", "expect_fail"),
        [
            ([_reconcile_event()], [_record()], False),
            ([_reconcile_event()], [], True),
            ([_blocked_event(), _resolved_event()], [], False),
            ([_unverifiable_event(), _manual_attested_event()], [], False),
            ([_resolved_event()], [], True),
            ([_reconcile_event(expected_a="x")], [_record(a="a1")], True),
        ],
        ids=[
            "valid_reconcile",
            "empty_records",
            "blocked_resolved",
            "unverifiable_attested",
            "empty_resolved_illegal",
            "mismatch",
        ],
    )
    def test_fn_and_cli_agree(
        self,
        tmp_path: Path,
        events: list[dict],
        records: list[dict],
        expect_fail: bool,
    ) -> None:
        fn_state, fn_violations = resolve_trial(events, committed_records=records)
        fn_failed = len(fn_violations) > 0

        data = {"events": events, "committed_records": records}
        src = tmp_path / "consistency.json"
        src.write_text(json.dumps(data, ensure_ascii=False))
        proc = subprocess.run(
            ["python3", "-m", "akb2.harvest.trial_verify", str(src)],
            capture_output=True,
            text=True,
            env=CLI_ENV,
            cwd=REPO_ROOT,
        )
        cli_failed = proc.returncode == CLI_EXIT_INVALID

        assert fn_failed == cli_failed, (
            f"函数与 CLI 判定不一致 — 函数失败={fn_failed}, "
            f"CLI rc={proc.returncode}, violations={fn_violations!r}"
        )
        assert fn_failed == expect_fail, (
            f"期望失败={expect_fail}, 实际失败={fn_failed}"
        )
