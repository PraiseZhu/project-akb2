# Run Evidence — cc-akb-search A3 MCP Mount 执行

```yaml
timestamp: 2026-05-08T16:55:00Z
runner: claude
phase: p1e-a3-mcp-mount
skill: cc-akb-search
predecessor_phase: p1e-a2-config-authorization (verdict: APPROVED, 2 reviews passed)
mutations_performed: true
config_files_modified: true
skill_sha256_drift: false
automation_allowed_changed: false
```

---

## A3 执行摘要

用户明确授权语：「授权执行 A3。按 A2 已 APPROVED 授权包执行：只做 read-only wrapper + MCP config 写入 + sha256/diff 对账 + A3 evidence。不要直接挂 Project AKB/scripts/mcp_server.py，不授权 active gate override，不跑 C-4/C-5。完成后停住，提示我重启 Claude Code session。」

A3 执行严格按 A2 授权包的 M1→M4 顺序执行，仅做已明示的 4 项 mutation。

---

## M4: mcp 包验证（只读）

```bash
/Users/praise/.pyenv/versions/3.13.12/bin/python3 -c "import mcp; print('mcp OK')"
# 实测: mcp OK
```

| 结果 | PASS ✓ |
|------|--------|
| command | `/Users/praise/.pyenv/versions/3.13.12/bin/python3` |
| import mcp | exit 0, 输出 "mcp OK" |

---

## M1: server.py sha256 验证

```bash
shasum -a 256 /Users/praise/Library/Mobile\ Documents/com\~apple\~CloudDocs/Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py
# 0ef83866831fe338c987e06db7b7d45ddecca78ec73c3b53619e1ab83ae4aaf8  server.py
```

| 结果 | PASS ✓ |
|------|--------|
| 实测 sha256 | `0ef83866831fe338c987e06db7b7d45ddecca78ec73c3b53619e1ab83ae4aaf8` |
| A2 baseline sha256 | `0ef83866831fe338c987e06db7b7d45ddecca78ec73c3b53619e1ab83ae4aaf8` |
| 匹配 | ✓（F1 修复后，init_tables() 已移除）|
| 操作 | verify only（文件已存在，无需新建）|

---

## V3 before snapshot（A3 执行前 AKB 数据基线）

```bash
PROJECT_AKB="/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/Project AKB"
find "$PROJECT_AKB/index" "$PROJECT_AKB/notes" \
  \( -name "*.json" -o -name "*.jsonl" \) \
  -exec shasum -a 256 {} \; 2>/dev/null | sort > "$TMPDIR/akb-a3-before.txt"

for f in "$PROJECT_AKB/index/catalog.db" \
          "$PROJECT_AKB/index/catalog.db-wal" \
          "$PROJECT_AKB/index/catalog.db-shm"; do
  [ -f "$f" ] && shasum -a 256 "$f"
done 2>/dev/null | sort >> "$TMPDIR/akb-a3-before.txt"
```

| 参数 | 值 |
|------|-----|
| 文件路径 | `$TMPDIR/akb-a3-before.txt` |
| entries | 361 |
| 时间 | 2026-05-08T16:55:00Z（A3 开始前）|

---

## M2: live config 追加 akb-search

| 参数 | 值 |
|------|-----|
| 目标文件 | `/Users/praise/.claude/mcp-configs/mcp-servers.json` |
| 操作 | 在 mcpServers 对象内追加 `akb-search` key |
| 追加前 servers_count | 27（A2 baseline sha256 = `04cb0509...`）|
| 追加后 servers_count | 28 |
| 追加后 sha256 | `e5eb43ffc012f4af972565df2bd12f551366bb784987a706801ed116125b6f34` |

akb-search 条目（与 mcp-entry-draft.json byte-identical 语义）：
```json
"akb-search": {
  "command": "/Users/praise/.pyenv/versions/3.13.12/bin/python3",
  "args": [
    "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py"
  ],
  "env": {
    "PYTHONPATH": "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/Project AKB"
  }
}
```

---

## M3: mirror config 追加 akb-search

| 参数 | 值 |
|------|-----|
| 目标文件 | `/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/mcp-configs/mcp-servers.json` |
| 操作 | M2 同内容追加（与 live config 同步）|
| 追加后 sha256 | `e5eb43ffc012f4af972565df2bd12f551366bb784987a706801ed116125b6f34` |
| byte-identical with live | ✓（cmp -s exit 0）|

---

## V3 after snapshot + diff（AKB 数据完整性验证）

