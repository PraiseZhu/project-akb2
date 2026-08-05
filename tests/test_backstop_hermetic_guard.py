"""Cross-file guard for deterministic backstop process probes."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOCIAL_TEST = PROJECT_ROOT / "tests" / "briefing" / "test_briefing_backstop.py"
GITHUB_TEST = (
    PROJECT_ROOT / "tests" / "briefing" / "github" / "test_github_backstop.py"
)


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines = source.splitlines()
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"{path}: missing {name}")


def test_both_backstop_files_survive_injected_process(tmp_path):
    """A real matching argv must not steer either complete backstop test file."""
    social_fixture = _function_source(SOCIAL_TEST, "_stub_pgrep_probe")
    github_fixture = _function_source(GITHUB_TEST, "_stub_pgrep_probe")
    assert social_fixture == github_fixture, "the two PATH-level pgrep fixtures drifted"
    assert "AKB2_GITHUB_BACKSTOP_PGREP_PATTERN" not in GITHUB_TEST.read_text(
        encoding="utf-8"
    ), "GitHub tests must not fall back to an env-only probe mode"

    ready = tmp_path / "sleeper-ready"
    sleeper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(240)",
            str(ready),
            "akb2.briefing.pipeline",
            "akb2.briefing.github.pipeline",
        ]
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.01)
        assert ready.exists(), "injected process did not reach exec-ready state"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                str(SOCIAL_TEST.relative_to(PROJECT_ROOT)),
                str(GITHUB_TEST.relative_to(PROJECT_ROOT)),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=240,
            env={
                **os.environ,
                "PYTHONPATH": str(PROJECT_ROOT / "src"),
                "VAULT_ROOT": str(tmp_path / "vault"),
            },
        )
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sleeper.kill()
            sleeper.wait(timeout=5)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
