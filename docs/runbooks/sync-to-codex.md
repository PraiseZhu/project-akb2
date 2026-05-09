# Runbook: sync to codex

> 占位 — sync 脚本实现后填实质内容。

## 用途

将 `toolbox/cc-{slug}/` 同步到 install target：

- `~/.codex/skills/cc-{slug}/`（Codex 运行时）
- `~/.claude/skills/{slug}/`（按需）
- `Codex/plugins/.../mcp-servers/akb-search-readonly/`（cc-akb-search 专用）

## 命令

```bash
bash scripts/sync-skills-to-codex.sh
bash scripts/sync-skills-to-claude.sh
bash scripts/sync-mcp-server.sh
bash scripts/verify-no-drift.sh   # 校验所有 install target SHA 与权威源一致
```

## 历史漂移

当前 4/5 plugin 镜像 SHA 与 ~/.codex/skills/ 不一致：

| slug | ~/.codex SHA | plugin SHA | 状态 |
|------|--------------|------------|------|
| cc-executable-plan | `5af73fda…` | 无镜像 | — |
| cc-review | `592cf3ed…` | `22d993f4…` | DIFF |
| cc-local-llm | `b8050d3c…` | 无镜像 | — |
| cc-skill-stocktake | `df16be83…` | `c03bfae2…` | DIFF |
| cc-akb-search | `b669380d…` | `4214f196…` | DIFF |

sync 机制建立后需一次性清漂移。

## TODO

- [ ] 5 个脚本实现
- [ ] 漂移清理首跑记录
