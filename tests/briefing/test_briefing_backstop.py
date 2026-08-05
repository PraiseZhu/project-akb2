"""Tests for social briefing backstop deadline and frontmatter behavior."""

from __future__ import annotations

import fcntl
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import pytest

from tests.briefing.backstop_lock_test_support import (
    assert_child_exit_attribution_unambiguous,
    assert_descendant_cannot_pin_lock,
    assert_flock_interleaving_matrix,
    assert_no_forgeable_reentry_credential_exists,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKSTOP_SH = PROJECT_ROOT / "scripts" / "briefing_backstop.sh"
GITHUB_BACKSTOP_SH = PROJECT_ROOT / "scripts" / "github_briefing_backstop.sh"

# 本脚本自己放弃时的退出码（GIVE_UP_EXIT_CODE）。124 现在**只**属于
# run_with_deadline.py 终止 pipeline 这一种来源 —— 两者共用一码时，launchd 的
# "last exit code 124" 完全分不出是"pipeline 跑超时"还是"守卫压根没启动"。
GIVE_UP_EXIT_CODE = 125
DEADLINE_EXIT_CODE = 124

# pgrep -f 是全机器模糊匹配：同机器上任何一条含 `akb2.briefing.pipeline` 的命令行
# 都会让"主跑仍在运行"守卫命中，把用例推进 skip 分支（并行开发时实测复现）。
# 测试固定指向一个必然不会出现在真实命令行里的模式，取得确定性。
_IMPOSSIBLE_PGREP = "akb2-backstop-selftest-no-such-process-a7f3c1"


def _today() -> str:
    return date.today().isoformat()


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def _stub_pgrep_probe(tmp_path, monkeypatch):
    """Keep every test independent of the machine-global process table."""
    stub_bin = tmp_path / "hermetic-pgrep-bin"
    stub_bin.mkdir()
    _write_exec(stub_bin / "pgrep", "exit 1")
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.environ['PATH']}")


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
    # 锁与补跑入口也必须隔离：锁默认落机器本地状态根（~/Library/Application
    # Support/akb2/locks），测试不该碰真用户目录；补跑入口默认指向真
    # daily_briefing.sh，测试若意外走到那条路会真去跑 LLM 管线 —— 指向 /bin/false
    # 当绊线：真的走到了就硬失败，而不是静默跑起生产管线。
    isolated_lock = tempfile.NamedTemporaryFile(
        prefix="akb2-backstop-test-lock-", suffix=".pid", delete=False
    )
    isolated_lock.close()
    os.unlink(isolated_lock.name)
    env = {
        **os.environ,
        "AKB2_BRIEFING_DEADLINE_HOUR": "99",
        "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        "AKB2_BACKSTOP_LOG": isolated_log.name,
        "AKB2_BACKSTOP_LOCK_FILE": isolated_lock.name,
        "AKB2_BRIEFING_RERUN_SCRIPT": "/bin/false",
        "AKB2_BACKSTOP_PGREP_PATTERN": _IMPOSSIBLE_PGREP,
        **(env_overrides or {}),
    }
    return subprocess.run(args, capture_output=True, text=True, env=env)


def test_pgrep_guard_is_hermetic(tmp_path):
    """A real process matching the probe string must not steer the test harness."""
    sentinel = f"akb2-pgrep-hermetic-{os.getpid()}-{time.time_ns()}"
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)", sentinel]
    )
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(
        parents=True
    )
    try:
        result = _run_backstop(
            vault,
            ["--dry-run"],
            env_overrides={"AKB2_BACKSTOP_PGREP_PATTERN": sentinel},
        )
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sleeper.kill()
            sleeper.wait(timeout=5)

    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stderr, result.stderr
    assert "主跑仍在进行" not in result.stderr, result.stderr


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

    assert result.returncode == GIVE_UP_EXIT_CODE
    assert "deadline" in result.stderr
    assert "不再启动补跑" in result.stderr


def test_morning_deadline_compare_uses_decimal_time(tmp_path):
    """08:00 must not trip bash's octal parsing when comparing HHMM values."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exec(
        fake_bin / "date",
        f"""case "${{1:-}}" in
  +%H%M) echo 0800 ;;
  +%Y-%m-%d) echo {_today()} ;;
  *) echo "{_today()} 08:00:00" ;;
esac""",
    )
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
             "AKB2_BACKSTOP_LOCK_FILE": str(tmp_path / "lock.pid"),
             "AKB2_BRIEFING_RERUN_SCRIPT": "/bin/false",
             "AKB2_BACKSTOP_PGREP_PATTERN": _IMPOSSIBLE_PGREP,
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


def _today_briefing_path(vault: Path) -> Path:
    """当日 social 简报落点（与 briefing_backstop.sh 的 SOCIAL_FILE 同源）。"""
    return (
        vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social"
        / f"{_today()} Daily Briefing.md"
    )


def _write_today_briefing(vault: Path, text: str | bytes) -> Path:
    out_dir = vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{_today()} Daily Briefing.md"
    if isinstance(text, bytes):
        target.write_bytes(text)
    else:
        target.write_text(text, encoding="utf-8")
    return target


def _rerun_marker(tmp_path: Path) -> Path:
    """补跑 stub 的调用台账 —— 用来断言"补跑到底有没有被触发"。"""
    return tmp_path / "rerun-marker.txt"


def _rerun_calls(tmp_path: Path) -> int:
    marker = _rerun_marker(tmp_path)
    if not marker.exists():
        return 0
    return len([ln for ln in marker.read_text(encoding="utf-8").splitlines() if ln.strip()])


def _run_via_fixture(run_script, vault: Path, tmp_path: Path, extra_env=None, args=None):
    """走 conftest 的 run_script fixture（AKB2_TEST_HOME 模式）跑 bash 脚本。"""
    log = tmp_path / "isolated-backstop.log"
    # 默认补跑 stub：只记账 + 失败。任何"本不该重跑却重跑了"的回归都会在
    # _rerun_calls() 上现形，而不是悄悄去跑真 pipeline。
    stub = _write_exec(
        tmp_path / "default-rerun-stub.sh",
        f'echo "invoked" >> "{_rerun_marker(tmp_path)}"\nexit 1',
    )
    env = {
        "AKB2_BACKSTOP_LOG": str(log),
        "AKB2_BACKSTOP_READ_RETRY_SLEEP": "0",
        "AKB2_BACKSTOP_LOCK_FILE": str(tmp_path / "backstop.lock"),
        "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
        "AKB2_BRIEFING_RERUN_BACKOFF": "0",
        "AKB2_BRIEFING_DEADLINE_HOUR": "99",
        "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        "AKB2_BACKSTOP_PGREP_PATTERN": _IMPOSSIBLE_PGREP,
        **(extra_env or {}),
    }
    # `args or [...]` 会把显式传入的 `[]`（= 我要跑真链路，不要 --dry-run）当成"没传"
    # 而塞回 --dry-run —— 用 `is None` 区分"没传"与"传了空"。
    extra = ["--dry-run"] if args is None else args
    result = run_script(
        "scripts/briefing_backstop.sh",
        ["--vault-root", str(vault), *extra],
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


def test_missing_verdict_key_is_labelled_no_verdict(run_script, tmp_path):
    """读盘成功但 frontmatter 无 audit_verdict → 独立状态 no_verdict，仍 fail-closed 重跑。"""
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\ndate: 2026-07-30\n---\n# briefing\n")

    _result, log = _run_via_fixture(run_script, vault, tmp_path)

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

    assert result.returncode == GIVE_UP_EXIT_CODE
    content = log.read_text(encoding="utf-8")
    assert "今日未发布" not in content, f"文件存在时不得谎报未发布:\n{content}"
    assert "status=warn_or_fail" in content
    assert "不再启动补跑" in content


def test_deadline_exit_code_is_named_and_logged(run_script, tmp_path):
    """SC3: 放弃码必须在日志里自证来源（本脚本 deadline 判定，非外层 timeout）。"""
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"AKB2_BRIEFING_DEADLINE_HOUR": "0"},
    )

    assert result.returncode == GIVE_UP_EXIT_CODE
    content = log.read_text(encoding="utf-8")
    assert f"exit_code={GIVE_UP_EXIT_CODE}" in content, content
    assert "reason=deadline_missed" in content, content
    script = (PROJECT_ROOT / "scripts" / "briefing_backstop.sh").read_text(encoding="utf-8")
    assert "DEADLINE_EXIT_CODE=124" in script, "124 必须是具名常量"
    assert "GIVE_UP_EXIT_CODE=125" in script, "主动放弃码必须是具名常量"
    assert "VERDICT_READ_ATTEMPT_BUDGET" in script, "读盘重试预算必须是具名常量"
    assert "RERUN_ATTEMPT_BUDGET" in script, "补跑重试预算必须是具名常量"


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


# ── L3-1/L3-2 回归：判据单点 + 终态过度断言 ─────────────────────────────────
#
# 根因（2026-08-02 排查）：`/Volumes/AKB2` 卷级**瞬时 open() 失败** + **判据单点**
# （只有一条 awk 读者，无交叉验证）。同一个根因两种表现：
#   08:00 → 判 unreadable → fail-closed 重跑 → **双跑**（38 天）
#   12:05 → 判 unreadable → 过 deadline → **假 FAILED**（38/40 = 95% 假阳性）
#
# 已排除的错误假设：不是路径含空格未加引号（引号本来就对，且在 launchd 精确环境
# 实测通过）；`source line number 15` 是 one-true-awk 在任何 FATAL 时无条件打印的
# curnode->lineno，与失败原因无关；报错里没有 `input record number` ⇒ NR==0 ⇒
# open() 在读到第一条记录前就失败 ⇒ 纯 open 失败。errno 不可知（awk 不带
# strerror），所以修法必须对 errno 不可知保持鲁棒。
#
# 为什么读者要注入而不是 chmod 000：生产卷是 **noowners 挂载**，chmod 拦不住读取
# → 旧用例在真机等价环境下 `pytest.skip`，在最需要它的环境里恰好不生效。


def _failing_reader(tmp_path: Path, fail_times: int, *, delete_target: bool = False) -> Path:
    """构造注入式主读者 stub：前 fail_times 次返回非 0，之后真读文件。

    失败文案刻意复刻 one-true-awk 的真实输出（不带 strerror 的 can't open file
    + 无条件打印的 source line number），这样"err= 里只有 awk 那一句"这个缺陷
    形状能被真实复现。
    """
    counter = tmp_path / "reader-attempts.txt"
    # delete_target 复刻真实 TOCTOU：`[[ -f ]]` 通过之后文件被删/换掉，
    # 于是回退读者也拿不到 —— 这是"两个读者都失败"唯一不依赖挂载语义的构造。
    drop = 'rm -f "$1"' if delete_target else ":"
    body = f"""
