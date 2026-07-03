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
    import subprocess
    import sys
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


# ---------------------------------------------------------------------------
# R16 staging 候选豁免（promote Gate-4 与 gate CLI HC-01 互斥修复）
# ---------------------------------------------------------------------------


def test_r16_exempts_staging_candidate_with_directory_scope(monkeypatch):
    """R16 豁免 staging/candidate/ 下、directory_scope 已声明 canonical 目标的候选页。

    痛点:gate CLI HC-01 强制候选必须在 staging/candidate/，但 R16 要求 source 模板在
    wiki/sources/ ——两条规则对 source 候选互斥，导致 promote Gate-4 永远过不了
    (本机真跑 addy 卡在此)。豁免后:staging 候选物理路径待升格后落地 canonical 目录，
    frontmatter directory_scope 已声明目标即合规。
    断言绑定可观测行为:staging 候选 + directory_scope=wiki/sources/ → 0 findings。
    """
    from src.akb2.lint import akb2_lint

    monkeypatch.setattr(akb2_lint, "VAULT_ROOT", Path("/tmp/akb2-r16-test-vault"))
    rule = akb2_lint.rule_r16_template_directory_mismatch
    fm_ok = (
        "template_type: source\n"
        "directory_scope: wiki/sources/\n"
        "status: candidate\n"
    )
    ctx = {"template_type": "source"}
    staging_path = Path("/tmp/akb2-r16-test-vault/staging/candidate/batch/test.md")

    assert rule(staging_path, fm_ok, "body", ctx) == [], (
        "staging 候选 + directory_scope 声明 wiki/sources/ 应豁免 R16"
    )


def test_r16_still_flags_non_staging_source_template(monkeypatch):
    """R16 对非 staging 路径(如 wiki/cql/)的 source 模板仍报 TEMPLATE-DIRECTORY-MISMATCH。

    断言绑定可观测行为:豁免只覆盖 staging/candidate/，active 误放目录仍被拦(护栏不破)。
    """
    from src.akb2.lint import akb2_lint

    monkeypatch.setattr(akb2_lint, "VAULT_ROOT", Path("/tmp/akb2-r16-test-vault"))
    rule = akb2_lint.rule_r16_template_directory_mismatch
    fm = (
        "template_type: source\n"
        "directory_scope: wiki/sources/\n"
        "status: active\n"
    )
    ctx = {"template_type": "source"}
    bad_path = Path("/tmp/akb2-r16-test-vault/wiki/cql/test.md")  # 非 staging/candidate/

    findings = rule(bad_path, fm, "body", ctx)
    assert any(f["rule_id"] == "TEMPLATE-DIRECTORY-MISMATCH" for f in findings), (
        "非 staging 路径的 source 模板应仍报 R16(豁免不外溢)"
    )


def test_r16_still_flags_staging_candidate_with_wrong_directory_scope(monkeypatch):
    """R16 对 directory_scope 未声明正确 canonical 目标的 staging 候选仍报(豁免需匹配)。

    断言绑定可观测行为:豁免要求 directory_scope 含 expected 目录——乱声明的候选仍被拦。
    """
    from src.akb2.lint import akb2_lint

    monkeypatch.setattr(akb2_lint, "VAULT_ROOT", Path("/tmp/akb2-r16-test-vault"))
    rule = akb2_lint.rule_r16_template_directory_mismatch
    fm_bad_scope = (
        "template_type: source\n"
        "directory_scope: wiki/tools/\n"  # source 应是 wiki/sources/，声明错了
        "status: candidate\n"
    )
    ctx = {"template_type": "source"}
    staging_path = Path("/tmp/akb2-r16-test-vault/staging/candidate/batch/test.md")

    findings = rule(staging_path, fm_bad_scope, "body", ctx)
    assert any(f["rule_id"] == "TEMPLATE-DIRECTORY-MISMATCH" for f in findings), (
        "directory_scope 不匹配 expected 的 staging 候选应仍报 R16"
    )


def test_r14_candidate_empty_related_not_flagged():
    """F8-B(lead 裁决 1):R14 加 status=='active' 守卫——candidate(非 active)空 related
    不再误报。冷启动 related 空是诚实状态(frontmatter 语义不失真,不硬塞 component 名)。

    痛点:R14 规则名/docstring 都写明 active-only,但代码漏 status 守卫,对 candidate
    误报 ACTIVE-MISSING-RELATED → F7-C 曾用"塞组件名进 related_concepts"保底绕过,
    但保底失真 frontmatter 语义。守卫对齐后 candidate 不触发,保底可回退。
    断言绑定可观测行为:candidate + 0 个 related_* → 0 findings。
    """
    from src.akb2.lint import akb2_lint

    rule = akb2_lint.rule_r14_active_missing_related
    fm_candidate = (
        "template_type: concept\n"
        "status: candidate\n"
        "slug: test-concept\n"
    )
    ctx = {"template_type": "concept"}
    path = Path("/tmp/akb2-r14-test/staging/candidate/batch/test.md")

    assert rule(path, fm_candidate, "body", ctx) == [], (
        "candidate(非 active)空 related 不应触发 R14(守卫对齐)"
    )


def test_r14_proposal_empty_related_not_flagged():
    """F8-B:proposal 状态空 related 也不触发 R14(非 active)。"""
    from src.akb2.lint import akb2_lint

    rule = akb2_lint.rule_r14_active_missing_related
    fm_proposal = (
        "template_type: concept\n"
        "status: proposal\n"
        "slug: test-concept\n"
    )
    ctx = {"template_type": "concept"}
    path = Path("/tmp/akb2-r14-test/staging/candidate/batch/test.md")

    assert rule(path, fm_proposal, "body", ctx) == [], (
        "proposal(非 active)空 related 不应触发 R14"
    )


def test_r14_active_empty_related_still_flagged():
    """F8-B:active + 空 related → 仍触发 R14(规则本身行为对 active 不变)。

    断言绑定可观测行为:active 0 个 related_* → 1 finding(守卫不放松 active 要求)。
    """
    from src.akb2.lint import akb2_lint

    rule = akb2_lint.rule_r14_active_missing_related
    fm_active = (
        "template_type: concept\n"
        "status: active\n"
        "slug: test-concept\n"
    )
    ctx = {"template_type": "concept"}
    path = Path("/tmp/akb2-r14-test/wiki/concepts/test-concept.md")

    findings = rule(path, fm_active, "body", ctx)
    assert any(f["rule_id"] == "ACTIVE-MISSING-RELATED" for f in findings), (
        "active + 0 个 related_* 应仍报 R14(守卫不放松 active 要求)"
    )


def test_r14_active_with_related_not_flagged():
    """F8-B:active + 非空 related → 不触发 R14(sanity,规则正常放行)。"""
    from src.akb2.lint import akb2_lint

    rule = akb2_lint.rule_r14_active_missing_related
    fm_active_ok = (
        "template_type: concept\n"
        "status: active\n"
        "slug: test-concept\n"
        "related_concepts:\n"
        "  - [[other-concept]]\n"
    )
    ctx = {"template_type": "concept"}
    path = Path("/tmp/akb2-r14-test/wiki/concepts/test-concept.md")

    assert rule(path, fm_active_ok, "body", ctx) == [], (
        "active + 非空 related_concepts 应放行(规则正常)"
    )

