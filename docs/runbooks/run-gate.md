# Runbook: run-gate

> 占位 — 迁入 akb_gate_check.py 后填实质内容。

## 用途

跑 `src/akb2/gate/akb_gate_check.py` 检查 staging 候选是否满足 wiki active 入门门槛。

## 命令

```bash
python -m akb2.gate.akb_gate_check --vault-config config/vault.toml
```

## TODO

- [ ] CLI 参数
- [ ] 输出 manifest 格式
- [ ] 与 lint 的差异（lint 检查存量 active，gate 检查候选）
