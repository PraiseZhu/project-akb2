"""Tests for src/akb2/gate/akb2_gate_check.py.

Scope: static-only verification — AST parse, structural checks, no vault I/O.
The gate engine requires live Obsidian vault staging/ to run; we verify
the module is syntactically correct and exports expected entry points.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def gate_path(repo_root: Path) -> Path:
    src = repo_root / "src" / "akb2" / "gate" / "akb2_gate_check.py"
    assert src.exists(), f"akb2_gate_check.py missing at {src}"
    return src


@pytest.fixture(scope="module")
def gate_source(gate_path: Path) -> str:
    return gate_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def gate_tree(gate_source: str) -> ast.Module:
    return ast.parse(gate_source)


def test_gate_syntax_valid(gate_source: str):
    """akb2_gate_check.py must parse without SyntaxError."""
    try:
        ast.parse(gate_source)
    except SyntaxError as e:
        pytest.fail(f"akb2_gate_check.py has syntax error: {e}")


def test_gate_has_main_function(gate_tree: ast.Module):
    """Module must define a main() function (CLI entry point)."""
    func_names = {
        node.name
        for node in ast.walk(gate_tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert "main" in func_names, "akb2_gate_check.py must define main()"


def test_gate_has_dunder_main_guard(gate_source: str):
    """Module must have if __name__ == '__main__' guard."""
    assert (
        '__name__ == "__main__"' in gate_source
        or "__name__ == '__main__'" in gate_source
    ), "akb2_gate_check.py must have __main__ guard"


def test_gate_readonly_contract(gate_source: str):
    """Gate must not import or call LLM router (L3 proposal layer is read-only)."""
    # Gate is strictly L3 proposal layer — no LLM calls
    forbidden = ["LLMRouter", "llm_router", "openai.chat", "anthropic.messages"]
    for token in forbidden:
        assert token not in gate_source, (
            f"akb2_gate_check.py must not reference '{token}' (L3 read-only layer)"
        )


def test_gate_module_size_nonzero(gate_path: Path):
    """akb2_gate_check.py must be a substantial file (>= 10 KB), not a stub."""
    size = gate_path.stat().st_size
    assert size >= 10_000, (
        f"akb2_gate_check.py is suspiciously small: {size} bytes (expected >= 10 KB)"
    )
