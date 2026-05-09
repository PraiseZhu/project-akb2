# Install 拓扑

> 更新日期：2026-05-09（Step 6 v4 plan Step 3 Config契約同步）

## 权威源 → install target / backup mirror

```
权威源（git 跟踪，唯一维护位置）
    Project AKB2/toolbox/cc-{slug}/
              │
              ├─ sync-skills-to-codex.sh ────→ ~/.codex/skills/cc-{slug}/
              │                                 （Codex install target，运行时加载）
              │
              ├─ sync-skills-to-claude-backup.sh → Claude Code/skills/cc-{slug}/
              │                                     （workspace backup mirror，
              │                                      不保证被 Claude Code loader 读取）
              │
              └─ sync-mcp-server.sh ─────────→ Codex/plugins/.../mcp-servers/akb-search-readonly/
                                                （MCP install target，仅 cc-akb-search）
```

## 核心规则

1. **toolbox/ 是唯一权威源**：工具变更只在 `Project AKB2/toolbox/cc-{slug}/` 进行
2. **`~/.codex/skills/cc-{slug}/` 是 Codex install target**：由 sync 脚本维护，Codex 运行时加载
3. **`Claude Code/skills/cc-{slug}/` 是 backup mirror**：不保证被 Claude Code loader 读取
4. **`~/.claude/skills/` 禁写**：v4 plan 约束，不作为 sync 目标
5. **MCP config 只指向工程根稳定 wrapper**：`toolbox/cc-akb-search/mcp_server/server.py`
6. **Obsidian `toolbox/` 只记元数据**（状态卡、接口卡、eval 卡、风险卡）
7. **每次工具变更必须跑 eval/lint + 更新 manifest**

## 路径契约

| 角色 | 路径 | 维护方式 |
|------|------|---------|
| 权威源 | `Project AKB2/toolbox/cc-{slug}/` | git 版本控制 |
| Codex install target | `~/.codex/skills/cc-{slug}/` | `scripts/sync-skills-to-codex.sh` |
| Claude Code backup | `Claude Code/skills/cc-{slug}/` | `scripts/sync-skills-to-claude-backup.sh` |
| MCP install target | `Codex/plugins/.../mcp-servers/akb-search-readonly/` | `scripts/sync-mcp-server.sh` |
| MCP 权威 wrapper | `toolbox/cc-akb-search/mcp_server/server.py` | git 版本控制 |

## 5 个工具 slug

`cc-executable-plan` / `cc-review` / `cc-local-llm` / `cc-skill-stocktake` / `cc-akb-search`

## sync 脚本行为

- 所有 sync 脚本默认 **dry-run**（只显示将要复制的文件）
- 真实写入需显式传 `--write`
- `verify-no-drift.sh` 校验所有 install target / backup mirror SHA 与权威源一致
- `post-change-pipeline.sh` 串联所有验证步骤

## 不做的事

- 不把 `~/.claude/skills/` 作为 sync 目标（禁写约束）
- 不自动启用 MCP server / hook / cron / LaunchAgent
- 不迁移旧 `Project AKB` backend（作为 `external_readonly_dependency` 保留）
