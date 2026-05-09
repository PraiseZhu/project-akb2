"""Tests for src/akb2/toolbox_scan/akb2_toolbox_scan/.

Import-based tests using the pythonpath = ["src/akb2/toolbox_scan"] config
in pyproject.toml. Validates module structure, no-execution policy, and
that the discoverer can be instantiated without live file access.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import smoke tests (pythonpath must include src/akb2/toolbox_scan)
# ---------------------------------------------------------------------------


def test_toolbox_scan_package_importable():
    """akb2_toolbox_scan package must be importable without errors."""
    try:
        import akb2_toolbox_scan  # noqa: F401
    except ImportError as e:
        pytest.fail(f"akb2_toolbox_scan not importable: {e}")


def test_toolbox_scan_discover_importable():
    """discover module must be importable and expose RootSpec and discover_candidates."""
    try:
        from akb2_toolbox_scan.discover import RootSpec, discover_candidates
    except ImportError as e:
        pytest.fail(f"discover module import failed: {e}")
    assert callable(discover_candidates), "discover_candidates must be callable"


def test_toolbox_scan_fingerprint_importable():
    """fingerprint module must be importable and expose fingerprint()."""
    try:
        from akb2_toolbox_scan.fingerprint import fingerprint
    except ImportError as e:
        pytest.fail(f"fingerprint module import failed: {e}")
    assert callable(fingerprint), "fingerprint() must be callable"


def test_toolbox_scan_paths_importable():
    """paths module must be importable and expose validate_output_paths."""
    try:
        from akb2_toolbox_scan.paths import validate_output_paths
    except ImportError as e:
        pytest.fail(f"paths module import failed: {e}")
    assert callable(validate_output_paths)


def test_toolbox_scan_rootspec_fields():
    """RootSpec dataclass must have path and kind fields."""
    from akb2_toolbox_scan.discover import RootSpec
    import dataclasses

    fields = {f.name for f in dataclasses.fields(RootSpec)}
    assert "path" in fields, "RootSpec must have 'path' field"
    assert "kind" in fields, "RootSpec must have 'kind' field"


# ---------------------------------------------------------------------------
# Module file structure checks
# ---------------------------------------------------------------------------


def test_toolbox_scan_all_expected_modules_present(repo_root: Path):
    """All expected .py modules in akb2_toolbox_scan must be present."""
    pkg = repo_root / "src" / "akb2" / "toolbox_scan" / "akb2_toolbox_scan"
    expected = {"__init__.py", "__main__.py", "coverage.py", "discover.py",
                "fingerprint.py", "paths.py", "rank.py", "render.py", "writer.py"}
    actual = {p.name for p in pkg.iterdir() if p.suffix == ".py"}
    missing = expected - actual
    assert not missing, f"Missing modules in akb2_toolbox_scan: {missing}"


def test_toolbox_scan_no_subprocess_in_discover(repo_root: Path):
    """discover.py must not import subprocess (strict no-execution policy).

    Uses AST to detect actual import nodes, ignoring comment mentions.
    """
    import ast as _ast
    src = repo_root / "src" / "akb2" / "toolbox_scan" / "akb2_toolbox_scan" / "discover.py"
    tree = _ast.parse(src.read_text(encoding="utf-8"))
    subprocess_imports = [
        node for node in _ast.walk(tree)
        if isinstance(node, (_ast.Import, _ast.ImportFrom))
        and (
            (isinstance(node, _ast.Import) and any(a.name == "subprocess" for a in node.names))
            or (isinstance(node, _ast.ImportFrom) and node.module == "subprocess")
        )
    ]
    assert not subprocess_imports, (
        "discover.py must not import subprocess (no-execution policy)"
    )


def test_toolbox_scan_no_symlink_follow_in_discover(repo_root: Path):
    """discover.py must not follow symlinks (strict no-execution policy)."""
    src = repo_root / "src" / "akb2" / "toolbox_scan" / "akb2_toolbox_scan" / "discover.py"
    text = src.read_text(encoding="utf-8")
    # The policy comment explicitly says "we never follow symlinks"
    assert "follow_symlinks=True" not in text, (
        "discover.py must not follow symlinks (no-execution policy)"
    )
