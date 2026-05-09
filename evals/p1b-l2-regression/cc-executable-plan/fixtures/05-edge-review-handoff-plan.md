# Fixture 05 — edge: plan 必须含 review handoff + 可验收 evidence

## Goal

为 AKB2.0 工具库 P2.1 (Toolbox Lint v0) 设计第一版规则集,实现为 Bash 脚本(无 Python),输出 finding 到 stdout,plan 末尾必须含明确 `review handoff` 段(指出 reviewer 应抽查哪 3 个 finding 验证脚本逻辑)+ `验收 evidence` 段(列出 reviewer 可跑的 4 条对账命令 + 期望输出)。

## Context

- AKB2.0 8 类 lint 规则 (per 工具库 v2 §P2.1):五件套缺失 / status 不一致 / workspace_path 不存在 / sha256 漂移 / backlink_status pending / active L0 / eval stale 30d / automation_allowed:true
- 5 工具 slug:cc-{executable-plan,review,akb-search,local-llm,skill-stocktake}
- 输出位置:本轮 plan 不强求(由 plan 决定)

## Expected behavior(skill 应输出)

- 符合 plan-template.md 11 节模板
- `REVIEW_DOMAIN: ai_infra`
- §代码执行方向 必须明示"Bash 脚本不写 Python"
- §测试计划 必须含具体 reviewer 可跑命令(如 `bash bin/lint.sh > /tmp/findings.txt && wc -l ...`)
- **§实现步骤 末尾必须含 review handoff 段**(reviewer 的 sample check 项)
- **§验收 evidence**(可在结论或测试计划中)必须 ≥ 4 条对账命令 + 期望输出
- 不解锁 automation_allowed
