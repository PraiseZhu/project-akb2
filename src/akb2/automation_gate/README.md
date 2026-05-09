# automation_gate

> 占位 — 迁入 `Codex/ai-infra/akb2-toolbox-automation-gate/` 后填实质内容。

Automation Gate v0（dry-run）：8 条 gate 条件 → 决定工具是否可解锁 `automation_allowed=true`。当前所有 5 工具均 blocked。

- `policy.json` — 8 条条件 + invariants
- `schema/gate-result.schema.json`
- `fixtures/` — 5 类（positive + 4 类 blocked / hard-fail）
- `run_gate.sh` — 3 模式：self-test / dry-run-current / tool

详见 [`docs/runbooks/run-gate.md`](../../../docs/runbooks/run-gate.md)。