n=$(cat "{counter}" 2>/dev/null || echo 0)
n=$(( n + 1 ))
echo "$n" > "{counter}"
if [ "$n" -le {fail_times} ]; then
  {drop}
  printf '%s\\n' "awk: can't open file $1" >&2
  printf '%s\\n' " source line number 15" >&2
  exit 2
fi
sed -n 's/^[[:space:]]*audit_verdict[[:space:]]*:[[:space:]]*//p' "$1" | head -1
exit 0
"""
    return _write_exec(tmp_path / "reader-stub.sh", body.strip())


def test_native_fallback_reader_rescues_pass_verdict(run_script, tmp_path):
    """SC-1（本 lane 核心）: awk 主读者恒失败 + 纯 bash 回退读者读出 pass。

    必须同时成立：终态 skip、日志记 read_path=native_fallback、**日志不含
    terminal=failed**、**补跑一次都没被触发**。这三条合起来正是"把 social 主跑装
    回 07:30 后，08:00 的 backstop 会不会再双跑"的判据。
    """
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\naudit_verdict: pass\n---\n# briefing\n")
    reader = _failing_reader(tmp_path, fail_times=99)

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"AKB2_BACKSTOP_READER": str(reader)},
        args=[],  # 非 dry-run：真走到"要不要补跑"那一步才有意义
    )

    content = log.read_text(encoding="utf-8")
    assert result.returncode == 0, f"回退读者读到 pass 应正常 skip:\n{content}"
    assert "read_path=native_fallback" in content, content
    assert "terminal=skip" in content, content
    assert "terminal=failed" not in content, f"回退成功后不得报 failed:\n{content}"
    assert "terminal=indeterminate" not in content, content
    assert _rerun_calls(tmp_path) == 0, "回退读者已确认 pass，绝不能触发补跑"


def test_spacey_path_is_read_by_primary_awk_without_fallback(run_script, tmp_path):
    """SC-2（不回归基线）: 含空格的真实落点路径下，awk 主读者自己就该成功。

    路径形如 `<tmp>/vault/Praise Space/AKB2.0/AI TRENDS/Social/<today> Daily
    Briefing.md`（三处空格）。断言 read_path=awk_primary 而不只是"结果对"——
    后者在回退读者兜住时也会通过，测不出主读者已经坏了。
    """
    vault = tmp_path / "vault"
    target = _write_today_briefing(vault, "---\naudit_verdict: pass\n---\n# briefing\n")
    # 自证 fixture 真的含空格（否则这条基线在测一个不含空格的路径，等于没测）
    assert " " in target.name, target.name
    spacey_dirs = [p for p in target.parts if " " in p and p != target.name]
    assert len(spacey_dirs) >= 2, f"路径里应有多段含空格目录: {spacey_dirs}"

    _result, log = _run_via_fixture(run_script, vault, tmp_path)

    content = log.read_text(encoding="utf-8")
    assert "read_path=awk_primary" in content, f"含空格路径应由主读者直接读到:\n{content}"
    assert "can't open file" not in content, content
    assert "cannot open" not in content, content
    assert "audit_verdict=pass" in content, content
    assert "read_path=native_fallback" not in content, "主读者能读时不该走回退"


def test_transient_read_failure_recovers_within_budget(run_script, tmp_path):
    """SC-10: 瞬时读失败在重试预算内恢复 —— 断言"救回了什么"，不只是"重试了几次"。

    旧用例只断言 `attempt=1/` 与 `attempt=3/` 都出现（测了"有 3 次"），没测这 3 次
    能把结果从什么变成什么 —— 宪法「测试须绑可观测行为」在那里失守。
    这里 stub 前 2 次失败、第 3 次真读出 pass：终态必须是 skip、不得重跑、
    且**不需要**动用回退读者（证明主读者的重试窗口本身有效）。
    """
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\naudit_verdict: pass\n---\n# briefing\n")
    reader = _failing_reader(tmp_path, fail_times=2)

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"AKB2_BACKSTOP_READER": str(reader)},
        args=[],
    )

    content = log.read_text(encoding="utf-8")
    assert result.returncode == 0, content
    assert "attempt=1/" in content and "attempt=2/" in content, content
    assert "terminal=skip" in content, f"重试内恢复必须回到 skip:\n{content}"
    assert "audit_verdict=pass" in content, content
    assert "read_path=native_fallback" not in content, "主读者已在预算内恢复，不该走回退"
    assert _rerun_calls(tmp_path) == 0, "读到 pass 就不能补跑"


def test_read_attempt_budget_is_bounded(run_script, tmp_path):
    """SC-10 有界: 主读者尝试次数严格等于 VERDICT_READ_ATTEMPT_BUDGET，不多不少。"""
    script = (PROJECT_ROOT / "scripts" / "briefing_backstop.sh").read_text(encoding="utf-8")
    budget = int(
        re.search(r"^readonly VERDICT_READ_ATTEMPT_BUDGET=(\d+)$", script, re.M).group(1)
    )
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\naudit_verdict: pass\n---\n# briefing\n")
    reader = _failing_reader(tmp_path, fail_times=99)

    _result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"AKB2_BACKSTOP_READER": str(reader)},
    )

    content = log.read_text(encoding="utf-8")
    for i in range(1, budget + 1):
        assert f"attempt={i}/{budget}" in content, f"缺 attempt={i}/{budget}:\n{content}"
    assert f"attempt={budget + 1}/" not in content, f"超出预算:\n{content}"
    attempts_made = int((tmp_path / "reader-attempts.txt").read_text().strip())
    assert attempts_made == budget, f"主读者实际调用 {attempts_made} 次，预算 {budget}"


def test_both_readers_failing_exposes_non_awk_errno(run_script, tmp_path):
    """SC-3: 双读者都失败时，err= 必须带**非 awk 来源**的 errno 证据。

    one-true-awk 的 `can't open file` 不带 strerror —— 只靠它永远分不出 ENOENT /
    EIO / EACCES，宪法 P4「失败可见」在此失效。修法：awk 全轮失败后追加 `cat` 探针
    落 cat_rc=，再让纯 bash 读者独立试一次（bash 的重定向失败带 strerror）。
    构造用的是真实 TOCTOU 形状：`[[ -f ]]` 通过之后文件被换掉/删掉。
    """
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\naudit_verdict: pass\n---\n# briefing\n")
    reader = _failing_reader(tmp_path, fail_times=99, delete_target=True)

    _result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"AKB2_BACKSTOP_READER": str(reader)},
    )

    content = log.read_text(encoding="utf-8")
    assert "status=unreadable" in content, f"双读者都失败才算 unreadable:\n{content}"
    assert "read_path=both_failed" in content, content
    errs = re.findall(r'err="([^"]*)"', content)
    assert errs, f"必须落 err= 字段:\n{content}"
    assert all(e.strip() for e in errs), f"err= 不得为空:\n{content}"
    # 关键：err= 不能只有 awk 那一句。至少一条非 awk 来源的 errno 证据。
    final_err = errs[-1]
    non_awk_evidence = ("cat_rc=", "No such file", "Input/output error", "Permission denied")
    assert any(tok in final_err for tok in non_awk_evidence), (
        f"err= 里只有 awk 那句不带 strerror 的话，errno 依旧不可见:\n{final_err}"
    )
    assert "cat_rc=" in final_err, f"cat 探针的真实退出码必须落日志:\n{final_err}"
    # fail-closed 仍然保留：读不到就保守重跑（should_rerun 不变，改的只是终态）
    assert "rerun" in content


def test_unreadable_pass_file_must_not_terminal_failed(run_script, tmp_path):
    """SC-4 + SC-10: unreadable + 过 deadline → terminal=indeterminate，**绝不是 failed**。

    这正是 38 天假 FAILED 的形状，此前零测试守卫（旧用例只断言日志里有"读盘失败"
    四个字，完全不检查终态）。failed 断言的是「当天没有简报」，而 unreadable 恰恰
    不能支持这个断言 —— 假阳性降到 0 之前不能接告警通道，否则必然告警疲劳。
    """
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\naudit_verdict: pass\n---\n# briefing\n")
    reader = _failing_reader(tmp_path, fail_times=99, delete_target=True)

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={
            "AKB2_BACKSTOP_READER": str(reader),
            "AKB2_BRIEFING_DEADLINE_HOUR": "0",
        },
    )

    content = log.read_text(encoding="utf-8")
    assert result.returncode == GIVE_UP_EXIT_CODE, content
    assert "terminal=indeterminate" in content, f"无法判定必须记 indeterminate:\n{content}"
    assert "terminal=failed" not in content, f"读盘失败不得断言「当天没有简报」:\n{content}"
    assert "INDETERMINATE" in content
    assert "需人工" in content, "indeterminate 必须点明需要人工核实"
    terminals = [ln for ln in content.splitlines() if "terminal=" in ln]
    assert len(terminals) == 1, f"终态仍必须唯一:\n{content}"


def test_missing_after_deadline_still_terminal_failed(run_script, tmp_path):
    """SC-4 反面: status=missing + 过 deadline → 仍然 terminal=failed（真的没交付）。

    与上一个用例成对：两种终态必须**可区分**，否则解耦等于没做。
    """
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"AKB2_BRIEFING_DEADLINE_HOUR": "0"},
    )

    content = log.read_text(encoding="utf-8")
    assert result.returncode == GIVE_UP_EXIT_CODE, content
    assert "terminal=failed" in content, content
    assert "terminal=indeterminate" not in content, content
    assert "status=missing" in content, content


# ── L3-3 回归：重试维度是模型族，不是次数 ───────────────────────────────────
#
# 2026-08-02 实证反证：pipeline 的 synthesis 内部已有 3 次重试（`DeepSeek API
# failed after 3 retries`），主跑 05:59 用掉 3 次、backstop 07:30 又 3 次 ——
# **共 6 次同族尝试全 429**。在同族上加第 7/8/9 次只会多烧额度、把运行时间推向
# deadline，反而降低补跑成功率。所以预算 = 2，且第 2 轮必须换族。


def _counting_rerun_stub(tmp_path: Path, succeed_from: int, target: Path | None = None) -> Path:
    """补跑 stub：第 succeed_from 轮起成功（并可写出目标文件），每轮记录收到的 env。"""
    counter = tmp_path / "rerun-attempts.txt"
    env_log = tmp_path / "rerun-env.txt"
    marker = _rerun_marker(tmp_path)
    write_target = ":"
    if target is not None:
        write_target = (
            f'mkdir -p "{target.parent}"\n'
            f'printf -- "---\\naudit_verdict: pass\\n---\\n" > "{target}"'
        )
    body = f"""
