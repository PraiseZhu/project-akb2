# Runbook: re-anchor closure-manifest

> 占位 — 迁移阶段填实质内容。

## 背景

`src/akb2/closure/2026-05-07/closure-manifest.json`（primary baseline）SHA 在 Codex 路径下被锁为 `0fbd5370…`。迁移到本工程后路径变化，但 **字节内容不变 → SHA 不变**。

## 风险

manifest 内的 `evidence_paths` 字段含 Codex 相对路径（如 `ai-infra/akb2-toolbox-evals/p1e-cc-akb-search-a4/`），路径搬迁后这些字段引用失效。

## 解决方案（待定）

| 选项 | 含义 | 代价 |
|------|------|------|
| A | 保留旧 SHA + 新增 `path_migrated_to` 字段（指向 evals/ 新位置） | 低，audit 链完整 |
| B | 重写 evidence_paths → 重算 SHA → 写一份 v2 manifest | 中，需要 delta 重锚 |
| C | 路径写为 vault.toml 变量解析（如 `${akb2_root}/evals/...`）| 高，需要 manifest schema 升级 |

## TODO

- [ ] 与用户确认 A/B/C 选项
- [ ] 实施
- [ ] 更新 phase-c-reports 中的 manifest 引用
