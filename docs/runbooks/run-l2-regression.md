# Runbook: run-l2-regression

> 占位 — 迁入 evals/l2-regression-v1/ 后填实质内容。

## 用途

跑 5 工具的 L2 回归（决定性 fixture × 3 rounds），输出 `recommendation.json`（verdict ∈ {recommend_promote_L2 | needs_patch | blocked}）。

## 当前状态

- `cc-local-llm` / `cc-skill-stocktake` 已跑过 L2 v1，verdict=blocked（backend curl_exit=7）
- `cc-executable-plan` / `cc-review` 是 grandfathered L2，无 L2 evidence
- `cc-akb-search` 计划"观察 7 天后" 进入 search quality 回归

## TODO

- [ ] backend 恢复后重跑 cc-local-llm/cc-skill-stocktake
- [ ] cc-executable-plan/cc-review 补 L2 evidence
- [ ] cc-akb-search L2 v2
