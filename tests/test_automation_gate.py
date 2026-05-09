"""Tests for src/akb2/automation_gate/.

Scope: policy.json validity, schema file validity, fixture directory structure,
and the run_gate.sh shell script presence. No live execution against vault.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


GATE_DIR = None  # resolved via fixture


@pytest.fixture(scope="module")
def gate_dir(repo_root: Path) -> Path:
    d = repo_root / "src" / "akb2" / "automation_gate"
    assert d.is_dir(), f"automation_gate/ missing at {d}"
    return d


def test_policy_json_valid(gate_dir: Path):
    """policy.json must parse as valid JSON."""
    policy_path = gate_dir / "policy.json"
    assert policy_path.exists(), "policy.json must exist"
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        pytest.fail(f"policy.json is invalid JSON: {e}")
    assert isinstance(data, dict), "policy.json must be a JSON object"


def test_policy_json_has_required_keys(gate_dir: Path):
    """policy.json must have version, gate_conditions, and description keys."""
    data = json.loads((gate_dir / "policy.json").read_text(encoding="utf-8"))
    for key in ("version", "gate_conditions", "description"):
        assert key in data, f"policy.json missing required key: '{key}'"


def test_policy_gate_conditions_nonempty(gate_dir: Path):
    """policy.json gate_conditions must be a non-empty list."""
    data = json.loads((gate_dir / "policy.json").read_text(encoding="utf-8"))
    conditions = data.get("gate_conditions", [])
    assert isinstance(conditions, list) and len(conditions) > 0, (
        "policy.json gate_conditions must be a non-empty list"
    )


def test_schema_file_valid_json(gate_dir: Path):
    """gate-result.schema.json must parse as valid JSON with $schema key."""
    schema_path = gate_dir / "schema" / "gate-result.schema.json"
    assert schema_path.exists(), "gate-result.schema.json must exist"
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "$schema" in data, "schema file must have '$schema' key"
    assert "required" in data, "schema file must have 'required' key"


def test_fixtures_directory_has_five_cases(gate_dir: Path):
    """fixtures/ must contain at least 5 subdirectories (positive + 4 blocked cases)."""
    fixtures = gate_dir / "fixtures"
    assert fixtures.is_dir(), "fixtures/ directory must exist"
    subdirs = [d for d in fixtures.iterdir() if d.is_dir()]
    assert len(subdirs) >= 5, (
        f"fixtures/ must have >= 5 subdirectories, found {len(subdirs)}: "
        + ", ".join(d.name for d in subdirs)
    )


def test_fixtures_positive_case_exists(gate_dir: Path):
    """fixtures/positive/ must exist (the passing baseline case)."""
    positive = gate_dir / "fixtures" / "positive"
    assert positive.is_dir(), "fixtures/positive/ must exist"


def test_fixtures_expected_json_present(gate_dir: Path):
    """Each fixture case must have expected.json."""
    fixtures = gate_dir / "fixtures"
    for case in fixtures.iterdir():
        if not case.is_dir():
            continue
        expected = case / "expected.json"
        assert expected.exists(), f"fixtures/{case.name}/expected.json missing"
        # Must be valid JSON
        try:
            json.loads(expected.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            pytest.fail(f"fixtures/{case.name}/expected.json invalid JSON: {e}")


def test_run_gate_sh_present_and_executable(gate_dir: Path):
    """run_gate.sh must exist (not deleted during migration)."""
    script = gate_dir / "run_gate.sh"
    assert script.exists(), "run_gate.sh must exist in automation_gate/"
    # Check it has bash shebang
    first_line = script.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    assert first_line.startswith("#!"), f"run_gate.sh must have a shebang, got: {first_line!r}"
