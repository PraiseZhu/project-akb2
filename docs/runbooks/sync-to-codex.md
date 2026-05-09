# Runbook: 工具库同步

> 更新日期：2026-05-09（Step 6 v4 plan Step 3）

## 用途

将权威工具源 `toolbox/cc-{slug}/` 分发到各 install target / backup mirror。

## 三条 sync 路径

### 1. Codex install target（核心）

```bash
# dry-run（默认，只显示将要同步的文件）
bash scripts/sync-skills-to-codex.sh --all

# 真实写入（需显式 --write）
bash scripts/sync-skills-to-codex.sh --all --write

# 单工具
bash scripts/sync-skills-to-codex.sh cc-review --write
```

目标：`~/.codex/skills/cc-{slug}/`

### 2. Claude Code workspace backup mirror

```bash
bash scripts/sync-skills-to-claude-backup.sh --all
bash scripts/sync-skills-to-claude-backup.sh --all --write
```

目标：`Claude Code/skills/cc-{slug}/`

> 注意：这是 workspace backup mirror，不保证被 Claude Code loader 读取。
> 不写 `~/.claude/skills/`（v4 plan 禁写约束）。

### 3. MCP wrapper install target（cc-akb-search 专用）

```bash
bash scripts/sync-mcp-server.sh
bash scripts/sync-mcp-server.sh --write
```

源：`toolbox/cc-akb-search/mcp_server/server.py`
目标：`Codex/plugins/claude-code-ai-infra/mcp-servers/akb-search-readonly/server.py`

## 验证

```bash
# SHA 比对：权威源 vs 所有 install target / backup mirror
bash scripts/verify-no-drift.sh

# 全套管线验证
bash scripts/post-change-pipeline.sh
```

## 5 个工具 slug

| slug | 描述 |
|------|------|
| `cc-executable-plan` | 可执行计划生成 |
| `cc-review` | 代码审查 |
| `cc-local-llm` | 本地 LLM 管理 |
| `cc-skill-stocktake` | 技能盘点 |
| `cc-akb-search` | AKB 知识库搜索（含 MCP wrapper） |

## closure-manifest SHA 锚点

`verify-no-drift.sh` 同时校验：
- `src/akb2/closure/2026-05-07/closure-manifest.json`
- SHA: `0fbd53703ff58802299ba8cb04f5290e4b973c124729376dc341f862f06f90cc`
