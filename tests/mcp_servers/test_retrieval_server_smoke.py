"""sc-mcp-readonly-smoke：stdio 冒烟 + 只读契约 + 真变异。

用 MCP stdio client 对 server 真实握手（initialize → list_tools →
调一次检索工具），全程前后 fixture vault 树 hash 与索引/DB 文件字节
不变；反向变异（mcp-readonly-open）复用 scripts/mutation_harness.py，
不另写第二套夹具（verify 命令机器化执行）。

promoted 样张口径与 tests/retrieval/test_engine.py 同构。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "toolbox" / "cc-akb2-retrieval" / "mcp_server" / "server.py"

pytest.importorskip("mcp.client.stdio")

_PROMOTED = """---
title: Smoke Probe
page_status: active
status: active
slug: smoke-probe
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-01-01
---

## 论证与解释

smoke readonly token。

### 操作步骤

1. 步骤 `echo smoke`
"""


def _make_vault(tmp_path: Path) -> Path:
    root = tmp_path / "AKB2.0"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "smoke-probe.md").write_text(_PROMOTED, encoding="utf-8")
    # 索引/DB 类文件（只读观察面内的既有文件——检索不得改其字节）
    (sources / "00-Index.md").write_text("---\ntitle: 索引\n---\n", encoding="utf-8")
    (root / ".akb-index.db").write_bytes(b"\x00\x01\x02stub")
    (root / "wiki" / ".search-cache.json").write_text("{}", encoding="utf-8")
    return root


def _tree_fingerprint(root: Path) -> dict[str, tuple[int, int, str]]:
    fp: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            stat = p.stat()
            fp[p.relative_to(root).as_posix()] = (
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
    return fp


@pytest.fixture()
def smoke_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    root = _make_vault(tmp_path)
    env = os.environ.copy()
    env["AKB2_ROOT"] = str(root)
    env.pop("VAULT_ROOT", None)
    env.pop("AKB2_VAULT_ROOT", None)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return env, root


def test_smoke_no_write(smoke_env) -> None:
    """真实 stdio 握手 + 检索调用前后，vault 树指纹逐字节不变。

    变异反证：mcp-readonly-open 把 server 数据面改为写入后，
    本测试必红（verify 命令的 --predicted-red 唯一条目）。
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env, root = smoke_env
    before = _tree_fingerprint(root)

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
        env=env,
    )

    async def _drive():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool(
                    "akb2_retrieval_search",
                    {"query": "smoke readonly", "limit": 3},
                )
                return [t.name for t in tools.tools], _first_text(result)

    loop = asyncio.new_event_loop()
    try:
        tool_names, search_text = loop.run_until_complete(_drive())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

    # stdio 协议面：唯一工具 + 真实命中 promoted 正例（防零结果假绿）
    assert tool_names == ["akb2_retrieval_search"], tool_names
    assert "smoke-probe.md" in search_text, search_text[:200]
    payload = json.loads(search_text)
    hit = payload["results"][0]
    # 结果含 how_density / how_executability 契约字段（SC holds 明文）
    assert set(hit) >= {"path", "title", "how_density", "how_executability"}
    assert "echo smoke" in hit["how_executability"]

    after = _tree_fingerprint(root)
    assert before == after, (
        f"MCP 检索后 vault 字节级变化: "
        f"新增={sorted(set(after) - set(before))} "
        f"变化={[k for k in before if k in after and before[k] != after[k]]}"
    )


def test_smoke_handshake_shape(smoke_env) -> None:
    """握手协议面：initialize 返回 serverInfo.name=akb2-retrieval。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env, _root = smoke_env
    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER_PATH)], env=env
    )

    async def _drive():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                return init.serverInfo.name, init.protocolVersion

    loop = asyncio.new_event_loop()
    try:
        name, _proto = loop.run_until_complete(_drive())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
    assert name == "akb2-retrieval", name


def _first_text(result) -> str:
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            return text
    raise AssertionError(f"工具结果无 text content: {result}")
