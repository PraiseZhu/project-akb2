"""Tests for src/akb2/matrix/v0/collect_global_matrix.sh.

Scope: script presence, shebang, read-only contract assertions in source text.
No live execution (requires vault + jq + all 5 toolbox SKILL.md to be present).
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def matrix_script(repo_root: Path) -> Path:
    script = repo_root / "src" / "akb2" / "matrix" / "v0" / "collect_global_matrix.sh"
    assert script.exists(), f"collect_global_matrix.sh missing at {script}"
    return script


@pytest.fixture(scope="module")
def matrix_source(matrix_script: Path) -> str:
    return matrix_script.read_text(encoding="utf-8")


def test_collect_global_matrix_exists(matrix_script: Path):
    """collect_global_matrix.sh must exist at expected path."""
    assert matrix_script.is_file()


def test_collect_global_matrix_has_bash_shebang(matrix_source: str):
    """Script must start with a bash shebang."""
    first_line = matrix_source.splitlines()[0]
    assert first_line.startswith("#!/usr/bin/env bash") or first_line.startswith("#!/bin/bash"), (
        f"collect_global_matrix.sh must start with bash shebang, got: {first_line!r}"
    )


def test_collect_global_matrix_declares_5_slugs(matrix_source: str):
    """Script must reference all 5 tool slugs."""
    slugs = ["executable-plan", "review", "local-llm", "skill-stocktake", "akb-search"]
    for slug in slugs:
        assert slug in matrix_source, (
            f"collect_global_matrix.sh must reference slug '{slug}'"
        )


def test_collect_global_matrix_read_only_contract(matrix_source: str):
    """Script must not contain forbidden write operations (no rm -rf, no tee to SKILL.md)."""
    # The script comments explicitly list allowed tools; verify no SKILL.md writes
    forbidden_patterns = [
        "rm -rf",  # bulk delete forbidden
        "> SKILL.md",  # no overwriting skill files
        ">> SKILL.md",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in matrix_source, (
            f"collect_global_matrix.sh must not contain '{pattern}' (read-only contract)"
        )


def test_collect_global_matrix_outputs_exist(repo_root: Path):
    """v0/outputs/ directory with matrix files must exist (committed baseline)."""
    outputs = repo_root / "src" / "akb2" / "matrix" / "v0" / "outputs"
    assert outputs.is_dir(), "matrix/v0/outputs/ directory must exist"
    json_files = list(outputs.glob("*.json"))
    assert len(json_files) >= 1, "outputs/ must contain at least one .json matrix file"
