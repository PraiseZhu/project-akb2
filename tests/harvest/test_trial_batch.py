"""sc-trial-manifest 验收：试投批次 manifest 校验 + 残留面处置协议。

校验覆盖：
- 正例：valid batch 零违规，函数 + CLI 双入口通过
- 预测红集：payload 缺失/不合格在校验期必红
- 阶段特定字段：intent/in_flight/settled/failed 各阶段字段约束
- 结构违规：version/planned 数量/phase 枚举/路径形态/status 校验
- 跨条目校验：attempted ⊆ planned / 全局唯一性 / 终态枚举
- 顶层 session_id 身份闸：小写 canonical UUID4 + 与内嵌 payload 同源
  （判定函数复用 assembler.validate_harvest_run_id，单一真相源）
- 函数与 CLI 一致性
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
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# 判定函数复用断言锚点：batch 侧身份闸必须与 schema 侧同一函数对象
import trial_batch  # noqa: E402
from trial_batch import (  # noqa: E402  # type: ignore[import-untyped]
    BATCH_VERSION,
    CLI_EXIT_INVALID,
    CLI_EXIT_OK,
    validate_trial_batch,
)

from akb2.harvest.assembler import validate_harvest_run_id  # noqa: E402

CLI_ENV = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))

# ---------------------------------------------------------------------------
# 合格 fixture
# ---------------------------------------------------------------------------


def _valid_payload_procedural(session_id: str) -> dict:
    """构造合格 procedural 车道 payload（how_steps≥5，cmd 全非空）。"""
    return {
        "schema_version": "1",
        "session_id": session_id,
        "title": "trial batch 测试 payload",
        "lane_hint": "procedural",
        "components": ["gate", "runner"],
        "causal_edges": [],
        "atoms": [
            {"id": "a1", "kind": "command", "text": "cmd 1"},
            {"id": "a2", "kind": "output", "text": "output 1"},
            {"id": "a3", "kind": "error", "text": "error 1"},
            {"id": "a4", "kind": "diff", "text": "diff 1"},
            {"id": "a5", "kind": "judgment", "text": "judgment 1"},
            {"id": "a6", "kind": "context", "text": "context 1"},
        ],
        "how_steps": [
            {"anchor": "a1", "provenance": "log", "cmd": "step1", "expect": "ok"},
            {"anchor": "a2", "provenance": "log", "cmd": "step2", "expect": "ok"},
            {"anchor": "a3", "provenance": "log", "cmd": "step3", "expect": "ok"},
            {"anchor": "a4", "provenance": "log", "cmd": "step4", "expect": "ok"},
            {"anchor": "a5", "provenance": "log", "cmd": "step5", "expect": "ok"},
        ],
        "source_meta": {
            "session_id": session_id,
            "tool": "claude-code",
            "fetched_at": "2026-08-13T15:00:00+08:00",
        },
    }


def _valid_intent_entry() -> dict:
    return {
        "phase": "intent",
        "status": "pending",
        "harvest_run_id": "d4e5f6a7-b8c9-4d0e-8f1a-2b3c4d5e6f78",
        "session_id": "4e1c8a92-3b7d-4f6a-9c25-1d8e0b3a7f64",
        "expected_slug": "4e1c8a92-3b7d-4f6a-9c25-1d8e0b3a7f64",
        "intent_timestamp": "2026-08-13T15:00:00+08:00",
        "raw_path": None,
        "pipeline_content_hash": None,
        "final_state": None,
    }


def _valid_in_flight_entry() -> dict:
    return {
        "phase": "in_flight",
        "status": "running",
        "harvest_run_id": "b5f6a7c8-d9e0-4f1a-8b2c-3d4e5f6a7b89",
        "session_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        "expected_slug": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        "intent_timestamp": "2026-08-13T15:05:00+08:00",
        "raw_path": "/Volumes/AKB2/harvest/raw/a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d.md",
        "pipeline_content_hash": "a1b2c3d4e5f6a7b8",
        "delivery_timestamp": "2026-08-13T15:10:00+08:00",
        "deadline": "2026-08-13T16:00:00+08:00",
        "reconciliation_state": "not_required",
        "final_state": None,
    }


def _valid_settled_entry() -> dict:
    return {
        "phase": "settled",
        "status": "completed",
        "harvest_run_id": "c6a7b8d9-e0f1-4a2b-9c3d-4e5f6a7b8c90",
        "session_id": "f1e2d3c4-b5a6-4c7d-8e9f-0a1b2c3d4e5f",
        "expected_slug": "f1e2d3c4-b5a6-4c7d-8e9f-0a1b2c3d4e5f",
        "intent_timestamp": "2026-08-13T15:15:00+08:00",
        "raw_path": "/Volumes/AKB2/harvest/raw/f1e2d3c4-b5a6-4c7d-8e9f-0a1b2c3d4e5f.md",
        "pipeline_content_hash": "f1e2d3c4b5a6c7d8",
        "delivery_timestamp": "2026-08-13T15:20:00+08:00",
        "deadline": "2026-08-13T16:00:00+08:00",
        "reconciliation_state": "committed",
        "final_state": "committed",
    }


def _valid_failed_entry() -> dict:
    """failed 条目：终态必须 ∈ {submit_error, duplicate_content}。"""
    return {
        "phase": "failed",
        "status": "failed",
        "harvest_run_id": "d7a8b9c0-e1f2-4a3b-9d4e-5f6a7b8c9d01",
        "session_id": "f1e2d3c4-b5a6-4c7d-8e9f-0a1b2c3d4e5f",
        "expected_slug": "f1e2d3c4-b5a6-4c7d-8e9f-0a1b2c3d4e5f",
        "intent_timestamp": "2026-08-13T15:25:00+08:00",
        "raw_path": "/Volumes/AKB2/harvest/raw/d7a8b9c0-e1f2-4a3b-9d4e-5f6a7b8c9d01.md",
        "pipeline_content_hash": "d7a8b9c0e1f2a3b4",
        "delivery_timestamp": "2026-08-13T15:30:00+08:00",
        "deadline": "2026-08-13T16:00:00+08:00",
        "reconciliation_state": "blocked",
        "final_state": "submit_error",
        "failure_reason": "车道校验失败：concept 证据不足",
    }


def make_valid_batch() -> dict:
    """构造完全合规的 trial batch manifest。"""
    return {
        "version": BATCH_VERSION,
        "planned": [
            {
                "session_id": "4e1c8a92-3b7d-4f6a-9c25-1d8e0b3a7f64",
                "title": "测试 planned 1",
                "payload": _valid_payload_procedural(
                    "4e1c8a92-3b7d-4f6a-9c25-1d8e0b3a7f64"
                ),
            },
            {
                "session_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
                "title": "测试 planned 2",
                "payload": _valid_payload_procedural(
                    "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
                ),
            },
            {
                "session_id": "f1e2d3c4-b5a6-4c7d-8e9f-0a1b2c3d4e5f",
                "title": "测试 planned 3",
                "payload": _valid_payload_procedural(
                    "f1e2d3c4-b5a6-4c7d-8e9f-0a1b2c3d4e5f"
                ),
            },
        ],
        "attempted": [
            _valid_intent_entry(),
            _valid_in_flight_entry(),
            _valid_settled_entry(),
            _valid_failed_entry(),
        ],
    }


# ---------------------------------------------------------------------------
# 正例
# ---------------------------------------------------------------------------


class TestValid:
    def test_valid_batch_clean(self) -> None:
        """完全合规的 manifest 零违规。"""
        assert validate_trial_batch(make_valid_batch()) == []

    def test_planned_schema_check(self) -> None:
        """每个 planned 的 payload 都通过 schema 校验。"""
        batch = make_valid_batch()
        vs = validate_trial_batch(batch)
        assert vs == [], f"valid batch 应零违规,实际 {vs}"

    def test_attempted_all_stages(self) -> None:
        """四种阶段各一个 entry 通过校验。"""
        batch = make_valid_batch()
        vs = validate_trial_batch(batch)
        assert vs == []


# ---------------------------------------------------------------------------
# 负样例：预测红集
# ---------------------------------------------------------------------------

NEGATIVE_CASES: list[tuple[str, callable, list[str]]] = [
    (
        "missing_planned_item_payload",
        lambda b: b["planned"][0].pop("payload"),
        ["planned[0].payload", "缺失"],
    ),
    (
        "planned_item_payload_not_dict",
        lambda b: b["planned"][0].update({"payload": "not-a-dict"}),
        ["planned[0].payload", "期望对象"],
    ),
    (
        "planned_item_payload_bad_schema",
        lambda b: b["planned"][0]["payload"].update({"schema_version": "2"}),
        ["schema_version", "期望值", "1", "2"],
    ),
    (
        "planned_item_payload_missing_how_steps",
        lambda b: b["planned"][0]["payload"].pop("how_steps"),
        ["how_steps", "期望字段"],
    ),
    (
        "planned_too_few",
        lambda b: b["planned"].pop(0),
        ["planned", "期望恰好 3 篇", "attempted[0]", "不在 planned 中"],
    ),
    (
        "planned_too_many",
        lambda b: b["planned"].append(
            {
                "session_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
                "title": "extra",
                "payload": {},
            }
        ),
        ["planned", "期望恰好 3 篇"],
    ),
    (
        "planned_not_list",
        lambda b: b.update({"planned": "not-a-list"}),
        ["planned", "期望列表"],
    ),
    (
        "wrong_version",
        lambda b: b.update({"version": "2"}),
        ["version", "期望值 '1'"],
    ),
    (
        "attempted_invalid_phase",
        lambda b: b["attempted"][0].update({"phase": "unknown_phase"}),
        ["attempted[0].phase", "unknown_phase"],
    ),
    (
        "attempted_missing_status",
        lambda b: b["attempted"][0].pop("status"),
        ["attempted[0].status", "期望字符串"],
    ),
    (
        "attempted_invalid_status",
        lambda b: b["attempted"][0].update({"status": "invalid_status"}),
        ["attempted[0].status", "invalid_status"],
    ),
    (
        "attempted_session_id_not_in_planned",
        lambda b: b["attempted"][0].update({"session_id": "unknown-session-id"}),
        ["attempted[0].session_id", "不在 planned 中"],
    ),
    (
        "duplicate_harvest_run_id",
        lambda b: b["attempted"][1].update(
            {"harvest_run_id": b["attempted"][0]["harvest_run_id"]}
        ),
        ["harvest_run_id", "重复"],
    ),
    (
        "duplicate_pipeline_content_hash",
        lambda b: b["attempted"][2].update(
            {"pipeline_content_hash": b["attempted"][1]["pipeline_content_hash"]}
        ),
        ["pipeline_content_hash", "重复"],
    ),
    (
        "raw_path_traversal",
        lambda b: b["attempted"][1].update(
            {"raw_path": "/Volumes/AKB2/raw/../victim.md"}
        ),
        ["raw_path", "路径逃逸"],
    ),
    (
        "raw_path_traversal_percent_encoded",
        lambda b: b["attempted"][1].update(
            {"raw_path": "/Volumes/AKB2/raw/%2e%2e/victim.md"}
        ),
        ["raw_path", "路径逃逸"],
    ),
    (
        "raw_path_traversal_double_percent_encoded",
        lambda b: b["attempted"][1].update(
            {"raw_path": "/Volumes/AKB2/raw/%252e%252e/victim.md"}
        ),
        ["raw_path", "路径逃逸"],
    ),
    (
        "raw_path_traversal_bare_segment_no_trailing_slash",
        lambda b: b["attempted"][1].update(
            {"raw_path": "/Volumes/AKB2/raw/.."}
        ),
        ["raw_path", "路径逃逸"],
    ),
    (
        "failed_invalid_final_state",
        lambda b: b["attempted"][3].update({"final_state": "invalid_fs"}),
        ["attempted[3].final_state", "failed 阶段", "invalid_fs"],
    ),
    (
        "duplicate_content_missing_previous_run_id",
        lambda b: b["attempted"][3].update({"final_state": "duplicate_content"}),
        ["first_submitted_at", "previous_run_id"],
    ),
    (
        "intent_raw_path_not_null",
        lambda b: b["attempted"][0].update({"raw_path": "/some/path"}),
        ["attempted[0].raw_path", "intent 阶段期望 null"],
    ),
    (
        "intent_final_state_not_null",
        lambda b: b["attempted"][0].update({"final_state": "committed"}),
        ["attempted[0].final_state", "intent 阶段期望 null"],
    ),
    (
        "in_flight_missing_raw_path",
        lambda b: b["attempted"][1].pop("raw_path"),
        ["attempted[1].raw_path", "期望字符串"],
    ),
    (
        "in_flight_relative_raw_path",
        lambda b: b["attempted"][1].update({"raw_path": "relative/path.md"}),
        ["attempted[1].raw_path", "期望绝对路径"],
    ),
    (
        "in_flight_bad_pipeline_hash_length",
        lambda b: b["attempted"][1].update({"pipeline_content_hash": "too-short"}),
        ["attempted[1].pipeline_content_hash", "期望 16 字符"],
    ),
    (
        "in_flight_missing_deadline",
        lambda b: b["attempted"][1].pop("deadline"),
        ["attempted[1].deadline", "期望字符串"],
    ),
    (
        "settled_missing_final_state",
        lambda b: b["attempted"][2].pop("final_state"),
        ["attempted[2].final_state", "期望字符串"],
    ),
    (
        "failed_missing_final_state",
        lambda b: b["attempted"][3].pop("final_state"),
        ["attempted[3].final_state", "期望字符串"],
    ),
    (
        "invalid_reconciliation_state",
        lambda b: b["attempted"][1].update({"reconciliation_state": "unknown"}),
        ["attempted[1].reconciliation_state", "unknown"],
    ),
    (
        "batch_not_dict",
        lambda b: b.update({"not-a-dict": True}) or True,  # make it not a dict
        None,  # special: handled separately
    ),
]


class TestNegativePredictedRed:
    """预测红集：这些用例对 valid manifest 必须红。"""

    @pytest.mark.parametrize(
        ("case_id", "mutate", "expected_substrings"),
        [c for c in NEGATIVE_CASES if c[2] is not None],
        ids=[c[0] for c in NEGATIVE_CASES if c[2] is not None],
    )
    def test_validate_reports_violations(
        self, case_id: str, mutate, expected_substrings: list[str]
    ) -> None:
        batch = make_valid_batch()
        mutate(batch)
        violations = validate_trial_batch(batch)
        assert violations, f"{case_id}: 预测红集,validate 必须返回非空清单"
        joined = " ".join(violations)
        for sub in expected_substrings:
            assert sub in joined, (
                f"{case_id}: 违规消息应点名 {sub!r},"
                f"实际 violations={violations!r}"
            )

    def test_batch_not_dict(self) -> None:
        """batch 非 dict 直接报错。"""
        for val in ("not-a-dict", None, []):
            violations = validate_trial_batch(val)
            assert violations, f"非 dict 输入 {val!r} 应报违规"
            joined = " ".join(violations)
            assert "期望对象" in joined, f"应点名类型不符,实际 {violations!r}"


# ---------------------------------------------------------------------------
# 字段级负样例
# ---------------------------------------------------------------------------


class TestNegativeFields:
    def test_planned_item_missing_session_id(self) -> None:
        batch = make_valid_batch()
        batch["planned"][0].pop("session_id")
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "planned[0].session_id" in joined

    def test_planned_item_empty_title(self) -> None:
        batch = make_valid_batch()
        batch["planned"][0]["title"] = ""
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "planned[0].title" in joined

    def test_attempted_missing_harvest_run_id(self) -> None:
        batch = make_valid_batch()
        batch["attempted"][0].pop("harvest_run_id")
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "attempted[0].harvest_run_id" in joined

    def test_attempted_missing_intent_timestamp(self) -> None:
        batch = make_valid_batch()
        batch["attempted"][0].pop("intent_timestamp")
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "attempted[0].intent_timestamp" in joined

    def test_attempted_missing_expected_slug(self) -> None:
        batch = make_valid_batch()
        batch["attempted"][0].pop("expected_slug")
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "attempted[0].expected_slug" in joined

    def test_attempted_not_a_dict(self) -> None:
        batch = make_valid_batch()
        batch["attempted"][0] = "not-a-dict"
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "attempted[0]" in joined
        assert "期望对象" in joined


# ---------------------------------------------------------------------------
# 顶层 session_id 身份闸（sc-trial-batch-dual-entry）
# ---------------------------------------------------------------------------


class TestSessionIdIdentityGate:
    """planned[]/attempted[] 顶层 session_id 身份闸：

    - 必须小写 canonical UUID4（判定复用 assembler.validate_harvest_run_id，
      与 schema 侧同一函数对象，单一真相源）
    - planned 条目还必须与内嵌 payload.session_id 逐字同源
    - 两入口（_validate_planned_item / _validate_attempted_item）fail-closed
      等级一致
    """

    def test_gate_reuses_assembler_predicate_object(self) -> None:
        """单一真相源：batch 侧闸用的就是 assembler.validate_harvest_run_id 本尊。"""
        assert trial_batch.validate_harvest_run_id is validate_harvest_run_id

    def test_planned_top_level_non_uuid4_rejected(self) -> None:
        """planned 顶层 session_id 非 UUID4 必拒（payload 同步改，排除同源干扰）。"""
        batch = make_valid_batch()
        batch["planned"][0]["session_id"] = "not-a-uuid"
        batch["planned"][0]["payload"]["session_id"] = "not-a-uuid"
        batch["planned"][0]["payload"]["source_meta"]["session_id"] = "not-a-uuid"
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "planned[0].session_id" in joined
        assert "期望小写 canonical UUID4" in joined

    def test_planned_top_level_uppercase_uuid4_rejected(self) -> None:
        """大写 UUID4 非小写 canonical 形态，必拒。"""
        batch = make_valid_batch()
        up = batch["planned"][0]["session_id"].upper()
        batch["planned"][0]["session_id"] = up
        batch["planned"][0]["payload"]["session_id"] = up
        batch["planned"][0]["payload"]["source_meta"]["session_id"] = up
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "期望小写 canonical UUID4" in joined

    def test_planned_uuid1_style_rejected(self) -> None:
        """version 位非 4（UUID1 形态）必拒。"""
        batch = make_valid_batch()
        sid = "4e1c8a92-3b7d-1f6a-9c25-1d8e0b3a7f64"
        batch["planned"][0]["session_id"] = sid
        batch["planned"][0]["payload"]["session_id"] = sid
        batch["planned"][0]["payload"]["source_meta"]["session_id"] = sid
        violations = validate_trial_batch(batch)
        joined = " ".join(violations)
        assert "期望小写 canonical UUID4" in joined

    def test_planned_top_level_diverges_from_payload_rejected(self) -> None:
        """顶层 session_id 与内嵌 payload.session_id 分叉必拒（各自都是合法 UUID4）。"""
        batch = make_valid_batch()
        # 换成另一个 planned 的合法 UUID4：UUID4 闸过、同源闸必红
        batch["planned"][0]["session_id"] = batch["planned"][2]["session_id"]
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "不同源" in joined
        assert "planned[0].session_id" in joined

    def test_attempted_top_level_non_uuid4_rejected(self) -> None:
        """attempted 顶层 session_id 非 UUID4 必拒（attempted 无内嵌 payload，
        只走 UUID4 一闸；与 planned 入口 fail-closed 等级一致）。"""
        batch = make_valid_batch()
        batch["attempted"][0]["session_id"] = "plain-session-id"
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "attempted[0].session_id" in joined
        assert "期望小写 canonical UUID4" in joined

    def test_attempted_uppercase_uuid4_rejected(self) -> None:
        """attempted 侧大写 UUID4 同样必拒。"""
        batch = make_valid_batch()
        batch["attempted"][0]["session_id"] = (
            "4E1C8A92-3B7D-4F6A-9C25-1D8E0B3A7F64"
        )
        violations = validate_trial_batch(batch)
        joined = " ".join(violations)
        assert "期望小写 canonical UUID4" in joined

    def test_missing_session_id_still_reports_nonempty(self) -> None:
        """身份闸不吞既有非空校验：缺失仍报「期望字符串」。"""
        batch = make_valid_batch()
        batch["planned"][0].pop("session_id")
        violations = validate_trial_batch(batch)
        joined = " ".join(violations)
        assert "planned[0].session_id" in joined
        assert "期望字符串" in joined


class TestSessionIdGateMutationIsolation:
    """反向变异：挖掉 batch 侧身份闸后，上述用例必须转红。

    演示方式：monkeypatch trial_batch.validate_harvest_run_id 为恒 True
    （等价于「UUID4 闸被移除」），断言非 UUID4 用例从「拒」翻转为「放行」
    ——证明这些用例钉死的正是 batch 侧闸本身，而非 schema 侧或别处行为。
    注：monkeypatch 只影响 trial_batch 模块绑定，assembler 原函数对象不动，
    schema 侧用例（test_schema.py）不受影响。

    钉层纪律（schema 侧 UUID4 闸合入后）：validate_trial_batch 对 planned
    条目还会经 validate_harvest(payload) 走 schema 侧同谓词闸，monkeypatch
    挖 batch 绑定挖不掉那一层。因此变异用例只调 batch 自己的入口函数
    （_validate_planned_item / _validate_attempted_item），且 planned 用例保持
    内嵌 payload 合法让 schema 侧物理绿灯——被观测的翻转只能来自 batch 闸。
    """

    def test_mutation_non_uuid4_flips_to_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 内嵌 payload 保持合法 UUID4：schema 侧闸物理绿灯，
        # UUID4 拒绝只能来自 batch 侧顶层闸（顶层与 payload 分叉另报同源违规，
        # 属不同闸，不含 UUID4 字样）。
        batch = make_valid_batch()
        batch["planned"][0]["session_id"] = "not-a-uuid"
        item = batch["planned"][0]

        # 基线：闸在 → batch 入口必因 UUID4 拒
        with_uuid4_gate = trial_batch._validate_planned_item(item, 0)
        assert any("UUID4" in v for v in with_uuid4_gate)

        # 变异：闸挖掉 → 同一输入必须不再因 UUID4 拒（用例转红）
        monkeypatch.setattr(
            trial_batch, "validate_harvest_run_id", lambda _s: True
        )
        mutated = trial_batch._validate_planned_item(item, 0)
        assert not any("UUID4" in v for v in mutated), (
            "挖掉 batch 侧 UUID4 闸后仍拒 = 用例没钉在 batch 侧闸上: "
            f"{mutated!r}"
        )
        # assembler 原函数对象不受 monkeypatch 影响（schema 侧不受影响）
        assert validate_harvest_run_id("not-a-uuid") is False

    def test_mutation_attempted_flips_to_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        batch = make_valid_batch()
        item = batch["attempted"][0]
        item["session_id"] = "plain-session-id"

        # attempted 无内嵌 payload，不进 schema；直接调 batch 入口钉层
        with_gate = trial_batch._validate_attempted_item(item, 0)
        assert any("UUID4" in v for v in with_gate)

        monkeypatch.setattr(
            trial_batch, "validate_harvest_run_id", lambda _s: True
        )
        mutated = trial_batch._validate_attempted_item(item, 0)
        assert not any("UUID4" in v for v in mutated), (
            f"attempted 侧同样未钉死在 batch 闸: {mutated!r}"
        )


# ---------------------------------------------------------------------------
# 交叉互换防御：每条缺口必须有独立失败消息，互换不能仍绿
# ---------------------------------------------------------------------------


class TestCrossContamination:
    """验证每条校验的失败消息独占且不可互换。

    8 条 finding 的对应校验:
    - 字段名: phase (非 stage)
    - status 合规
    - attempted ⊆ planned
    - harvest_run_id / pipeline_content_hash 全局唯一
    - raw_path 逃逸
    - duplicate_content 带首投时间与旧 run_id
    - failed 终态枚举
    """

    def test_phase_wrong_field_name_fails(self) -> None:
        """使用 stage 而非 phase 不会通过校验。"""
        batch = make_valid_batch()
        for entry in batch["attempted"]:
            entry["stage"] = entry.pop("phase", None)
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "phase" in joined
        assert "期望字符串" in joined

    def test_status_missing_isolated(self) -> None:
        """仅缺失 status 应产生 status 相关违规，不与其他混同。"""
        batch = make_valid_batch()
        batch["attempted"][0].pop("status")
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "attempted[0].status" in joined
        assert "期望字符串" in joined

    def test_session_id_not_in_planned_isolated(self) -> None:
        """仅 attempted session_id 不在 planned 中应产生 ⊆ 违规。"""
        batch = make_valid_batch()
        batch["attempted"][0]["session_id"] = "totally-unknown-session"
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "不在 planned 中" in joined

    def test_duplicate_run_id_isolated(self) -> None:
        """仅 duplicate harvest_run_id 应产生重复违规。"""
        batch = make_valid_batch()
        batch["attempted"][1]["harvest_run_id"] = batch["attempted"][0][
            "harvest_run_id"
        ]
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "重复" in joined
        assert "harvest_run_id" in joined

    def test_duplicate_hash_isolated(self) -> None:
        """仅 duplicate pipeline_content_hash 应产生重复违规。"""
        batch = make_valid_batch()
        batch["attempted"][2]["pipeline_content_hash"] = batch["attempted"][1][
            "pipeline_content_hash"
        ]
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "重复" in joined
        assert "pipeline_content_hash" in joined

    def test_raw_path_traversal_isolated(self) -> None:
        """仅 raw_path 包含 ../ 应产生逃逸违规。"""
        batch = make_valid_batch()
        batch["attempted"][1]["raw_path"] = "/Volumes/AKB2/../etc/passwd"
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "路径逃逸" in joined

    def test_raw_path_traversal_percent_encoded_isolated(self) -> None:
        """百分号编码的 '..'（%2e%2e）不得绕过逃逸检测。"""
        batch = make_valid_batch()
        batch["attempted"][1]["raw_path"] = "/Volumes/AKB2/%2e%2e/etc/passwd"
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "路径逃逸" in joined

    def test_raw_path_traversal_double_percent_encoded_isolated(self) -> None:
        """双重百分号编码（%252e%252e）同样不得绕过逃逸检测。"""
        batch = make_valid_batch()
        batch["attempted"][1]["raw_path"] = "/Volumes/AKB2/%252e%252e/etc/passwd"
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "路径逃逸" in joined

    def test_failed_final_state_enum_isolated(self) -> None:
        """仅 failed 阶段 final_state 非法应产生枚举违规。"""
        batch = make_valid_batch()
        batch["attempted"][3]["final_state"] = "bogus"
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "failed 阶段" in joined
        assert "期望值" in joined

    def test_duplicate_content_missing_fields_isolated(self) -> None:
        """仅 duplicate_content 缺字段应产生缺失违规。"""
        batch = make_valid_batch()
        batch["attempted"][3]["final_state"] = "duplicate_content"
        batch["attempted"][3].pop("first_submitted_at", None)
        batch["attempted"][3].pop("previous_run_id", None)
        violations = validate_trial_batch(batch)
        assert violations
        joined = " ".join(violations)
        assert "first_submitted_at" in joined
        assert "previous_run_id" in joined


# ---------------------------------------------------------------------------
# CLI 双入口一致性
# ---------------------------------------------------------------------------


class TestCli:
    """CLI 入口：与函数入口同等 fail-closed。"""

    @pytest.fixture()
    def cli_target(self) -> list[str]:
        return ["python3", "scripts/trial_batch.py"]

    def _run_cli(
        self, tmp_path: Path, batch: dict, cli_target: list[str]
    ) -> subprocess.CompletedProcess:
        src = tmp_path / "trial-batch.json"
        src.write_text(json.dumps(batch, ensure_ascii=False))
        proc = subprocess.run(
            cli_target + [str(src)],
            capture_output=True,
            text=True,
            env=CLI_ENV,
            cwd=REPO_ROOT,
        )
        return proc

    def test_valid_batch_exit_zero(self, tmp_path, cli_target) -> None:
        proc = self._run_cli(tmp_path, make_valid_batch(), cli_target=cli_target)
        assert proc.returncode == CLI_EXIT_OK, proc.stderr
        assert "校验通过" in proc.stderr

    @pytest.mark.parametrize(
        ("case_id", "mutate", "expected_substrings"),
        [c for c in NEGATIVE_CASES if c[2] is not None],
        ids=[c[0] for c in NEGATIVE_CASES if c[2] is not None],
    )
    def test_negative_exit_2(
        self,
        tmp_path,
        cli_target,
        case_id: str,
        mutate,
        expected_substrings: list[str],
    ) -> None:
        batch = make_valid_batch()
        mutate(batch)
        proc = self._run_cli(tmp_path, batch, cli_target=cli_target)
        assert proc.returncode == CLI_EXIT_INVALID, (
            f"{case_id}: CLI 应 exit 2,实际 {proc.returncode} stderr={proc.stderr!r}"
        )
        out = proc.stdout + proc.stderr
        for sub in expected_substrings:
            assert sub in out, (
                f"{case_id}: CLI 输出应点名 {sub!r},实际 {out!r}"
            )

    def test_cli_writes_no_files(self, tmp_path, cli_target) -> None:
        """零文件产出：校验失败不产生任何文件。"""
        batch = make_valid_batch()
        batch["version"] = "bad"
        src = tmp_path / "trial-batch.json"
        src.write_text(json.dumps(batch, ensure_ascii=False))
        before = {p.name for p in tmp_path.iterdir()}
        proc = subprocess.run(
            cli_target + [str(src)],
            capture_output=True,
            text=True,
            env=CLI_ENV,
            cwd=REPO_ROOT,
        )
        assert proc.returncode == CLI_EXIT_INVALID
        after = {p.name for p in tmp_path.iterdir()}
        assert after == before, f"CLI 校验失败不得产出文件,新增={after - before}"

    def test_cli_missing_file(self, tmp_path, cli_target) -> None:
        proc = subprocess.run(
            cli_target + [str(tmp_path / "nope.json")],
            capture_output=True,
            text=True,
            env=CLI_ENV,
            cwd=REPO_ROOT,
        )
        assert proc.returncode == CLI_EXIT_INVALID

    def test_cli_invalid_json(self, tmp_path, cli_target) -> None:
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


class TestFunctionCliConsistency:
    """函数与 CLI 对同一负样例判定一致。"""

    @pytest.mark.parametrize(
        ("case_id", "mutate"),
        [(c[0], c[1]) for c in NEGATIVE_CASES if c[2] is not None],
        ids=[c[0] for c in NEGATIVE_CASES if c[2] is not None],
    )
    def test_function_and_cli_agree(
        self, tmp_path, mutate, case_id: str
    ) -> None:
        batch = make_valid_batch()
        mutate(batch)
        fn_violations = validate_trial_batch(batch)

        src = tmp_path / f"{case_id}.json"
        src.write_text(json.dumps(batch, ensure_ascii=False))
        proc = subprocess.run(
            ["python3", "scripts/trial_batch.py", str(src)],
            capture_output=True,
            text=True,
            env=CLI_ENV,
            cwd=REPO_ROOT,
        )
        assert (len(fn_violations) > 0) == (
            proc.returncode == CLI_EXIT_INVALID
        ), (
            f"{case_id}: 函数与 CLI 判定不一致 — 函数违规={fn_violations!r} "
            f"CLI rc={proc.returncode}"
        )
