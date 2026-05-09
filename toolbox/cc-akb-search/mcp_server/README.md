# cc-akb-search MCP server

> 占位 — 迁入 `Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py`（3.6 KB）后填实质内容。

## 角色

cc-akb-search 工具的 MCP 只读 wrapper（`mcp__akb-search`），暴露 `akb_search(query, limit)` 给 Claude Code / Codex。

## 安装拓扑

- **权威源**（git 跟踪）：本目录
- **install target**：`Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py`
- **同步**：`scripts/sync-mcp-server.sh`

## 安全约束（Review 2 三轮 APPROVED 后定）

- SQLite 强制 `mode=ro` URI（引擎级护栏）
- `router=None`（构造期切断 LLM）
- `cache=None when router is None`
- 0 文件写入 / 0 网络调用 / 响应 < 5s

## TODO

- [ ] 启动文档
- [ ] 健康检查脚本
- [ ] sync-mcp-server.sh 实现
