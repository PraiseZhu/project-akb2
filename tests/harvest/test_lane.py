"""sc-asm-lane 验收：车道-证据一致性判定（纯确定性计数，无 LLM）。

4 类错配 fixture 全部 exit 2 且零落盘；错误消息含两车道各自的证据计数。
判定逻辑经 run_lane_check() 纯函数 + CLI 双入口同等 fail-closed。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from akb2.harvest.schema import (
    LANE_AMBIGUOUS_EXIT,
    LANE_CONCEPT,
    LANE_EVIDENCE_INSUFFICIENT_EXIT,
    LANE_PROCEDURAL,
    check_lane,
    run_lane_check,
)

# 仓库运行 python3 -m akb2.* 的标准方式：PYTHONPATH=<repo>/src
REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_ENV = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))

# sc-asm-schema 定案：session_id 必为小写 canonical UUID4（替换 Wave 1 的
# 旧值 "sess-lane-test-0001"）。车道判定不读该字段，此处只为不留非法样例。
VALID_SESSION_ID = "7a3f5d21-9e4c-4b8a-b1d6-0c2e5f7a9b34"

# ---------------------------------------------------------------------------
# fixture 构造（沿用 sc-asm-schema 的合格 payload 结构）
# ---------------------------------------------------------------------------


def _base(
    how_steps: list[dict],
    components: list[str],
    causal_edges: list[dict],
    *,
    lane_hint: str,
) -> dict:
    return {
        "schema_version": "1",
        "session_id": VALID_SESSION_ID,
        "title": "车道错配 fixture",
        "lane_hint": lane_hint,
        "components": components,
        "causal_edges": causal_edges,
        "atoms": [
            {"id": f"a{i}", "kind": "command", "text": f"cmd {i}"}
            for i in range(1, 8)
        ],
        "how_steps": how_steps,
        "source_meta": {"tool": "claude-code"},
    }


def _how(n: int) -> list[dict]:
    return [
        {"anchor": f"a{i}", "provenance": "log", "cmd": f"cmd-{i}", "expect": "ok"}
        for i in range(1, n + 1)
    ]


def _concept_payload(components: int, edges: int) -> dict:
    return _base(
        how_steps=_how(3),  # 概念车道不需要 5 步
        components=[f"c{i}" for i in range(components)],
        causal_edges=[{"from": "c1", "to": "c2"} for _ in range(edges)],
        lane_hint=LANE_CONCEPT,
    )


def _proc_payload(steps: int) -> dict:
    return _base(
        how_steps=_how(steps),
        components=[],
        causal_edges=[],
        lane_hint=LANE_PROCEDURAL,
    )


def _valid_concept() -> dict:
    return _concept_payload(components=4, edges=2)


def _valid_proc() -> dict:
    return _proc_payload(5)


# ---------------------------------------------------------------------------
# 4 类错配 fixture：(label, payload, 期望 exit code)
# 内容证据与声明车道解耦：_proc_payload / _concept_payload 只给「内容证据」，
# 由每条 fixture 的 lane_hint 决定「声明车道」，构成 4 类错配。
# ---------------------------------------------------------------------------

MISMATCH_FIXTURES = [
    # 1. procedural 内容证据标 concept —— concept 车道证据不足（components 0 / edges 0）
    (
        "proc_evidence_marked_concept",
        _base(_how(5), [], [], lane_hint=LANE_CONCEPT),
        LANE_EVIDENCE_INSUFFICIENT_EXIT,
    ),
    # 2. concept 内容证据标 procedural —— procedural 车道证据不足（how_steps 3 < 5）
    (
        "concept_evidence_marked_proc",
        _base(_how(3), ["c1", "c2", "c3", "c4"], [{"from": "c1", "to": "c2"}], lane_hint=LANE_PROCEDURAL),
        LANE_EVIDENCE_INSUFFICIENT_EXIT,
    ),
    # 3. 两车道证据同时满足 —— 歧义交人（lane_hint 值不影响歧义判据）
    (
        "both_satisfied",
        _base(
            _how(5),
            ["c1", "c2", "c3"],
            [{"from": "c1", "to": "c2"}],
            lane_hint=LANE_PROCEDURAL,
        ),
        LANE_AMBIGUOUS_EXIT,
    ),
    # 4. 两车道证据同时不满足 —— 歧义交人
    (
        "both_unsatisfied",
        _base(_how(2), ["c1"], [], lane_hint=LANE_PROCEDURAL),
        LANE_AMBIGUOUS_EXIT,
    ),
]


def _run_cli(payload: dict, tmp_path) -> subprocess.CompletedProcess:
    src = tmp_path / "lane.json"
    src.write_text(json.dumps(payload, ensure_ascii=False))
    return subprocess.run(
        ["python3", "-m", "akb2.harvest.schema", str(src)],
        capture_output=True,
        text=True,
        env=CLI_ENV,
    )


class TestLaneCheckPure:
    """车道判定纯函数：返回 (ok, exit_code, message)。"""

    def test_valid_proc_ok(self) -> None:
        ok, code, msg = run_lane_check(_valid_proc())
        assert ok is True
        assert code == 0
        assert msg == ""

    def test_valid_concept_ok(self) -> None:
        ok, code, msg = run_lane_check(_valid_concept())
        assert ok is True
        assert code == 0
        assert msg == ""

    def test_check_lane_alias_matches_run_lane_check(self) -> None:
        """check_lane 是 run_lane_check 的兼容别名，判定结果必须逐字段一致。"""
        assert check_lane(_valid_proc()) == run_lane_check(_valid_proc())
        assert check_lane(_valid_concept()) == run_lane_check(_valid_concept())

    @pytest.mark.parametrize(
        ("label", "payload", "expected_code"),
        MISMATCH_FIXTURES,
        ids=[f[0] for f in MISMATCH_FIXTURES],
    )
    def test_check_lane_alias_forwards_failures(
        self, label, payload, expected_code
    ) -> None:
        """别名必须同等 fail-closed：4 类错配下 (ok, exit_code, message) 逐字段一致。

        只测通过路径会让「别名退化成恒真」这类改动照样绿，故失败路径必须覆盖：
        断言 exit code 与点名消息都与 run_lane_check 相同，而非各自「都失败了」。
        """
        alias = check_lane(payload)
        canonical = run_lane_check(payload)
        assert alias == canonical, f"{label}: 别名与本体判定不一致 {alias} != {canonical}"
        assert alias[0] is False, f"{label}: 错配必须判定失败"
        assert alias[1] == expected_code, f"{label}: 期望 exit {expected_code},实际 {alias[1]}"
        assert alias[2], f"{label}: 失败必须带点名消息"

    @pytest.mark.parametrize(
        ("label", "payload", "expected_code"),
        MISMATCH_FIXTURES,
        ids=[f[0] for f in MISMATCH_FIXTURES],
    )
    def test_mismatch_not_ok(self, label, payload, expected_code) -> None:
        payload = dict(payload)  # 防御性拷贝（构造时已生成新 dict，此处无需 deep copy）
        ok, code, msg = run_lane_check(payload)
        assert ok is False, f"{label}: 错配必须判定失败"
        assert code == expected_code, f"{label}: 期望 exit {expected_code},实际 {code}"
        assert msg, f"{label}: 失败必须带点名消息"

    def test_both_satisfied_msg_has_both_counts(self) -> None:
        """双满足歧义消息含两车道各自的证据计数。"""
        _, _, msg = run_lane_check(
            _base(_how(5), ["c1", "c2", "c3"], [{"from": "c1", "to": "c2"}], lane_hint=LANE_PROCEDURAL)
        )
        assert "5" in msg and "3" in msg and "1" in msg, msg

    def test_both_unsatisfied_msg_has_both_counts(self) -> None:
        _, _, msg = run_lane_check(
            _base(_how(2), ["c1"], [], lane_hint=LANE_PROCEDURAL)
        )
        assert "2" in msg and "1" in msg and "0" in msg, msg

    def test_insufficient_msg_has_expected_and_actual(self) -> None:
        """证据不足消息含期望计数+实际计数。"""
        payload = _concept_payload(4, 2)
        payload["lane_hint"] = LANE_PROCEDURAL
        _, _, msg = run_lane_check(payload)
        assert "5" in msg and "3" in msg, msg

    def test_how_step_empty_cmd_breaks_proc_evidence(self) -> None:
        """procedural 要求每条 cmd 非空。"""
        payload = _valid_proc()
        payload["how_steps"][2]["cmd"] = "   "
        ok, _, msg = run_lane_check(payload)
        assert ok is False
        assert "cmd" in msg

    def test_zero_components_zero_edges_fails_concept(self) -> None:
        payload = _valid_concept()
        payload["components"] = []
        payload["causal_edges"] = []
        ok, code, _ = run_lane_check(payload)
        assert ok is False
        assert code == LANE_EVIDENCE_INSUFFICIENT_EXIT


class TestLaneCli:
    """CLI 双入口同等 fail-closed：4 类错配 exit 2 且零产出。"""

    @pytest.mark.parametrize(
        ("label", "payload", "expected_code"),
        MISMATCH_FIXTURES,
        ids=[f[0] for f in MISMATCH_FIXTURES],
    )
    def test_mismatch_exit_2_no_output(
        self, tmp_path, label, payload, expected_code
    ) -> None:
        src = tmp_path / "lane.json"
        src.write_text(json.dumps(payload, ensure_ascii=False))
        before = {p.name for p in tmp_path.iterdir()}
        proc = _run_cli(payload, tmp_path)
        assert proc.returncode == expected_code, (
            f"{label}: CLI 应 exit {expected_code},实际 {proc.returncode} stderr={proc.stderr!r}"
        )
        after = {p.name for p in tmp_path.iterdir()}
        assert after == before, f"{label}: raw/ 零产出,新增={after - before}"

    def test_cli_both_satisfied_message(self, tmp_path) -> None:
        payload = _base(_how(5), ["c1", "c2", "c3"], [{"from": "c1", "to": "c2"}], lane_hint=LANE_PROCEDURAL)
        proc = _run_cli(payload, tmp_path)
        out = proc.stdout + proc.stderr
        assert "5" in out and "3" in out and "1" in out, out

    def test_cli_both_unsatisfied_message(self, tmp_path) -> None:
        payload = _base(_how(2), ["c1"], [], lane_hint=LANE_PROCEDURAL)
        proc = _run_cli(payload, tmp_path)
        out = proc.stdout + proc.stderr
        assert "2" in out and "1" in out and "0" in out, out

    def test_valid_proc_exit_zero(self, tmp_path) -> None:
        proc = _run_cli(_valid_proc(), tmp_path)
        assert proc.returncode == 0, proc.stderr

    def test_valid_concept_exit_zero(self, tmp_path) -> None:
        proc = _run_cli(_valid_concept(), tmp_path)
        assert proc.returncode == 0, proc.stderr


class TestLaneFunctionCliConsistency:
    """函数与 CLI 对同一错配判定一致。"""

    @pytest.mark.parametrize(
        ("label", "payload", "expected_code"),
        MISMATCH_FIXTURES,
        ids=[f[0] for f in MISMATCH_FIXTURES],
    )
    def test_agree(self, tmp_path, label, payload, expected_code) -> None:
        ok, code, _ = run_lane_check(payload)
        proc = _run_cli(payload, tmp_path)
        assert ok is False
        assert proc.returncode == expected_code
        assert code == proc.returncode, f"{label}: 函数与 CLI exit code 不一致"
