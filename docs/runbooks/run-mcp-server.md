# Runbook: run-mcp-server

> 占位 — 迁入 mcp_server/server.py 后填实质内容。

## 用途

启动 cc-akb-search 的 MCP 只读 wrapper（提供 `mcp__akb-search` 工具）。

## 命令

```bash
python toolbox/cc-akb-search/mcp_server/server.py
```

## MCP 注册（Claude Code / Codex）

参见 `config/mcp-servers.local.json`（gitignore，本地建）。

## TODO

- [ ] 启动参数（vault-config 路径）
- [ ] 健康检查命令
- [ ] 与 install target（Codex/plugins/.../mcp-servers/akb-search-readonly/server.py）的同步规则
