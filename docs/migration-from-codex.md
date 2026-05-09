# Migration from Codex 工程容器

> **迁移已完成（2026-05-09）** — 详细闭环报告见
> [`reports/migration/akb2-engineering-root-migration-closure-2026-05-09.md`](../reports/migration/akb2-engineering-root-migration-closure-2026-05-09.md)

## 迁移摘要

Project AKB2 现为 AKB2 工程文件和工具库的**唯一权威维护目录**，原 Codex 容器中的旧原位副本已全部清理。

### 迁移来源 → 目标

| 来源（已删除） | 目标（Project AKB2 内） |
|----------------|------------------------|
| `Codex/ai-infra/akb2/{gate_check,toolbox_scan}/` | `src/akb2/{gate,toolbox_scan}/` |
| `Codex/ai-infra/akb2-toolbox-{automation-gate,closure,evals,lint,matrix,promotion-gate,search-quality}/` | `src/akb2/{automation_gate,closure,matrix,promotion_gate,search_quality}/` + `evals/` + `lint_evals/` |
| `Codex/ai-infra/akb2-toolbox-evals/cc-akb-search`（eval 版本） | `toolbox/cc-akb-search/`（权威） |
| `Claude Code/plans/akb-*.md`（4 份） | `plans/` |
| `Obsidian/.akb-runtime/lint/akb2-lint.py` | `src/akb2/lint/akb2_lint.py` |
| `Claude Code/scripts/akb-pre-commit.sh` | `scripts/`（工程根权威） |
| `Claude Code/ai-infra/evals/skill-routing/akb-search.json` | `evals/`（工程根权威） |

### 保留不变（install target / external readonly）

| 路径 | 角色 |
|------|------|
| `~/.codex/skills/cc-{5 工具}/` | Codex install target（由 sync 脚本维护） |
| `Claude Code/skills/cc-{5 工具}/` | Claude backup mirror |
| `Claude Code/skills/claude-active/cc-{5 工具}/` | Claude active mirror |
| `Codex/plugins/.../akb-search-readonly/server.py` | MCP wrapper install target |
| `Project AKB/index/catalog.db` + `scripts/` | AKB 1.0 external readonly dependency |

### 关键证据文件

| 文件 | 说明 |
|------|------|
| `reports/migration/file-map-2026-05-09.tsv` | 373 行源→目标映射（366 ok / 5 grouped） |
| `reports/migration/akb2-engineering-root-migration-closure-2026-05-09.md` | 完整闭环报告（含双轨证明 + rollback §X） |
| `reports/backups/pre-step8-cleanup-2026-05-09/cleanup-manifest.tsv` | 47 项候选分类 + 证据 + post_state |
| `reports/backups/pre-step8-cleanup-2026-05-09/pre-delete-sha256.tsv` | 24 项删除前 SHA 基线 |
| `docs/allowed-residuals.md` | 7-enum 路径引用保留白名单 |
