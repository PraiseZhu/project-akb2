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


if __name__ == "__main__":
    sys.exit(main())
