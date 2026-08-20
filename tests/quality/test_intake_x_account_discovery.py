"""x1-shared-constants：共享常量单一定义点 + l0 re-export 等值。

g6 会往本文件追加 x_account_discovery collector / content-path 用例；本文件
这一轮只覆盖 `shared_constants_reexport`（-k 过滤），不实现 discovery。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from akb2.briefing.intake import l0_collector as l0
from akb2.shared import twitter_cli_constants as shared

_REEXPORT_NAMES = (
    "UPSTREAM_FATAL_SIGNATURES",
    "UPSTREAM_FATAL_FAILFAST_STREAK",
    "UPSTREAM_HARD_FAILURE_FAILFAST_STREAK",
    "HARD_FAILURE_TIMEOUT",
    "HARD_FAILURE_BAD_OUTPUT",
    "_twitter_cli_to_akb2",
)


def test_shared_constants_reexport_identity() -> None:
    """akb2.shared 与 briefing l0_collector 的签名/阈值/适配器是同一对象。"""
    for name in _REEXPORT_NAMES:
        assert getattr(shared, name) is getattr(l0, name), name
    assert shared.twitter_cli_to_akb2 is l0._twitter_cli_to_akb2
    assert shared.twitter_cli_to_akb2 is shared._twitter_cli_to_akb2


def test_shared_constants_reexport_quality_import_skips_briefing() -> None:
    """quality 侧 import 共享常量不得把 akb2.briefing 拖进 sys.modules。"""
    repo = Path(__file__).resolve().parents[2]
    script = (
        "import sys\n"
        "from akb2.shared.twitter_cli_constants import UPSTREAM_FATAL_SIGNATURES\n"
        "assert UPSTREAM_FATAL_SIGNATURES\n"
        "loaded = [\n"
        "    n for n in sys.modules\n"
        "    if n == 'akb2.briefing' or n.startswith('akb2.briefing.')\n"
        "]\n"
        "assert not loaded, loaded\n"
    )
    env = os.environ.copy()
    src = str(repo / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo),
    )
    assert result.returncode == 0, result.stdout + result.stderr