n=$(cat "{counter}" 2>/dev/null || echo 0)
n=$(( n + 1 ))
echo "$n" > "{counter}"
echo "attempt=$n AKB2_LLM_FAMILY_FALLBACK=${{AKB2_LLM_FAMILY_FALLBACK:-<unset>}}" >> "{env_log}"
echo "invoked" >> "{marker}"
if [ "$n" -lt {succeed_from} ]; then
  exit 1
fi
{write_target}
exit 0
"""
    return _write_exec(tmp_path / "counting-rerun-stub.sh", body.strip())


def test_rerun_second_attempt_switches_model_family(run_script, tmp_path):
    """SC-5: 第 1 轮失败 → 第 2 轮成功，且第 2 轮的环境里 AKB2_LLM_FAMILY_FALLBACK=1。

    断言绑可观测行为：不是"循环跑了两次"，而是"第二次换了模型族"（COMMON.md 契约
    1 的生产端）。同族已被证明连续 6 次 429，重试维度选错等于白烧额度。
    """
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    target = vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social" / f"{_today()} Daily Briefing.md"
    stub = _counting_rerun_stub(tmp_path, succeed_from=2, target=target)

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_ATTEMPTS": "2",
            "AKB2_BRIEFING_RERUN_BACKOFF": "0",
        },
        args=[],
    )

    content = log.read_text(encoding="utf-8")
    assert result.returncode == 0, content
    assert "terminal=recovered" in content, content
    assert "attempt=1/2" in content and "attempt=2/2" in content, content
    assert "attempt=3/2" not in content, f"预算之外的轮次不得出现:\n{content}"

    env_lines = (tmp_path / "rerun-env.txt").read_text(encoding="utf-8").splitlines()
    assert len(env_lines) == 2, f"应恰好补跑 2 轮:\n{env_lines}"
    assert "AKB2_LLM_FAMILY_FALLBACK=0" in env_lines[0], f"第 1 轮不该换族: {env_lines[0]}"
    assert "AKB2_LLM_FAMILY_FALLBACK=1" in env_lines[1], f"第 2 轮必须换族: {env_lines[1]}"


def test_rerun_loop_stops_at_deadline_mid_flight(run_script, tmp_path):
    """SC-7: 重试循环受 deadline 约束 —— 预算给到 9 也不能越过 deadline。

    本条专锁**循环入口**那道 `deadline_missed`（睡前投影那道由
    test_backoff_gives_up_instead_of_sleeping_past_deadline 单独锁）。

    假 date 的调用计数必须和脚本里 `date +%H%M` 的实际次序对齐，改动脚本时要一起看：
      #1 pre-loop deadline_missed        → 08:00 放行
      #2 循环第 1 轮 deadline_missed      → 08:00 放行 → 真跑 stub（失败）
      #3 deadline_missed_after(退避 0s)   → 08:00 余量充足 → 不放弃、睡 0s
      #4 循环第 2 轮 deadline_missed      → 23:00 已过界 → give_up
    所以阈值是 `-le 3`。退出码是 GIVE_UP_EXIT_CODE 而非 124（124 只属 run_with_deadline）。
    """
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    stub = _counting_rerun_stub(tmp_path, succeed_from=99)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    counter = tmp_path / "date-hm-calls.txt"
    _write_exec(
        fake_bin / "date",
        f"""case "${{1:-}}" in
  +%H%M)
    n=$(cat "{counter}" 2>/dev/null || echo 0)
    n=$(( n + 1 ))
    echo "$n" > "{counter}"
    if [ "$n" -le 3 ]; then echo 0800; else echo 2300; fi
    ;;
  +%Y-%m-%d) echo {_today()} ;;
  *) echo "{_today()} 08:00:00" ;;
