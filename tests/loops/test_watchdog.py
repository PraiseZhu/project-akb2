"""SC-s4: watchdog 脚本测试。

约束：
- --self-test 退出码 0
- 脚本可执行
- 行数 ≤80
- 无 LLM 调用（curl/*chat/openai/deepseek）
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_watchdog_is_executable(scripts_dir: Path) -> None:
    """脚本必须有执行权限。"""
    p = scripts_dir / "akb2-watchdog.sh"
    assert p.is_file(), "watchdog script missing"
    assert p.stat().st_mode & 0o111, "watchdog not executable"


def test_watchdog_line_count_under_80(scripts_dir: Path) -> None:
    """脚本行数 ≤80（宪法设计约束）。"""
    p = scripts_dir / "akb2-watchdog.sh"
    count = len(p.read_text().splitlines())
    assert count <= 80, f"watchdog is {count} lines, exceeds 80 limit"


def test_self_test_exit_zero(worktree_root: Path) -> None:
    """--self-test 退出码 0。"""
    script = worktree_root / "scripts" / "akb2-watchdog.sh"
    result = subprocess.run(
        [str(script), "--self-test"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr} | stdout: {result.stdout}"
    assert "SELF_TEST_PASS" in result.stdout


def test_no_llm_calls(scripts_dir: Path) -> None:
    """脚本不含 LLM API 调用（curl *chat / openai / deepseek）。"""
    import re

    p = scripts_dir / "akb2-watchdog.sh"
    content = p.read_text()
    # 排除注释行后检查
    code_lines = [line for line in content.splitlines() if not line.strip().startswith("#")]
    code = "\n".join(code_lines)
    patterns = [
        r"curl.*chat",
        r"openai",
        r"deepseek",
        r"anthropic",
        r"gemini",
    ]
    for pat in patterns:
        assert not re.search(pat, code, re.IGNORECASE), f"found LLM API pattern: {pat}"


def test_watchdog_no_args_usage(worktree_root: Path) -> None:
    """无参数运行时输出 usage 并退出 4。"""
    script = worktree_root / "scripts" / "akb2-watchdog.sh"
    result = subprocess.run(
        [str(script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 4
    assert "Usage:" in result.stderr


def test_self_test_rejects_extra_args(worktree_root: Path) -> None:
    """--self-test 不接受额外参数（应忽略）。"""
    script = worktree_root / "scripts" / "akb2-watchdog.sh"
    result = subprocess.run(
        [str(script), "--self-test", "extra"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0  # self-test takes precedence
    assert "SELF_TEST_PASS" in result.stdout
