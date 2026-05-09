# Run Evidence — cc-akb-search A2 配置授权计划

```yaml
timestamp: 2026-05-08T00:00:00Z
runner: claude
phase: p1e-a2-config-authorization
skill: cc-akb-search
plan_version: v1.0
mutations_performed: false
config_files_modified: false
skill_sha256_drift: false
automation_allowed_changed: false
```

---

## Step 1: Baseline Freeze

### SKILL.md sha256
```
b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982
/Users/praise/.codex/skills/cc-akb-search/SKILL.md
```
byte-identical with A1 + C-4b baselines ✓

### Config sha256 (live + mirror)
```
04cb0509472fc0b7e3a36bd52f2a5b6a0d92a9d9160cd5c76a86dd7a95ca0406
/Users/praise/.claude/mcp-configs/mcp-servers.json

04cb0509472fc0b7e3a36bd52f2a5b6a0d92a9d9160cd5c76a86dd7a95ca0406
/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/mcp-configs/mcp-servers.json
```
两文件 byte-identical；servers_count = 27（含 26 个已有 + 自 A1 后新增 1 个）；均不含 akb-search。

### AKB Data Before Snapshot (V3 baseline)
```bash
# Project AKB data directories:
# index: 554 files; notes: 2848 files; embeddings: 0 json/jsonl files
# json/jsonl snapshot: 358 files captured at A2 time
# stored at $TMPDIR/akb-a2-before.txt (session-local)
```
> A3 执行前需重新运行 V3 §步骤1 命令获取最新 before snapshot（curator 可能在 A2→A3 间有新增）。

---

## Step 2: wrapper-draft.py 静态审查

| check | result |
|-------|--------|
| import boundary: `from scripts.core.search import search_knowledge` | ✓（非 scripts.mcp_server）|
| 仅暴露 akb_search tool | ✓ |
| 无 include_summary 参数 | ✓（移除，原因：调 LLM 非只读）|
| include_summary 在 search_knowledge 调用中强制 False | ✓ |
| router=None 传入 search_knowledge | ✓（无 LLM 路由）|
| 0 写文件操作 | ✓（静态审查确认）|
| 0 网络调用 | ✓（静态审查确认）|
| lifespan 仅 import CatalogDB | ✓（未 import StorageManager / LLMRouter）|
| init_tables() 已移除 | ✓（F1 修复：init_tables 执行 schema executescript + migration INSERT 写 catalog.db，违反只读合约；AKB 正式库已完全初始化，只读场景无需调用）|
| cache.db 写入已文档化为预期行为 | ✓（search_knowledge 内 KnowledgeCache() 写 cache.db 是性能缓存行为；cache.db 仅存 search 结果缓存，不存 AKB 知识内容；V3 显式排除 cache.db*）|

### import boundary 实证
```
scripts.mcp_server → akb_manage(action="apply") 写操作 → 禁止 import
scripts.core.search → search_knowledge 只读查询函数 → 允许 import
scripts.utils.db → CatalogDB → 仅 CatalogDB() 初始化（设 db_path），不调 init_tables()
  注：init_tables() 已从 lifespan 中移除（F1 修复）
  注：search_knowledge 内 KnowledgeCache() 写 cache.db 是预期性能行为，不违反 AKB 数据只读合约
```

---

## Step 3: mcp-entry-draft.json 审查

| check | result |
|-------|--------|
| command = pyenv python3 3.13.12 | ✓ |
| pyenv python3 含 mcp 包 | ✓（`import mcp` exit 0 实测）|
| PYTHONPATH = Project AKB root | ✓（scripts.* import 需要）|
| settings.json 不含 akb-search 条目 | ✓（settings.json mcpServers = {} 不变）|
| A3 target files 明示在 _comment | ✓ |

### Python 环境验证（只读 M4 预验）
```bash
/Users/praise/.pyenv/versions/3.13.12/bin/python3 -c "import mcp; print('mcp OK')"
# 实测: mcp OK
```

---

## Step 4: a2-verification-plan.md 审查

| check | result |
|-------|--------|
| V1 presence check 有明确通过条件 | ✓ |
| V2 smoke call 有明确通过条件 | ✓ |
| V3 数据完整性 check 含 before/after snapshot 命令 | ✓ |
| V3 diff 期望 0 行（严格条件）| ✓ |
| V3 失败处理 → A3 rollback 步骤 | ✓ |
| rollback 快速参考含 sha256 target | ✓ |

---

## Invariants

| 约束 | 验证状态 |
|------|---------|
| SKILL.md sha256 不变 | ✓（before = after = b669380de8a0...） |
| 五件套文件未改动 | ✓ |
| automation_allowed 全 false | ✓（5/5） |
| mcp-configs 未修改 | ✓（sha256 = baseline）|
| settings.json mcpServers 未修改 | ✓（仍为 {}）|
| toolbox_status 仍为 candidate | ✓ |
| eval_level 仍为 L0 | ✓ |
| wrapper-draft.py 仅 draft，未 deploy | ✓（evidence pack copy + canonical Codex/plugins 路径均为 draft；未注册进任何 MCP config）|
| mcp-entry-draft.json 仅 draft，未写入配置 | ✓ |

---

## Next Step

A3 执行（需用户明确授权）：
1. 确认本 A2 plan APPROVED（用户审批）
2. A3 开始前 re-verify config sha256 与 a2-config-baseline.json 一致
3. 执行 M4（验证 mcp 包）→ M1（verify server.py sha256 匹配 A2 draft baseline 0ef83866...（F1 修复后），文件已存在无需新建）→ M2+M3（追加 akb-search 到 mcp-configs）
4. 重启 Claude Code session
5. 执行 V1 → V2 → V3 验证
