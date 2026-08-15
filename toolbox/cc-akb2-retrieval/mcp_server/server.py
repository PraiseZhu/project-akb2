"""AKB2 Retrieval Readonly MCP Server — 克隆 cc-akb-search 只读模式

Only tool: akb2_retrieval_search
Import boundary: ONLY from akb2.retrieval（只读检索引擎）
NOT from akb2.quality（写管线 / 升格出口），也不从 akb2 其他子包 import。

Read-only contract:
- 0 file writes（引擎只读扫描 wiki/sources/，不写 vault 任何字节）
- 0 network calls
- 0 LLM calls（lifespan 显式 router=None；engine.search 本身无 LLM 通路）
- promoted 样本集 exact 口径由 engine 保证（staging/隔离区/未升格永不入结果）

vault 根解析（lifespan 启动期，fail-closed）：
按 akb2.loops.paths 文档化 env 契约 AKB2_ROOT > AKB2_VAULT_ROOT > VAULT_ROOT，
父根自动归一到 Praise Space/AKB2.0 子根；三者皆未设置时启动即报错。
PYTHONPATH 需含本工程 src/ 使 akb2.* import 可解析（mcp-entry 配置）。
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("akb2.retrieval.readonly")

# 生命周期诊断计数（sc-mcp-lifespan-stdio 经 stderr 日志行外部读取）：
# started/stopped 各恰一行是进入/退出各恰一次的证据。
_lifespan_enters = 0
_lifespan_exits = 0

# vault 父根 → AKB2.0 子根的归一段（与 akb2.loops.paths._AKB2_RELATIVE_PARTS 同值；
# 按只读 import 边界在此复制常量，不 import akb2.loops）
_AKB2_RELATIVE_PARTS = ("Praise Space", "AKB2.0")

# env 优先序与 akb2.loops.paths._env_akb2_root 一致
_ENV_ROOT_NAMES = ("AKB2_ROOT", "AKB2_VAULT_ROOT", "VAULT_ROOT")


def _resolve_vault_root() -> Path:
    """从 env 解析 AKB2.0 子根；未设置时 fail-closed 报错（不静默读错盘）。"""
    for name in _ENV_ROOT_NAMES:
        raw = os.environ.get(name)
        if raw:
            expanded = Path(os.path.expandvars(raw)).expanduser()
            if expanded.name == "AKB2.0":
                return expanded.resolve()
            return expanded.joinpath(*_AKB2_RELATIVE_PARTS).resolve()
    raise RuntimeError(
        "AKB2 Retrieval MCP Server: 未设置 AKB2_ROOT/AKB2_VAULT_ROOT/VAULT_ROOT，"
        "无法解析 vault 根（fail-closed）。请在 mcp-entry 配置中注入。"
    )


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Initialize read-only resources: vault root only, no router (no LLM)."""
    global _lifespan_enters, _lifespan_exits
    _lifespan_enters += 1
    vault_root = _resolve_vault_root()
    # router=None：无 LLM 通路（克隆 cc-akb-search 只读契约的显式标记；
    # akb2.retrieval.engine.search 本身即为纯关键词检索，不接 LLM）
    # started/stopped 行是 sc-mcp-lifespan-stdio 的外部计数探针
    # （日志走 stderr，不污染 stdio JSON-RPC 帧）
    logger.info(
        "AKB2 Retrieval Readonly MCP Server started (router=None, vault_root=%s)",
        vault_root,
    )
    try:
        yield {"vault_root": vault_root, "router": None}
    finally:
        _lifespan_exits += 1
        logger.info("AKB2 Retrieval Readonly MCP Server stopped")


mcp = FastMCP(
    "akb2-retrieval",
    instructions=(
        "AKB2 只读检索 MCP Server — 仅提供 akb2_retrieval_search 工具"
        "（promoted 笔记关键词检索），无写操作。"
    ),
    lifespan=app_lifespan,
)


def _get_ctx(ctx: Context) -> dict:
    return ctx.request_context.lifespan_context


@mcp.tool()
def akb2_retrieval_search(
    query: str,
    limit: int = 5,
    ctx: Context = None,
) -> dict:
    """搜索 AKB 知识库，返回匹配文章列表。

    Args:
        query: 搜索关键词（如 "loop engineering", "MCP 只读", 空白分词 AND 匹配）
        limit: 返回结果数量上限

    Read-only contract: 不写文件，不写 DB，不调网络，不调 LLM（router=None）。
    结果集只含主链路已升格（promoted）笔记；staging/隔离区/未升格内容永不返回。
    """
    # Import boundary: ONLY from akb2.retrieval（只读引擎入口）
    # 禁止引入 akb2.quality（写管线）或任何带写操作的模块
    from akb2.retrieval import engine

    res = _get_ctx(ctx)
    results = engine.search(
        res["vault_root"],
        query,
        limit=limit,
    )
    return {
        "query": query,
        "total_results": len(results),
        "results": [r.to_dict() for r in results],
    }


if __name__ == "__main__":
    mcp.run()
