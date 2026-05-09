# Runbook: MCP Server（cc-akb-search）

> 更新日期：2026-05-09（Step 6 v4 plan Step 3）

## 用途

启动 cc-akb-search 的 MCP 只读 wrapper（提供 `mcp__akb-search__akb_search` 工具）。

## 两条路径

| 路径 | 位置 | 说明 |
|------|------|------|
| 权威源 | `toolbox/cc-akb-search/mcp_server/server.py` | git 版本控制，变更在这里发生 |
| MCP install target | `Codex/plugins/.../mcp-servers/akb-search-readonly/server.py` | 实际被 MCP config 引用，由 sync 脚本维护 |

> MCP config（`mcp-servers.json` 或 `mcp-servers.local.json`）指向 install target 位置。
> 权威源变更后需跑 `scripts/sync-mcp-server.sh --write` 同步到 install target。

## 启动命令

```bash
# 从 install target 启动（生产路径）
python "~/Library/Mobile Documents/com~apple~CloudDocs/Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py"

# 从权威源启动（开发调试）
python toolbox/cc-akb-search/mcp_server/server.py
```

## 外部依赖

MCP wrapper 在运行时读取 Project AKB（旧）的以下文件（external_readonly_dependency）：

| 依赖 | 路径 | 消费方式 |
|------|------|---------|
| SQLite 知识库 | `Project AKB/index/catalog.db` | `CatalogDB(readonly=True)` |
| 搜索后端 | `Project AKB/scripts/core/search.py` | `import scripts.core.search` via PYTHONPATH |
| DB 工具类 | `Project AKB/scripts/utils/db.py` | `import scripts.utils.db` via PYTHONPATH |

## sync 同步

```bash
# 检查权威源与 install target 是否一致
bash scripts/verify-no-drift.sh

# dry-run
bash scripts/sync-mcp-server.sh

# 真实同步
bash scripts/sync-mcp-server.sh --write
```

## MCP 注册

MCP config 文件不入 git。本地创建示例：

```jsonc
// config/mcp-servers.local.json（gitignore）
{
  "akb-search": {
    "command": "python",
    "args": ["<install-target-path>/server.py"]
  }
}
```

## 不做的事

- 不自动修改 live MCP config（需单独授权）
- 不启动 MCP server 作为 daemon / LaunchAgent
- 不写 `~/.claude/` 下任何文件
