"""Tests for social briefing backstop deadline and frontmatter behavior."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import pytest

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


# ── 缺陷 A 回归：frontmatter 解析误判 + 失败归因 ────────────────────────────
#
# 生产实证（2026-07-23 ~ 07-30，logs/backstop.log）：08:00 补跑成功入库后，
# 12:05 的第二次调用把同一个 `audit_verdict: pass` 的文件记成
# 「今日文件存在但无法解析 audit_verdict('')」并进而记 FAILED。三处根因：
#   1. PUBLISHED_VERDICT 在 `$( )` 子 shell 里赋值 → 调用方永远拿到空串，
#      日志里的 `('')` 是无条件输出，不是真实解析结果；
#   2. `sed -n '/^---$/,/^---$/p'` 只认严格 `^---$`，BOM/CRLF/fence 尾随空格
#      任一出现即整段取不到 → 好文件被判「无法解析」→ 重跑已入库的当日简报；
#   3. 读盘错误被 `2>/dev/null` 吞掉后与「解析不出字段」合并成同一状态，
#      I/O / 权限故障被冒名成解析故障（违反宪法 P4 失败可见）。

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
PRODUCTION_FRONTMATTER = FIXTURE_DIR / "social_briefing_frontmatter_20260730.md"


def _write_today_briefing(vault: Path, text: str | bytes) -> Path:
    out_dir = vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{_today()} Daily Briefing.md"
    if isinstance(text, bytes):
        target.write_bytes(text)
    else:
        target.write_text(text, encoding="utf-8")
    return target


def _run_via_fixture(run_script, vault: Path, tmp_path: Path, extra_env=None, args=None):
    """走 conftest 的 run_script fixture（AKB2_TEST_HOME 模式）跑 bash 脚本。"""
    log = tmp_path / "isolated-backstop.log"
    env = {
        "AKB2_BACKSTOP_LOG": str(log),
        "AKB2_BACKSTOP_READ_RETRY_SLEEP": "0",
        "AKB2_BRIEFING_DEADLINE_HOUR": "99",
        "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        **(extra_env or {}),
    }
    result = run_script(
        "scripts/briefing_backstop.sh",
        ["--vault-root", str(vault), *(args or ["--dry-run"])],
        home=tmp_path / "home",
        env=env,
    )
    return result, log


def test_production_frontmatter_fixture_parses_as_pass(run_script, tmp_path):
    """SC1: 07-30 生产 Social 文件的 frontmatter 原样 → 必须解析出 pass 并跳过补跑。"""
    vault = tmp_path / "vault"
    _write_today_briefing(vault, PRODUCTION_FRONTMATTER.read_text(encoding="utf-8"))

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"AKB2_BRIEFING_DEADLINE_HOUR": "0"},
    )

    assert result.returncode == 0, result.stderr
    assert "audit_verdict=pass" in result.stderr
    assert "无需补跑" in result.stderr
    content = log.read_text(encoding="utf-8")
    assert "无法解析" not in content
    assert "rerun" not in content
    assert "FAILED" not in content


def test_bom_crlf_fence_whitespace_still_parses_pass(run_script, tmp_path):
    """SC2 回归: BOM + CRLF + fence 尾随空格 + 引号值 → 仍认 pass，不再误判重跑。

    旧实现（sed '/^---$/,/^---$/p'）在这三种任一变体下都取不到 frontmatter，
    把已入库的 pass 简报记成「无法解析 audit_verdict('')」并触发重跑。
    """
    vault = tmp_path / "vault"
    _write_today_briefing(
        vault,
        b'\xef\xbb\xbf--- \r\ndate: 2026-07-30\r\naudit_verdict:  "pass"  \r\n---\r\n# briefing\r\n',
    )

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"AKB2_BRIEFING_DEADLINE_HOUR": "0"},
    )

    assert result.returncode == 0, result.stderr
    assert "audit_verdict=pass" in result.stderr
    content = log.read_text(encoding="utf-8")
    assert "无法解析" not in content, f"BOM/CRLF 变体不得被判解析失败:\n{content}"
    assert "rerun" not in content
    assert "FAILED" not in content


def test_warn_verdict_is_logged_with_its_real_value(run_script, tmp_path):
    """SC2 回归: warn 必须在日志里以真实取值出现（旧实现子 shell 丢值 → 打印空）。

    强制 LC_ALL=C 复刻 launchd 的无 LANG 环境：bash 3.2 在单字节 locale 下会把
    紧跟变量的中文标点首字节并进变量名，`$VAR，` 写法会以 unbound variable 崩掉。
    """
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\naudit_verdict: warn\n---\n# briefing\n")

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"LC_ALL": "C", "LC_CTYPE": "C"},
    )
    assert "unbound variable" not in result.stderr, result.stderr

    assert "audit_verdict=warn" in result.stderr, f"verdict 取值丢失:\n{result.stderr}"
    assert "audit_verdict=，" not in result.stderr
    assert "rerun" in log.read_text(encoding="utf-8")


def test_unreadable_file_reported_as_read_failure_not_parse_failure(run_script, tmp_path):
    """SC2 回归: 文件内容合法但读盘失败 → 归因为读盘失败并带真实错误，不冒名解析失败。

    这是生产 12:05 那条日志的诚实版本：同一份文件按需读取解析得出 pass，
    说明失败发生在读盘而非解析；旧实现 2>/dev/null 把真因吞掉。
    """
    vault = tmp_path / "vault"
    target = _write_today_briefing(vault, "---\naudit_verdict: pass\n---\n# briefing\n")
    target.chmod(0o000)
    if os.access(target, os.R_OK):  # root / 特殊挂载下 chmod 000 拦不住读取
        target.chmod(0o644)
        pytest.skip("当前用户仍可读 chmod 000 文件（root?），无法构造读盘失败")
    try:
        result, log = _run_via_fixture(run_script, vault, tmp_path)
    finally:
        target.chmod(0o644)

    content = log.read_text(encoding="utf-8")
    assert "读盘失败" in content, f"读盘失败必须显式归因:\n{content}"
    assert "无法解析 audit_verdict" not in content, "读盘失败不得冒名成解析失败"
    # 真实错误必须可见（宪法 P4），不得只留一个空 err=""。
    # 断言不绑 awk 方言文案（BSD "can't open" / gawk+mawk "cannot open"），
    # 只要求真实错误里带上出问题的文件名。
    errs = re.findall(r'err="([^"]*)"', content)
    assert errs, f"必须落 err= 字段:\n{content}"
    assert all(e.strip() for e in errs), f"err= 不得为空:\n{content}"
    assert any("Daily Briefing.md" in e for e in errs), f"err= 须指名文件:\n{content}"
    # 重试次数有界且落日志（宪法 P1）
    assert "attempt=1/" in content and "attempt=3/" in content
    assert "attempt=4/" not in content
    # fail-closed 仍然保留：读不到就保守重跑
    assert "rerun" in content


def test_missing_verdict_key_is_labelled_no_verdict(run_script, tmp_path):
    """读盘成功但 frontmatter 无 audit_verdict → 独立状态 no_verdict，仍 fail-closed 重跑。"""
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\ndate: 2026-07-30\n---\n# briefing\n")

    result, log = _run_via_fixture(run_script, vault, tmp_path)

    content = log.read_text(encoding="utf-8")
    assert "status=no_verdict" in content, content
    assert "读盘失败" not in content
    assert "rerun" in content


def test_post_deadline_message_reflects_real_status(run_script, tmp_path):
    """缺陷 A 噪音: 文件已发布(warn) + 超 deadline → 不得再谎报「今日未发布」。"""
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\naudit_verdict: warn\n---\n# briefing\n")

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"AKB2_BRIEFING_DEADLINE_HOUR": "0"},
    )

    assert result.returncode == 124
    content = log.read_text(encoding="utf-8")
    assert "今日未发布" not in content, f"文件存在时不得谎报未发布:\n{content}"
    assert "status=warn_or_fail" in content
    assert "不再启动补跑" in content


def test_deadline_exit_code_is_named_and_logged(run_script, tmp_path):
    """SC3: 124 必须在日志里自证来源（本脚本 deadline 判定，非外层 timeout）。"""
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"AKB2_BRIEFING_DEADLINE_HOUR": "0"},
    )

    assert result.returncode == 124
    content = log.read_text(encoding="utf-8")
    assert "exit_code=124" in content, content
    assert "reason=deadline_missed" in content, content
    script = (PROJECT_ROOT / "scripts" / "briefing_backstop.sh").read_text(encoding="utf-8")
    assert "DEADLINE_EXIT_CODE=124" in script, "124 必须是具名常量"
    assert "VERDICT_READ_ATTEMPT_BUDGET" in script, "读盘重试预算必须是具名常量"


def test_log_lines_carry_host_for_shared_log_attribution(run_script, tmp_path):
    """logs/ 目录经 Syncthing 双机共享 → 每行必须带 host，否则两机日志混排像自相矛盾。"""
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\naudit_verdict: pass\n---\n# briefing\n")

    _result, log = _run_via_fixture(run_script, vault, tmp_path)

    for line in log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            assert "host=" in line, f"缺 host 归属: {line}"


def test_single_terminal_state_per_run(run_script, tmp_path):
    """单次 run 只允许一条终态行，且终态与 run_id 绑定。"""
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\naudit_verdict: pass\n---\n# briefing\n")

    _result, log = _run_via_fixture(run_script, vault, tmp_path)

    content = log.read_text(encoding="utf-8")
    terminals = [ln for ln in content.splitlines() if "terminal=" in ln]
    assert len(terminals) == 1, f"单 run 终态必须唯一:\n{content}"
    assert "terminal=skip" in terminals[0]
