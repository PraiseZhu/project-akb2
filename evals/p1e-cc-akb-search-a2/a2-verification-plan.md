# A2 验证计划 — cc-akb-search Unblock

A3 完成（重启 Claude Code session）后按以下三步验证。**V1 + V2 + V3 必须全部通过**才宣告 A3 成功。

---

## V1. Tool Presence Check（必要，非充分）

**目的**：确认 akb-search MCP server 已 mount 进 Claude Code session。

**操作**：
- 在新 Claude Code session 中观察 live tool list，应出现 `mcp__akb-search__akb_search`
- 或在 Claude Code Settings → MCP 面板看到 akb-search 状态为 running

**通过条件**：tool list 包含 `mcp__akb-search__akb_search`（名称可能因 MCP namespace 略有变体）

**失败处理**：
- 若 session 启动时出现 akb-search 错误 → 检查 server.py PYTHONPATH 和 pyenv 路径
- 若 server.py import 失败 → 检查 scripts.utils.db / scripts.core.search 是否可访问

---

## V2. Smoke Call（功能验证）

**目的**：确认 akb_search 工具可正常调用并返回结果。

**操作**：
```
调用 mcp__akb-search__akb_search(query="LangGraph", limit=3)
```

**通过条件**：
- 返回 JSON dict，含 `query`, `total_results`, `results` 字段
- `results` 为数组（即使为空也可接受）
- 无 Python traceback / import error
- 响应时间 < 5s（关键词搜索，无 LLM）

**失败处理**：
- `ModuleNotFoundError: scripts.*` → 检查 PYTHONPATH 是否正确设为 Project AKB 根目录
- `import mcp` 失败 → 检查 command 路径是否指向 pyenv 3.13.12
- DB 错误 → 检查 CatalogDB 路径是否在 Project AKB

---

## V3. AKB 数据完整性 Check（核心：read-only 合约验证）

**目的**：确认 smoke call 没有写入任何 AKB 数据文件，验证 wrapper 的只读契约。

### 步骤 1：A3 执行前记录 before snapshot

```bash
PROJECT_AKB="/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/Project AKB"

# ① AKB 内容文件（json/jsonl）
find "$PROJECT_AKB/index" "$PROJECT_AKB/notes" "$PROJECT_AKB/embeddings" \
  \( -name "*.json" -o -name "*.jsonl" \) \
  -exec shasum -a 256 {} \; 2>/dev/null | sort > "$TMPDIR/akb-before.txt"

# ② catalog.db*（schema-critical；F1 移除 init_tables() 后预期 0 diff）
for f in "$PROJECT_AKB/index/catalog.db" \
          "$PROJECT_AKB/index/catalog.db-wal" \
          "$PROJECT_AKB/index/catalog.db-shm"; do
  [ -f "$f" ] && shasum -a 256 "$f"
done 2>/dev/null | sort >> "$TMPDIR/akb-before.txt"

# ③ cache.db* 显式排除：KnowledgeCache() 写 cache.db 是预期性能行为（search 结果缓存），
#    不违反 AKB 数据只读合约。cache.db 不存储 AKB 知识内容，仅存放关键词搜索缓存。

echo "before snapshot: $(wc -l < "$TMPDIR/akb-before.txt") entries"
```

> **A2 阶段已记录 before snapshot**：A2 执行时实测 358 个 json/jsonl 文件（Project AKB index/ + notes/）。
> A3 执行前需重新运行上述命令以获得最新 before snapshot（中间可能有 curator 新增内容）。

### 步骤 2：A3 完成 + V2 smoke call 后记录 after snapshot

```bash
# ① AKB 内容文件（json/jsonl）
find "$PROJECT_AKB/index" "$PROJECT_AKB/notes" "$PROJECT_AKB/embeddings" \
  \( -name "*.json" -o -name "*.jsonl" \) \
  -exec shasum -a 256 {} \; 2>/dev/null | sort > "$TMPDIR/akb-after.txt"

# ② catalog.db*（同 before 步骤）
for f in "$PROJECT_AKB/index/catalog.db" \
          "$PROJECT_AKB/index/catalog.db-wal" \
          "$PROJECT_AKB/index/catalog.db-shm"; do
  [ -f "$f" ] && shasum -a 256 "$f"
done 2>/dev/null | sort >> "$TMPDIR/akb-after.txt"

echo "after snapshot: $(wc -l < "$TMPDIR/akb-after.txt") entries"
```

### 步骤 3：Diff（期望 0 diff）

```bash
diff "$TMPDIR/akb-before.txt" "$TMPDIR/akb-after.txt"
# 期望：无输出（0 diff）
```

**通过条件**（修订版）：
- AKB 内容文件（*.json/*.jsonl）：diff 0 行
- catalog.db*：diff 0 行（F1 移除 init_tables() 后，纯读查询不触发 WAL commits）
- cache.db*：不纳入 V3 检查范围（预期写入，search 性能缓存，已文档化）

**总通过条件**：`diff` 无输出，exit 0

**失败处理（V3 diff 非空）**：
- 说明 wrapper 有写副作用（cache 写入 / log 文件 / 其他意外写操作）
- **立即触发 A3 rollback**：
  1. M1 rollback（仅当 A3 期间修改了 server.py）：还原 server.py 至 A2 draft baseline sha256（0ef83866...，F1 修复后）；若 A3 未改动 server.py 则跳过此步
  2. 删除 M2/M3 中的 akb-search key（还原 config sha256 至 a2-config-baseline.json）
  3. 重启 Claude Code session（session 无 akb-search）
  4. 分析 diff 找写副作用来源，修正 wrapper 后重新走 A3

---

## A3 Rollback 快速参考

```bash
# M1 rollback — server.py 由 A2 创建，A3 未新建；仅当 A3 期间修改了内容才需还原：
# shasum -a 256 ".../server.py"  →  应为 0ef83866831fe338c987e06db7b7d45ddecca78ec73c3b53619e1ab83ae4aaf8（F1 修复后）
# 若 hash 漂移: cp p1e-cc-akb-search-a2/wrapper-draft.py ".../server.py"（还原 A2 baseline 内容）
# 若 hash 未变: M1 rollback 无操作

# M2/M3 rollback — 从 a2-config-baseline.json 读取 baseline sha256
# 手动删除 live + mirror config 中的 akb-search key
# 验证 sha256 恢复至 04cb0509472fc0b7e3a36bd52f2a5b6a0d92a9d9160cd5c76a86dd7a95ca0406
```

---

## 验证总结（填写于 A3/A4 实施后）

| 验证项 | 期望 | 实测结果 | 通过 |
|--------|------|---------|------|
| V1 tool presence | mcp__akb-search 出现在 tool list | — | — |
| V2 smoke call | 返回 results 数组，无 error | — | — |
| V3 data integrity | diff 0 行 | — | — |