esac""",
    )

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_ATTEMPTS": "9",
            "AKB2_BRIEFING_RERUN_BACKOFF": "0",
            "AKB2_BRIEFING_DEADLINE_HOUR": "12",
        },
        args=[],
    )

    content = log.read_text(encoding="utf-8")
    assert result.returncode == GIVE_UP_EXIT_CODE, f"主动放弃必须是 125，实际 {result.returncode}:\n{content}"
    assert result.returncode != DEADLINE_EXIT_CODE, "124 只属 run_with_deadline 终止 pipeline"
    assert "attempt=1/9" in content, content
    assert "attempt=3/9" not in content, f"deadline 后不得继续重试:\n{content}"
    assert "terminal=give_up" in content, content
    assert "reason=deadline_missed" in content, content
    assert _rerun_calls(tmp_path) == 1, "只应真正补跑 1 轮就被 deadline 拦下"


# ── L3-4 回归：无锁 → 装回 social 主跑就引爆双跑 ─────────────────────────────
#
# pgrep 结构性救不了：social 主跑 07:30、backstop 08:00，间隔 30 分钟 > 主跑实际
# 耗时（历史 8–16 分钟），08:00 时 pgrep **必然扑空**。这正是 38 天双跑的成型条件。


def test_concurrent_backstop_blocked_by_flock(tmp_path):
    """SC-8: 第二个 backstop 必须被 flock 挡住，补跑 stub 只被调用 1 次。"""
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    lock = tmp_path / "shared-backstop.lock"
    marker = tmp_path / "pipeline-marker.txt"
    release = tmp_path / "release"
    target = _today_briefing_path(vault)
    # 持锁者：记账 → 等 release（有界，最多 20s，模拟一次真在跑的补跑）→ **写出达 pass
    # 的产物**再 exit 0。写产物是必需的：recovered 现在必须由产物状态证明
    # （假 recovered 闸），只 exit 0 不写产物会被判 false_recovery 并再补跑一轮，
    # 那样 stub 会被调用 2 次，把本用例真正要测的「锁挡住了第二个进程」搅浑。
    holder_stub = _write_exec(
        tmp_path / "holder-stub.sh",
        f'''echo "invoked" >> "{marker}"
i=0
while [ ! -f "{release}" ] && [ "$i" -lt 100 ]; do sleep 0.2; i=$(( i + 1 )); done
mkdir -p "{target.parent}"
printf -- "---\\naudit_verdict: pass\\n---\\n" > "{target}"
exit 0''',
    )
    second_stub = _write_exec(
        tmp_path / "second-stub.sh",
        f'echo "invoked" >> "{marker}"\nexit 0',
    )

    common_env = {
        **os.environ,
        "AKB2_BRIEFING_DEADLINE_HOUR": "99",
        "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        "AKB2_BACKSTOP_LOCK_FILE": str(lock),
        "AKB2_BRIEFING_RERUN_BACKOFF": "0",
        "AKB2_BACKSTOP_PGREP_PATTERN": _IMPOSSIBLE_PGREP,
    }
    first = subprocess.Popen(
        ["/bin/bash", str(BACKSTOP_SH), "--vault-root", str(vault)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**common_env,
             "AKB2_BACKSTOP_LOG": str(tmp_path / "first.log"),
             "AKB2_BRIEFING_RERUN_SCRIPT": str(holder_stub)},
    )
    try:
        # 等第一个进程真正拿到锁并进入补跑
        deadline = time.time() + 20
        while time.time() < deadline:
            if lock.exists() and marker.exists():
                break
            time.sleep(0.1)
        assert lock.exists(), "第一个 backstop 应已建立锁文件"
        assert lock.exists(), "flock 的共享 inode 必须在持锁期间存在"

        second = subprocess.run(
            ["/bin/bash", str(BACKSTOP_SH), "--vault-root", str(vault)],
            capture_output=True, text=True,
            env={**common_env,
                 "AKB2_BACKSTOP_LOG": str(tmp_path / "second.log"),
                 "AKB2_BRIEFING_RERUN_SCRIPT": str(second_stub)},
        )
    finally:
        release.write_text("go", encoding="utf-8")
        try:
            first.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            first.kill()
            first.communicate()

    second_log = (tmp_path / "second.log").read_text(encoding="utf-8")
    assert second.returncode == 0, f"被锁挡住是正常早退，不是失败:\n{second_log}"
    assert "already-running" in second_log, f"必须明示被谁挡住:\n{second_log}"
    assert "terminal=skip" in second_log, second_log
    calls = len([ln for ln in marker.read_text(encoding="utf-8").splitlines() if ln.strip()])
    assert calls == 1, f"补跑只应被调用 1 次（双跑防线），实际 {calls} 次"


def test_stale_lock_is_reclaimed(tmp_path):
    """SC-8 配套: 残留 inode/旧 PID 内容不能阻塞没有内核 owner 的后继者。"""
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    lock = tmp_path / "stale.lock"
    # 保留旧协议形状的内容；flock 不读取它，唯一权威是内核是否仍有 owner。
    dead = subprocess.Popen(["/bin/bash", "-c", "exit 0"])
    dead.wait()
    lock.write_text(f"{dead.pid}\n", encoding="utf-8")
    marker = tmp_path / "marker.txt"
    target = _today_briefing_path(vault)
    # stub 必须写出达 pass 的产物：recovered 由产物状态证明（假 recovered 闸），
    # 只 exit 0 不写产物会被判 false_recovery —— 那是另一条用例的主题。
    stub = _write_exec(
        tmp_path / "stub.sh",
        f'echo invoked >> "{marker}"\n'
        f'mkdir -p "{target.parent}"\n'
        f'printf -- "---\\naudit_verdict: pass\\n---\\n" > "{target}"\n'
        "exit 0",
    )

    result = _run_backstop(
        vault,
        env_overrides={
            "AKB2_BACKSTOP_LOCK_FILE": str(lock),
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_BACKOFF": "0",
        },
    )

    assert result.returncode == 0, result.stderr
    assert marker.exists(), f"陈旧锁应被接管并正常补跑:\n{result.stderr}"
    assert "already-running" not in result.stderr


def _run_atomic_lock_barrier_case(tmp_path: Path, kind: str) -> None:
    case_dir = tmp_path / kind
    case_dir.mkdir()
    vault = case_dir / "vault"
    lock = case_dir / "shared.lock"
    marker = case_dir / "rerun-marker.txt"
    release = case_dir / "release"
    barrier = case_dir / "barrier"
    if kind == "social":
        script = BACKSTOP_SH
        target = (
            vault
            / "Praise Space"
            / "AKB2.0"
            / "AI TRENDS"
            / "Social"
            / f"{_today()} Daily Briefing.md"
        )
        script_env = {
            "AKB2_BACKSTOP_LOCK_FILE": str(lock),
            "AKB2_BRIEFING_RERUN_ATTEMPTS": "1",
            "AKB2_BRIEFING_RERUN_BACKOFF": "0",
            "AKB2_BRIEFING_DEADLINE_HOUR": "99",
            "AKB2_BACKSTOP_PGREP_PATTERN": _IMPOSSIBLE_PGREP,
        }
        artifact = "---\\naudit_verdict: pass\\n---\\n"
    else:
        script = GITHUB_BACKSTOP_SH
        target = (
            vault
            / "Praise Space"
            / "AKB2.0"
            / "AI TRENDS"
            / "GitHub"
            / f"{_today()} GitHub Briefing.md"
        )
        script_env = {
            "AKB2_GITHUB_BACKSTOP_LOCK_FILE": str(lock),
            "AKB2_GITHUB_RERUN_ATTEMPTS": "1",
            "AKB2_GITHUB_RERUN_BACKOFF": "0",
            "AKB2_GITHUB_DEADLINE_HOUR": "99",
            "AKB2_GITHUB_ENV_FILE": "/dev/null",
            "AKB2_GITHUB_BACKSTOP_PGREP_PATTERN": _IMPOSSIBLE_PGREP,
        }
        artifact = "# GitHub briefing\\n"
    target.parent.mkdir(parents=True)
    holder = _write_exec(
        case_dir / "holder.sh",
        f'''echo invoked >> "{marker}"
i=0
while [ ! -f "{release}" ] && [ "$i" -lt 500 ]; do
  sleep 0.01
  i=$(( i + 1 ))
done
printf -- "{artifact}" > "{target}"
exit 0''',
    )
    barrier_runner = _write_exec(
        case_dir / "barrier-runner.sh",
        'while [ ! -f "$1" ]; do sleep 0.01; done\nshift\nexec "$@"',
    )

    process_count = 24
    processes = []
    for index in range(process_count):
        log_key = (
            "AKB2_BACKSTOP_LOG"
            if kind == "social"
            else "AKB2_GITHUB_BACKSTOP_LOG"
        )
        rerun_key = (
            "AKB2_BRIEFING_RERUN_SCRIPT"
            if kind == "social"
            else "AKB2_GITHUB_RERUN_SCRIPT"
        )
        env = {
            **os.environ,
            **script_env,
            log_key: str(case_dir / f"backstop-{index}.log"),
            rerun_key: str(holder),
        }
        processes.append(
            subprocess.Popen(
                [
                    str(barrier_runner),
                    str(barrier),
                    "/bin/bash",
                    str(script),
                    "--vault-root",
                    str(vault),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
        )

    try:
        barrier.write_text("go", encoding="utf-8")
        deadline = time.time() + 10
        while time.time() < deadline and not marker.exists():
            time.sleep(0.02)
        assert marker.exists(), f"{kind}: no process entered the rerun"
        deadline = time.time() + 3
        while time.time() < deadline:
            completed = sum(proc.poll() is not None for proc in processes)
            if completed >= process_count - 1:
                break
            time.sleep(0.02)
        assert lock.exists()
        release.write_text("go", encoding="utf-8")
        results = [proc.communicate(timeout=15) for proc in processes]
    finally:
        release.touch(exist_ok=True)
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    calls = marker.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1, f"{kind}: {len(calls)} processes entered the paid rerun"
    assert all(proc.returncode == 0 for proc in processes), results


def test_lock_acquire_is_atomic_under_barrier_start(tmp_path):
    """fcntl.flock acquisition permits one rerun owner under a true barrier start."""
    # R6 起只做行为断言：不再检查任何锁实现细节（helper 文件名断言已随 helper 一起删除）。
    for kind in ("social", "github"):
        _run_atomic_lock_barrier_case(tmp_path, kind)


def test_stale_takeover_interleaving_matrix(tmp_path):
    """Flock has no rename takeover: stale inode, N-way start, and broken python are safe."""
    assert_flock_interleaving_matrix(tmp_path, "social", PROJECT_ROOT)


def test_no_forgeable_reentry_credential_exists(tmp_path):
    """SC-R6-01: no env forgery (bool / fd / interpreter knob) can bypass the flock."""
    assert_no_forgeable_reentry_credential_exists(tmp_path, "social", PROJECT_ROOT)


def test_child_exit_code_attribution_unambiguous(tmp_path):
    """SC-R6-02: child rc passes through; no protocol codes or stderr framing exist."""
    assert_child_exit_attribution_unambiguous(tmp_path, "social", PROJECT_ROOT)


def test_descendant_cannot_pin_lock(tmp_path):
    """SC-R6-03: a descendant outliving the backstop never inherits the lock fd."""
    assert_descendant_cannot_pin_lock(tmp_path, "social", PROJECT_ROOT)


def test_artifact_is_rechecked_after_lock_acquisition(tmp_path):
    """An artifact published after the first guard must cancel the paid rerun."""
    for kind in ("social", "github"):
        case_dir = tmp_path / f"post-lock-{kind}"
        case_dir.mkdir()
        vault = case_dir / "vault"
        if kind == "social":
            script = BACKSTOP_SH
            rerun = case_dir / "rerun.sh"
            target = (
                vault
                / "Praise Space"
                / "AKB2.0"
                / "AI TRENDS"
                / "Social"
                / f"{_today()} Daily Briefing.md"
            )
            env = {
                "AKB2_BACKSTOP_LOG": str(case_dir / "backstop.log"),
                "AKB2_BACKSTOP_LOCK_FILE": str(case_dir / "backstop.lock"),
                "AKB2_BRIEFING_RERUN_SCRIPT": str(rerun),
                "AKB2_BRIEFING_RERUN_ATTEMPTS": "1",
                "AKB2_BRIEFING_RERUN_BACKOFF": "0",
                "AKB2_BRIEFING_DEADLINE_HOUR": "99",
            }
            artifact = "---\\naudit_verdict: pass\\n---\\n"
        else:
            script = GITHUB_BACKSTOP_SH
            rerun = case_dir / "rerun.sh"
            target = (
                vault
                / "Praise Space"
                / "AKB2.0"
                / "AI TRENDS"
                / "GitHub"
                / f"{_today()} GitHub Briefing.md"
            )
            env = {
                "AKB2_GITHUB_BACKSTOP_LOG": str(case_dir / "backstop.log"),
                "AKB2_GITHUB_BACKSTOP_LOCK_FILE": str(case_dir / "backstop.lock"),
                "AKB2_GITHUB_RERUN_SCRIPT": str(rerun),
                "AKB2_GITHUB_RERUN_ATTEMPTS": "1",
                "AKB2_GITHUB_DEADLINE_HOUR": "99",
                "AKB2_GITHUB_ENV_FILE": "/dev/null",
            }
            artifact = "# GitHub briefing\\n"
        target.parent.mkdir(parents=True)
        marker = case_dir / "paid-rerun.txt"
        _write_exec(rerun, f'echo invoked > "{marker}"\nexit 1')
        probe_bin = case_dir / "probe-bin"
        probe_bin.mkdir()
        _write_exec(
            probe_bin / "pgrep",
            f'printf -- "{artifact}" > "{target}"\nexit 1',
        )
        result = subprocess.run(
            ["/bin/bash", str(script), "--vault-root", str(vault)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                **env,
                "PATH": f"{probe_bin}{os.pathsep}{os.environ['PATH']}",
            },
        )
        assert result.returncode == 0, result.stderr
        assert not marker.exists(), f"{kind}: artifact recheck did not cancel rerun"
        assert "lock_artifact_recheck=pass" in result.stderr, result.stderr


# ── 缺陷 D 回归：BSD-only 语法在 CI(ubuntu) 上炸 ──────────────────────────────
#
# 生产实证（PR #65 合并后 CI tests job 红）：`mktemp -t akb2-backstop-read` 是
# BSD/macOS 语法（-t 后跟前缀，自动补随机后缀）；GNU mktemp 把它当模板，缺
# XXXXXX 直接 rc=1「mktemp: too few X's in template」→ err_file 取不到 →
# set -e 掀掉整个 resolve_published_state → tests/briefing 连带失败。
# 本机 macOS 全绿测不出，必须靠静态守卫锁死这一类。
#
# 守卫已上移到 `tests/test_shell_portability.py`，扫 `scripts/*.sh` 全量（本处
# 两个 backstop 脚本仍在覆盖范围内，且那里有非空过断言钉住这一点）。原先只
# parametrize 两个脚本的版本抓不到别处的同类问题 —— 实际漏掉了
# `akb-pre-commit.sh` 的 `stat -f %z`。不在此处保留平行副本：两套同职责守卫
# 比一套差（宪法「convention beats novelty」同理）。


def test_no_literal_attempt_one_of_one():
    """SC-6: `attempt=1/1` 这个字面量必须彻底消失（它把"无循环"伪装成"有轮次"）。"""
    for name in ("briefing_backstop.sh", "github_briefing_backstop.sh"):
        text = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "attempt=1/1" not in text, f"{name} 仍有 attempt=1/1 字面量"


def test_give_up_and_deadline_exit_codes_are_distinct():
    """SC-9 静态: 两个退出码都是具名常量且取值不同（launchd 层必须可分辨）。"""
    for name in ("briefing_backstop.sh", "github_briefing_backstop.sh"):
        text = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
        deadline = re.search(r"^readonly DEADLINE_EXIT_CODE=(\d+)$", text, re.M)
        give_up = re.search(r"^readonly GIVE_UP_EXIT_CODE=(\d+)$", text, re.M)
        assert deadline, f"{name} 缺具名 DEADLINE_EXIT_CODE"
        assert give_up, f"{name} 缺具名 GIVE_UP_EXIT_CODE"
        assert deadline.group(1) != give_up.group(1), (
            f"{name} 的两个退出码同值 —— launchd 的 'last exit code' 又不可分辨了"
        )
        assert deadline.group(1) == "124", f"{name} 的 deadline 码须与 run_with_deadline 对齐"


def test_deadline_exit_code_still_propagated_from_rerun(run_script, tmp_path):
    """SC-9 运行时: 补跑子进程返回 124（run_with_deadline 终止）时，backstop 照传 124。

    与"backstop 主动放弃 → 125"成对：两种 124/125 的来源必须能分开看。
    """
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    stub = _write_exec(
        tmp_path / "deadline-killed-stub.sh",
        f'echo invoked >> "{_rerun_marker(tmp_path)}"\nexit 124',
    )

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_ATTEMPTS": "1",
        },
        args=[],
    )

    content = log.read_text(encoding="utf-8")
    assert result.returncode == DEADLINE_EXIT_CODE, f"应原样传出 124:\n{content}"
    assert "run_with_deadline" in content, content
    assert f"exit_code={DEADLINE_EXIT_CODE}" in content, content


# ── 双读者判据一致性（review 补漏）─────────────────────────────────────────
#
# 回退读者的价值前提是「换观测者，不换结论」。如果哪天有人给 awk 主读者加了一种新的
# 格式容忍（比如支持 `audit_verdict = pass` 这种带空格的等号写法）却忘了同步纯 bash
# 读者，两个读者就会在同一份文件上给出**不同的 verdict** —— 那比没有回退更危险：
# 回退会在主读者失败时悄悄把 pass 读成 no_verdict，反过来触发一次不该有的重跑。
# 所以这条一致性必须有常驻守卫，而不是只在实现当天手工比对过一次。

_READER_PARITY_CASES: dict[str, bytes] = {
    "plain": b"---\naudit_verdict: pass\n---\n# b\n",
    "bom_crlf_quote_ws": (
        b'\xef\xbb\xbf--- \r\ndate: 2026-07-30\r\naudit_verdict:  "pass"  \r\n---\r\n# b\r\n'
    ),
    "warn": b"---\naudit_verdict: warn\n---\n",
    "no_verdict_key": b"---\ndate: 2026-07-30\n---\n# b\n",
    "no_frontmatter": b"# just a heading\naudit_verdict: pass\n",
    "trailing_yaml_comment": b"---\naudit_verdict: pass   # gate ok\n---\n",
    "single_quoted": b"---\naudit_verdict: 'fail'\n---\n",
    "dots_close_fence": b"---\naudit_verdict: pass\n...\n",
    "leading_blank_lines": b"\n\n---\naudit_verdict: pass\n---\n",
    "key_outside_frontmatter": b"---\ndate: x\n---\naudit_verdict: pass\n",
    "no_trailing_newline": b"---\naudit_verdict: pass\n---",
    "tab_indent": b"---\n audit_verdict:\tpass\n---\n",
    "unknown_value": b"---\naudit_verdict: weird\n---\n",
    "empty_file": b"",
}


def _reader_harness() -> str:
    """把脚本里的两个读者函数原样抽出来，单独可跑（不执行脚本主流程）。"""
    src = BACKSTOP_SH.read_text(encoding="utf-8")

    def grab(header: str) -> str:
        start = src.index("\n" + header) + 1
        end = src.index("\n}\n", start) + len("\n}\n")
        return src[start:end]

    awk_fn = grab("read_frontmatter_verdict() {")
    native_fn = grab("read_frontmatter_verdict_native() {")
    res = re.findall(r"^_RE_\w+='[^']*'$", src, re.M)
    max_lines = re.search(r"^readonly NATIVE_READER_MAX_LINES=\d+$", src, re.M)
    # 守卫自身的非空过：抽取失败时必须炸，不能静默变成"两个空函数一致"
    assert "LC_ALL=C awk" in awk_fn, "未抽到 awk 主读者"
    assert "while IFS= read -r line" in native_fn, "未抽到纯 bash 回退读者"
    assert len(res) >= 4, f"未抽到回退读者用的正则常量: {res}"
    assert max_lines, "未抽到 NATIVE_READER_MAX_LINES"
    return "\n".join(
        ['set -uo pipefail', max_lines.group(0), 'BACKSTOP_READER=""', *res, awk_fn, native_fn]
    ) + "\n"


def _read_with(harness: str, fn: str, path: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["/bin/bash", "-c", harness + f'{fn} "$1"', "_", str(path)],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout


def test_native_reader_matches_awk_reader_on_every_format_variant(tmp_path):
    """两个读者在**每一种**格式变体上必须给出完全相同的 (rc, verdict)。

    包含生产真样本（07-30 fixture）与 13 种已知会出现的格式抖动。任何一处分歧都意味着
    "主读者失败时回退会改变结论"，那正是回退读者最不该有的行为。
    """
    harness = _reader_harness()
    cases = dict(_READER_PARITY_CASES)
    cases["production_fixture"] = PRODUCTION_FRONTMATTER.read_bytes()

    mismatches = []
    for name, data in cases.items():
        target = tmp_path / f"{name}.md"
        target.write_bytes(data)
        awk_out = _read_with(harness, "read_frontmatter_verdict", target)
        native_out = _read_with(harness, "read_frontmatter_verdict_native", target)
        if awk_out != native_out:
            mismatches.append(f"  {name}: awk={awk_out!r} native={native_out!r}")

    assert not mismatches, "双读者判据分歧（回退会改变结论）:\n" + "\n".join(mismatches)


def test_reader_parity_matrix_actually_covers_the_pass_verdict(tmp_path):
    """守卫的非空过：矩阵里必须真有解析出 pass 的样本，否则上一条可能在比较"全都读不出"。"""
    harness = _reader_harness()
    target = tmp_path / "sanity.md"
    target.write_bytes(_READER_PARITY_CASES["plain"])
    for fn in ("read_frontmatter_verdict", "read_frontmatter_verdict_native"):
        rc, out = _read_with(harness, fn, target)
        assert rc == 0 and out.strip() == "pass", f"{fn} 未读出 pass: rc={rc} out={out!r}"


# ── 非重试退出码（对账 Lane 6 的 pipeline EXIT_* 表后补）────────────────────
#
# Lane 6 把 social pipeline 的退出码定成 0=已交付 / 1=未交付 / 2=audit fail /
# 3=intake fixture 阻断 / 4=gate 不收敛 / 5=去重账本不可用，并显式保留 124/125。
# 其中 3（确定性阻断）、5（基础设施故障）、124（这一轮跑不完）**换模型族救不了**——
# 对它们重试就是这批修复要消掉的「明知无望还烧额度」，只是换了个位置复发。


def _fixed_rc_stub(tmp_path: Path, rc: int) -> Path:
    return _write_exec(
        tmp_path / f"rc{rc}-stub.sh",
        f'echo "invoked" >> "{_rerun_marker(tmp_path)}"\nexit {rc}',
    )


def _declared_nonretryable_codes(script_path: Path) -> set[str]:
    """读脚本里声明的非重试码集合，并解析其中的具名常量引用。

    清单里允许出现 `$SHARED_BUDGET_BLOCKED_EXIT_CODE` 这类引用（宪法 P4 要求预算/
    退出码常量具名），所以断言不能只按字面量比 —— 但也不能因为"有变量"就放弃校验：
    这里把同文件内的 `readonly NAME=<数字>` 声明解析出来做替换，得到**有效集合**。
    """
    script = script_path.read_text(encoding="utf-8")
    declared = re.search(
        r'^readonly RERUN_NONRETRYABLE_EXIT_CODES="([^"]*)"$', script, re.M
    )
    assert declared, "非重试码必须是具名常量（宪法 P4 预算显式）"
    consts = dict(re.findall(r'^readonly ([A-Z_][A-Z0-9_]*)=(\d+)$', script, re.M))
    resolved: set[str] = set()
    for token in declared.group(1).split():
        if token.startswith("$"):
            name = token[1:].strip("{}")
            assert name in consts, f"非重试码引用了未声明的常量: {token}"
            resolved.add(consts[name])
        else:
            resolved.add(token)
    return resolved


def test_nonretryable_exit_codes_skip_second_attempt(run_script, tmp_path):
    """3 / 5 / 124 / 65 必须只跑 1 轮，并把停因写进日志（不是静默少跑一轮）。"""
    codes = _declared_nonretryable_codes(BACKSTOP_SH)
    assert codes == {"124", "3", "5", "65"}, f"非重试码清单意外变化: {sorted(codes)}"

    for rc in (3, 5, 124):
        case_dir = tmp_path / f"case{rc}"
        case_dir.mkdir()
        vault = case_dir / "vault"
        (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
        stub = _fixed_rc_stub(case_dir, rc)
        result, log = _run_via_fixture(
            run_script, vault, case_dir,
            extra_env={
                "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
                "AKB2_BRIEFING_RERUN_ATTEMPTS": "2",
            },
            args=[],
        )
        content = log.read_text(encoding="utf-8")
        assert _rerun_calls(case_dir) == 1, f"rc={rc} 应只补跑 1 轮:\n{content}"
        assert "rerun_stop=nonretryable_exit" in content, f"rc={rc} 停因必须可见:\n{content}"
        assert "attempt=1/2" in content, content
        assert "attempt=2/2" not in content, f"rc={rc} 不该进第 2 轮:\n{content}"
        assert result.returncode == rc, f"rc={rc} 应原样透传，实际 {result.returncode}"


def test_retryable_exit_codes_still_switch_family(run_script, tmp_path):
    """反面：1 / 2 / 4 仍必须用满预算并在第 2 轮换族 —— 清单不得过宽。

    这三类都可能由 LLM 输出质量或 429 驱动，换族确有机会救回；把它们一起拦掉
    等于把本 lane 的主修法（换族补跑）关掉。
    """
    for rc in (1, 2, 4):
        case_dir = tmp_path / f"case{rc}"
        case_dir.mkdir()
        vault = case_dir / "vault"
        (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
        stub = _counting_rerun_stub(case_dir, succeed_from=99)
        # succeed_from=99 的 stub 退 1；2/4 用固定码 stub 另测透传
        if rc != 1:
            stub = _write_exec(
                case_dir / f"rc{rc}-env-stub.sh",
                f'''n=$(cat "{case_dir / "rerun-attempts.txt"}" 2>/dev/null || echo 0)
n=$(( n + 1 ))
echo "$n" > "{case_dir / "rerun-attempts.txt"}"
echo "attempt=$n AKB2_LLM_FAMILY_FALLBACK=${{AKB2_LLM_FAMILY_FALLBACK:-<unset>}}" >> "{case_dir / "rerun-env.txt"}"
echo "invoked" >> "{_rerun_marker(case_dir)}"
exit {rc}''',
            )
        result, log = _run_via_fixture(
            run_script, vault, case_dir,
            extra_env={
                "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
                "AKB2_BRIEFING_RERUN_ATTEMPTS": "2",
            },
            args=[],
        )
        content = log.read_text(encoding="utf-8")
        assert _rerun_calls(case_dir) == 2, f"rc={rc} 应用满 2 轮预算:\n{content}"
        assert "rerun_stop=nonretryable_exit" not in content, f"rc={rc} 不该被当非重试码"
        env_lines = (case_dir / "rerun-env.txt").read_text(encoding="utf-8").splitlines()
        assert "AKB2_LLM_FAMILY_FALLBACK=1" in env_lines[1], f"rc={rc} 第 2 轮须换族: {env_lines}"
        assert result.returncode == rc, f"rc={rc} 应原样透传，实际 {result.returncode}"


def test_rerun_result_labels_match_pipeline_exit_table(run_script, tmp_path):
    """每个已定义码的日志归因必须说对，且不得再把 3/4/5 混称"异常"。

    旧实现把 1 一律叫 "gate reject"（1 实际还覆盖上游硬失败与 vault 不可用），并把
    3/4/5 丢进 `*)` 的"异常" —— 它们是 pipeline 已定义的结局。误标归因会把排查带偏。
    """
    expected = {
        1: "未交付",
        2: "audit 判 fail",
        3: "intake 降级到测试 fixture",
        4: "gate 在最终文件上不收敛",
        5: "跨日去重账本不可用",
    }
    for rc, phrase in expected.items():
        case_dir = tmp_path / f"label{rc}"
        case_dir.mkdir()
        vault = case_dir / "vault"
        (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
        stub = _fixed_rc_stub(case_dir, rc)
        _result, log = _run_via_fixture(
            run_script, vault, case_dir,
            extra_env={
                "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
                "AKB2_BRIEFING_RERUN_ATTEMPTS": "1",
            },
            args=[],
        )
        content = log.read_text(encoding="utf-8")
        assert phrase in content, f"rc={rc} 归因文案缺「{phrase}」:\n{content}"
        assert "(异常)" not in content, f"rc={rc} 是已定义结局，不得叫异常:\n{content}"
        assert f"exit_code={rc}" in content, content


# ── 回退不得悄悄变成常态（集成期提醒后补）──────────────────────────────────
#
# 回退读者只有作为**例外路径**才有价值。若 awk 本身不可解析（launchd 给的是固定最小
# PATH，见 launchd/com.akb2.briefing.backstop.plist 的 PATH 键），主读者会**每次**失败，
# native 回退将永久承担 100% 判据 —— 而 verdict 依然正确，于是这个结构性缺陷被回退
# 悄悄掩盖：`read_path=native_fallback` 本该表示"今天卷抖了一下"，却变成"永远如此"，
# 信号价值归零。把响亮的故障变成安静的长期降级，正是宪法 P4 要禁的。


def test_structurally_unavailable_primary_reader_is_attributed_separately(run_script, tmp_path):
    """主读者结构性不可用 → 单独归因 reader_unavailable=，且不空跑重试窗口。

    与"卷级瞬时失败"必须可区分：后者重试有意义（4 轮 20s 窗口），前者重试毫无意义
    （二进制不存在，等 20s 只是把 backstop 往 deadline 推）。
    """
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\naudit_verdict: pass\n---\n# briefing\n")

    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        # 指向不存在的读者 → primary_reader_available() 判否
        extra_env={"AKB2_BACKSTOP_READER": str(tmp_path / "no-such-reader.sh")},
        args=[],
    )

    content = log.read_text(encoding="utf-8")
    assert "reader_unavailable=" in content, f"结构性不可用必须单独归因:\n{content}"
    assert "需人工修环境" in content, "必须点明这要人修环境，不是等它自愈"
    # 不空跑重试窗口：只尝试 1 轮
    assert "attempt=1/1" in content, f"结构性不可用时预算应收到 1:\n{content}"
    assert "attempt=2/" not in content, f"不得对缺失的二进制重试:\n{content}"
    # 判据仍然正确（回退兜住），终态仍是 skip
    assert "read_path=native_fallback" in content, content
    assert "terminal=skip" in content, content
    assert result.returncode == 0
    assert _rerun_calls(tmp_path) == 0


def test_transient_failure_still_uses_full_retry_window(run_script, tmp_path):
    """反面：读者**存在**但读不到（卷抖动）时，仍必须用满 4 轮窗口。

    与上一条成对 —— 防止"结构性不可用"的判定过宽，把真正该重试的卷抖动也一并缩成 1 轮
    （那会把 20s 容错窗口悄悄关掉，双读者修法的第一道防线就没了）。
    """
    script = BACKSTOP_SH.read_text(encoding="utf-8")
    budget = int(
        re.search(r"^readonly VERDICT_READ_ATTEMPT_BUDGET=(\d+)$", script, re.M).group(1)
    )
    vault = tmp_path / "vault"
    _write_today_briefing(vault, "---\naudit_verdict: pass\n---\n# briefing\n")
    reader = _failing_reader(tmp_path, fail_times=99)   # 存在且可执行，只是恒失败

    _result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={"AKB2_BACKSTOP_READER": str(reader)},
        args=[],
    )

    content = log.read_text(encoding="utf-8")
    assert "reader_unavailable=" not in content, "读者存在时不得判成结构性不可用"
    assert f"attempt={budget}/{budget}" in content, f"应用满 {budget} 轮窗口:\n{content}"


def test_native_fallback_is_not_the_default_path(run_script, tmp_path):
    """一切正常时绝不能出现 native_fallback —— 它是例外信号，常态化即失去价值。

    这条与 test_spacey_path_is_read_by_primary_awk_without_fallback 一起构成
    "回退必须罕见" 的双保险：那条盯含空格路径，这条盯普通路径 + 生产 fixture。
    """
    for name, payload in (
        ("plain", "---\naudit_verdict: pass\n---\n# briefing\n"),
        ("production", PRODUCTION_FRONTMATTER.read_text(encoding="utf-8")),
    ):
        case_dir = tmp_path / name
        case_dir.mkdir()
        vault = case_dir / "vault"
        _write_today_briefing(vault, payload)
        _result, log = _run_via_fixture(run_script, vault, case_dir)
        content = log.read_text(encoding="utf-8")
        assert "read_path=awk_primary" in content, f"{name}: 主读者应直接成功:\n{content}"
        assert "read_path=native_fallback" not in content, f"{name}: 常态不得走回退:\n{content}"
        assert "reader_unavailable=" not in content, content
        # 主读者一次就该成功：不该出现任何 warn 级读盘失败行
        assert "读取 audit_verdict 失败" not in content, content


# ── 退避前的 deadline 投影（fix-exit-truth 时长风险核对后补）──────────────────
#
# 退避 sleep 发生在**下一轮 deadline 检查之前**，所以原来会"白睡 600s、醒来才发现
# 已过界然后放弃"。除了浪费，睡着期间还一直**占着补跑锁** —— 把随后那趟 backstop
# （12:05）也挡在门外。默认常量（07:30 起跑 / deadline 12:00 / 退避 600s）余量约 4 小时
# 触不到这条；但只要有人按建议调大重试轮次、或把 deadline 提前，它立刻变成活风险。


def test_backoff_gives_up_instead_of_sleeping_past_deadline(run_script, tmp_path):
    """睡完就会过界时必须**当场放弃**，不能先睡再放弃（且不得白占补跑锁）。

    构造：假 date 恒返回 11:50，deadline 12:00，退避 600s → 11:50+10min = 12:00 已过界。
    断言绑可观测行为：终态 give_up + reason=deadline_would_pass + 只补跑 1 轮，
    并且**实测耗时远小于退避时长**（证明真的没睡）。
    """
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    stub = _counting_rerun_stub(tmp_path, succeed_from=99)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exec(
        fake_bin / "date",
        f"""case "${{1:-}}" in
  +%H%M) echo 1150 ;;
  +%Y-%m-%d) echo {_today()} ;;
  *) echo "{_today()} 11:50:00" ;;
