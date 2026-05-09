# Step 6-7 Sync Validation Report — 2026-05-09

执行时间：2026-05-09T11:54–12:00 UTC  
计划版本：v3  
前置提交：`1ab2fe2` (test(step5): replace all placeholder skips with real assertions)

---

## 1. Preflight 状态

| 检查项 | 结果 |
|--------|------|
| git working tree | clean（`git status --short` 无输出） |
| Step 5 commit | `1ab2fe2` 已就位 |
| codex sync `--all)` flag | PASS |
| claude-backup sync `--all)` flag | PASS |
| claude-backup `check_forbidden` guard | PASS |
| claude-backup `FORBIDDEN_PREFIX.*\.claude` | PASS |
| mcp sync `target directory missing` guard | PASS |
| pytest -q | 60 passed, exit 0 |
| post-change-pipeline.sh | exit 0, `dry_run_no_write=true` |

**5 项脚本静态守卫：全部 PASS**

---

## 2. Pre-sync 状态

- backup 目录：`reports/backups/pre-step6-sync-2026-05-09/`
- `sha-manifest.tsv`：17 行（header + 15 数据行 + 1 MCP 行 = 16 数据行）
- 本地 tar.gz 归档：5 个（codex 5 × 1 = 5，claude-backup/active targets 不存在故无 tar.gz）
- MCP 文件备份：`mcp-akb-search-readonly-server.py.bak`
- `before-no-drift.txt`：已记录（10/17 MISSING，符合预期——claude-backup/active 尚未初始化）

**Before drift 摘要（符合预期）**：
- codex 5 项：MATCH（已由阶段 A 之前同步）
- claude-backup 5 项：MISSING（首次创建）
- claude-active 5 项：MISSING（首次创建）
- mcp wrapper：MATCH
- closure-manifest：MATCH

---

## 3. Sync 命令与退出码

| 命令 | exit | log |
|------|------|-----|
| `sync-skills-to-codex.sh --all --write` | 0 | `sync-codex.log` |
| `sync-skills-to-claude-backup.sh --all --write` | 0 | `sync-claude-backup.log` |
| `sync-mcp-server.sh --write` | 0 | `sync-mcp.log` |

sync 完成后执行 `sleep 5` 等待 iCloud 同步沉淀。

---

## 4. After no-drift（17 项全 MATCH）

| # | check | result | sha（前 8 位） |
|---|-------|--------|----------------|
| 1 | codex:cc-executable-plan | MATCH | 5af73fda |
| 2 | codex:cc-review | MATCH | 592cf3ed |
| 3 | codex:cc-local-llm | MATCH | 3a31ebe4 |
| 4 | codex:cc-skill-stocktake | MATCH | df16be83 |
| 5 | codex:cc-akb-search | MATCH | b669380d |
| 6 | claude-backup:cc-executable-plan | MATCH | 5af73fda |
| 7 | claude-backup:cc-review | MATCH | 592cf3ed |
| 8 | claude-backup:cc-local-llm | MATCH | 3a31ebe4 |
| 9 | claude-backup:cc-skill-stocktake | MATCH | df16be83 |
| 10 | claude-backup:cc-akb-search | MATCH | b669380d |
| 11 | claude-active:cc-executable-plan | MATCH | 5af73fda |
| 12 | claude-active:cc-review | MATCH | 592cf3ed |
| 13 | claude-active:cc-local-llm | MATCH | 3a31ebe4 |
| 14 | claude-active:cc-skill-stocktake | MATCH | df16be83 |
| 15 | claude-active:cc-akb-search | MATCH | b669380d |
| 16 | mcp:akb-search-readonly | MATCH | 7a26f5ca |
| 17 | closure-manifest | MATCH | 0fbd5370 |

`verify-no-drift.sh` exit 0，`[OK] all 17 checks MATCH — no drift detected`

---

## 5. After pipeline / pytest

| 检查项 | 结果 |
|--------|------|
| `post-change-pipeline.sh` exit | 0 |
| `dry_run_no_write=true` 存在 | ✓ |
| `pytest_skipped_until_step5` 不存在 | ✓ |
| `pytest -q` | 60 passed, exit 0 |

---

## 6. 抽样 byte-identical

**MCP wrapper diff -q**：无输出（MATCH）  
`diff -q toolbox/cc-akb-search/mcp_server/server.py <Codex>/mcp-servers/akb-search-readonly/server.py`

**5 工具 × 4 位置 SKILL.md SHA 抽样**（全部一致）：

| slug | toolbox | codex | claude-backup | claude-active |
|------|---------|-------|---------------|---------------|
| executable-plan | 5af73fda | 5af73fda | 5af73fda | 5af73fda |
| review | 592cf3ed | 592cf3ed | 592cf3ed | 592cf3ed |
| local-llm | 3a31ebe4 | 3a31ebe4 | 3a31ebe4 | 3a31ebe4 |
| skill-stocktake | df16be83 | df16be83 | df16be83 | df16be83 |
| akb-search | b669380d | b669380d | b669380d | b669380d |

完整 SHA 详见 `reports/backups/pre-step6-sync-2026-05-09/after-sample-sha.txt`。

---

## 7. Rollback 命令（失败时直接照抄）

若需要回滚到 sync 前状态：

```bash
ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/Project AKB2"
BACKUP="$ROOT/reports/backups/pre-step6-sync-2026-05-09"

# 5 工具 Codex 回滚（tar 文件名经 / → _ , 空格 → _ 替换）
for slug in executable-plan review local-llm skill-stocktake akb-search; do
  base="$HOME/.codex/skills/cc-$slug"
  SAFE_NAME=$(echo "$base" | tr '/' '_' | tr ' ' '_')
  ARCHIVE="$BACKUP/${SAFE_NAME}.tar.gz"
  if [[ -f "$ARCHIVE" ]]; then
    rm -rf "$base"
    tar -xzf "$ARCHIVE" -C "$(dirname "$base")"
    echo "restored: $base"
  fi
done

# claude-backup / claude-active 回滚（首次创建，无 tar.gz 归档，直接删除即可）
for slug in executable-plan review local-llm skill-stocktake akb-search; do
  rm -rf "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/skills/cc-$slug"
  rm -rf "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/skills/claude-active/cc-$slug"
  echo "removed: claude-backup/active cc-$slug"
done

# MCP wrapper 单文件回滚
MCP="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py"
[[ -f "$BACKUP/mcp-akb-search-readonly-server.py.bak" ]] && \
  cp "$BACKUP/mcp-akb-search-readonly-server.py.bak" "$MCP" && \
  echo "restored: mcp server.py"

# iCloud 沉淀 + 验证
sleep 5
bash "$ROOT/scripts/verify-no-drift.sh"
```

> **注意**：三个 sync 脚本都是 idempotent（SHA compare 在前，rsync --delete 总收敛到 source）。
> 绝大多数失败应直接重跑全 pipeline，rollback 仅用于源 toolbox/ 被意外破坏等不可恢复错误。

---

## 8. 明确未执行（边界）

| 项目 | 状态 |
|------|------|
| MCP server enable | ❌ 未执行 |
| hook / cron / LaunchAgent 修改 | ❌ 未执行 |
| `/Users/praise/.claude/**` 任何修改 | ❌ 未执行 |
| Obsidian AKB2 vault 内容修改 | ❌ 未执行 |
| 旧散落源清理（属 Step 8） | ❌ 未执行 |
| MCP config / Claude config 修改 | ❌ 未执行 |
