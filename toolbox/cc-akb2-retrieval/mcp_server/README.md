# cc-akb2-retrieval MCP server

## 角色

AKB2 检索工具的 MCP 只读 wrapper（`mcp__akb2-retrieval`），暴露唯一的 `akb2_retrieval_search(query, limit)` 工具给 Claude Code / Codex。

## 安装拓扑

- **权威源**（git 跟踪）：本目录 `toolbox/cc-akb2-retrieval/mcp_server/`
- **工程层登记**：`src/akb2/mcp_servers/README.md`
- **运行方式**：`PYTHONPATH=<repo>/src python3 toolbox/cc-akb2-retrieval/mcp_server/server.py`（stdio）

## 只读安全约束

- **Import 边界**：只允许 `akb2.retrieval`（只读检索引擎）入口；禁止 `akb2.quality`（写管线）及 `akb2` 其他子包
- **lifespan `router=None`**：构造期切断 LLM 通路，纯关键词检索
- **vault 根 fail-closed**：`AKB2_ROOT` / `AKB2_VAULT_ROOT` / `VAULT_ROOT`（按此优先序）皆未设置时启动即报错，不静默读错盘
- **0 文件写入 / 0 网络调用 / 0 DB 写**：引擎只读扫描 `wiki/sources/` 内 promoted 笔记

## 工具契约

`akb2_retrieval_search(query, limit=5)` → `{query, total_results, results[{path, title, how_density, how_executability, score}]}`

- `query` 空白分词，全部 token 命中（AND，大小写不敏感）
- 结果集只含主链路已升格（promoted）笔记：`page_status/status: active` + `gate_decision: active` + `reviewer: akb2-quality-loop-v1` 四要素缺一不可
- staging 候选 / 隔离区 / 未升格笔记 / 索引页永不进入结果
