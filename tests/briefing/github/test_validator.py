"""Tests for GitHub briefing schema validator — bidirectional (PASS + FAIL)."""

from pathlib import Path

from akb2.briefing.github.validator import load_schema, validate

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "briefing" / "github"
VALID_FIXTURE = FIXTURES / "schema-valid-sample.json"
INVALID_FIXTURE = FIXTURES / "schema-invalid-missing-summary.json"


def test_valid_fixture_passes():
    """SC-0: valid sample fixture must pass schema validation."""
    errors = validate(str(VALID_FIXTURE))
    assert errors == [], f"Expected no errors, got: {errors}"


def test_invalid_fixture_fails():
    """SC-0: invalid fixture (missing summary) must fail schema validation."""
    errors = validate(str(INVALID_FIXTURE))
    assert len(errors) > 0, "Expected validation errors"


def test_validator_rejects_empty_json(tmp_path):
    """Validator rejects empty JSON object."""
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    errors = validate(str(empty))
    assert len(errors) > 0


def test_schema_loads():
    """Schema file is valid JSON and loadable."""
    schema = load_schema()
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert schema["title"] == "github_briefing_v1"


def test_invalid_fixture_missing_summary_specifically():
    """Invalid fixture fails specifically on missing 'summary'."""
    errors = validate(str(INVALID_FIXTURE))
    summary_errors = [e for e in errors if "summary" in e.lower()]
    assert len(summary_errors) > 0, f"No errors mention 'summary': {errors}"
