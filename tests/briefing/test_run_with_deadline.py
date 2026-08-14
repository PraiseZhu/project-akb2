"""Tests for the same-day deadline wrapper."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from tests.briefing.shanghai_today import shanghai_today


def _today() -> str:
    """Same calendar day as run_with_deadline.py (`Asia/Shanghai`)."""
    return shanghai_today()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = PROJECT_ROOT / "scripts" / "run_with_deadline.py"


def _pid_alive(pid: int) -> bool:
    """True if pid is still alive (signal 0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_deadline_already_missed_does_not_start_child(tmp_path):
    marker = tmp_path / "started"
    result = subprocess.run(
        [
            sys.executable,
            str(WRAPPER),
            "--name",
            "test-deadline",
            "--date",
            _today(),
            "--deadline-hour",
            "0",
            "--deadline-minute",
            "0",
            "--",
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('started')",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 124
    assert "deadline already missed" in result.stderr
    assert not marker.exists()


def test_sigterm_terminates_child_process_group(tmp_path):
    """launchd 停任务发 SIGTERM —— wrapper 必须杀掉整个子进程组，不留孤儿。

    子命令拉起一个 sleep 孙进程并把其 pid 写盘，然后自身长睡。给 wrapper 发
    SIGTERM 后，验证：(1) wrapper 以 143 (128+SIGTERM) 退出；(2) 孙进程已死。
    回归前（无 SIGTERM handler）wrapper 被默认杀死、孙进程成孤儿存活。
    """
    grandchild_pidfile = tmp_path / "grandchild.pid"
    # 子命令：spawn 一个独立 sleep 孙进程（写 pid），再自身长睡占住前台。
    child_script = (
        "import os, subprocess, sys, time\n"
        f"p = subprocess.Popen(['sleep', '60'])\n"
        f"open({str(grandchild_pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    # deadline 必须是「今天且未到」才进入轮询循环：_deadline_for 对非今天日期返回
    # None → 走 subprocess.call 旁路（不注册 handler）。设今天 23:59 确保进轮询。
    today = _today()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(WRAPPER),
            "--name",
            "test-sigterm",
            "--date",
            today,
            "--deadline-hour",
            "23",
            "--deadline-minute",
            "59",
            "--term-grace-seconds",
            "5",
            "--",
            sys.executable,
            "-c",
            child_script,
        ],
        stderr=subprocess.PIPE,
        text=True,
    )

    # 等子命令把孙进程 pid 写盘（最多 ~5s）。
    grandchild_pid = None
    for _ in range(50):
        if grandchild_pidfile.exists():
            content = grandchild_pidfile.read_text().strip()
            if content:
                grandchild_pid = int(content)
                break
        time.sleep(0.1)
    assert grandchild_pid is not None, "子命令未能拉起孙进程"
    assert _pid_alive(grandchild_pid), "孙进程应在 SIGTERM 前存活"

    # 给 wrapper 发 SIGTERM（模拟 launchd 停止任务）。
    proc.send_signal(signal.SIGTERM)
    rc = proc.wait(timeout=15)

    # wrapper 以 128+SIGTERM 退出，向 launchd 暴露可见非零状态。
    assert rc == 128 + signal.SIGTERM, f"wrapper 应以 143 退出，实际 {rc}"

    # 孙进程应被进程组终止杀掉（给最多 ~3s 收尾）。
    for _ in range(30):
        if not _pid_alive(grandchild_pid):
            break
        time.sleep(0.1)
    assert not _pid_alive(grandchild_pid), "SIGTERM 后孙进程仍存活 = 孤儿进程组泄漏"
