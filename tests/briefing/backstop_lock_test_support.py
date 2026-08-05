"""Process-level flock matrix shared by the two backstop test modules."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

LOCK_BUSY_EXIT_CODE = 73
LOCK_HELPER_ERROR_EXIT_CODE = 74


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _wait_for(path: Path, *, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not path.exists():
        time.sleep(0.01)
    assert path.exists(), f"timed out waiting for {path}"


def _assert_fd_survives_exec_and_death_releases(
    case_dir: Path, flock_helper: Path
) -> None:
    """PEP 446 must not drop the lock at exec; SIGKILL must release it."""
    lock = case_dir / "helper.lock"
    ready = case_dir / "helper-ready"
    blocked_marker = case_dir / "blocked-marker"
    recovered_marker = case_dir / "recovered-marker"
    common = [
        sys.executable,
        str(flock_helper),
        "--lock-file",
        str(lock),
        "--busy-exit-code",
        str(LOCK_BUSY_EXIT_CODE),
        "--error-exit-code",
        str(LOCK_HELPER_ERROR_EXIT_CODE),
        "--",
    ]
    holder = subprocess.Popen(
        [
            *common,
            sys.executable,
            "-c",
            "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)",
            str(ready),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for(ready)
        blocked = subprocess.run(
            [
                *common,
                sys.executable,
                "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()",
                str(blocked_marker),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert blocked.returncode == LOCK_BUSY_EXIT_CODE, blocked.stderr
        assert "flock_exec: busy" in blocked.stderr.splitlines(), blocked.stderr
        assert not blocked_marker.exists(), "PEP 446 closed the lock fd during exec"

        holder.kill()
        holder.communicate(timeout=10)
        recovered = subprocess.run(
            [
                *common,
                sys.executable,
                "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()",
                str(recovered_marker),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert recovered.returncode == 0, recovered.stderr
        assert recovered_marker.exists(), "dead flock owner blocked its successor"
        assert lock.exists(), "shared flock inode must persist after owner death"
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=10)


def _backstop_case(kind: str, case_dir: Path, project_root: Path) -> tuple:
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
    return script, vault, lock, marker, release, env, rerun_key


def _assert_n_contenders_have_one_owner(
    case_dir: Path,
    kind: str,
    project_root: Path,
    *,
    credential_env: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> None:
    script, vault, lock, marker, release, common_env, _ = _backstop_case(
        kind, case_dir, project_root
    )
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
        log_key = (
            "AKB2_BACKSTOP_LOG"
            if kind == "social"
            else "AKB2_GITHUB_BACKSTOP_LOG"
        )
        env = {
            **os.environ,
            **common_env,
            **(credential_env or {}),
            "AKB2_MATRIX_ACTOR": str(index),
            log_key: str(case_dir / f"backstop-{index}.log"),
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


def assert_flock_reentry_rejects_forged_env(
    tmp_path: Path, kind: str, project_root: Path
) -> None:
    """Neither the retired boolean nor an unopened fd number may bypass flock."""
    cases = {
        "legacy-bool": {"AKB2_BACKSTOP_FLOCK_HELD": "1"},
        "closed-fd": {"AKB2_BACKSTOP_FLOCK_FD": "7"},
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
            credential_env={"AKB2_BACKSTOP_FLOCK_FD": str(wrong_fd)},
            pass_fds=(wrong_fd,),
        )
    finally:
        os.close(wrong_fd)

    same_inode_case = tmp_path / "same-inode-unlocked"
    same_inode_case.mkdir()
    _assert_unlocked_same_inode_fd_cannot_bypass(same_inode_case, kind, project_root)


def _assert_unlocked_same_inode_fd_cannot_bypass(
    case_dir: Path, kind: str, project_root: Path
) -> None:
    """An open-but-unlocked descriptor for the lock inode is not owner authority."""
    script, vault, lock, marker, release, common_env, rerun_key = _backstop_case(
        kind, case_dir, project_root
    )
    log_key = "AKB2_BACKSTOP_LOG" if kind == "social" else "AKB2_GITHUB_BACKSTOP_LOG"
    holder = subprocess.Popen(
        ["/bin/bash", str(script), "--vault-root", str(vault)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            **common_env,
            "AKB2_MATRIX_ACTOR": "owner",
            log_key: str(case_dir / "owner.log"),
        },
    )
    forged_fd: int | None = None
    try:
        _wait_for(marker)
        forged_fd = os.open(lock, os.O_RDWR)
        contender = _write_exec(
            case_dir / "forged-contender.sh",
            f'echo "forged" >> "{marker}"\nexit 1',
        )
        blocked = subprocess.run(
            ["/bin/bash", str(script), "--vault-root", str(vault)],
            capture_output=True,
            text=True,
            timeout=20,
            pass_fds=(forged_fd,),
            env={
                **os.environ,
                **common_env,
                "AKB2_BACKSTOP_FLOCK_FD": str(forged_fd),
                "AKB2_MATRIX_ACTOR": "forged",
                rerun_key: str(contender),
                log_key: str(case_dir / "forged.log"),
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


def assert_child_exit_codes_passthrough(
    tmp_path: Path, kind: str, project_root: Path
) -> None:
    """Protected child rc 73/74 lacks helper framing and must pass through unchanged."""
    for child_rc in (LOCK_BUSY_EXIT_CODE, LOCK_HELPER_ERROR_EXIT_CODE):
        case_dir = tmp_path / f"child-{child_rc}"
        case_dir.mkdir()
        script, vault, _, marker, _, common_env, rerun_key = _backstop_case(
            kind, case_dir, project_root
        )
        common_env[rerun_key] = _write_exec(
            case_dir / "child.sh",
            f'echo "invoked" >> "{marker}"\nexit {child_rc}',
        ).as_posix()
        result = subprocess.run(
            ["/bin/bash", str(script), "--vault-root", str(vault)],
            capture_output=True,
            text=True,
            timeout=20,
            env={**os.environ, **common_env},
        )
        assert result.returncode == child_rc, result.stderr
        assert marker.read_text(encoding="utf-8").splitlines() == ["invoked"]
        combined = result.stdout + result.stderr
        assert "already-running" not in combined, combined
        assert "lock_setup=failed" not in combined, combined


def _assert_missing_python_fails_closed(
    case_dir: Path, kind: str, project_root: Path
) -> None:
    script, vault, lock, marker, _, common_env, _ = _backstop_case(
        kind, case_dir, project_root
    )
    result = subprocess.run(
        ["/bin/bash", str(script), "--vault-root", str(vault)],
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **os.environ,
            **common_env,
            "AKB2_BACKSTOP_PYTHON": "akb2-python3-missing-for-test",
        },
    )
    assert result.returncode != 0, result.stderr
    assert "python3" in result.stderr and "lock_setup=failed" in result.stderr
    assert not marker.exists(), "missing python3 silently launched an unlocked rerun"
    assert not lock.exists(), "the flock helper ran despite missing python3"


def assert_flock_interleaving_matrix(
    tmp_path: Path, kind: str, project_root: Path
) -> None:
    """Exercise inherited-fd, stale-file, N-way, crash, and setup-failure states."""
    helper_case = tmp_path / "helper"
    helper_case.mkdir()
    _assert_fd_survives_exec_and_death_releases(
        helper_case, project_root / "scripts" / "lib" / "flock_exec.py"
    )

    competition_case = tmp_path / "competition"
    competition_case.mkdir()
    _assert_n_contenders_have_one_owner(competition_case, kind, project_root)

    missing_python_case = tmp_path / "missing-python"
    missing_python_case.mkdir()
    _assert_missing_python_fails_closed(missing_python_case, kind, project_root)
