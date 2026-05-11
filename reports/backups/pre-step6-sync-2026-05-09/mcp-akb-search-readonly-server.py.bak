"""AKB Search Readonly MCP Server — DRAFT (A2 phase, not deployed)

Only tool: akb_search
Import boundary: ONLY from scripts.core.search and scripts.utils.db
NOT from scripts.mcp_server (exposes akb_manage write operations)

Read-only contract:
- 0 file writes
- 0 network calls
- 0 AKB content writes（notes/, index/*.json{,l}, embeddings/）
- catalog.db must remain unchanged（init_tables 已不调用）
- cache.db may change as search cache and is excluded from V3 data-integrity gate
- router=None (no LLM → no include_summary)

PYTHONPATH must be set to Project AKB root so 'scripts.*' imports resolve.
Command: ~/.pyenv/versions/3.13.12/bin/python3 server.py
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Context

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("akb.search.readonly")

# Project AKB root must be in sys.path for scripts.* imports
# Set via PYTHONPATH env var in mcp-entry-draft.json


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Initialize read-only resources: DB only, no router."""
    # Import boundary: scripts.utils.db only (read-only CatalogDB)
    # NOT importing scripts.utils.router (LLMRouter) — not needed for readonly search
    from scripts.utils.db import CatalogDB

    db = CatalogDB(readonly=True)
    # readonly=True: 使用 file:path?mode=ro URI，SQLite 拒绝任何写操作。
    # init_tables() 不调用：AKB 正式库 catalog.db 已完全初始化。

    logger.info("AKB Search Readonly MCP Server started (router=None, read-only mode)")
    yield {"db": db, "router": None}
    logger.info("AKB Search Readonly MCP Server stopped")


mcp = FastMCP(
    "akb-search",
    instructions="AKB 知识库只读搜索 MCP Server — 仅提供 akb_search 工具，无写操作。",
    lifespan=app_lifespan,
)


def _get_ctx(ctx: Context) -> dict:
    return ctx.request_context.lifespan_context


@mcp.tool()
def akb_search(
    query: str,
    semantic: bool = False,
    limit: int = 5,
    ctx: Context = None,
) -> dict:
    """搜索 AKB 知识库。返回匹配文章列表。

    Args:
        query: 搜索关键词（如 "LangGraph", "MCP protocol", "agent evaluation"）
        semantic: 是否启用语义搜索（server 以 router=None 运行时自动降级为关键词搜索）
        limit: 返回结果数量上限

    Read-only contract: 不写文件，不写 DB，不调网络。
    Note: include_summary 已移除——可能调用 LLM，非严格只读。
    """
    # Import boundary: ONLY from scripts.core.search (NOT from scripts.mcp_server)
    # scripts.mcp_server 注册了 akb_manage(action="apply") 等写操作，禁止引入
    from scripts.core.search import search_knowledge

    res = _get_ctx(ctx)
    result = search_knowledge(
        query=query,
        db=res["db"],
        router=res["router"],  # None — LLM 功能禁用
        semantic=semantic,
        limit=limit,
        include_summary=False,  # 强制 False — 调 LLM 非只读
    )

    return {
        "query": query,
        "total_results": len(result.get("results", [])),
        "results": [
            {
                "item_id": r.get("item_id", "") or r.get("id", ""),
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "source": r.get("source", ""),
                "date": r.get("date", ""),
                "tags": r.get("tags", ""),
                "snippet": r.get("snippet", "")[:300],
            }
            for r in result.get("results", [])
        ],
    }


if __name__ == "__main__":
    mcp.run()