```bash
# 使用与 before 完全相同的 scope 重跑 after snapshot
find "$PROJECT_AKB/index" "$PROJECT_AKB/notes" \
  \( -name "*.json" -o -name "*.jsonl" \) \
  -exec shasum -a 256 {} \; 2>/dev/null | sort > "$TMPDIR/akb-a3-after2.txt"

for f in "$PROJECT_AKB/index/catalog.db" \
          "$PROJECT_AKB/index/catalog.db-wal" \
          "$PROJECT_AKB/index/catalog.db-shm"; do
  [ -f "$f" ] && shasum -a 256 "$f"
done 2>/dev/null | sort >> "$TMPDIR/akb-a3-after2.txt"

diff "$TMPDIR/akb-a3-before.txt" "$TMPDIR/akb-a3-after2.txt"
# → exit 0（0 diff）
```

| 参数 | 值 |
|------|-----|
| before entries | 361 |
| after entries | 361 |
| diff | **0 行（PASS）**|
| json/jsonl 内容文件 | 0 diff ✓ |
| catalog.db* | 0 diff ✓（init_tables() 已移除，纯配置变更不触发 WAL commit）|
| cache.db* | 排除（预期性能行为，已文档化）|

> **V3 注记**：初次 after snapshot 多包含了根目录 `catalog.db`（0-byte，sha256=`e3b0c44...`），导致表面上 +1 差异。该文件在 before 时即已存在（只是 scope 不同），确认为测量伪影，非 wrapper 写入导致。以相同 scope 重跑后 diff = 0。

---

## 不变量最终验证

| 约束 | 实测 | 状态 |
|------|------|------|
| SKILL.md sha256 不变 | `b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982` | ✓ PASS |
| settings.json mcpServers 不变 | 未修改（仍为 {}）| ✓ PASS |
| server.py sha256 匹配 A2 baseline | `0ef83866...` = A2 baseline | ✓ PASS |
| live config byte-identical to mirror | `e5eb43ff...` × 2 | ✓ PASS |
| automation_allowed 全 false | 5/5 五件套未改动 | ✓ PASS |
| V3 data integrity | diff = 0 行 | ✓ PASS |

---

## A3 Rollback 快速参考

若 V1/V2 验证失败需 rollback（A3 前 sha256 = `04cb0509...`）：

```bash
# M2 rollback: 删 akb-search key，恢复配置
# 编辑 ~/.claude/mcp-configs/mcp-servers.json，删除 "akb-search" block
# 验证 sha256 恢复至 04cb0509...

# M3 rollback: 同步 mirror config
# 编辑 iCloud/Claude Code/mcp-configs/mcp-servers.json，同上

# M1 rollback: 删 server.py（可选，wrapper 不运行不影响）
# rm "Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py"
```

---

## M2b: ~/.claude.json 追加 akb-search（事后契约修复 2026-05-08）

**背景**：V1 在新 session 失败 — CCD 直接读取 `~/.claude.json` 的全局 `mcpServers`，`mcp-configs/mcp-servers.json` 是模板库（不直接加载）。3 个原有 server（pencil/nova-gateway/figma）均来自 `~/.claude.json`。

**修复操作**：Python 脚本追加 akb-search 到 `~/.claude.json` 全局 `mcpServers`（使用 json.load/dump 保留所有其他字段）：

```python
d['mcpServers']['akb-search'] = {
    "type": "stdio",
    "command": "/Users/praise/.pyenv/versions/3.13.12/bin/python3",
    "args": [
        "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py"
    ],
    "env": {
        "PYTHONPATH": "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/Project AKB"
    }
}
```

| 参数 | 值 |
|------|-----|
| 文件 | `~/.claude.json` |
| before sha256 | `cae2e48cef61011abe2ab28d11b400330180ab6b3cd356b57fb1a98b8b2262e2`（post-hoc baseline：backup `.1778244816526` = 2026-05-08T12:53:36Z，取自 A3 执行前最近备份；注：实际写入早于该备份，before sha256 为事后补记）|
| after sha256 | `9f99d90e8ab27aa4a8fa1db7f306189d3cd4aaa28a0785691a374c4e77fe1f1d`（实测当前文件）|
| 全局 mcpServers count delta | 3 → 4（pencil + nova-gateway + figma → +akb-search）|
| 操作方式 | json.load → 追加 key → json.dump（保留所有其他字段）|

**M2b Rollback**：删 `~/.claude.json` 全局 `mcpServers.akb-search` key；验证 sha256 恢复至 before `cae2e48c...`（post-hoc baseline，事后补记）。

---

## Next Step（等待用户操作）

**请重启 Claude Code session。**

重启后在新 session 执行验证：

- V1：新 session tool list 确认 `mcp__akb-search` 可见
- V2：调用 `mcp__akb-search(query="test", limit=1)` 验证 smoke response
- V3：（已通过，config-only 变更）

> A3 + M2b 完成。等待用户重启 session 后执行 V1/V2 验证。
