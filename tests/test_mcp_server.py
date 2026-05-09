"""Tests for toolbox/cc-akb-search/mcp_server/server.py.

Strategy: AST-based analysis only — no module import (server.py requires
mcp, scripts.core.search, scripts.utils.db which are not installed here).
AST lets us verify the read-only contract without executing any code.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def server_path(repo_root: Path) -> Path:
    src = repo_root / "toolbox" / "cc-akb-search" / "mcp_server" / "server.py"
    assert src.exists(), f"server.py missing at {src}"
    return src


@pytest.fixture(scope="module")
def server_source(server_path: Path) -> str:
    return server_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def server_tree(server_source: str) -> ast.Module:
    return ast.parse(server_source)


def test_mcp_server_syntax_valid(server_source: str):
    """server.py must parse without SyntaxError."""
    try:
        ast.parse(server_source)
    except SyntaxError as e:
        pytest.fail(f"server.py has syntax error: {e}")


def test_mcp_server_imports_fastmcp(server_source: str):
    """server.py must import FastMCP (MCP framework entry point)."""
    assert "FastMCP" in server_source, "server.py must use FastMCP"


def test_mcp_server_readonly_catalogdb(server_source: str):
    """server.py must instantiate CatalogDB with readonly=True (read-only contract)."""
    # Check source text for the readonly=True call pattern
    assert "CatalogDB" in server_source, "server.py must reference CatalogDB"
    assert "readonly=True" in server_source, (
        "server.py must instantiate CatalogDB(readonly=True) — read-only SQLite contract"
    )


def test_mcp_server_no_init_tables(server_tree: ast.Module):
    """server.py must NOT call init_tables() (would write to catalog.db).

    Uses AST to detect actual Call nodes, not just comment mentions.
    """
    calls_to_init_tables = [
        node
        for node in ast.walk(server_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "init_tables"
    ]
    # Also check for bare init_tables() call (not attribute)
    bare_calls = [
        node
        for node in ast.walk(server_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "init_tables"
    ]
    assert not calls_to_init_tables and not bare_calls, (
        "server.py must not call init_tables() — AKB catalog.db is fully initialized"
    )


def test_mcp_server_no_write_imports(server_tree: ast.Module):
    """server.py must not import from scripts.mcp_server (exposes write operations).

    Uses AST to detect actual import nodes, not comment mentions.
    """
    forbidden_module = "scripts.mcp_server"
    bad_imports = []
    for node in ast.walk(server_tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == forbidden_module or mod.startswith(forbidden_module + "."):
                    bad_imports.append(mod)
            else:
                for alias in node.names:
                    if alias.name == forbidden_module or alias.name.startswith(forbidden_module + "."):
                        bad_imports.append(alias.name)
    assert not bad_imports, (
        f"server.py must not import from scripts.mcp_server (contains akb_manage write ops); "
        f"found: {bad_imports}"
    )


def test_mcp_server_akb_search_tool_defined(server_tree: ast.Module):
    """server.py must define the akb_search tool function."""
    func_names = {
        node.name
        for node in ast.walk(server_tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert "akb_search" in func_names, "server.py must define akb_search() tool function"


def test_mcp_server_router_none_contract(server_source: str):
    """server.py must use router=None (no LLM → no include_summary)."""
    assert "router=None" in server_source, (
        "server.py must pass router=None (LLM disabled per read-only contract)"
    )


def test_mcp_server_include_summary_false(server_source: str):
    """server.py must force include_summary=False (calling LLM is non-read-only)."""
    assert "include_summary=False" in server_source, (
        "server.py must pass include_summary=False (LLM call violates read-only contract)"
    )
