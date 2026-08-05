"""Process-level flock matrix shared by the two backstop test modules.

R6 起锁形状 = 本 shell 内联持有（`exec 9>` + python 代调 flock(2)，无 helper、无
re-exec、无重入凭据）。本模块只做**行为**断言：互斥、归因、fail-closed、后代不扣锁。
不得断言实现细节（helper 文件名、协议码、framing 字符串）—— 那类断言在 R6 重写里
全部作废过一次。
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import NamedTuple

# 73/74 是**已删除**的旧锁协议码。保留数值只为证明「与历史协议撞码的子进程退出码
# 不再被特殊解释」—— 它们如今只是两个普通的失败退出码。
HISTORIC_BUSY_LOOKALIKE_RC = 73
HISTORIC_HELPER_ERROR_LOOKALIKE_RC = 74

# 已删除的重入凭据 env 名，用拼接构造：实现区（scripts/src/docs）对完整名字的
# git grep 必须保持 0 命中，测试自己也不许成为命中来源。
LEGACY_HELD_ENV = "AKB2_BACKSTOP_FLOCK" + "_HELD"
LEGACY_FD_ENV = "AKB2_BACKSTOP_FLOCK" + "_FD"


class BackstopCase(NamedTuple):
    script: Path
    vault: Path
    lock: Path
    marker: Path
    release: Path
    env: dict
    rerun_key: str
    target: Path
    artifact: str  # printf -- 就绪的转义形态


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _wait_for(path: Path, *, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not path.exists():
        time.sleep(0.01)
    assert path.exists(), f"timed out waiting for {path}"


def _backstop_case(kind: str, case_dir: Path, project_root: Path) -> BackstopCase:
    vault = case_dir / "vault"
    lock = case_dir / "backstop.lock"
    marker = case_dir / "rerun-marker"
    release = case_dir / "release"
    today = date.today().isoformat()
    if kind == "social":
        script = project_root / "scripts" / "briefing_backstop.sh"
        target = (
            vault
            / "Praise Space"
            / "AKB2.0"
            / "AI TRENDS"
            / "Social"
            / f"{today} Daily Briefing.md"
        )
        env = {
            "AKB2_BACKSTOP_LOCK_FILE": str(lock),
            "AKB2_BACKSTOP_LOG": str(case_dir / "unused.log"),
            "AKB2_BRIEFING_RERUN_ATTEMPTS": "1",
            "AKB2_BRIEFING_RERUN_BACKOFF": "0",
            "AKB2_BRIEFING_DEADLINE_HOUR": "99",
        }
        rerun_key = "AKB2_BRIEFING_RERUN_SCRIPT"
        artifact = "---\\naudit_verdict: pass\\n---\\n"
    elif kind == "github":
        script = project_root / "scripts" / "github_briefing_backstop.sh"
        target = (
            vault
            / "Praise Space"
            / "AKB2.0"
            / "AI TRENDS"
            / "GitHub"
            / f"{today} GitHub Briefing.md"
        )
        env = {
            "AKB2_GITHUB_BACKSTOP_LOCK_FILE": str(lock),
            "AKB2_GITHUB_BACKSTOP_LOG": str(case_dir / "unused.log"),
            "AKB2_GITHUB_RERUN_ATTEMPTS": "1",
            "AKB2_GITHUB_RERUN_BACKOFF": "0",
            "AKB2_GITHUB_DEADLINE_HOUR": "99",
            "AKB2_GITHUB_ENV_FILE": "/dev/null",
        }
        rerun_key = "AKB2_GITHUB_RERUN_SCRIPT"
        artifact = "# GitHub briefing\\n"
    else:
        raise AssertionError(f"unknown backstop kind: {kind}")
    target.parent.mkdir(parents=True)
    runner = _write_exec(
        case_dir / "holder.sh",
        f'''echo "$AKB2_MATRIX_ACTOR" >> "{marker}"
i=0
while [ ! -f "{release}" ] && [ "$i" -lt 1500 ]; do
  sleep 0.01
  i=$(( i + 1 ))
done
printf -- "{artifact}" > "{target}"
exit 0''',
    )
    env[rerun_key] = str(runner)
    return BackstopCase(script, vault, lock, marker, release, env, rerun_key, target, artifact)


def _log_key(kind: str) -> str:
    return "AKB2_BACKSTOP_LOG" if kind == "social" else "AKB2_GITHUB_BACKSTOP_LOG"


def _run_backstop(
    case: BackstopCase, kind: str, case_dir: Path, *, extra_env: dict | None = None, **kwargs
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        **case.env,
        "AKB2_MATRIX_ACTOR": "solo",
        _log_key(kind): str(case_dir / "solo.log"),
        **(extra_env or {}),
    }
    return subprocess.run(
        ["/bin/bash", str(case.script), "--vault-root", str(case.vault)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        **kwargs,
    )


def _assert_n_contenders_have_one_owner(
    case_dir: Path,
    kind: str,
    project_root: Path,
    *,
    credential_env: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> None:
    case = _backstop_case(kind, case_dir, project_root)
    lock, marker, release = case.lock, case.marker, case.release
    # A dead PID payload is deliberately left behind. flock ignores file contents;
    # only a live kernel lock can block a successor, so no stale-takeover path exists.
    lock.write_text("999999\n", encoding="utf-8")
    barrier = case_dir / "barrier"
    barrier_runner = _write_exec(
        case_dir / "barrier-runner.sh",
        'while [ ! -f "$1" ]; do sleep 0.01; done\nshift\nexec "$@"',
    )
    processes: list[subprocess.Popen] = []
    contender_count = 12
    for index in range(contender_count):
        env = {
            **os.environ,
            **case.env,
            **(credential_env or {}),
            "AKB2_MATRIX_ACTOR": str(index),
            _log_key(kind): str(case_dir / f"backstop-{index}.log"),
        }
        processes.append(
            subprocess.Popen(
                [
                    str(barrier_runner),
                    str(barrier),
                    "/bin/bash",
                    str(case.script),
                    "--vault-root",
                    str(case.vault),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                pass_fds=pass_fds,
            )
        )

    results: list[tuple[str, str]] = []
    try:
        barrier.touch()
        _wait_for(marker)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            completed = sum(process.poll() is not None for process in processes)
            if completed >= contender_count - 1:
                break
            time.sleep(0.02)
        assert completed >= contender_count - 1, "flock contenders did not settle"
        release.touch()
        results = [process.communicate(timeout=20) for process in processes]
    finally:
        release.touch(exist_ok=True)
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)

    calls = marker.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1, f"{kind}: {len(calls)} contenders entered the paid rerun"
    assert all(process.returncode == 0 for process in processes), results
    loser_logs = [
        (case_dir / f"backstop-{index}.log").read_text(encoding="utf-8")
        for index in range(contender_count)
    ]
    assert sum("already-running" in content for content in loser_logs) == contender_count - 1
    assert lock.exists(), "the shared flock inode must never be unlinked"


def assert_no_forgeable_reentry_credential_exists(
    tmp_path: Path, kind: str, project_root: Path
) -> None:
    """SC-R6-01: 重入凭据这个概念已不存在，任何 env 伪造都改变不了互斥结果。

    · 旧布尔 / 旧 fd 号 / 已删除的解释器旋钮（/usr/bin/true 组合）全部是惰性 env：
      N 个同刻竞争者仍恰好 1 个进入付费补跑；
    · 传真实 fd（错误 inode / 同 inode 未持锁）同样不构成 owner 授权；
    · 实现区对旧凭据名的 git grep 必须 0 命中（名字都不存在的东西无法被伪造）。
    """
    cases = {
        "legacy-bool": {LEGACY_HELD_ENV: "1"},
        "closed-fd": {LEGACY_FD_ENV: "7"},
        # R5 的打穿姿势原样重放：解释器旋钮 + fd 号。R6 里两者都必须是惰性 env。
        "interpreter-combo": {"AKB2_BACKSTOP_PYTHON": "/usr/bin/true", LEGACY_FD_ENV: "7"},
    }
    for name, credential_env in cases.items():
        case_dir = tmp_path / name
        case_dir.mkdir()
        _assert_n_contenders_have_one_owner(
            case_dir,
            kind,
            project_root,
            credential_env=credential_env,
        )

    wrong_inode_case = tmp_path / "wrong-inode"
    wrong_inode_case.mkdir()
    wrong_fd = os.open(wrong_inode_case / "not-the-lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _assert_n_contenders_have_one_owner(
            wrong_inode_case,
            kind,
            project_root,
            credential_env={LEGACY_FD_ENV: str(wrong_fd)},
            pass_fds=(wrong_fd,),
        )
    finally:
        os.close(wrong_fd)

    same_inode_case = tmp_path / "same-inode-unlocked"
    same_inode_case.mkdir()
    _assert_unlocked_same_inode_fd_cannot_bypass(same_inode_case, kind, project_root)

    # 凭据面消失的静态半：实现与文档不得再引用旧凭据名（测试侧用拼接构造，
    # 所以整棵 tracked 树的完整名字命中数必须为 0）。
    for legacy_name in (LEGACY_HELD_ENV, LEGACY_FD_ENV):
        result = _run_legacy_credential_grep(legacy_name, project_root)
        _assert_legacy_credential_grep_clean(result, legacy_name)


class LegacyCredentialGrepResult(NamedTuple):
    """`git grep` 的三态结果，与 subprocess 解耦以便注入伪造值做单测。"""

    returncode: int
    hits: list[str]
    stderr: str


def _run_legacy_credential_grep(legacy_name: str, project_root: Path) -> LegacyCredentialGrepResult:
    grep = subprocess.run(
        ["git", "grep", "-l", "-F", legacy_name, "--", "."],
        capture_output=True,
        text=True,
        cwd=project_root,
        timeout=60,
    )
    hits = [line for line in grep.stdout.splitlines() if line]
    return LegacyCredentialGrepResult(grep.returncode, hits, grep.stderr)


def _assert_legacy_credential_grep_clean(
    result: LegacyCredentialGrepResult, legacy_name: str
) -> None:
    """三态判别：rc=0 有命中即失败；rc=1 无命中即通过；其余 rc 是工具错误，绝不能
    被当作阴性结果放行——工具没跑成，守卫就没生效，不是「没找到」。
    """
    if result.returncode == 0:
        raise AssertionError(
            f"legacy credential name {legacy_name} still referenced in: {result.hits}"
        )
    if result.returncode == 1:
        return
    raise AssertionError(
        f"git grep for legacy credential name {legacy_name} exited with a tool error "
        f"(rc={result.returncode}), not a clean no-match — this guard did NOT run: "
        f"{result.stderr}"
    )


def _assert_unlocked_same_inode_fd_cannot_bypass(
    case_dir: Path, kind: str, project_root: Path
) -> None:
    """An open-but-unlocked descriptor for the lock inode is not owner authority."""
    case = _backstop_case(kind, case_dir, project_root)
    marker, release = case.marker, case.release
    holder = subprocess.Popen(
        ["/bin/bash", str(case.script), "--vault-root", str(case.vault)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            **case.env,
            "AKB2_MATRIX_ACTOR": "owner",
            _log_key(kind): str(case_dir / "owner.log"),
        },
    )
    forged_fd: int | None = None
    try:
        _wait_for(marker)
        forged_fd = os.open(case.lock, os.O_RDWR)
        contender = _write_exec(
            case_dir / "forged-contender.sh",
            f'echo "forged" >> "{marker}"\nexit 1',
        )
        blocked = subprocess.run(
            ["/bin/bash", str(case.script), "--vault-root", str(case.vault)],
            capture_output=True,
            text=True,
            timeout=20,
            pass_fds=(forged_fd,),
            env={
                **os.environ,
                **case.env,
                LEGACY_FD_ENV: str(forged_fd),
                "AKB2_MATRIX_ACTOR": "forged",
                case.rerun_key: str(contender),
                _log_key(kind): str(case_dir / "forged.log"),
            },
        )
        assert blocked.returncode == 0, blocked.stderr
        assert "already-running" in blocked.stderr, blocked.stderr
        assert marker.read_text(encoding="utf-8").splitlines() == ["owner"]
    finally:
        if forged_fd is not None:
            os.close(forged_fd)
        release.touch(exist_ok=True)
        if holder.poll() is None:
            holder.communicate(timeout=20)


def assert_child_exit_attribution_unambiguous(
    tmp_path: Path, kind: str, project_root: Path
) -> None:
    """SC-R6-02: pipeline 的 rc 就是它自己的 rc，不存在协议码/framing 可撞。

    · 子进程真实 exit 73/74 → 终态是真实失败归因（terminal=failed），原码透传；
    · 子进程往 stderr 写历史 framing 字符串 → 终态不受影响（framing 通道不存在）；
    · 真 busy（另一实例持锁）→ 正确让路 exit 0 + already-running。
    """
    for child_rc in (HISTORIC_BUSY_LOOKALIKE_RC, HISTORIC_HELPER_ERROR_LOOKALIKE_RC):
        case_dir = tmp_path / f"child-{child_rc}"
        case_dir.mkdir()
        case = _backstop_case(kind, case_dir, project_root)
        case.env[case.rerun_key] = _write_exec(
            case_dir / "child.sh",
            f'echo "invoked" >> "{case.marker}"\nexit {child_rc}',
        ).as_posix()
        result = _run_backstop(case, kind, case_dir)
        assert result.returncode == child_rc, result.stderr
        assert case.marker.read_text(encoding="utf-8").splitlines() == ["invoked"]
        combined = result.stdout + result.stderr
        assert "terminal=failed" in combined, combined
        assert "already-running" not in combined, combined
        assert "lock_setup=failed" not in combined, combined

    spoof_dir = tmp_path / "stderr-spoof"
    spoof_dir.mkdir()
    case = _backstop_case(kind, spoof_dir, project_root)
    case.env[case.rerun_key] = _write_exec(
        spoof_dir / "spoof.sh",
        f'''echo "invoked" >> "{case.marker}"
echo "flock_exec: busy" >&2
printf -- "{case.artifact}" > "{case.target}"
exit 0''',
    ).as_posix()
    result = _run_backstop(case, kind, spoof_dir)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, result.stderr
    assert "terminal=recovered" in combined, combined
    assert "already-running" not in combined, combined

    busy_dir = tmp_path / "true-busy"
    busy_dir.mkdir()
    case = _backstop_case(kind, busy_dir, project_root)
    holder = subprocess.Popen(
        ["/bin/bash", str(case.script), "--vault-root", str(case.vault)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            **case.env,
            "AKB2_MATRIX_ACTOR": "owner",
            _log_key(kind): str(busy_dir / "owner.log"),
        },
    )
    try:
        _wait_for(case.marker)
        blocked = _run_backstop(
            case, kind, busy_dir, extra_env={"AKB2_MATRIX_ACTOR": "latecomer"}
        )
        assert blocked.returncode == 0, blocked.stderr
        assert "already-running" in blocked.stderr, blocked.stderr
        assert case.marker.read_text(encoding="utf-8").splitlines() == ["owner"]
    finally:
        case.release.touch(exist_ok=True)
        if holder.poll() is None:
            holder.communicate(timeout=20)


def assert_descendant_cannot_pin_lock(
    tmp_path: Path, kind: str, project_root: Path
) -> None:
    """SC-R6-03: `9<&-` 让 pipeline 及其后代从根上拿不到锁 fd。

    · 起一个活过 backstop 的后代，backstop 退出后锁必须立即可取（不再 errno=35）；
    · 该后代自报的 fd 表中不含指向锁 inode 的 fd。
    """
    case_dir = tmp_path / "descendant"
    case_dir.mkdir()
    case = _backstop_case(kind, case_dir, project_root)
    fd_audit = case_dir / "fd-audit.txt"
    daemon_pid_file = case_dir / "daemon.pid"
    audit_py = (
        "import os, sys, time\n"
        "lines = []\n"
        'for name in os.listdir("/dev/fd"):\n'
        "    try:\n"
        "        st = os.fstat(int(name))\n"
        "        lines.append(name + \" \" + str(st.st_dev) + \" \" + str(st.st_ino))\n"
        "    except OSError:\n"
        "        pass\n"
        'open(sys.argv[1], "w").write("\\n".join(lines))\n'
        "time.sleep(45)\n"
    )
    case.env[case.rerun_key] = _write_exec(
        case_dir / "spawner.sh",
        f'''python3 -c '{audit_py}' "{fd_audit}" </dev/null >/dev/null 2>&1 &
echo $! > "{daemon_pid_file}"
printf -- "{case.artifact}" > "{case.target}"
exit 0''',
    ).as_posix()

    daemon_pid: int | None = None
    try:
        result = _run_backstop(case, kind, case_dir)
        assert result.returncode == 0, result.stderr
        assert "terminal=recovered" in result.stdout + result.stderr, result.stderr
        _wait_for(daemon_pid_file)
        daemon_pid = int(daemon_pid_file.read_text(encoding="utf-8").strip())
        os.kill(daemon_pid, 0)  # 后代必须仍然活着，否则本测试没有测到"活过 backstop"

        # backstop 已退出而后代仍活 → 锁必须立即可取。
        with open(case.lock, "a+") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

        # 后代自报 fd 表中不得出现锁 inode（不是"没锁上"，是"根本拿不到 fd"）。
        _wait_for(fd_audit)
        lock_stat = os.stat(case.lock)
        for line in fd_audit.read_text(encoding="utf-8").splitlines():
            _, dev, ino = line.split()
            assert (int(dev), int(ino)) != (lock_stat.st_dev, lock_stat.st_ino), (
                f"descendant still holds an fd to the lock inode: {line}"
            )
    finally:
        if daemon_pid is not None:
            try:
                os.kill(daemon_pid, 9)
            except ProcessLookupError:
                pass


def _assert_broken_python_fails_closed(
    case_dir: Path, kind: str, project_root: Path
) -> None:
    """解释器损坏（可解析但不是 python）→ fail-closed，绝不误判成 busy 让路。"""
    case = _backstop_case(kind, case_dir, project_root)
    stub_bin = case_dir / "bin"
    stub_bin.mkdir()
    _write_exec(stub_bin / "python3", "exit 7")  # 无输出、非零退出：探针无法确认锁状态
    result = _run_backstop(
        case,
        kind,
        case_dir,
        extra_env={"PATH": f"{stub_bin}:{os.environ.get('PATH', '')}"},
    )
    assert result.returncode == 1, result.stderr
    assert "lock_setup=failed" in result.stderr, result.stderr
    assert "already-running" not in result.stderr, (
        "a broken interpreter must fail closed, not be misread as lock contention"
    )
    assert not case.marker.exists(), "broken python silently launched an unlocked rerun"


def assert_flock_interleaving_matrix(
    tmp_path: Path, kind: str, project_root: Path
) -> None:
    """Exercise stale-file, N-way barrier start, and interpreter-failure states."""
    competition_case = tmp_path / "competition"
    competition_case.mkdir()
    _assert_n_contenders_have_one_owner(competition_case, kind, project_root)

    broken_python_case = tmp_path / "broken-python"
    broken_python_case.mkdir()
    _assert_broken_python_fails_closed(broken_python_case, kind, project_root)
