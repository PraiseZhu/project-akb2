"""sc-mcp-import-boundary-runtime：import 边界运行期审计。

在真实加载并**调用** server 工具函数的进程内，以 sys.modules 事实面
记录实际加载的 akb2.* 模块集，与允许清单 exact 比对。静态 AST 断言
（tests/mcp_servers/test_retrieval_server.py）判源码文本，本测试判
进程事实——三条静态看不见的绕行路径各有显式用例：
  - importlib.import_module 动态导入
  - __import__ 内建调用
  - 经中间模块转发/再导出

审计面说明：server 的 `from akb2.retrieval import engine` 在工具函数
体内（延迟加载），只加载模块体看不到——必须真实调用 akb2_retrieval_search
一次才能把进程事实面填满。

反向变异（mcp-dynamic-import-bypass）由 scripts/mutation_harness.py
机器化执行：注入 importlib.import_module('akb2.quality.promote') 后
test_runtime_import_set_exact 必红（此时静态 AST 轨仍绿——正是本闸
的存在理由）。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "toolbox" / "cc-akb2-retrieval" / "mcp_server" / "server.py"

# 允许加载的 akb2.* 模块集（exact 清单：server 只准经 akb2.retrieval 进）
ALLOWED_AKB2_PREFIXES = ("akb2.retrieval",)

_PROMOTED_FM = """---
title: Runtime Audit Probe
page_status: active
status: active
slug: runtime-audit-probe
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-01-01
---

## 论证与解释

runtime audit token。

### 操作步骤

1. 步骤 `echo audit`
"""


@pytest.fixture()
def audit_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "runtime-audit-probe.md").write_text(_PROMOTED_FM, encoding="utf-8")
    return root


def _load_server_with_stub():
    """注入 FastMCP stub 后按 file location 加载 server.py，返回 module。"""
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")

    class _FakeRequestContext:
        def __init__(self, lifespan_context):
            self.lifespan_context = lifespan_context

    class _FakeContext:
        def __init__(self, lifespan_context):
            self.request_context = _FakeRequestContext(lifespan_context)

    class _FakeFastMCP:
        def __init__(self, name, instructions=None, lifespan=None, **kwargs):
            self.name = name
            self.instructions = instructions
            self.lifespan = lifespan
            self.tools: dict[str, object] = {}

        def tool(self, name=None, **kwargs):
            def deco(fn):
                self.tools[name or fn.__name__] = fn
                return fn
            return deco

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

    src_path = str(REPO_ROOT / "src")
    added = src_path not in sys.path
    if added:
        sys.path.insert(0, src_path)
    try:
        spec = importlib.util.spec_from_file_location(
            "akb2_runtime_audit_server", SERVER_PATH
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for key, mod in saved.items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod
        if added:
            sys.path.remove(src_path)


def _purge_akb2() -> None:
    """清掉全部已加载 akb2.* 模块（隔离每个用例的进程事实面起点）。"""
    for name in [n for n in sys.modules if n.startswith("akb2")]:
        del sys.modules[name]


@pytest.fixture(autouse=True)
def _restore_akb2_modules():
    """快照并在用例结束后恢复 akb2.* 模块，替换审计期间产生的新副本。

    _purge_akb2 会把收集期已加载的 akb2 模块从 sys.modules 删掉；若不恢复，
    后续测试文件（收集期绑定了旧模块对象）与 monkeypatch 重新 import 的新副本
    会双模块分裂——补丁打在新副本上、被测函数走旧副本，isinstance 也会因类
    身份不同而失败（CI 实证：scrapling fallback / source_ops_scanners /
    source_validator 全套跟着红）。"""
    saved = {n: m for n, m in sys.modules.items() if n.startswith("akb2")}
    yield
    for name in [n for n in sys.modules if n.startswith("akb2")]:
        del sys.modules[name]
    sys.modules.update(saved)


def test_runtime_import_set_exact(audit_vault: Path) -> None:
    """加载 + 真实调用工具后，进程内 akb2.* 模块集恰为允许前缀子集。

    变异反证：mcp-dynamic-import-bypass 在工具函数体内注入
    importlib.import_module('akb2.quality.promote') → 调用后
    akb2.quality.* 进入 sys.modules → 本断言红。
    """
    _purge_akb2()
    module = _load_server_with_stub()

    fn = module.mcp.tools["akb2_retrieval_search"]
    ctx = _fake_ctx({"vault_root": audit_vault, "router": None})
    payload = fn(query="runtime audit", limit=3, ctx=ctx)
    assert payload["total_results"] == 1, "正例必须命中（防审计器空转）"

    loaded = {n for n in sys.modules if n.startswith("akb2.")}
    assert loaded, "server 调用后未加载任何 akb2.* 模块——审计器失效"
    offenders = sorted(
        n for n in loaded
        if not any(n == p or n.startswith(p + ".") for p in ALLOWED_AKB2_PREFIXES)
    )
    assert offenders == [], (
        f"运行期加载越界模块: {offenders}（只允许 {ALLOWED_AKB2_PREFIXES}；"
        "akb2.quality 是写管线，静态 AST 看不见的动态 import 也逃不过本闸）"
    )
    _purge_akb2()


def _fake_ctx(lifespan_ctx: dict):
    """与 stub FastMCP 配套的最小 Context。"""
    return _StubCtx(lifespan_ctx)


class _StubCtx:
    def __init__(self, lifespan_context):
        class _RC:
            pass

        rc = _RC()
        rc.lifespan_context = lifespan_context
        self.request_context = rc


# ---------------------------------------------------------------------------
# 三条绕行路径的原则性验证（受控源码，同一 sys.modules 事实面）
# ---------------------------------------------------------------------------


def _audit_inline_src(src: str) -> set[str]:
    """清空 akb2.* 后 exec 受控源码，返回进入 sys.modules 的 akb2.* 集。"""
    _purge_akb2()
    src_path = str(REPO_ROOT / "src")
    added = src_path not in sys.path
    if added:
        sys.path.insert(0, src_path)
    try:
        module = types.ModuleType("audit_probe_mod")
        exec(compile(src, "<audit-probe>", "exec"), module.__dict__)
        return {n for n in sys.modules if n.startswith("akb2.")}
    finally:
        if added:
            sys.path.remove(src_path)


def test_dynamic_import_bypass_detected_in_principle() -> None:
    """绕行一：importlib.import_module 动态导入越界模块 → 进事实面。"""
    loaded = _audit_inline_src(
        "import importlib\nimportlib.import_module('akb2.loops.paths')\n"
    )
    assert "akb2.loops.paths" in loaded, "审计器没抓到 importlib 动态导入"


def test_dunder_import_bypass_detected_in_principle() -> None:
    """绕行二：__import__ 内建调用 → 同样进事实面。"""
    loaded = _audit_inline_src("__import__('akb2.loops.paths')\n")
    assert "akb2.loops.paths" in loaded, "审计器没抓到 __import__ 内建调用"


def test_reexport_bypass_detected_in_principle() -> None:
    """绕行三：经中间模块转发（from X import y）→ 中间模块进事实面。"""
    loaded = _audit_inline_src(
        "from akb2.loops import paths\nassert paths is not None\n"
    )
    assert loaded & {"akb2.loops", "akb2.loops.paths"}, (
        f"转发 import 未进事实面: {loaded}"
    )
    _purge_akb2()
