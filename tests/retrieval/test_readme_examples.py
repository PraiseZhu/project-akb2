"""runbook（docs/runbooks/retrieval-interface.md）可运行示例测试。

从 runbook 提取所有 bash 块内以 PYTHONPATH=src python3 -m akb2.retrieval.cli
开头的命令行，逐条交给 bash 执行并断言真实输出（hardening 7：按文档操作
必须真能跑，文档存在 ≠ 命令可跑）。

vault 用临时 fixture 构造（与 test_engine.py 同口径的 promoted/staging
样本），不依赖本机 /Volumes/AKB2；vault 根经 AKB2_ROOT env 注入子进程，
命令行本体与文档逐字一致（不注入、不改写）。

场景覆盖表 _SCENARIOS 与 runbook §3 的命令行一一对齐——文档改命令而不改
测试会红；测试加了场景而文档没命令也会红（零漂移双向锁定）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

_RUNBOOK = Path("docs/runbooks/retrieval-interface.md")
_CMD_PREFIX = "PYTHONPATH=src python3 -m akb2.retrieval.cli"

_BASH_BLOCK_RE = re.compile(r"```bash\n(.*?)```", re.DOTALL)

# 只认命令行首缀（排除 export 行与续行）；文档里的命令必须单行完整
_CMD_RE = re.compile(rf"^{re.escape(_CMD_PREFIX)}\s+(.+)$", re.MULTILINE)

# ---------------------------------------------------------------------------
# fixture vault（promoted/staging 同 test_engine.py 口径）
# ---------------------------------------------------------------------------

_PROMOTED_LOOP = """---
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

正文论证段落，含 loop 与 engineering 关键词，也是 retrieval 工程实践一例。

### 操作步骤

1. 定义验证目标并跑全量测试 `pytest -q tests/`

## 适用边界

适用于可机器验证的工程 loop。
"""

_STAGING_LOOP = """---
title: Loop Engineering 黄金样张
page_status: candidate
status: candidate
slug: addy-osmani-loop-engineering
gate_decision: null
proposed_gate_decision: active
---

## 论证与解释

### 操作步骤

1. 草稿 loop engineering `echo draft`
"""

_PROMOTED_MCP = """---
title: MCP 只读 Server 模式
page_status: active
status: active
slug: mcp-readonly-server
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-07-01
---

## TLDR

只读 mcp server 是零写零网络契约的检索接口。

## 论证与解释

retrieval 工程实践：server 侧关键词检索。

### 操作步骤

