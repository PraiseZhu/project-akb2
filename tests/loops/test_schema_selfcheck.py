"""SC-s1: Schema 自洽性测试。

三个 schema 文件存在 / JSON 有效 / repair-adapter-card 9 字段全 required。
"""

from __future__ import annotations

import json
from pathlib import Path


def _load_schema(path: Path) -> dict:
    """加载并解析 JSON schema 文件。"""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_schema_files_exist(schema_dir: Path) -> None:
    """三个 schema 文件必须存在。"""
    assert (schema_dir / "loop-ledger.schema.json").is_file(), "loop-ledger.schema.json 缺失"
    assert (schema_dir / "failure-signature.schema.json").is_file(), "failure-signature.schema.json 缺失"
    assert (schema_dir / "repair-adapter-card.schema.json").is_file(), "repair-adapter-card.schema.json 缺失"


def test_schemas_are_valid_json(schema_dir: Path) -> None:
    """三个 schema 文件必须能被 json.load 解析。"""
    for name in [
        "loop-ledger.schema.json",
        "failure-signature.schema.json",
        "repair-adapter-card.schema.json",
    ]:
        s = _load_schema(schema_dir / name)
        assert isinstance(s, dict), f"{name} 不是 dict"


def test_repair_adapter_nine_required(schema_dir: Path) -> None:
    """修复适配卡 schema 必须有恰好 9 个 required 字段。"""
    s = _load_schema(schema_dir / "repair-adapter-card.schema.json")
    required_fields = s.get("required", [])
    assert len(required_fields) == 9, f"expected 9 required fields, got {len(required_fields)}: {required_fields}"
    expected = {
        "failure_signatures",
        "diagnosis_hints",
        "s_whitelist",
        "b_editable",
        "r_redlines",
        "verify",
        "rollback",
        "watchdog_config",
        "shared_deps",
    }
    assert set(required_fields) == expected, f"field mismatch: extra={set(required_fields)-expected}, missing={expected-set(required_fields)}"


def test_ledger_schema_has_seven_lifecycle_events(schema_dir: Path) -> None:
    """账本 schema 的 event 枚举必须包含七段生命线事件。"""
    s = _load_schema(schema_dir / "loop-ledger.schema.json")
    events = s["properties"]["event"]["enum"]
    expected_events = {
        "intake_raw",
        "admission",
        "distill_extract",
        "distill_compose",
        "grade_structural",
        "grade_llm",
        "repair_loop",
        "promote",
        "finalize",
        "abort",
    }
    assert expected_events.issubset(set(events)), f"missing events: {expected_events - set(events)}"


def test_ledger_schema_required_fields(schema_dir: Path) -> None:
    """账本 schema 必须包含 loop_id/node/event/content_hash/ts 必填字段。"""
    s = _load_schema(schema_dir / "loop-ledger.schema.json")
    required = set(s.get("required", []))
    assert {"loop_id", "node", "event", "content_hash", "ts"}.issubset(
        required
    ), f"missing required: {required}"


def test_failure_signature_required_fields(schema_dir: Path) -> None:
    """故障签名 schema 必须包含 loop/node/error_class/count/ts 必填字段。"""
    s = _load_schema(schema_dir / "failure-signature.schema.json")
    required = set(s.get("required", []))
    assert {"loop", "node", "error_class", "count", "ts"}.issubset(
        required
    ), f"missing required: {required}"


def test_failure_signature_error_classes(schema_dir: Path) -> None:
    """故障签名 schema 的 error_class 枚举必须包含 7 种故障类。"""
    s = _load_schema(schema_dir / "failure-signature.schema.json")
    classes = s["properties"]["error_class"]["enum"]
    expected = {
        "fetch_fail",
        "anchor_mismatch",
        "admission_error",
        "distill_no_converge",
        "grader_all_down",
        "promote_integrity_fail",
        "quarantine_overflow",
    }
    assert expected.issubset(set(classes)), f"missing error classes: {expected - set(classes)}"
