# Runbook: post-change pipeline

> 占位 — pipeline 脚本实现后填实质内容。

## 用途

工具源 commit 后强制流程，实施"5 条规则"的规则 5：每次工具变更必须跑 eval/lint，并更新 manifest。

## 流程

```
工具源改动 (commit to Project AKB2/toolbox/cc-{slug}/)
    ↓
1. 跑 evals/{slug} 的 fixture（确保不退化）
    ↓
2. 跑 src/akb2/lint/akb2_lint.py（结构 + 接口检查）
    ↓
3. 更新 vault toolbox/evals/{slug}.md（last_run_status / last_run_at / sha256）
4. 更新 vault toolbox/registry/{slug}.md（如版本号变）
5. 更新 src/akb2/closure/<日期>-delta-manifest.json（如状态跃迁）
    ↓
6. sync-skills-to-codex.sh + sync-mcp-server.sh
    ↓
7. verify-no-drift.sh（confirm install targets 已对齐）
```

## 命令

```bash
bash scripts/post-change-pipeline.sh --slug cc-akb-search
```

## TODO

- [ ] pipeline 脚本实现
- [ ] failure handling（哪步失败回滚哪步）
- [ ] git pre-commit hook 集成
- [ ] dry-run 模式
