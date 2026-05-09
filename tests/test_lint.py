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
