"""Tests for social briefing backstop deadline and frontmatter behavior."""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKSTOP_SH = PROJECT_ROOT / "scripts" / "briefing_backstop.sh"


def _today() -> str:
    return date.today().isoformat()


def _run_backstop(
    vault_root: Path,
    extra_args: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    args = ["/bin/bash", str(BACKSTOP_SH), "--vault-root", str(vault_root)]
    if extra_args:
        args += extra_args
    # SC-2 验收1：测试默认把日志隔离到 tmp，绝不污染仓库 logs/backstop.log。
    # 测试可经 env_overrides["AKB2_BACKSTOP_LOG"] 显式指定自定义日志路径。
    isolated_log = tempfile.NamedTemporaryFile(
        prefix="akb2-backstop-test-", suffix=".log", delete=False
    )
    isolated_log.close()
    env = {
        **os.environ,
        "AKB2_BRIEFING_DEADLINE_HOUR": "99",
        "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        "AKB2_BACKSTOP_LOG": isolated_log.name,
        **(env_overrides or {}),
    }
    return subprocess.run(args, capture_output=True, text=True, env=env)


def test_existing_pass_report_skips_even_after_deadline(tmp_path):
    """macOS-compatible frontmatter parsing must recognize audit_verdict=pass."""
    vault = tmp_path / "vault"
    out_dir = vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social"
    out_dir.mkdir(parents=True)
    (out_dir / f"{_today()} Daily Briefing.md").write_text(
        "---\naudit_verdict: pass\n---\n# briefing\n",
        encoding="utf-8",
    )

    result = _run_backstop(
        vault,
        ["--dry-run"],
        env_overrides={
            "AKB2_BRIEFING_DEADLINE_HOUR": "0",
            "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        },
    )

    assert result.returncode == 0
    assert "audit_verdict=pass" in result.stderr
    assert "无需补跑" in result.stderr


def test_missing_report_after_deadline_fails_visibly(tmp_path):
    """After the noon deadline, missing Social output must not start a late rerun."""
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    result = _run_backstop(
        vault,
        ["--dry-run"],
        env_overrides={
            "AKB2_BRIEFING_DEADLINE_HOUR": "0",
            "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        },
    )

    assert result.returncode == 124
    assert "deadline" in result.stderr
    assert "不再启动补跑" in result.stderr


def test_morning_deadline_compare_uses_decimal_time(tmp_path):
    """08:00 must not trip bash's octal parsing when comparing HHMM values."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_date = fake_bin / "date"
    fake_date.write_text(
        """#!/usr/bin/env bash
case "${1:-}" in
  +%H%M) echo 0800 ;;
  +%Y-%m-%d) echo 2026-06-19 ;;
  *) echo "2026-06-19 08:00:00" ;;
esac
""",
        encoding="utf-8",
    )
    fake_date.chmod(0o755)
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    result = _run_backstop(
        vault,
        ["--dry-run"],
        env_overrides={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "AKB2_BRIEFING_DEADLINE_HOUR": "12",
            "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        },
    )

    assert result.returncode == 0
    assert "dry-run" in result.stderr
    assert "value too great for base" not in result.stderr


# ── SC-2: 日志隔离 + 单一判定 ─────────────────────────────────────────────


def test_custom_log_path_isolated_single_skip_line(tmp_path):
    """SC-2 验收2: AKB2_BACKSTOP_LOG 指向自定义路径；pass 时只一条 skip 行，无 rerun/FAILED。

    断言绑定可观测行为：pass 路径只写一条 skip 日志（含 audit_verdict=pass 与 run_id），
    不出现同 run_id 的 rerun/FAILED — 锁死"同一轮自相矛盾"的回归。
    """
    vault = tmp_path / "vault"
    out_dir = vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social"
    out_dir.mkdir(parents=True)
    (out_dir / f"{_today()} Daily Briefing.md").write_text(
        "---\naudit_verdict: pass\n---\n# briefing\n",
        encoding="utf-8",
    )

    custom_log = tmp_path / "custom-backstop.log"
    result = _run_backstop(
        vault,
        ["--dry-run"],
        env_overrides={
            "AKB2_BACKSTOP_LOG": str(custom_log),
            "AKB2_BRIEFING_DEADLINE_HOUR": "0",
            "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        },
    )

    assert result.returncode == 0
    assert custom_log.exists()
    content = custom_log.read_text(encoding="utf-8")
    lines = [ln for ln in content.splitlines() if ln.strip()]
    assert len(lines) == 1, f"pass 路径应只一条日志行，实际:\n{content}"
    assert "skip" in lines[0]
    assert "audit_verdict=pass" in lines[0]
    assert "无需补跑" in lines[0]
    assert "run_id=" in lines[0]
    # 同 run_id 不应出现 rerun / FAILED
    assert "rerun" not in content
    assert "FAILED" not in content


def test_repo_backstop_log_not_modified(tmp_path):
    """SC-2 验收1: 测试运行不改仓库 logs/backstop.log（日志已隔离到 tmp）。"""
    repo_log = PROJECT_ROOT / "logs" / "backstop.log"
    repo_log.parent.mkdir(parents=True, exist_ok=True)
    if not repo_log.exists():
        repo_log.write_text("", encoding="utf-8")
    before = repo_log.stat().st_mtime_ns

    vault = tmp_path / "vault"
    out_dir = vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social"
    out_dir.mkdir(parents=True)
    (out_dir / f"{_today()} Daily Briefing.md").write_text(
        "---\naudit_verdict: pass\n---\n# briefing\n",
        encoding="utf-8",
    )
    _run_backstop(vault, ["--dry-run"])

    after = repo_log.stat().st_mtime_ns
    assert before == after, "测试不应修改仓库 logs/backstop.log"


def test_log_path_arg_overrides_env(tmp_path):
    """--log-path 优先级高于 AKB2_BACKSTOP_LOG。"""
    vault = tmp_path / "vault"
    out_dir = vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social"
    out_dir.mkdir(parents=True)
    (out_dir / f"{_today()} Daily Briefing.md").write_text(
        "---\naudit_verdict: pass\n---\n# briefing\n",
        encoding="utf-8",
    )
    arg_log = tmp_path / "arg.log"
    env_log = tmp_path / "env.log"
    result = subprocess.run(
        ["/bin/bash", str(BACKSTOP_SH), "--vault-root", str(vault),
         "--dry-run", "--log-path", str(arg_log)],
        capture_output=True, text=True,
        env={**os.environ,
             "AKB2_BACKSTOP_LOG": str(env_log),
             "AKB2_BRIEFING_DEADLINE_HOUR": "0",
             "AKB2_BRIEFING_DEADLINE_MINUTE": "0"},
    )
    assert result.returncode == 0
    assert arg_log.exists() and arg_log.read_text(encoding="utf-8").strip()
    assert not env_log.exists()
