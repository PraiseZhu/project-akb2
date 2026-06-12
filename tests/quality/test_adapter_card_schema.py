"""适配卡 schema 校验测试 — SC-6.7。

验证 skillpack/quality/repair-adapter-card.yaml 通过
schema/repair-adapter-card.schema.json 的 jsonschema 校验。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

try:
    import jsonschema
except ImportError:
    jsonschema = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def adapter_card_schema() -> dict:
    """加载 repair-adapter-card.schema.json。"""
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "schema"
        / "repair-adapter-card.schema.json"
    )
    return json.loads(schema_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def adapter_card_instance() -> dict:
    """加载 repair-adapter-card.yaml。"""
    card_path = (
        Path(__file__).resolve().parents[2]
        / "skillpack"
        / "quality"
        / "repair-adapter-card.yaml"
    )
    return yaml.safe_load(card_path.read_text(encoding="utf-8"))


class TestAdapterCardSchema:
    """SC-6.7: 适配卡 schema 自洽验证。"""

    def test_schema_valid_json(self, adapter_card_schema: dict) -> None:
        """schema 自身是合法 JSON schema。"""
        assert "$schema" in adapter_card_schema
        assert adapter_card_schema["type"] == "object"

    def test_jsonschema_validate(self, adapter_card_schema: dict, adapter_card_instance: dict) -> None:
        """适配卡实例通过 schema 校验。"""
        if jsonschema is None:
            pytest.skip("jsonschema 未安装")
        jsonschema.validate(adapter_card_instance, adapter_card_schema)

    def test_nine_required_fields(self, adapter_card_instance: dict) -> None:
        """9 个必填字段齐全。"""
        required = {
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
        assert set(adapter_card_instance.keys()) >= required, (
            f"缺少字段: {required - set(adapter_card_instance.keys())}"
        )

    def test_failure_signatures_seven_items(self, adapter_card_instance: dict) -> None:
        """failure_signatures 含 7 项故障签名。"""
        sigs = adapter_card_instance["failure_signatures"]
        assert len(sigs) == 7, f"期望 7 项故障签名，实际 {len(sigs)}"

        error_classes = {s["error_class"] for s in sigs}
        expected = {
            "fetch_fail",
            "anchor_mismatch",
            "admission_error",
            "distill_no_converge",
            "grader_all_down",
            "promote_integrity_fail",
            "quarantine_overflow",
        }
        assert error_classes == expected, f"故障签名不匹配: {error_classes ^ expected}"

    def test_failure_signatures_tiers_valid(self, adapter_card_instance: dict) -> None:
        """每条故障签名 tier 在 S/B/R 枚举内。"""
        valid_tiers = {"S", "B", "R"}
        for sig in adapter_card_instance["failure_signatures"]:
            assert sig["tier"] in valid_tiers, (
                f"{sig['error_class']}: tier={sig['tier']} 不在 {valid_tiers}"
            )

    def test_diagnosis_hints_covers_all_signatures(self, adapter_card_instance: dict) -> None:
        """diagnosis_hints 覆盖所有故障签名。"""
        sigs = {s["error_class"] for s in adapter_card_instance["failure_signatures"]}
        hints = set(adapter_card_instance["diagnosis_hints"].keys())
        assert hints >= sigs, f"diagnosis_hints 缺少: {sigs - hints}"

    def test_s_whitelist_non_empty(self, adapter_card_instance: dict) -> None:
        """S 档白名单非空。"""
        assert len(adapter_card_instance["s_whitelist"]) > 0

    def test_b_editable_non_empty(self, adapter_card_instance: dict) -> None:
        """B 档可碰路径非空。"""
        assert len(adapter_card_instance["b_editable"]) > 0

    def test_r_redlines_non_empty(self, adapter_card_instance: dict) -> None:
        """R 档红线非空。"""
        assert len(adapter_card_instance["r_redlines"]) > 0

    def test_verify_commands_non_empty(self, adapter_card_instance: dict) -> None:
        """验证命令列表非空。"""
        cmds = adapter_card_instance["verify"]["commands"]
        assert len(cmds) > 0

    def test_rollback_commands_non_empty(self, adapter_card_instance: dict) -> None:
        """回滚命令列表非空。"""
        cmds = adapter_card_instance["rollback"]["commands"]
        assert len(cmds) > 0

    def test_watchdog_config_required_fields(self, adapter_card_instance: dict) -> None:
        """watchdog_config 含 heartbeat_path 和 ledger_path。"""
        wc = adapter_card_instance["watchdog_config"]
        assert "heartbeat_path" in wc
        assert "ledger_path" in wc

    def test_shared_deps_has_required_kinds(self, adapter_card_instance: dict) -> None:
        """shared_deps 每条含 name 和 kind。"""
        valid_kinds = {"api", "file", "directory", "service"}
        for dep in adapter_card_instance["shared_deps"]:
            assert "name" in dep
            assert "kind" in dep
            assert dep["kind"] in valid_kinds, (
                f"{dep['name']}: kind={dep['kind']} 不在 {valid_kinds}"
            )
