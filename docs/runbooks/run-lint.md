# Runbook: run-lint

> 占位 — 迁入 akb2_lint.py 后填实质内容。

## 用途

跑 `src/akb2/lint/akb2_lint.py` 检查 vault 内 active 页是否合规。

## 命令

```bash
python -m akb2.lint.akb2_lint --vault-config config/vault.toml
```

## 期望输出

- `manifests/lint/<日期>-akb2-lint.jsonl`（写到 vault 内）
- 控制台摘要：scanned / passed / failed / warnings

## TODO

- [ ] 实际 CLI 参数说明
- [ ] 退出码定义
- [ ] CI 集成方式
