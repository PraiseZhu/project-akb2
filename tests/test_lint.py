"""Tests for src/akb2/lint/akb2_lint.py.

Scope: static-only verification — AST parse, structural checks, no vault I/O.
The lint engine requires the full Obsidian vault to run meaningfully; we verify
the module is syntactically correct and exports the expected entry points.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def lint_path(repo_root: Path) -> Path:
    src = repo_root / "src" / "akb2" / "lint" / "akb2_lint.py"
    assert src.exists(), f"akb2_lint.py missing at {src}"
    return src


@pytest.fixture(scope="module")
def lint_source(lint_path: Path) -> str:
    return lint_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lint_tree(lint_source: str) -> ast.Module:
    return ast.parse(lint_source)


def test_lint_syntax_valid(lint_source: str):
    """akb2_lint.py must parse without SyntaxError."""
    try:
        ast.parse(lint_source)
    except SyntaxError as e:
        pytest.fail(f"akb2_lint.py has syntax error: {e}")


def test_lint_has_main_function(lint_tree: ast.Module):
    """Module must define a main() function (CLI entry point)."""
    func_names = {
        node.name
        for node in ast.walk(lint_tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert "main" in func_names, "akb2_lint.py must define main()"


def test_lint_has_dunder_main_guard(lint_source: str):
    """Module must have if __name__ == '__main__' guard."""
    assert (
        '__name__ == "__main__"' in lint_source
        or "__name__ == '__main__'" in lint_source
    ), "akb2_lint.py must have if __name__ == '__main__' guard"


def test_lint_rule_count_at_least_18(lint_source: str):
    """Module documents at least 18 lint rules (R1-R18 baseline per docstring)."""
    rule_ids = set(re.findall(r"\bR(\d+)\b", lint_source))
    rule_ints = {int(r) for r in rule_ids if r.isdigit()}
    assert max(rule_ints, default=0) >= 18, (
        f"Expected at least 18 rules, found max rule id: {max(rule_ints, default=0)}"
    )


def test_lint_module_size_nonzero(lint_path: Path):
    """akb2_lint.py must be a substantial file (>= 10 KB), not a stub."""
    size = lint_path.stat().st_size
    assert size >= 10_000, (
        f"akb2_lint.py is suspiciously small: {size} bytes (expected >= 10 KB)"
    )


def test_query_result_valid_no_p0_p1(repo_root: Path):
    """valid-query-result.md should lint with 0 P0/P1 findings."""
    import subprocess
    fixture = repo_root / "tests" / "fixtures" / "compound" / "valid-query-result.md"
    result = subprocess.run(
        ["python3", str(repo_root / "src" / "akb2" / "lint" / "akb2_lint.py"),
         "--version", "v0.3", str(fixture)],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result.returncode == 0, f"lint failed on valid fixture: {result.stdout}"


def test_query_result_missing_derived_r23(repo_root: Path):
    """invalid-missing-derived.md should trigger R23."""
    import subprocess
    fixture = repo_root / "tests" / "fixtures" / "compound" / "invalid-missing-derived.md"
    result = subprocess.run(
        ["python3", str(repo_root / "src" / "akb2" / "lint" / "akb2_lint.py"),
         "--version", "v0.3", str(fixture)],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result.returncode != 0
    assert "DERIVED-FROM" in result.stdout


def test_query_result_bad_derived_slug_r23_with_explicit_vault_root(repo_root: Path):
    """R23 must catch bad derived_from slug even when fixture is NOT under wiki/.

    Regression for path-guessing bug: invalid-bad-derived-slug.md lives under
    tests/fixtures/compound/ (not under wiki/), so path-based wiki_root inference
    yields None and R23 silently skips. With --vault-root explicit, wiki_root
    is taken from <vault-root>/wiki and R23 fires correctly.
    """
    import sys
    import subprocess
    fixture = repo_root / "tests" / "fixtures" / "compound" / "invalid-bad-derived-slug.md"
    vault_root = repo_root / "tests" / "fixtures" / "compound"
    result = subprocess.run(
        [sys.executable, str(repo_root / "src" / "akb2" / "lint" / "akb2_lint.py"),
         "--version", "v0.3",
         "--vault-root", str(vault_root),
         str(fixture)],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result.returncode != 0, (
        f"R23 must fail on bad-derived-slug; got rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "DERIVED-FROM-MISSING" in result.stdout, (
        f"expected DERIVED-FROM-MISSING in stdout; got: {result.stdout}"
    )


def test_query_result_bad_confidence_r24(repo_root: Path):
    """invalid-bad-confidence.md should trigger R24."""
    import subprocess
    fixture = repo_root / "tests" / "fixtures" / "compound" / "invalid-bad-confidence.md"
    result = subprocess.run(
        ["python3", str(repo_root / "src" / "akb2" / "lint" / "akb2_lint.py"),
         "--version", "v0.3", str(fixture)],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result.returncode != 0
    assert "CONFIDENCE" in result.stdout


def test_query_result_empty_query_r25(repo_root: Path):
    """invalid-empty-query.md should trigger R25."""
    import subprocess
    fixture = repo_root / "tests" / "fixtures" / "compound" / "invalid-empty-query.md"
    result = subprocess.run(
        ["python3", str(repo_root / "src" / "akb2" / "lint" / "akb2_lint.py"),
         "--version", "v0.3", str(fixture)],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result.returncode != 0
    assert "QUERY-EMPTY" in result.stdout
