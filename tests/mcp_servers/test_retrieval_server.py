"""tests for toolbox/cc-akb2-retrieval/mcp_server/server.py（sc-mcp-server）。

双轨策略（与先例 tests/test_mcp_server.py 的纯 AST 不同，这里加行为轨）：

1. AST 契约轨：静态验证 import 边界（只允许 akb2.retrieval）、router=None、
   FastMCP 入口、唯一工具名 —— 不依赖 mcp 包安装。
2. 行为轨：mcp 不是本工程声明依赖（CI 只装 -e ".[dev]"），且验证命令要求
   零 skip —— 故不能 pytest.importorskip。做法：向 sys.modules 注入
   FastMCP/Context stub（签名与真包一致：tool 装饰器登记函数名），再以
   importlib 按 file location 加载 server.py，断言其真实注册行为。
   本机装有真 mcp 时同样适用（stub 只覆盖 mcp.server.fastmcp 一个入口，
   server.py 未用其他 mcp 符号 —— 由 AST 轨 test_server_imports_only_fastmcp 保证）。

fixture vault 复用 tests/retrieval/test_engine.py 的 promoted 样张口径。
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 路径与源码 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server_path(repo_root: Path) -> Path:
    src = repo_root / "toolbox" / "cc-akb2-retrieval" / "mcp_server" / "server.py"
    assert src.exists(), f"server.py missing at {src}"
    return src


@pytest.fixture(scope="module")
def server_source(server_path: Path) -> str:
    return server_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def server_tree(server_source: str) -> ast.Module:
    return ast.parse(server_source)


# ---------------------------------------------------------------------------
# fixture vault 构造（promoted 样张口径，与 tests/retrieval/test_engine.py 同构）
# ---------------------------------------------------------------------------

_PROMOTED_FM = """---
title: Loop Engineering 黄金样张
page_status: active
status: active
slug: addy-osmani-loop-engineering
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-06-11
---
"""

_HOW_BODY = """## TLDR

Loop engineering 是把任务做成有目标、验证与停止条件的受控闭环。

## 论证与解释

### 操作步骤

1. [raw §C] (source_grounded) 定义验证目标并跑全量测试 `pytest -q tests/`
"""


def _make_vault(tmp_path: Path) -> Path:
    """构造只含一篇 promoted 正例 + 一篇未升格负例的 fixture vault。"""
    root = tmp_path / "vault"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)

    (sources / "addy-osmani-loop-engineering.md").write_text(
        _PROMOTED_FM + _HOW_BODY, encoding="utf-8"
    )
    # 负例：未升格笔记，内容同主题也永不入结果
    (sources / "not-promoted-yet.md").write_text(
        """---
title: Loop Engineering 未升格稿
page_status: candidate
status: candidate
gate_decision: null
---

### 操作步骤

