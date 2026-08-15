# Runbook: MCP Server（cc-akb-search / cc-akb2-retrieval）

> 更新日期：2026-05-09（Step 6 v4 plan Step 3，cc-akb-search 段）
> 增补日期：2026-08-15（cc-akb2-retrieval 段，wave3 g5）

## 用途

本文档覆盖两个 MCP 只读 wrapper 的运行方式：

| Server | 工具 | 知识库 |
|--------|------|--------|
| cc-akb-search | `mcp__akb-search__akb_search` | Project AKB（旧） |
| cc-akb2-retrieval | `akb2_retrieval_search` | AKB2.0 vault（promoted 笔记） |

## cc-akb-search 段

### 两条路径

| 路径 | 位置 | 说明 |
|------|------|------|
| 权威源 | `toolbox/cc-akb-search/mcp_server/server.py` | git 版本控制，变更在这里发生 |
| MCP install target | `Codex/plugins/.../mcp-servers/akb-search-readonly/server.py` | 实际被 MCP config 引用，由 sync 脚本维护 |

> MCP config（`mcp-servers.json` 或 `mcp-servers.local.json`）指向 install target 位置。
> 权威源变更后需跑 `scripts/sync-mcp-server.sh --write` 同步到 install target。

### 启动命令

```bash
# 从 install target 启动（生产路径）
python "~/AI-Agent/Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py"

# 从权威源启动（开发调试）
python toolbox/cc-akb-search/mcp_server/server.py
```

### 外部依赖

MCP wrapper 在运行时读取 Project AKB（旧）的以下文件（external_readonly_dependency）：

| 依赖 | 路径 | 消费方式 |
|------|------|---------|
| SQLite 知识库 | `Project AKB/index/catalog.db` | `CatalogDB(readonly=True)` |
| 搜索后端 | `Project AKB/scripts/core/search.py` | `import scripts.core.search` via PYTHONPATH |
| DB 工具类 | `Project AKB/scripts/utils/db.py` | `import scripts.utils.db` via PYTHONPATH |

### sync 同步

```bash
# 检查权威源与 install target 是否一致
bash scripts/verify-no-drift.sh

# dry-run
bash scripts/sync-mcp-server.sh

# 真实同步
bash scripts/sync-mcp-server.sh --write
```

### MCP 注册

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

## cc-akb2-retrieval 段（AKB2.0 只读检索 server）

### 定位

AKB2.0 检索的 MCP 只读 wrapper：server 名 `akb2-retrieval`，唯一工具
`akb2_retrieval_search(query, limit)`，对主链路已升格（promoted）笔记做
关键词检索。权威文档见 `toolbox/cc-akb2-retrieval/mcp_server/README.md`
与 `docs/runbooks/retrieval-interface.md`（引擎契约）。

### 权威启动命令

唯一权威源：`toolbox/cc-akb2-retrieval/mcp_server/server.py`（git 跟踪，
无 install target 拓扑）。下述 bash 块由 `tests/mcp_servers/test_run_doc_examples.py`
逐条真跑——真实拉起 server 子进程、经 stdio JSON-RPC 完成 initialize 握手；
错误转录放 text 块，不进 bash 块。

```bash
# 仓库根目录执行；解释器 = python3（pyenv shim）
# PYTHONPATH 必含本工程 src/，否则 akb2.retrieval 只读引擎 import 失败
# vault 根经 env 注入：优先序 AKB2_ROOT > AKB2_VAULT_ROOT > VAULT_ROOT，
# 父根自动归一到 Praise Space/AKB2.0 子根
PYTHONPATH=src python3 toolbox/cc-akb2-retrieval/mcp_server/server.py
```

stdio 上的握手序列（客户端示例帧；text 块仅为转录，真跑断言在测试内）：

```text
--> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"<client>","version":"<version>"}}}
<-- {"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"akb2-retrieval","version":"..."},"protocolVersion":"...","capabilities":{"tools":{...}},"instructions":"..."}}
--> {"jsonrpc":"2.0","method":"notifications/initialized"}
--> {"jsonrpc":"2.0","id":2,"method":"tools/list"}
<-- tools 仅含 akb2_retrieval_search，入参 schema 为 query（必填）与 limit
--> {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"akb2_retrieval_search","arguments":{"query":"...","limit":5}}}
<-- content 为单条 text，正文 JSON 含 query / total_results / results
```

### fail-closed 行为（缺 vault env 即拒启）

```bash
# 三个 vault env 皆未设置：启动即报错退出（fail-closed，不静默读错盘）
env -u AKB2_ROOT -u AKB2_VAULT_ROOT -u VAULT_ROOT PYTHONPATH=src python3 toolbox/cc-akb2-retrieval/mcp_server/server.py
```

stderr 报 `RuntimeError ... 无法解析 vault 根（fail-closed）...`，退出码非零；
stdout 不输出任何 JSON-RPC 响应。请在 mcp-entry 配置的 env 段注入 vault 根。

### 只读契约（cc-akb2-retrieval 只读保证等级：T1）

对齐 `docs/runbooks/retrieval-interface.md` §2 的等级口径——T1 指
防疏忽/措辞漂移等级，不是防对抗输入的等级：

- 0 文件写入：引擎只读扫描 `wiki/sources/`，不写 vault 任何字节
- 0 网络调用、0 LLM 调用（lifespan 显式 `router=None`，纯关键词检索）
- 结果集只含主链路已升格（promoted）笔记；staging 候选 / 隔离区 /
  未升格内容永不进入结果
- import 边界：只允许 `akb2.retrieval`（只读引擎）；禁止 `akb2.quality`
  （写管线 / 升格出口）及 akb2 其他子包
- 该等级由 server 的只读实现与 `tests/mcp_servers/test_retrieval_server.py`
  （AST 契约轨 + 行为轨）维护；本文措辞由 `tests/mcp_servers/test_doc_wording.py`
  锁定（T2 禁用词与漂移数值两类扫描）

### MCP 注册

MCP config 文件不入 git。本地创建示例：

```jsonc
// config/mcp-servers.local.json（gitignore）
{
  "akb2-retrieval": {
    "command": "python3",
    "args": ["<repo 根>/toolbox/cc-akb2-retrieval/mcp_server/server.py"],
    "env": {
      "PYTHONPATH": "<repo 根>/src",
      "VAULT_ROOT": "<vault 父根，或用 AKB2_ROOT 指向 AKB2.0 子根>"
    }
  }
}
```

## 不做的事（两个 server 通用）

- 不自动修改 live MCP config（需单独授权）
- 不启动 MCP server 作为 daemon / LaunchAgent
- 不写 `~/.claude/` 下任何文件
