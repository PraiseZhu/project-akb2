"""runbook（docs/runbooks/run-mcp-server.md cc-akb2-retrieval 段）可运行示例测试。

从 runbook bash 块提取 server 启动命令（PYTHONPATH=src python3 + server.py 入口），
逐条真跑：真实拉起 server 子进程，经 stdio JSON-RPC 完成 initialize 握手，
再走 tools/list 与 tools/call，断言真实响应（文档存在 ≠ 命令可跑）。

模式与先例对齐：
- 命令提取/真跑：tests/retrieval/test_readme_examples.py（命令逐字执行，
  vault 用临时 fixture 构造，env 注入子进程，不改写命令行本体）
- 外部依赖缺失时的 fail/skip 契约：tests/quality/test_intake_extract.py
  （AKB2_REQUIRE_DEFUDDLE 先例——CI 禁 skip，本地可 skip）

mcp 不是本工程声明依赖（pyproject 只装 -e ".[dev]"），但本测试只依赖
server 子进程的 stdio JSON-RPC 行为，不需要客户端 import mcp——真包缺失时
server 自身也起不来，故沿用同一契约：AKB2_REQUIRE_MCP=1 时缺 mcp 即失败，
未设时本地 skip（装法指引见 skip 消息本体 _MCP_INSTALL_HINT，非 runbook）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_RUNBOOK = Path("docs/runbooks/run-mcp-server.md")
_SERVER_ENTRY = "toolbox/cc-akb2-retrieval/mcp_server/server.py"

REQUIRE_MCP_ENV = "AKB2_REQUIRE_MCP"
_MCP_INSTALL_HINT = (
    "mcp（FastMCP）不在本工程声明依赖内（pyproject 只装 -e \".[dev]\"）；"
    "本机 pyenv python3 需 pip install mcp 后 server 才能启动。"
)

_BASH_BLOCK_RE = re.compile(r"```bash\n(.*?)```", re.DOTALL)

# server 启动命令：PYTHONPATH 前缀 + python3 + 入口路径。
# prefix 组允许 bash 注释行（# 开头，命令说明）与 env -u 前缀（fail-closed 演示：
# 单个 env 后跟任意多个 -u VAR 对）。
_CMD_RE = re.compile(
    r"^(?P<prefix>(?:#[^\n]*\n(?:#[^\n]*\n)*|\s*)(?:env\s+(?:-u\s+\S+\s+)+)?)"
    r"PYTHONPATH=src\s+python3\s+(?P<entry>\S+)\s*$",
    re.MULTILINE,
)


def _mcp_available() -> bool:
    """server 需要的真 mcp 包是否可 import（只探测，不加载）。"""
    return importlib.util.find_spec("mcp") is not None


def _require_mcp() -> None:
    """缺 mcp 时按契约 fail（CI，AKB2_REQUIRE_MCP=1）或 skip（本地）。"""
    if _mcp_available():
        return
    if os.environ.get(REQUIRE_MCP_ENV) == "1":
        pytest.fail(
            f"mcp 包不在当前解释器（{sys.executable}），而 {REQUIRE_MCP_ENV}=1 "
            f"要求它必须存在。{_MCP_INSTALL_HINT}",
            pytrace=False,
        )
    pytest.skip(_MCP_INSTALL_HINT)


# ---------------------------------------------------------------------------
# fixture vault（promoted/staging 口径与 tests/retrieval/test_engine.py 同构）
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

### 操作步骤

1. 跑全量测试
"""

_STAGING_LOOP = """---
title: Loop Engineering 未升格稿
page_status: candidate
status: candidate
slug: addy-osmani-loop-engineering
gate_decision: null
---

### 操作步骤

1. 草稿 loop engineering
"""


def _make_fixture_vault(base: Path) -> Path:
    """构造含 promoted 正例 + staging 负例的 vault，返回 AKB2.0 子根。"""
    akb2_root = base / "Obsidian" / "Praise Space" / "AKB2.0"
    sources = akb2_root / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "addy-osmani-loop-engineering.md").write_text(
        _PROMOTED_LOOP, encoding="utf-8"
    )
    staging = akb2_root / "staging" / "candidate" / "manual-source-intake-2026-06-11"
    staging.mkdir(parents=True)
    (staging / "addy-osmani-loop-engineering.md").write_text(_STAGING_LOOP, encoding="utf-8")
    return akb2_root


# ---------------------------------------------------------------------------
# runbook 命令提取
# ---------------------------------------------------------------------------


def _runbook_text(repo_root: Path) -> str:
    doc = repo_root / _RUNBOOK
    assert doc.is_file(), f"runbook 不存在: {doc}"
    return doc.read_text(encoding="utf-8")


