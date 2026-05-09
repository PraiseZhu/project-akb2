# Install 拓扑

> 占位 — sync 脚本实现后填实质内容。

## 权威源 → install target

```
权威源（git 跟踪）
    Project AKB2/toolbox/cc-{slug}/
              │
              ├── scripts/sync-skills-to-codex.sh ───→ ~/.codex/skills/cc-{slug}/
              ├── scripts/sync-skills-to-claude.sh ──→ ~/.claude/skills/{slug}/         （按需）
              └── scripts/sync-mcp-server.sh ────────→ Codex/plugins/.../mcp-servers/akb-search-readonly/
                                                       （仅 cc-akb-search）
```

## 核心规则

1. 工具源**只在** `Project AKB2/toolbox/` 维护
2. `~/.codex/skills/`、`~/.claude/skills/` **只是镜像**
3. MCP config **只指向** 工程根稳定 wrapper
4. Obsidian `toolbox/` **只记元数据**（状态、接口、eval、风险）
5. 每次工具变更 **必须** 跑 eval/lint，更新 manifest

## TODO

- [ ] 5 个 sync 脚本实现
- [ ] verify-no-drift.sh 设计（SHA 对比 install target 与权威源）
- [ ] 历史漂移修复：4/5 plugin 镜像 SHA 不一致
