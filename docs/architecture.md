# Project AKB2 工程架构

> 占位 — 迁移完成后填实质内容。

## 范围

- **包含**：lint / gate / intake / closure / automation_gate / matrix / promotion_gate / search_quality / mcp_servers / 工具库权威源
- **不含**：知识库本体（留 Obsidian vault `Praise Space/AKB2.0/`）

## 与 vault 的边界

详见 [`config/vault.toml`](../config/vault.toml) + [`path-conventions.md`](path-conventions.md)。

## TODO

- [ ] 三层（Schema / Wiki / Raw）与本工程的引用方向图
- [ ] 数据流：vault → 工程 lint/gate → 回写 vault toolbox 元数据
- [ ] 安全边界：本工程禁止写 vault 任何 .py/.sh
