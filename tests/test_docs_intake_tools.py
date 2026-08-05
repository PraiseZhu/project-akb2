"""校验 bootstrap_intake_launchd.sh 的 defuddle 缺失提示指向正确的安装命令。

回归背景:该提示曾写 `pip install defuddle`——defuddle 是 npm 包提供的外部 CLI,
pip 装不到,运维照做只会继续卡在 preflight。见 docs/runbooks/intake-tools-setup.md §6。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "bootstrap_intake_launchd.sh"
RUNBOOK_PATH = REPO_ROOT / "docs" / "runbooks" / "intake-tools-setup.md"


def _defuddle_preflight_block(script_text: str) -> str:
    """提取脚本里 `defuddle CLI 不可用` 报错分支的完整 if 块正文。"""
    match = re.search(
        r"if ! command -v defuddle.*?\n(.*?)\nfi\n",
        script_text,
        re.DOTALL,
    )
    assert match, "脚本里找不到 defuddle 缺失检测的 if 块——preflight 结构被改动了"
    return match.group(1)


def test_defuddle_hint_points_to_npm_cli(tmp_path: Path) -> None:
    script_text = SCRIPT_PATH.read_text(encoding="utf-8")
    block = _defuddle_preflight_block(script_text)

    assert "pip install defuddle" not in block, (
        "defuddle 是 npm 包提供的外部 CLI,不是 pip 包——回归了错误的安装命令"
    )
    assert "npm install -g defuddle-cli@0.7.0" in block, (
        "缺失提示必须给出仓库钉死的正确安装命令 npm install -g defuddle-cli@0.7.0"
    )
    assert "defuddle@0.1.0" in block, (
        "缺失提示必须保留包身份陷阱警告(同名 defuddle@0.1.0 没有 bin,"
        "不能按 --version 打印的 0.1.0 去装它),否则运维会重新装错包"
    )

    runbook_text = RUNBOOK_PATH.read_text(encoding="utf-8")
    assert "npm install -g defuddle-cli@0.7.0" in runbook_text, (
        "runbook 记载的安装命令应与脚本提示一致(单一权威来源)"
    )

    # 动态验证:真实跑脚本、真实缺失 defuddle 时,preflight 打印的是修复后的提示,
    # 而不是只在源码字符串层面对上——防止改动没有落在真正被执行的分支里。
    real_defuddle = shutil.which("defuddle")
    fake_path_dir = tmp_path / "path-without-defuddle"
    fake_path_dir.mkdir()
    # 把 PATH 里除 defuddle 所在目录外的其它目录原样保留(脚本还要用到 bash/mkdir/等)。
    filtered_path_entries = [
        p
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if not (real_defuddle and Path(p) == Path(real_defuddle).parent)
    ]
    fake_path = os.pathsep.join([str(fake_path_dir), *filtered_path_entries])

    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    env = dict(os.environ)
    env["PATH"] = fake_path
    env["VAULT_ROOT"] = str(vault_root)
    env["AKB2_ALLOW_WORKTREE_INSTALL"] = "1"
    env["AKB2_ALLOW_TMP_VAULT_ROOT"] = "1"

    assert shutil.which("defuddle", path=fake_path) is None, (
        "测试环境构造失败:过滤后的 PATH 里仍能找到 defuddle,preflight 不会触发缺失分支"
    )

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 3, (
        f"preflight 应在 defuddle 缺失时 fail-closed(exit 3),实际 returncode="
        f"{result.returncode}\nstderr:\n{result.stderr}"
    )
    assert "pip install defuddle" not in result.stderr
    assert "npm install -g defuddle-cli@0.7.0" in result.stderr
    assert "defuddle@0.1.0" in result.stderr