def _extract_server_commands(repo_root: Path) -> list[dict]:
    """提取 runbook bash 块内的 server 启动命令。

    返回 [{cmdline, entry, env_unsets}]；cmdline 与文档逐字一致。
    """
    cmds: list[dict] = []
    for block in _BASH_BLOCK_RE.findall(_runbook_text(repo_root)):
        for m in _CMD_RE.finditer(block):
            cmds.append(
                {
                    "cmdline": m.group(0).strip(),
                    "entry": m.group("entry"),
                    "env_unsets": re.findall(r"-u\s+(\S+)", m.group("prefix") or ""),
                }
            )
    return cmds


def _server_commands(repo_root: Path) -> list[dict]:
    cmds = _extract_server_commands(repo_root)
    assert cmds, "runbook 未提取到任何 server 启动命令（启动命令段缺失？）"
    return [c for c in cmds if c["entry"] == _SERVER_ENTRY]


# ---------------------------------------------------------------------------
# stdio JSON-RPC 客户端（最小实现）
# ---------------------------------------------------------------------------


def _rpc_frames() -> str:
    """构造握手 + 工具调用帧序列（单次注入 stdin 后收集全部响应）。"""
    return "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "doc-example-test", "version": "0.0.0"},
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "akb2_retrieval_search",
                        "arguments": {"query": "loop engineering", "limit": 5},
                    },
                }
            ),
        ]
    ) + "\n"