1. 冒烟验证 `python3 -m akb2.retrieval.cli search mcp`
"""


def _make_fixture_vault(base: Path) -> Path:
    """构造含 promoted 正例 + staging 负例的 vault，返回 AKB2.0 子根。"""
    akb2_root = base / "Obsidian" / "Praise Space" / "AKB2.0"
    sources = akb2_root / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "addy-osmani-loop-engineering.md").write_text(
        _PROMOTED_LOOP, encoding="utf-8"
    )
    (sources / "mcp-readonly-server.md").write_text(_PROMOTED_MCP, encoding="utf-8")
    staging = akb2_root / "staging" / "candidate" / "manual-source-intake-2026-06-11"
    staging.mkdir(parents=True)
    (staging / "addy-osmani-loop-engineering.md").write_text(
        _STAGING_LOOP, encoding="utf-8"
    )
    return akb2_root


# ---------------------------------------------------------------------------
# runbook 命令提取
# ---------------------------------------------------------------------------


def _extract_runbook_commands(repo_root: Path) -> list[str]:
    """提取 runbook bash 块内全部 CLI 命令行（含前缀，逐字）。"""
    doc = repo_root / _RUNBOOK
    text = doc.read_text(encoding="utf-8")
    cmds: list[str] = []
    for block in _BASH_BLOCK_RE.findall(text):
        for m in _CMD_RE.finditer(block):
            cmds.append(f"{_CMD_PREFIX} {m.group(1).strip()}")
    return cmds


def _run_bash(repo_root: Path, cmdline: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    """经 bash -c 执行文档命令行本体（逐字，含 PYTHONPATH 前缀展开）。"""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    env.update(env_extra)
    return subprocess.run(
        ["bash", "-c", cmdline],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env=env,
    )


def _json_stdout(proc: subprocess.CompletedProcess) -> dict:
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# 场景覆盖表：与 runbook §3 命令行一一对齐
# ---------------------------------------------------------------------------

# key = 场景名（报错定位用）；断言函数收到 (proc, akb2_root_env)
_SCENARIOS: dict[str, list[str]] = {
    # §3.1 用法：--help 两条 + §3.2-3.5 检索命令，退出码 0
    "help_top": ["--help"],
    "help_search": ["search", "--help"],
    # §3.2 基本检索：命中 1 条 + 契约字段
    "search_basic": ["search", "loop engineering", "--limit", "5",
                     "--output", "json", "--vault-root", '"$AKB2_ROOT"'],
    # §3.3 无命中：count 0 / results []
    "search_no_hit": ["search", "zzz-wubqi-nohit", "--vault-root", '"$AKB2_ROOT"'],
    # §3.4 limit 截断：retrieval 命中两篇但 limit=1 只回一条
    "search_limit": ["search", "retrieval", "--limit", "1",
                     "--vault-root", '"$AKB2_ROOT"'],
    # §3.5 env 解析根（命令行无 --vault-root）
    "search_env_root": ["search", "mcp", "--limit", "3"],
    # §3.6 不支持格式：退出码 2
    "bad_output_format": ["search", "loop engineering", "--output", "yaml",
                          "--vault-root", '"$AKB2_ROOT"'],
    # §3.7 未知子命令：退出码 2
    "unknown_subcommand": ["frobnicate"],
}


def _assert_help_ok(proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == 0, f"--help 失败: rc={proc.returncode} err={proc.stderr}"
    assert "search" in proc.stdout


def _assert_json_contract(payload: dict, *, query: str, limit: int) -> dict:
    assert set(payload) == {"query", "limit", "count", "results"}
    assert payload["query"] == query
    assert payload["limit"] == limit
    assert payload["count"] == len(payload["results"])
    return payload


def _assert_result_fields(hit: dict) -> None:
    assert set(hit) == {
        "path",
        "title",
        "how_density",
        "how_executability",
        "score",
        "related_concepts",
        "graph_links",
    }


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_runbook_commands_extracted(repo_root):
    """前置健康检查：runbook 至少提取出覆盖表全部场景的命令。"""
    cmds = _extract_runbook_commands(repo_root)
    assert len(cmds) >= len(_SCENARIOS), (
        f"runbook 提取到 {len(cmds)} 条命令，少于场景表 {len(_SCENARIOS)}"
    )
    # 每个场景的关键词都必须出现在文档命令里（文档改命令不改测试 → 红）
    joined = "\n".join(cmds)
    for name, args in _SCENARIOS.items():
        # 场景标识词：args 里第一个非常量 token 或最后一条路径 token
        marker = _scenario_marker(args)
        assert marker in joined, f"runbook 缺场景 {name} 的命令（找标识 {marker!r}）"


def _scenario_marker(args: list[str]) -> str:
    if args == ["--help"]:
        return "--help"
    if args[:2] == ["search", "--help"]:
        return "search --help"
    if args[0] == "frobnicate":
        return "frobnicate"
    # 检索命令：用 引号包裹的查询词 组合当标识
    return f'"{args[1]}"'


def test_runbook_help_commands_run(repo_root, tmp_path):
    """§3.1 两条 --help：退出码 0，用法文本含 search。"""
    akb2_root = _make_fixture_vault(tmp_path)
    cmds = _extract_runbook_commands(repo_root)
    help_cmds = [c for c in cmds if c.rstrip().endswith("--help")]
    assert len(help_cmds) >= 2, "runbook 必须含 top-level 与 search 两条 --help"
    for cmdline in help_cmds:
        proc = _run_bash(repo_root, cmdline, {"AKB2_ROOT": str(akb2_root)})
        _assert_help_ok(proc)


def test_runbook_basic_search_output_contract(repo_root, tmp_path):
    """§3.2 基本检索：真实输出满足 §5 契约（顶层字段 + 单条固定字段）。"""
    akb2_root = _make_fixture_vault(tmp_path)
    cmds = _extract_runbook_commands(repo_root)
    target = next(c for c in cmds if '"loop engineering" --limit 5' in c)
    proc = _run_bash(repo_root, target, {"AKB2_ROOT": str(akb2_root)})
    assert proc.returncode == 0, f"命令失败: {proc.stderr}"
    payload = _assert_json_contract(_json_stdout(proc), query="loop engineering", limit=5)
    assert payload["count"] >= 1
    hit = payload["results"][0]
    _assert_result_fields(hit)
    assert hit["path"].startswith("wiki/sources/")
    assert hit["path"].endswith(".md")
    assert hit["title"]
    assert isinstance(hit["score"], int) and hit["score"] > 0
    assert isinstance(hit["how_executability"], list)


def test_runbook_no_hit_is_valid_zero(repo_root, tmp_path):
    """§3.3 无命中：count 为零、results 为空、退出码 0（合法空结果）。"""
    akb2_root = _make_fixture_vault(tmp_path)
    cmds = _extract_runbook_commands(repo_root)
    target = next(c for c in cmds if "zzz-wubqi-nohit" in c)
    proc = _run_bash(repo_root, target, {"AKB2_ROOT": str(akb2_root)})
    assert proc.returncode == 0
    payload = _json_stdout(proc)
    assert payload["count"] == 0
    assert payload["results"] == []


def test_runbook_limit_truncates(repo_root, tmp_path):
    """§3.4 --limit 1：fixture 中 retrieval 可命中两篇，但只返回一条。"""
    akb2_root = _make_fixture_vault(tmp_path)
    cmds = _extract_runbook_commands(repo_root)
    target = next(c for c in cmds if '"retrieval" --limit 1' in c)
    proc = _run_bash(repo_root, target, {"AKB2_ROOT": str(akb2_root)})
    assert proc.returncode == 0
    payload = _json_stdout(proc)
    assert payload["limit"] == 1
    assert payload["count"] == 1
    assert len(payload["results"]) == 1


def test_runbook_env_root_resolution(repo_root, tmp_path):
    """§3.5 不传 --vault-root：经 AKB2_ROOT env 解析（paths.py 契约）。"""
    akb2_root = _make_fixture_vault(tmp_path)
    cmds = _extract_runbook_commands(repo_root)
    target = next(c for c in cmds if '"mcp" --limit 3' in c)
    proc = _run_bash(repo_root, target, {"AKB2_ROOT": str(akb2_root)})
    assert proc.returncode == 0, proc.stderr
    payload = _json_stdout(proc)
    assert payload["count"] >= 1
    assert any("mcp-readonly-server.md" in r["path"] for r in payload["results"])


def test_runbook_unsupported_output_fails_closed(repo_root, tmp_path):
    """§3.6 --output yaml：退出码 2，不静默降级。"""
    akb2_root = _make_fixture_vault(tmp_path)
    cmds = _extract_runbook_commands(repo_root)
    target = next(c for c in cmds if "--output yaml" in c)
    proc = _run_bash(repo_root, target, {"AKB2_ROOT": str(akb2_root)})
    assert proc.returncode == 2
    assert "invalid choice" in proc.stderr
    # 失败时不得产出半截 JSON
    assert not proc.stdout.strip()


def test_runbook_unknown_subcommand_fails_closed(repo_root, tmp_path):
    """§3.7 未知子命令：退出码 2。"""
    akb2_root = _make_fixture_vault(tmp_path)
    cmds = _extract_runbook_commands(repo_root)
    target = next(c for c in cmds if c.endswith(" frobnicate"))
    proc = _run_bash(repo_root, target, {"AKB2_ROOT": str(akb2_root)})
    assert proc.returncode == 2
    assert "invalid choice" in proc.stderr


def test_runbook_examples_are_not_hallucinated(repo_root, tmp_path):
    """文档命令可提取且逐条可跑：全量命令在 fixture 环境下无一条报 shell 错。

    覆盖 readme_examples 的死角：不是挑几条跑，而是 runbook 里每一条 CLI
    命令都真实执行；预期失败的（退出码 2）单独断言，其余必须 rc=0。
    """
    akb2_root = _make_fixture_vault(tmp_path)
    cmds = _extract_runbook_commands(repo_root)
    assert cmds, "runbook 未提取到任何 CLI 命令"
    expected_fail = {
        c for c in cmds
        if "--output yaml" in c or c.rstrip().endswith(" frobnicate")
    }
    for cmdline in cmds:
        proc = _run_bash(repo_root, cmdline, {"AKB2_ROOT": str(akb2_root)})
        if cmdline in expected_fail:
            assert proc.returncode == 2, f"应 fail-closed: {cmdline}"
        else:
            assert proc.returncode == 0, (
                f"文档命令跑不通: {cmdline}\nstderr: {proc.stderr}"
            )
