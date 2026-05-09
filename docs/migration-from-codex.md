# Migration from Codex 工程容器

> 占位 — 物理迁移阶段填实质内容。

## 迁移源

| 来源 | 目标 |
|------|------|
| `Codex/ai-infra/akb2/{gate_check, toolbox_scan}/` | `src/akb2/{gate, toolbox_scan}/` |
| `Codex/ai-infra/akb2-toolbox-{automation-gate, closure, evals, lint, matrix, promotion-gate, search-quality}/` | `src/akb2/{automation_gate, closure, matrix, promotion_gate, search_quality}/` + `evals/` + `lint_evals/` |
| `Codex/plugins/.../mcp-servers/akb-search-readonly/server.py` | `toolbox/cc-akb-search/mcp_server/server.py`（权威）+ Codex 路径保留为 install target |
| `Codex/plans/akb2-*` (2 份) | `plans/` |
| `Obsidian/.akb-runtime/lint/akb2-lint.py` | `src/akb2/lint/akb2_lint.py`（snake_case） |
| `~/.codex/skills/cc-{5 工具}/` | `toolbox/cc-{5 工具}/`（权威）+ 用户级目录保留为 install target |

## Path-rewrite 清单

约 70 处可定位修改：
- 17 份 phase reports × 56 处跨容器引用
- 9 处 manifest 跨容器引用
- 4 处 policy.json 路径前缀
- 1 处 run_gate.sh CODEX_BASE → AKB2_BASE 重命名
- 3 处 closure-manifest.json 路径字符串

## TODO

- [ ] 完整 mv-plan（含每个文件源/目标对照）
- [ ] 完整 path-rewrite 表（含 sed 规则）
- [ ] SHA baseline 重锚记录（primary closure-manifest.json）
- [ ] commit 分批策略（按模块独立 commit）
