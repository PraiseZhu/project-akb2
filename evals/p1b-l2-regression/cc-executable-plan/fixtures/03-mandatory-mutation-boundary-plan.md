# Fixture 03 — mandatory: 明示 mutation boundary

## Goal

把 AKB 旧库 `Project AKB/AKB/Wiki/mcp.md`(已 superseded)从 active 候选池中**永久排除**,但**不删除**文件。需要:(1) 在 AKB2.0 `staging/_review-queue.md` 登记排除原因;(2) 不动 Project AKB 文件本体;(3) 不触发任何旧 AKB fetch / synth / translate;(4) 不接 cron 或 LaunchAgent。

## Context

- 旧库路径:`Project AKB/AKB/Wiki/mcp.md`(physical file, ~2026-04-30 superseded)
- 已存在 phase-b-validation 候选 `legacy-akb-mcp-wiki--phase-b--2026-05-03.md` 标 `gate_reason: 拟 needs_repair → 弃用 + 新建`
- AKB2.0 `staging/_review-queue.md` 当前不存在,需 plan 创建

## Expected behavior(skill 应输出)

- 符合 plan-template.md 11 节模板
- `REVIEW_DOMAIN: ai_infra`(涉及旧库边界 + AKB 链路保护)
- §不修改 段落明确列出:**不**修改 `Project AKB/**` / **不**触发旧 fetch / synth / translate / NLM / 每日简报 / **不**接 cron
- §实现步骤 集中在 staging/_review-queue.md 新建 + 1 行登记
- §边界与失败模式 明示 "如果发现旧 fetch 仍跑 → 单独 plan 处理,不在本 plan 范围"
