#!/usr/bin/env python3
"""Run a command until a same-day Asia/Shanghai deadline.

This is a small macOS-safe replacement for GNU timeout.  It creates a process
group, polls until the child exits or the deadline arrives, then terminates the
whole group so launchd can observe a visible non-zero status.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from types import FrameType
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")
_DEADLINE_EXIT = 124


def _log(name: str, message: str) -> None:
    ts = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} [{name}] {message}", file=sys.stderr, flush=True)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="run_with_deadline.py")
    parser.add_argument("--name", required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD in Asia/Shanghai")
    parser.add_argument("--deadline-hour", type=int, default=12)
    parser.add_argument("--deadline-minute", type=int, default=0)
    parser.add_argument("--term-grace-seconds", type=int, default=20)
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.cmd and args.cmd[0] == "--":
        args.cmd = args.cmd[1:]
    if not args.cmd:
        parser.error("command after -- is required")
    return args


def _deadline_for(date_str: str, hour: int, minute: int) -> datetime | None:
    today = datetime.now(_TZ).strftime("%Y-%m-%d")
    if date_str != today:
        return None
    return datetime.strptime(
        f"{date_str} {hour:02d}:{minute:02d}:00",
        "%Y-%m-%d %H:%M:%S",
    ).replace(tzinfo=_TZ)


def _terminate_group(proc: subprocess.Popen, grace_seconds: int) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        proc.terminate()

    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        proc.kill()
    proc.wait()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    deadline = _deadline_for(args.date, args.deadline_hour, args.deadline_minute)

    if os.environ.get("AKB2_DISABLE_DEADLINE") == "1" or deadline is None:
        _log(args.name, "deadline disabled for this run")
        return subprocess.call(args.cmd)

    now = datetime.now(_TZ)
    if now >= deadline:
        _log(args.name, f"deadline already missed ({deadline.isoformat()}); not starting")
        return _DEADLINE_EXIT

    _log(args.name, f"start; deadline={deadline.isoformat()}; cmd={args.cmd[0]}")
    proc = subprocess.Popen(args.cmd, start_new_session=True)

    # launchd 停止任务/系统关机发 SIGTERM（非 SIGINT）。若不挂 handler，默认行为是
    # 直接终止本 wrapper，start_new_session=True 创建的子进程组会成为孤儿继续运行，
    # 简报管线卡死白占资源。捕获 SIGTERM/SIGHUP，先杀整个进程组再带信号退出。
    def _handle_termination_signal(signum: int, _frame: FrameType | None) -> None:
        _log(args.name, f"received signal {signum}; terminating process group")
        _terminate_group(proc, args.term_grace_seconds)
        # 以 128+signum 退出，向 launchd 暴露可见的非零状态（被信号终止的约定）。
        sys.exit(128 + signum)

    previous_handlers: list[tuple[int, Any]] = []
    for sig in (signal.SIGTERM, signal.SIGHUP):
        previous_handlers.append((sig, signal.signal(sig, _handle_termination_signal)))

    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                _log(args.name, f"exit={rc}")
                return rc
            if datetime.now(_TZ) >= deadline:
                _log(args.name, "deadline missed; terminating process group")
                _terminate_group(proc, args.term_grace_seconds)
                return _DEADLINE_EXIT
            time.sleep(5)
    except KeyboardInterrupt:
        _terminate_group(proc, args.term_grace_seconds)
        raise
    finally:
        for sig, handler in previous_handlers:
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
