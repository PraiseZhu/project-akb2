"""akb-search skill 路由可跑测试（sc-skill-routing）。

live skill 源（仓外绝对路径）：
    ~/AI-Agent/Claude/capabilities/source/skills/claude-active/akb-search/SKILL.md

从 SKILL.md 的 bash 块解析出每条可执行命令（cd 行 + CLI 行），逐条对隔离
夹具 vault 真实执行，断言退出码 0 与 stdout 是含 results 数组的 JSON
（hardening 7：『文档里写了』与『命令真能跑』是两个独立判据，grep 只证前者）。

变异反证（R1-C8 机器化）：mutation_harness 的 skill-routing-broken-path 把
SKILL.md 里的模块路径改成不存在的 akb2.retrieval.broken 后，本测试必须红。

vault 用临时 fixture 构造（与 tests/retrieval/test_readme_examples.py 同口径
的 promoted/staging 样本），不依赖本机 /Volumes/AKB2。

live skill 源是 host-local 真相源（元工作区 ~/AI-Agent/Claude/...），不随本仓
checkout 保证存在——CI runner 与其他开发者机器上必然缺失。整文件按模块级
pytestmark skipif 跳过（而非硬失败），与 tests/harvest/test_anchor_bootstrap.py
的 dual_identity_available() skipif 同一约定。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

# live skill 源是仓外绝对路径（~/.claude/skills symlink 的指向目标）
_SKILL_MD = (
    Path.home()
    / "AI-Agent"
    / "Claude"
    / "capabilities"
    / "source"
    / "skills"
    / "claude-active"
    / "akb-search"
    / "SKILL.md"
)

pytestmark = pytest.mark.skipif(
    not _SKILL_MD.is_file(),
    reason="live skill 源不在本机（host-local 真相源，CI 上必然缺失）",
)

# 已归档 v1 库路径出现在 SKILL.md 任何位置 = 路由回退，直接红
_ARCHIVED_V1_PATH = "archive/projects/Project AKB"

_BASH_BLOCK_RE = re.compile(r"```bash\n(.*?)```", re.DOTALL)


# ---------------------------------------------------------------------------
# fixture vault（promoted/staging 同 tests/retrieval 口径）
# ---------------------------------------------------------------------------

_PROMOTED_NOTE = """---
title: Loop Engineering 黄金样张
page_status: active
status: active
slug: addy-osmani-loop-engineering
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-06-11
---

## TLDR

Loop engineering 是把任务做成受控闭环。

## 论证与解释

正文论证段落，含 loop 与 engineering 关键词。

### 操作步骤

1. 定义验证目标并跑全量测试 `pytest -q tests/`
"""

_STAGING_NOTE = """---
title: Loop Engineering 黄金样张
page_status: candidate
slug: addy-osmani-loop-engineering
proposed_gate_decision: active
---

## 论证与解释

### 操作步骤

1. 草稿 loop engineering `echo draft`
"""


def _make_fixture_vault(base: Path) -> Path:
    """构造含 promoted 正例 + staging 负例的 vault，返回 AKB2.0 子根。"""
    akb2_root = base / "Obsidian" / "Praise Space" / "AKB2.0"
    sources = akb2_root / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "addy-osmani-loop-engineering.md").write_text(
        _PROMOTED_NOTE, encoding="utf-8"
    )
    staging = akb2_root / "staging" / "candidate" / "manual-source-intake-2026-06-11"
    staging.mkdir(parents=True)
    (staging / "addy-osmani-loop-engineering.md").write_text(
        _STAGING_NOTE, encoding="utf-8"
    )
    return akb2_root


# ---------------------------------------------------------------------------
# SKILL.md 命令解析
# ---------------------------------------------------------------------------


def _skill_text() -> str:
    assert _SKILL_MD.is_file(), f"live skill 源不存在: {_SKILL_MD}"
    return _SKILL_MD.read_text(encoding="utf-8")


def _extract_cli_commands(text: str) -> list[str]:
    """提取 bash 块内全部 python3 -m 模块调用行（含前缀赋值，逐字）。

    只认 python3 -m <module> 形态的行——MCP 调用是会话内工具名不是 shell
    命令，不在解析域；cd 行作为块内工作目录指令由执行器单独处理。
    """
    cmds: list[str] = []
    for block in _BASH_BLOCK_RE.findall(text):
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("python3 -m ") or re.match(
                r"^\w+=\S+\s+python3 -m ", stripped
            ):
                cmds.append(stripped)
    return cmds


def _resolve_cmdline(repo_root: Path, cmdline: str) -> str:
    """把命令行里的 ~/ 路径与相对 cwd 解析为可执行形态。

    SKILL.md 的兜底命令假定在 Project AKB2 仓根运行（cd 行声明）；本测试
    把仓根固定为当前 repo（worktree 同构），并将 ~/ 展开为真实 HOME。
    """
    expanded = os.path.expanduser(cmdline)
    # PYTHONPATH=src 是仓根相对值，显式改写为绝对路径注入，避免 cwd 依赖歧义
    m = re.match(r"^PYTHONPATH=(\S+)\s+(.*)$", expanded, re.DOTALL)
    if m:
        pp, rest = m.group(1), m.group(2)
        pp_abs = str((repo_root / pp).resolve()) if not pp.startswith("/") else pp
        return f"PYTHONPATH={pp_abs} {rest}"
    return expanded


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_routed_commands_runnable(repo_root, tmp_path):
    """SKILL.md 里每条可执行命令对隔离夹具 vault 真跑：rc=0 + JSON 含 results。

    反证链：模块路径被变异成不存在的 akb2.retrieval.broken 时，python3 -m
    报 ModuleNotFoundError 退出码 1，本测试红。
    """
    text = _skill_text()

    # 路由健康前置：归档 v1 库引用必须已清除
    assert _ARCHIVED_V1_PATH not in text, (
        f"SKILL.md 仍引用已归档 v1 库路径 {_ARCHIVED_V1_PATH!r}"
    )

    cmds = _extract_cli_commands(text)
    assert cmds, "SKILL.md 未解析出任何可执行命令（路由已漂移成空壳）"
    # 路由必须指向 AKB2 检索入口
    assert any("akb2.retrieval" in c for c in cmds), (
        "SKILL.md 命令未指向 AKB2 检索入口 akb2.retrieval"
    )

    akb2_root = _make_fixture_vault(tmp_path)
    for cmdline in cmds:
        resolved = _resolve_cmdline(repo_root, cmdline)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "AKB2_ROOT": str(akb2_root),
        }
        proc = subprocess.run(
            ["bash", "-c", resolved],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            env=env,
        )
        assert proc.returncode == 0, (
            f"skill 命令跑不通: {cmdline}\nstderr: {proc.stderr}"
        )
        payload = json.loads(proc.stdout)
        assert "results" in payload and isinstance(payload["results"], list), (
            f"输出 JSON 缺 results 数组: {cmdline}\nstdout: {proc.stdout[:500]}"
        )


def test_skill_routes_to_akb2_entry(repo_root):
    """路由静态断言：MCP 工具 akb2-retrieval 优先 + v1 归档库零引用。

    grep 段的测试化镜像：即使命令执行段被绕过，路由指向本身也必须成立。
    """
    text = _skill_text()
    assert _ARCHIVED_V1_PATH not in text
    assert re.search(r"akb2-retrieval|akb2\.retrieval", text), (
        "SKILL.md 未路由到 akb2-retrieval / akb2.retrieval 入口"
    )