esac""",
    )

    started = time.monotonic()
    result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_ATTEMPTS": "2",
            "AKB2_BRIEFING_RERUN_BACKOFF": "600",   # 真实生产值，不是 0
            "AKB2_BRIEFING_DEADLINE_HOUR": "12",
            "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        },
        args=[],
    )
    elapsed = time.monotonic() - started

    content = log.read_text(encoding="utf-8")
    assert result.returncode == GIVE_UP_EXIT_CODE, content
    assert "terminal=give_up" in content, content
    assert "reason=deadline_would_pass" in content, f"停因必须与「已过界」区分:\n{content}"
    assert _rerun_calls(tmp_path) == 1, f"只应补跑 1 轮:\n{content}"
    # 断"实际开跑的轮次"而不是裸子串：give-up 行本身会带 next_attempt=2/2
    #（那是诚实的——它标的是"被我们拒绝启动的那一轮"），裸 "attempt=2/2" 会误命中它。
    # 用唯一 token："run — " 会被 "rerun — " 包含，"attempt=2/2" 会被 "next_attempt=2/2" 包含
    run_lines = [ln for ln in content.splitlines() if "开始补跑 attempt=" in ln]
    assert len(run_lines) == 1, f"只应有 1 条开跑行:\n{chr(10).join(run_lines)}"
    # 没有真的睡 600s —— 这是本条的核心（宽松阈值，只要远小于退避即可）
    assert elapsed < 60, f"不得先睡再放弃，实测耗时 {elapsed:.1f}s"
    # inode 永久保留，但内核锁必须已释放（睡着时占锁会把下一趟一起挡掉）。
    lock = tmp_path / "backstop.lock"
    assert lock.exists(), "共享 flock inode 不得删除"
    with lock.open("a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def test_backoff_still_sleeps_when_margin_is_sufficient(run_script, tmp_path):
    """反面：余量充足时退避必须照常发生 —— 投影判据不得过宽把重试机制关掉。

    构造：假 date 恒返回 08:00，deadline 12:00，退避 1s → 余量 4 小时，应正常睡完并跑第 2 轮。
    """
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    stub = _counting_rerun_stub(tmp_path, succeed_from=99)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exec(
        fake_bin / "date",
        f"""case "${{1:-}}" in
  +%H%M) echo 0800 ;;
  +%Y-%m-%d) echo {_today()} ;;
  *) echo "{_today()} 08:00:00" ;;