1. 草稿 loop engineering `echo draft`
""",
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# FastMCP stub（行为轨加载器）
# ---------------------------------------------------------------------------


class _FakeRequestContext:
    def __init__(self, lifespan_context: dict):
        self.lifespan_context = lifespan_context


class _FakeContext:
    def __init__(self, lifespan_context: dict):
        self.request_context = _FakeRequestContext(lifespan_context)


class _FakeFastMCP:
    """最小 FastMCP：登记 name/instructions/lifespan 与被装饰的工具函数。

    tool 装饰器语义与真包一致——按函数名登记（name=None 时用 fn.__name__）。
    """

    def __init__(self, name, instructions=None, lifespan=None, **kwargs):
        self.name = name
        self.instructions = instructions
        self.lifespan = lifespan
        self.tools: dict[str, object] = {}

    def tool(self, name=None, **kwargs):
        def deco(fn):
            key = name or fn.__name__
            self.tools[key] = fn
            return fn

        return deco


def _load_server_module(server_path: Path, repo_root: Path):
    """注入 FastMCP stub 后按 file location 加载 server.py，返回 module。"""
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = _FakeFastMCP
    fastmcp_mod.Context = _FakeContext
    mcp_pkg = types.ModuleType("mcp")
    server_pkg = types.ModuleType("mcp.server")
    mcp_pkg.server = server_pkg
    server_pkg.fastmcp = fastmcp_mod

    saved = {
        "mcp": sys.modules.get("mcp"),
        "mcp.server": sys.modules.get("mcp.server"),
        "mcp.server.fastmcp": sys.modules.get("mcp.server.fastmcp"),
    }
    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.server"] = server_pkg
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod
    try:
        spec = importlib.util.spec_from_file_location(
            "cc_akb2_retrieval_server_under_test", server_path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        # 恢复原 sys.modules（真 mcp 已安装时不污染其他测试）
        for key, mod in saved.items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod


@pytest.fixture(scope="module")
def server_module(server_path: Path, repo_root: Path):
    """已加载的 server 模块（stub FastMCP；PYTHONPATH 指向 src 使 akb2.* 可解析）。"""
    src_path = str(repo_root / "src")
    saved_pp = sys.path.index(src_path) if src_path in sys.path else None
    if saved_pp is None:
        sys.path.insert(0, src_path)
    try:
        return _load_server_module(server_path, repo_root)
    finally:
        if saved_pp is None:
            sys.path.remove(src_path)


# ---------------------------------------------------------------------------
# AST 契约轨
# ---------------------------------------------------------------------------


def test_server_syntax_valid(server_source: str):
    try:
        ast.parse(server_source)
    except SyntaxError as e:
        pytest.fail(f"server.py has syntax error: {e}")


def test_server_imports_only_fastmcp_from_mcp(server_tree: ast.Module):
    """server.py 只从 mcp 包 import FastMCP/Context，不依赖其他 mcp 符号。"""
    mcp_imports = []
    for node in ast.walk(server_tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("mcp"):
            mcp_imports.append((node.module, [a.name for a in node.names]))
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names if a.name.startswith("mcp")]
            if names:
                mcp_imports.append((None, names))
    assert mcp_imports, "server.py must import FastMCP from mcp.server.fastmcp"
    for mod, names in mcp_imports:
        assert mod == "mcp.server.fastmcp", f"unexpected mcp import: {mod}"
        assert set(names) <= {"FastMCP", "Context"}, (
            f"stub 只覆盖 FastMCP/Context；额外符号 {names} 会让行为轨失真"
        )


def test_server_import_boundary_akb2_retrieval_only(server_tree: ast.Module):
    """import 边界：akb2.* 只允许 akb2.retrieval（只读入口）。

    禁止 akb2.quality（写管线/升格出口）与 akb2 其他子包。
    """
    bad_imports: list[str] = []
    akb2_imports: list[str] = []
    for node in ast.walk(server_tree):
        target = None
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("akb2"):
            target = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("akb2"):
                    target = alias.name
        if target is None:
            continue
        akb2_imports.append(target)
        if target != "akb2.retrieval" and not target.startswith("akb2.retrieval."):
            bad_imports.append(target)
    assert akb2_imports, "server.py must import from akb2.retrieval (read-only engine)"
    assert not bad_imports, (
        f"import 边界违规：只允许 akb2.retrieval，发现 {bad_imports}；"
        "尤其禁止 akb2.quality（写管线）"
    )


def test_server_router_none_contract(server_tree: ast.Module):
    """lifespan 必须 yield router=None（构造期切断 LLM 通路）。"""
    yield_dicts = [
        node
        for node in ast.walk(server_tree)
        if isinstance(node, ast.Dict)
        and any(
            isinstance(k, ast.Constant) and k.value == "router"
            for k in node.keys
        )
    ]
    assert yield_dicts, "lifespan 必须显式 yield 含 router 键的 dict"
    for d in yield_dicts:
        router_val = next(
            v for k, v in zip(d.keys, d.values, strict=True)
            if isinstance(k, ast.Constant) and k.value == "router"
        )
        assert isinstance(router_val, ast.Constant) and router_val.value is None, (
            "router 必须 = None（无 LLM 通路）"
        )


def test_server_single_tool_akb2_retrieval_search(server_tree: ast.Module):
    """唯一工具：akb2_retrieval_search 函数 + @mcp.tool() 装饰。"""
    tool_funcs = [
        node.name
        for node in ast.walk(server_tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            (isinstance(d, ast.Call)
             and isinstance(d.func, ast.Attribute)
             and d.func.attr == "tool")
            or (isinstance(d, ast.Attribute) and d.attr == "tool")
            for d in node.decorator_list
        )
    ]
    assert tool_funcs == ["akb2_retrieval_search"], (
        f"只允许一个检索工具 akb2_retrieval_search，发现 {tool_funcs}"
    )


def test_server_no_write_calls(server_tree: ast.Module):
    """只读契约：不出现 open(w)/write_text/write_bytes/unlink/mkdir 等写调用。"""
    write_attrs = {"write_text", "write_bytes", "unlink", "mkdir", "rename", "touch"}
    offenders = [
        node.func.attr
        for node in ast.walk(server_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in write_attrs
    ]
    assert not offenders, f"发现写操作调用 {offenders}（read-only contract）"


# ---------------------------------------------------------------------------
# 行为轨（stub FastMCP 加载后验证真实注册与检索行为）
# ---------------------------------------------------------------------------


def test_server_registers_single_tool(server_module):
    assert server_module.mcp.name == "akb2-retrieval"
    assert list(server_module.mcp.tools) == ["akb2_retrieval_search"], (
        "FastMCP 实例只登记 akb2_retrieval_search 一个工具"
    )


def test_tool_search_returns_promoted_only(server_module, tmp_path):
    """正例命中 + 负例（未升格）不出现：经 lifespan ctx 注入 fixture vault。"""
    root = _make_vault(tmp_path)
    fn = server_module.mcp.tools["akb2_retrieval_search"]
    from akb2.retrieval import engine  # noqa: F401  # 确认引擎可解析（PYTHONPATH）

    ctx = _FakeContext({"vault_root": root, "router": None})
    payload = fn(query="loop engineering", limit=5, ctx=ctx)

    assert payload["query"] == "loop engineering"
    assert payload["total_results"] == 1
    (hit,) = payload["results"]
    assert hit["path"] == "wiki/sources/addy-osmani-loop-engineering.md"
    assert hit["title"] == "Loop Engineering 黄金样张"
    assert "pytest -q tests/" in hit["how_executability"]
    assert hit["score"] > 0


def test_tool_search_no_match_valid_empty(server_module, tmp_path):
    root = _make_vault(tmp_path)
    fn = server_module.mcp.tools["akb2_retrieval_search"]
    ctx = _FakeContext({"vault_root": root, "router": None})
    payload = fn(query="不存在关键词xyz", limit=5, ctx=ctx)
    assert payload["total_results"] == 0
    assert payload["results"] == []


def test_lifespan_router_none_and_env_resolution(server_module, tmp_path, monkeypatch):
    """lifespan：router=None + env 根解析（父根归一到 AKB2.0 子根）。

    VAULT_ROOT 指向 vault 父根（目录名非 AKB2.0），lifespan 应解析到
    <父根>/Praise Space/AKB2.0 —— 复用 test_engine 的 symlink 布局。
    """
    root = _make_vault(tmp_path)
    parent = tmp_path / "Obsidian"
    akb2_root = parent / "Praise Space" / "AKB2.0"
    akb2_root.parent.mkdir(parents=True, exist_ok=True)
    akb2_root.symlink_to(root.resolve(), target_is_directory=True)

    monkeypatch.delenv("AKB2_ROOT", raising=False)
    monkeypatch.delenv("AKB2_VAULT_ROOT", raising=False)
    monkeypatch.setenv("VAULT_ROOT", str(parent))

    import asyncio

    started = {}

    async def run():
        async with server_module.app_lifespan(server_module.mcp) as ctx_dict:
            started.update(ctx_dict)
            fn = server_module.mcp.tools["akb2_retrieval_search"]
            return fn(
                query="loop",
                limit=3,
                ctx=_FakeContext(ctx_dict),
            )

    payload = asyncio.run(run())
    assert started["router"] is None, "lifespan 必须 router=None（无 LLM）"
    assert started["vault_root"] == akb2_root.resolve()
    assert payload["total_results"] == 1
    akb2_root.unlink()


def test_lifespan_fail_closed_without_env(server_module, monkeypatch):
    """三个 env 都未设置时 fail-closed 报错，不静默读错盘。"""
    for name in ("AKB2_ROOT", "AKB2_VAULT_ROOT", "VAULT_ROOT"):
        monkeypatch.delenv(name, raising=False)

    import asyncio

    async def run():
        async with server_module.app_lifespan(server_module.mcp):
            return "should not reach"

    with pytest.raises(RuntimeError, match="fail-closed"):
        asyncio.run(run())
