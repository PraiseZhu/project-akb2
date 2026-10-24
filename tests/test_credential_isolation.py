"""测试会话拿不到真实飞书凭证，也不弹 macOS 通知（2026-10-24 事故回归）。

事故：在 Mini 上跑全量单测，兜底脚本用例继承了真实 HOME，子进程 feishu_sync.sh
source 了 ~/.akb2-secrets.sh，用真实机器人给主人私信发了约 20 条假告警。
"""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
from pathlib import Path


def _real_home() -> Path:
    # 不看 HOME 环境变量：它正是被隔离的对象
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def test_subprocess_cannot_reach_real_secrets_file():
    # 与 feishu_sync.sh / daily_briefing.sh / inflow_sentinel.sh 的查找写法一致
    result = subprocess.run(
        ["bash", "-c", 'printf %s "${HOME:-}/.akb2-secrets.sh"'],
        capture_output=True,
        text=True,
        check=True,
    )
    secrets = Path(result.stdout)
    assert secrets != _real_home() / ".akb2-secrets.sh"
    assert not secrets.exists()


def test_python_expanduser_cannot_reach_real_secrets_file():
    # feishu_sync/config.py 的 secrets_file() 缺省走 expanduser
    assert Path(os.path.expanduser("~/.akb2-secrets.sh")) != _real_home() / ".akb2-secrets.sh"


def test_osascript_resolves_to_noop_stub():
    resolved = shutil.which("osascript")
    assert resolved is not None
    assert not resolved.startswith("/usr/bin/")
    result = subprocess.run(
        ["osascript", "-e", 'display notification "x" with title "y"'],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ""
