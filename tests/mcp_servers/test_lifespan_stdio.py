"""sc-mcp-lifespan-stdio：FastMCP 生命周期与 stdio 通道语义。

真实子进程拉起 server（python server.py，stdio 传输），断言：
  1. lifespan 进入/退出各恰一次 —— started/stopped 日志行各恰一行
     （stderr；外部可观察，不引入第二工具破坏「唯一工具」契约）
  2. 退出后无残留 asyncio 任务（驱动侧事件循环 all_tasks 为空）
  3. stdout 全程只有 JSON-RPC 消息（mcp 1.26 stdio = newline-delimited
     JSON；任何 print/日志/警告污染 stdout 即红）
  4. client 关闭（stdin EOF）后进程在超时预算内自行退出且退出码为 0

stdout 洁净是 stdio 传输硬约束——日志必须走 stderr（server.py 已配置
logging.basicConfig(stream=sys.stderr)，本测试是该配置的回归守卫）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "toolbox" / "cc-akb2-retrieval" / "mcp_server" / "server.py"

# 具名预算（constitution P4）
SERVER_EXIT_TIMEOUT_SEC = 15.0

pytest.importorskip("mcp.client.stdio")

_STARTED_RE = re.compile(
    r"AKB2 Retrieval Readonly MCP Server started", re.IGNORECASE
)
_STOPPED_RE = re.compile(
    r"AKB2 Retrieval Readonly MCP Server stopped", re.IGNORECASE
)


@pytest.fixture()
def vault_env(tmp_path: Path) -> dict[str, str]:
    """fixture vault（AKB2_ROOT 直指归一子根）+ 隔离 env。"""
    root = tmp_path / "AKB2.0"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "lifespan-probe.md").write_text(
        """---
title: Lifespan Probe
page_status: active
status: active
slug: lifespan-probe
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-01-01
---

## 论证与解释

lifespan stdio token。

### 操作步骤

1. 步骤 `echo lifespan`
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    # AKB2_ROOT 直接指到 AKB2.0 子根（目录名以 AKB2.0 结尾，server 不再追加
    # Praise Space/AKB2.0 归一段）；屏蔽外层 pytest 的 VAULT_ROOT 污染
    env["AKB2_ROOT"] = str(root)
    env.pop("VAULT_ROOT", None)
    env.pop("AKB2_VAULT_ROOT", None)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return env


def test_stdio_roundtrip_official_client(vault_env) -> None:
    """官方 stdio client：握手 + 检索 + 唯一工具 + 无残留 asyncio 任务。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
        env=vault_env,
    )

    async def _drive():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "akb2_retrieval_search",
                    {"query": "lifespan stdio", "limit": 3},
                )
                tools = await session.list_tools()
                return (
                    _first_text(result),
                    [t.name for t in tools.tools],
                )

    loop = asyncio.new_event_loop()
    try:
        search_text, tool_names = loop.run_until_complete(_drive())
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

    assert "lifespan-probe.md" in search_text, search_text[:200]
    assert tool_names == ["akb2_retrieval_search"], tool_names
    assert pending == [], f"驱动侧事件循环残留任务: {[t.get_name() for t in pending]}"


def test_lifecycle_once_and_clean_exit_via_stderr(vault_env) -> None:
    """自管子进程（行协议驱动）：started/stopped 各恰一行；EOF 后 rc=0。"""
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PATH)],
        env=vault_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _send_line(proc.stdin, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "probe", "version": "1.0"},
            },
        })
        assert _read_json_line(proc.stdout)["id"] == 1
        _send_line(proc.stdin, {
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        _send_line(proc.stdin, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "akb2_retrieval_search",
                "arguments": {"query": "lifespan stdio", "limit": 2},
            },
        })
        tool_resp = _read_json_line(proc.stdout)
        assert tool_resp["id"] == 2
        assert "lifespan-probe.md" in json.dumps(tool_resp, ensure_ascii=False)

        proc.stdin.close()
        rc = proc.wait(timeout=SERVER_EXIT_TIMEOUT_SEC)
        stdout_rest = proc.stdout.read().decode("utf-8", errors="replace")
        stderr_all = proc.stderr.read().decode("utf-8", errors="replace")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    # 1. lifespan 进入/退出各恰一次（stderr 日志行计数）
    assert len(_STARTED_RE.findall(stderr_all)) == 1, stderr_all[:400]
    assert len(_STOPPED_RE.findall(stderr_all)) == 1, stderr_all[:400]

    # 4. 自行退出且 rc 0
    assert rc == 0, f"server 退出码 {rc}；stderr: {stderr_all[:400]}"

    # 3. stdout 洁净：EOF 前后全部输出都是逐行 JSON（污染即在此暴露）
    if stdout_rest.strip():
        _assert_json_lines(stdout_rest)


def test_stdout_pure_json_lines_under_traffic(vault_env) -> None:
    """连续多请求下 stdout 逐行可解析（污染面回归守卫）。"""
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PATH)],
        env=vault_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _send_line(proc.stdin, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "probe", "version": "1.0"},
            },
        })
        assert _read_json_line(proc.stdout)["id"] == 1
        _send_line(proc.stdin, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        for i in range(2, 6):
            _send_line(proc.stdin, {
                "jsonrpc": "2.0", "id": i, "method": "tools/call",
                "params": {
                    "name": "akb2_retrieval_search",
                    "arguments": {"query": "lifespan stdio", "limit": 1},
                },
            })
            assert _read_json_line(proc.stdout)["id"] == i
        proc.stdin.close()
        rc = proc.wait(timeout=SERVER_EXIT_TIMEOUT_SEC)
        tail = proc.stdout.read().decode("utf-8", errors="replace")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    assert rc == 0
    if tail.strip():
        _assert_json_lines(tail)


# ---------------------------------------------------------------------------
# helpers：newline-delimited JSON（mcp 1.26 stdio 传输的实际分帧形态）
# ---------------------------------------------------------------------------


def _send_line(stdin, payload: dict) -> None:
    stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    stdin.flush()


def _read_json_line(stdout) -> dict:
    line = stdout.readline()
    assert line, "server 提前关闭 stdout"
    return json.loads(line.decode("utf-8"))


def _first_text(result) -> str:
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            return text
    raise AssertionError(f"工具结果无 text content: {result}")


def _assert_json_lines(raw: str) -> None:
    """stdout 输出必须逐行是可解析 JSON-RPC 消息。"""
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert lines, "空输出无需断言（由调用侧 guard）"
    for ln in lines:
        payload = json.loads(ln)
        assert isinstance(payload, dict) and "jsonrpc" in payload, (
            f"stdout 非 JSON-RPC 行（print/日志污染）: {ln[:120]!r}"
        )
