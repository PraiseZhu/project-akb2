# Fixture 02 — mandatory: ai-infra plan

## Goal

为 `ai-infra/akb2-gate-checker/` 加一个 `--strict-mode` 旗标,启用后:(1) `proposed_gate_decision: needs_human_review` 任一记录 → exit 3 而不是 0;(2) 输出 stderr 警告含 `[STRICT-MODE]` 前缀。

## Context

- 现有包:`Codex/ai-infra/akb2-gate-checker/akb2_gate_check/__main__.py`(C-3 完成,已 APPROVED)
- 现有 CLI 已支持 `--mode {dry-run,write-proposal}`
- 18 unit tests 全过(C-3 + 后续 fix)
- 用户需求来源:某些 reviewer 不希望任何 needs_human_review 静默通过

## Expected behavior(skill 应输出)

- 符合 plan-template.md 11 节模板
- `REVIEW_DOMAIN: app_code`
- 包含运行边界(`--strict-mode` 不影响 `dry-run` 输出 vs 影响)
- 包含权限边界(不修改 candidate 文件 / 不写 active)
- 包含隔离测试(单测 + integration 验证 exit code)
- 不打开 automation_allowed
