"""E2E integration tests for knowledge-compounding pipeline.

Tests the full lint → gate → index pipeline, save-trigger plan doc,
and search-routing SKILL.md in an isolated temporary vault.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Create a minimal temporary vault with wiki pages and staging."""
    wiki = tmp_path / "wiki" / "concepts"
    wiki.mkdir(parents=True)
    staging = tmp_path / "staging" / "candidate"
    staging.mkdir(parents=True)

    # Create an existing active concept page
    (wiki / "existing-source.md").write_text(textwrap.dedent("""\
        ---
        title: Existing Source
        template_type: concept
        page_status: active
        slug: existing-source
        tldr: A test concept page
        tags: [test]
        graph_role: leaf
        evidence_provenance: source_grounded
        related_concepts: [some-other]
        ---

        ## TLDR
        Test concept page.

        ## 入选理由
        Test.

        ## 证据地图
        Test.

        ## 核心论点
        Test.

        ## 论证与解释
        Test.

        ## 适用边界
        Test.

        ## 图谱关系
        Test.

        ## Future Questions
        Test.

        ## 维护记录
        Test.
    """))

    # Create a valid query-result candidate
    (staging / "candidate-query-result.md").write_text(textwrap.dedent("""\
        ---
        title: Test Query Result
        template_type: query-result
        page_status: candidate
        slug: candidate-query-result
        derived_from: [existing-source]
        query: "What is a test?"
        confidence: high
        future_questions:
          - Follow-up question 1?
          - Follow-up question 2?
          - Follow-up question 3?
        ---

        ## TLDR
        This is a test query result.
    """))

    return tmp_path


def test_lint_pass_on_valid_query_result(repo_root: Path):
    """Lint should pass (exit 0) on valid query-result fixture."""
    fixture = repo_root / "tests" / "fixtures" / "compound" / "valid-query-result.md"
    result = subprocess.run(
        ["python3", str(repo_root / "src" / "akb2" / "lint" / "akb2_lint.py"),
         "--version", "v0.3", str(fixture)],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result.returncode == 0, f"lint failed: {result.stdout}"


def test_lint_fail_on_invalid_query_result(repo_root: Path):
    """Lint should fail (exit 1) on query-result with missing derived_from."""
    fixture = repo_root / "tests" / "fixtures" / "compound" / "invalid-missing-derived.md"
    result = subprocess.run(
        ["python3", str(repo_root / "src" / "akb2" / "lint" / "akb2_lint.py"),
         "--version", "v0.3", str(fixture)],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result.returncode != 0
    assert "DERIVED-FROM" in result.stdout


def test_gate_pass_on_query_result(repo_root: Path):
    """Gate should pass (exit 0) on valid query-result candidate."""
    result = subprocess.run(
        [sys.executable,
         str(repo_root / "src" / "akb2" / "gate" / "akb2_gate_check.py"),
         "check",
         "--akb-root", str(repo_root / "tests" / "fixtures" / "gate"),
         "--input", "staging/candidate/candidate-query-result.md",
         "--dry-run"],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result.returncode == 0, f"gate failed: {result.stdout}\n{result.stderr}"
    assert "proposed_gate_decision: active" in result.stdout


def test_index_generate_dry_run(tmp_vault: Path, repo_root: Path):
    """Index generator should produce output with ## sections for active pages."""
    result = subprocess.run(
        ["python3", str(repo_root / "src" / "akb2" / "index" / "generate_index.py"),
         "--dry-run", "--vault-root", str(tmp_vault)],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result.returncode == 0, f"index failed: {result.stdout}\n{result.stderr}"
    assert "##" in result.stdout
    assert "Existing Source" in result.stdout


def test_index_apply_and_self_exclude(tmp_vault: Path, repo_root: Path):
    """Index --apply should write 00-Index.md and exclude itself on re-run."""
    gen_script = str(repo_root / "src" / "akb2" / "index" / "generate_index.py")

    # First run: --apply writes the file
    result1 = subprocess.run(
        ["python3", gen_script, "--vault-root", str(tmp_vault), "--apply"],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result1.returncode == 0

    index_file = tmp_vault / "wiki" / "00-Index.md"
    assert index_file.exists(), "00-Index.md should be created by --apply"

    # Second run: should not include 00-Index.md in its own output
    result2 = subprocess.run(
        ["python3", gen_script, "--vault-root", str(tmp_vault), "--apply"],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert result2.returncode == 0
    content = index_file.read_text()
    lines = [line for line in content.split("\n") if line.startswith("- **")]
    assert not any("00-Index" in line for line in lines), (
        "00-Index.md must not appear as a page entry"
    )


def test_save_trigger_plan_doc_exists(repo_root: Path):
    """Step 6 plan doc must contain save-query-result workflow definition."""
    plan_doc = (repo_root / "plans" / "gd" / "2026-05-13-knowledge-compounding"
                / "gd-step-6-save-trigger.md")
    assert plan_doc.exists(), "Step 6 plan doc must exist"
    content = plan_doc.read_text()
    assert content.count("save-query-result") >= 3, "plan doc must mention save-query-result >= 3 times"
    assert any(w in content.lower() for w in ("deferred", "授权等待")), \
        "plan doc must declare deferred/授权等待"


def test_search_routing_skill_md(repo_root: Path):
    """Step 7 SKILL.md must define 3 search scopes and AKB2 graph-follow retrieval."""
    skill_md = repo_root / "toolbox" / "cc-akb-search" / "SKILL.md"
    assert skill_md.exists(), "cc-akb-search SKILL.md must exist"
    content = skill_md.read_text()
    assert "scope=toolbox" in content
    assert "scope=knowledge" in content
    assert "scope=all" in content
    assert content.count("知识库") >= 2
    assert content.count("工具库") >= 2
    assert "akb2_retrieval_search" in content
    assert "limit=1000" in content
    assert "[[wikilink]]" in content
    assert '--vault-root "/Users/praise/Obsidian/Praise Space/AKB2.0"' in content
    assert 'AKB2_ROOT="/Users/praise/Obsidian/Praise Space/AKB2.0"' in content
    assert "--vault-root /Users/praise/Obsidian\n" not in content
    assert "--vault-root /Users/praise/Obsidian " not in content