esac""",
    )

    _result, log = _run_via_fixture(
        run_script, vault, tmp_path,
        extra_env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_ATTEMPTS": "2",
            "AKB2_BRIEFING_RERUN_BACKOFF": "1",
            "AKB2_BRIEFING_DEADLINE_HOUR": "12",
            "AKB2_BRIEFING_DEADLINE_MINUTE": "0",
        },
        args=[],
    )

    content = log.read_text(encoding="utf-8")
    assert "reason=deadline_would_pass" not in content, f"余量充足时不得放弃:\n{content}"
    assert _rerun_calls(tmp_path) == 2, f"应正常跑满 2 轮:\n{content}"
    assert "attempt=2/2" in content, content
    env_lines = (tmp_path / "rerun-env.txt").read_text(encoding="utf-8").splitlines()
    assert "AKB2_LLM_FAMILY_FALLBACK=1" in env_lines[1], env_lines


# ═══════════════════════════════════════════════════════════════════════════
# SC-R1-07 / 29 / 30 / 31 —— 退出码 65 不可重试 / 锁所有权 / 建锁 fail-closed /
#                              recovered 必须由产物证明
# ═══════════════════════════════════════════════════════════════════════════

SHARED_BUDGET_BLOCKED_EXIT_CODE = 65


def test_exit_65_budget_blocked_is_nonretryable(run_script, tmp_path):
    """65（共享预算闸已短路）必须单轮停止：不退避、不换族、不空睡 600s。

    65 由 daily_briefing.sh 的预算闸在 **pipeline 启动之前** 产出，跟模型族无关 ——
    `AKB2_LLM_FAMILY_FALLBACK=1` 对它结构性无效。此前它不在非重试集合里，
    生产默认 RERUN_BACKOFF=600 会明知无望地空睡 600s，而这 600s 一直占着补跑锁。
    """
    assert SHARED_BUDGET_BLOCKED_EXIT_CODE in {
        int(code) for code in _declared_nonretryable_codes(BACKSTOP_SH)
    }
    # 两个 backstop 都必须拦（同一个 wrapper 码位，漏一个就漏一条链）
    assert SHARED_BUDGET_BLOCKED_EXIT_CODE in {
        int(code)
        for code in _declared_nonretryable_codes(
            PROJECT_ROOT / "scripts" / "github_briefing_backstop.sh"
        )
    }

    case_dir = tmp_path / "case65"
    case_dir.mkdir()
    vault = case_dir / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    stub = _fixed_rc_stub(case_dir, SHARED_BUDGET_BLOCKED_EXIT_CODE)
    result, log = _run_via_fixture(
        run_script, vault, case_dir,
        extra_env={
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_ATTEMPTS": "2",
            # 故意给一个"真睡就会让用例超时"的退避值：只要还进第 2 轮就会暴露
            "AKB2_BRIEFING_RERUN_BACKOFF": "600",
        },
        args=[],
    )
    content = log.read_text(encoding="utf-8")
    assert _rerun_calls(case_dir) == 1, f"65 应只补跑 1 轮:\n{content}"
    assert "rerun_stop=nonretryable_exit" in content, content
    assert "attempt=2/2" not in content, f"65 不该进第 2 轮:\n{content}"
    assert "next_attempt=" not in content, f"65 不该进退避:\n{content}"
    assert result.returncode == SHARED_BUDGET_BLOCKED_EXIT_CODE


def test_exit_65_is_classified_not_unknown(run_script, tmp_path):
    """A named budget exit must retain its meaning in the terminal record."""
    case_dir = tmp_path / "case65-classification"
    case_dir.mkdir()
    vault = case_dir / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(
        parents=True
    )
    stub = _fixed_rc_stub(case_dir, SHARED_BUDGET_BLOCKED_EXIT_CODE)
    result, log = _run_via_fixture(
        run_script,
        vault,
        case_dir,
        extra_env={
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_ATTEMPTS": "1",
        },
        args=[],
    )

    content = log.read_text(encoding="utf-8")
    assert result.returncode == SHARED_BUDGET_BLOCKED_EXIT_CODE
    assert "共享预算" in content, content
    assert "未知码" not in content, content
    assert "不在 pipeline 已定义集合" not in content, content


def test_flock_inode_is_never_unlinked_on_exit(tmp_path):
    """flock 文件是永久共享 inode；进程退出只能释放内核锁，绝不能 unlink。"""
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    lock = tmp_path / "own.lock"
    marker = tmp_path / "marker.txt"
    target = _today_briefing_path(vault)
    inert_payload = "contents-are-not-lock-ownership"
    # 即使文件内容改变，互斥归属也只由内核 fd 决定；退出后 inode 仍须原样存在。
    stub = _write_exec(
        tmp_path / "stub.sh",
        f'echo invoked >> "{marker}"\n'
        f'echo "{inert_payload}" > "{lock}"\n'
        f'mkdir -p "{target.parent}"\n'
        f'printf -- "---\\naudit_verdict: pass\\n---\\n" > "{target}"\n'
        "exit 0",
    )

    result = _run_backstop(
        vault,
        env_overrides={
            "AKB2_BACKSTOP_LOCK_FILE": str(lock),
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_BACKOFF": "0",
        },
    )

    assert result.returncode == 0, result.stderr
    assert marker.exists(), "补跑应被触发过"
    assert lock.exists(), "backstop 删除了永久共享 flock inode"
    assert lock.read_text(encoding="utf-8").strip() == inert_payload, (
        f"锁内容被改写: {lock.read_text(encoding='utf-8')!r}"
    )
    with lock.open("a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def test_lock_setup_failure_refuses_rerun(tmp_path):
    """建不了锁 → 拒绝以互斥语义启动付费补跑（fail-closed），且**不调** pipeline。

    与「别人持锁」严格区分：后者是正常让路 exit 0，前者是无法建立互斥。
    裸跑的后果是双跑烧双份额度（38 天双跑事故的成型条件），而"少跑一次"可由
    下一个 backstop 时段兜。
    """
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    # 不可写的锁目录：把锁指到一个 0500 目录下的子路径 → mkdir -p 必然失败
    readonly_root = tmp_path / "readonly-state"
    readonly_root.mkdir()
    readonly_root.chmod(0o500)
    marker = tmp_path / "marker.txt"
    stub = _write_exec(tmp_path / "stub.sh", f'echo invoked >> "{marker}"\nexit 0')
    try:
        result = _run_backstop(
            vault,
            env_overrides={
                "AKB2_BACKSTOP_LOCK_FILE": str(readonly_root / "locks" / "b.lock"),
                "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
                "AKB2_BRIEFING_RERUN_BACKOFF": "0",
            },
        )
    finally:
        readonly_root.chmod(0o700)

    assert result.returncode != 0, f"建不了锁却成功退出:\n{result.stderr}"
    assert result.returncode not in (0, 125), result.returncode
    assert "lock_setup=failed" in result.stderr, (
        f"建锁失败必须具名可见:\n{result.stderr}"
    )
    assert "terminal=failed" in result.stderr, result.stderr
    assert not marker.exists(), "建不了锁却还是启动了付费补跑"


def test_social_recovered_requires_artifact(tmp_path):
    """rc=0 且零产物 → 终态**不得**是 recovered（假 recovered 闸）。

    github 侧本来就有这道复查（`SC-5: recovered 必须复查文件`），social 侧此前是
    `(( rc == 0 )) && break` 后直接打 terminal=recovered，不做任何产物复查。
    同一文件头部花 8 行论证「假阳性降到 0 之前不能接告警通道」，指的是假 FAILED；
    这里留下的是同族的**假 recovered**，而 terminal= 正是告警通道要读的那个键。
    """
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    stub = _write_exec(tmp_path / "stub.sh", f'echo invoked >> "{marker}"\nexit 0')

    result = _run_backstop(
        vault,
        env_overrides={
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_BACKOFF": "0",
            "AKB2_BRIEFING_RERUN_ATTEMPTS": "1",
        },
    )

    log = result.stderr
    assert "terminal=recovered" not in log, f"零产物却报了 recovered:\n{log}"
    assert "terminal=failed" in log, log
    assert "reason=false_recovery" in log, f"终态归因必须如实说明:\n{log}"
    assert "post_status=missing" in log, log
    assert result.returncode != 0, "未交付即非零"
    assert marker.exists(), "补跑应真的被触发过（否则这条断言恒真）"


def test_social_recovered_when_artifact_reaches_pass(tmp_path):
    """反向守卫：产物真的达 pass 时仍必须报 recovered（别把闸做成"永不 recovered"）。"""
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    target = _today_briefing_path(vault)
    stub = _write_exec(
        tmp_path / "stub.sh",
        f'mkdir -p "{target.parent}"\n'
        f'printf -- "---\\naudit_verdict: pass\\n---\\n" > "{target}"\n'
        "exit 0",
    )

    result = _run_backstop(
        vault,
        env_overrides={
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_BACKOFF": "0",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "terminal=recovered" in result.stderr, result.stderr


def test_social_recovered_rejects_artifact_still_failing_audit(tmp_path):
    """rc=0 但产物 audit_verdict=fail → 不是 recovered（判据是 pass，不是"文件存在"）。"""
    vault = tmp_path / "vault"
    (vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "Social").mkdir(parents=True)
    target = _today_briefing_path(vault)
    stub = _write_exec(
        tmp_path / "stub.sh",
        f'mkdir -p "{target.parent}"\n'
        f'printf -- "---\\naudit_verdict: fail\\n---\\n" > "{target}"\n'
        "exit 0",
    )

    result = _run_backstop(
        vault,
        env_overrides={
            "AKB2_BRIEFING_RERUN_SCRIPT": str(stub),
            "AKB2_BRIEFING_RERUN_BACKOFF": "0",
            "AKB2_BRIEFING_RERUN_ATTEMPTS": "1",
        },
    )

    assert "terminal=recovered" not in result.stderr, result.stderr
    assert "post_status=warn_or_fail" in result.stderr, result.stderr
    assert result.returncode != 0
