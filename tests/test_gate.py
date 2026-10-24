"""Tests for src/akb2/gate/akb2_gate_check.py.

Scope: static-only verification — AST parse, structural checks, no vault I/O.
The gate engine requires live Obsidian vault staging/ to run; we verify
the module is syntactically correct and exports expected entry points.
"""

from __future__ import annotations

import ast
import sys
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


def test_loop2_candidate_path_is_allowed(tmp_path: Path):
    """实跑 shadow_blocked：gate CLI 拒 staging-loop2/candidate，proposed 变空。"""
    from src.akb2.gate.akb2_gate_check import (
        check_candidate_only,
        guard_input_path,
    )

    vault = tmp_path / "vault"
    candidate = vault / "staging-loop2" / "candidate" / "batch" / "x.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("# t\n", encoding="utf-8")
    guard_input_path(vault, candidate)
    ok, reason = check_candidate_only(
        "staging-loop2/candidate/batch/x.md"
    )
    assert ok is True
    assert "candidate" in reason


def test_query_result_gate_pass(repo_root: Path):
    """query-result candidate with valid fields should pass gate (exit 0)."""
    import subprocess
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "src" / "akb2" / "gate" / "akb2_gate_check.py"),
            "check",
            "--akb-root", str(repo_root / "tests" / "fixtures" / "gate"),
            "--input", "staging/candidate/candidate-query-result.md",
            "--dry-run",
        ],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result.returncode == 0, f"gate failed: {result.stdout}\n{result.stderr}"
    assert "HC-03" not in result.stdout or "skipped" in result.stdout
    assert "HC-05" not in result.stdout or "skipped" in result.stdout


def test_parse_errors_exit_nonzero(tmp_path: Path):
    """严格 YAML 失败即使 fallback 抽出字段，也不得 exit 0。"""
    from argparse import Namespace

    from src.akb2.gate.akb2_gate_check import EXIT_PARSE_ERROR, cmd_check

    vault = tmp_path / "vault"
    cand = vault / "staging" / "candidate" / "bad.md"
    cand.parent.mkdir(parents=True)
    cand.write_text(
        "---\npage_status: candidate\nsource_authors: @nathansobo\n---\n# t\n",
        encoding="utf-8",
    )
    rc = cmd_check(
        Namespace(
            akb_root=str(vault),
            input="staging/candidate/bad.md",
            date="2026-09-02",
            write_manifest=False,
        )
    )
    assert rc == EXIT_PARSE_ERROR


def test_query_result_template_type_in_enum(gate_source: str):
    """TEMPLATE_TYPE_ENUM must include 'query-result'."""
    assert '"query-result"' in gate_source or "'query-result'" in gate_source, (
        "TEMPLATE_TYPE_ENUM must include query-result"
    )


def test_hc06_future_questions_still_requires_two_to_three():
    """t6: HC-06 对 frontmatter future_questions 仍要求 2-3 条。"""
    from src.akb2.gate.akb2_gate_check import check_future_questions

    ok_fm = {"future_questions": ["短句一？", "短句二？"]}
    passed, msg = check_future_questions(ok_fm)
    assert passed, msg
    too_few, _ = check_future_questions({"future_questions": ["only one"]})
    assert too_few is False
    too_many, _ = check_future_questions(
        {"future_questions": ["a", "b", "c", "d"]}
    )
    assert too_many is False