def _run_server(
    repo_root: Path,
    cmdline: str,
    *,
    env_extra: dict[str, str],
    env_unsets: list[str],
    stdin_frames: str,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess:
    """按文档命令行逐字执行（env 由测试注入，命令本体不改写）。"""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    env.update(env_extra)
    for name in env_unsets:
        env.pop(name, None)
    # 规矩 stdio 客户端：写完帧后保持 stdin 打开，直到收齐全部带 id 请求的
    # 响应才关流。一次性 input+EOF（subprocess.run）在慢 runner 上会输给
    # server 的 EOF 关停竞态——CI 实证 initialize 有响应、后续请求被丢，
    # 本机快机器则稳定全收。fail-closed 用例（server 起不来）走 EOF 分支。
    expected_ids = {
        payload["id"]
        for line in stdin_frames.splitlines()
        if line.strip()
        for payload in [json.loads(line)]
        if "id" in payload
    }
    proc = subprocess.Popen(
        ["bash", "-c", cmdline],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(repo_root),
        env=env,
    )
    out_lines: list[str] = []
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(stdin_frames)
        proc.stdin.flush()
        got_ids: set[int] = set()
        while got_ids < expected_ids:
            line = proc.stdout.readline()
            if not line:
                break  # server 已退出（如 fail-closed 非零退出）
            out_lines.append(line)
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if "id" in payload:
                got_ids.add(payload["id"])
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
    try:
        rest_out, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        rest_out, stderr = proc.communicate()
    return subprocess.CompletedProcess(
        args=["bash", "-c", cmdline],
        returncode=proc.returncode,
        stdout="".join(out_lines) + (rest_out or ""),
        stderr=stderr or "",
    )


def _parse_responses(stdout: str) -> dict[int, dict]:
    """解析 stdout 每行 JSON-RPC 响应，按 id 索引。"""
    responses: dict[int, dict] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if "id" in payload:
            responses[payload["id"]] = payload
    return responses


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_runbook_server_commands_extracted(repo_root):
    """前置健康检查：runbook 提取到 cc-akb2-retrieval 启动命令（≥1 条）。"""
    _require_mcp()
    cmds = _server_commands(repo_root)
    assert len(cmds) >= 1, (
        "runbook 缺 cc-akb2-retrieval 的权威启动命令"
        f"（应为 PYTHONPATH=src python3 {_SERVER_ENTRY}）"
    )


def test_runbook_startup_command_initializes(repo_root, tmp_path):
    """核心 SC：按文档命令真实拉起 server，完成 initialize 握手。

    断言 serverInfo.name=akb2-retrieval（server 身份）、protocolVersion 回显、
    tools capability 存在。
    """
    _require_mcp()
    akb2_root = _make_fixture_vault(tmp_path)
    (cmd,) = [c for c in _server_commands(repo_root) if not c["env_unsets"]]
    proc = _run_server(
        repo_root,
        cmd["cmdline"],
        env_extra={"AKB2_ROOT": str(akb2_root)},
        env_unsets=[],
        stdin_frames=_rpc_frames(),
    )
    assert proc.returncode == 0, f"server 进程异常退出: {proc.stderr[-800:]}"
    responses = _parse_responses(proc.stdout)
    assert 1 in responses, f"未收到 initialize 响应，stdout: {proc.stdout[:500]}"
    result = responses[1]["result"]
    assert result["serverInfo"]["name"] == "akb2-retrieval", result["serverInfo"]
    assert result["protocolVersion"] == "2024-11-05"
    assert "tools" in result["capabilities"]
    # instructions 含只读定位（与 server.py instructions 一致）
    assert "只读" in result["instructions"]


def test_runbook_command_tools_list_and_call(repo_root, tmp_path):
    """握手后 tools/list 只含 akb2_retrieval_search；tools/call 返回 promoted 结果。"""
    _require_mcp()
    akb2_root = _make_fixture_vault(tmp_path)
    cmds = [c for c in _server_commands(repo_root) if not c["env_unsets"]]
    cmd = cmds[0]
    proc = _run_server(
        repo_root,
        cmd["cmdline"],
        env_extra={"AKB2_ROOT": str(akb2_root)},
        env_unsets=[],
        stdin_frames=_rpc_frames(),
    )
    assert proc.returncode == 0, f"server 进程异常退出: {proc.stderr[-800:]}"
    responses = _parse_responses(proc.stdout)
    assert 2 in responses and 3 in responses, (
        f"未收到 tools/list 或 tools/call 响应，stdout: {proc.stdout[:500]}"
    )

    tools = responses[2]["result"]["tools"]
    assert [t["name"] for t in tools] == ["akb2_retrieval_search"], tools
    props = tools[0]["inputSchema"]["properties"]
    assert set(props) == {"query", "limit"}, props

    call = responses[3]["result"]
    assert not call["isError"]
    (content,) = call["content"]
    assert content["type"] == "text"
    inner = json.loads(content["text"])
    assert inner["query"] == "loop engineering"
    assert inner["total_results"] == 1
    (hit,) = inner["results"]
    assert hit["path"] == "wiki/sources/addy-osmani-loop-engineering.md"
    assert hit["title"] == "Loop Engineering 黄金样张"
    # staging 负例永不入结果（唯一命中是 promoted 正例）
    assert not any("staging" in r["path"] for r in inner["results"])


def test_runbook_fail_closed_command_without_env(repo_root):
    """文档 fail-closed 命令（env -u 三连）：非零退出 + stdout 无响应 + stderr 报错。"""
    _require_mcp()
    fc_cmds = [c for c in _server_commands(repo_root) if c["env_unsets"]]
    assert fc_cmds, (
        "runbook 缺 fail-closed 演示命令"
        "（env -u AKB2_ROOT -u AKB2_VAULT_ROOT -u VAULT_ROOT 前缀）"
    )
    proc = _run_server(
        repo_root,
        fc_cmds[0]["cmdline"],
        env_extra={},
        env_unsets=fc_cmds[0]["env_unsets"],
        stdin_frames=_rpc_frames(),
    )
    assert proc.returncode != 0, "缺 vault env 时 server 必须非零退出（fail-closed）"
    assert not proc.stdout.strip(), "fail-closed 时不得输出任何 JSON-RPC 响应"
    assert "fail-closed" in proc.stderr, f"stderr 应含 fail-closed 报错: {proc.stderr[-500:]}"


def test_runbook_examples_are_not_hallucinated(repo_root, tmp_path):
    """runbook 内每一条 cc-akb2-retrieval 启动命令都真实可跑。

    覆盖挑跑的死角：不是只跑核心握手那条，而是文档里每一条该 server 的
    命令（含 fail-closed 演示）都真实执行；预期非零退出的单独断言，
    其余必须 rc=0 且完成 initialize 握手。
    """
    _require_mcp()
    akb2_root = _make_fixture_vault(tmp_path)
    for cmd in _server_commands(repo_root):
        proc = _run_server(
            repo_root,
            cmd["cmdline"],
            env_extra={"AKB2_ROOT": str(akb2_root)},
            env_unsets=cmd["env_unsets"],
            stdin_frames=_rpc_frames(),
        )
        if cmd["env_unsets"]:
            assert proc.returncode != 0, f"应 fail-closed: {cmd['cmdline']}"
            assert "fail-closed" in proc.stderr
        else:
            assert proc.returncode == 0, (
                f"文档命令跑不通: {cmd['cmdline']}\nstderr: {proc.stderr[-500:]}"
            )
            responses = _parse_responses(proc.stdout)
            assert (
                responses[1]["result"]["serverInfo"]["name"] == "akb2-retrieval"
            ), "文档命令拉起的 server 身份必须是 akb2-retrieval"


def test_server_entry_tracked_in_git(repo_root):
    """文档指向的入口必须真实存在且被 git 跟踪（防路径漂移）。"""
    entry = repo_root / _SERVER_ENTRY
    assert entry.is_file(), f"server 入口不存在: {entry}"
    tracked = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", _SERVER_ENTRY],
        capture_output=True,
        text=True,
    )
    assert tracked.stdout.strip() == _SERVER_ENTRY, f"server 入口未被 git 跟踪: {entry}"
