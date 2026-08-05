#!/usr/bin/env python3
"""Run a command while holding a non-blocking ``fcntl.flock`` lock."""

from __future__ import annotations

import argparse
import errno
import fcntl
import os
import sys
from pathlib import Path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-file", required=True)
    parser.add_argument("--busy-exit-code", required=True, type=int)
    parser.add_argument("--error-exit-code", required=True, type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command is required after --")
    for name in ("busy_exit_code", "error_exit_code"):
        value = getattr(args, name)
        if not 1 <= value <= 255:
            parser.error(f"--{name.replace('_', '-')} must be in 1..255")
    if args.busy_exit_code == args.error_exit_code:
        parser.error("busy and error exit codes must differ")
    return args


def _error(message: str) -> None:
    print(f"flock_exec: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    lock_path = Path(args.lock_file)
    fd: int | None = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # The file is a permanent shared inode. Never truncate, unlink, or replace it.
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                _error("busy")
                return args.busy_exit_code
            raise

        # PEP 446 makes new descriptors close-on-exec by default. The lock must remain
        # held by the backstop after this process is replaced by the target command.
        os.set_inheritable(fd, True)
        # The re-exec must prove possession of this exact inherited descriptor. A plain
        # boolean is forgeable and would let an unrelated caller bypass the lock helper.
        os.environ.pop("AKB2_BACKSTOP_FLOCK_HELD", None)
        os.environ["AKB2_BACKSTOP_FLOCK_FD"] = str(fd)
        os.execvp(args.command[0], args.command)
    except OSError as exc:
        _error("error")
        _error(f"cannot lock or exec: {exc}")
        return args.error_exit_code
    finally:
        # Reached only on contention or failure; successful exec replaces this process.
        if fd is not None:
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
