# Automation Gate v0

**状态**: implemented (dry-run mechanism only, 2026-05-08)
**先决条件**: L2 regression v1 至少 1 工具 `recommend_promote_L2`（当前未满足）
**触发**: 用户显式说"开始 automation gate v0 plan"后再执行真实 gate

## 机制概述

Automation Gate v0 是 **assert-only dry-run gate**：

- 评估工具箱中每个工具是否满足 8 条 gate 条件
- 输出 `global_verdict` 和逐工具 `blocker_reason`
- **永不** 修改 `automation_allowed`、SKILL.md、工具卡、closure-manifest 或任何正式 eval 文件
- 写入范围：仅限 `outputs/` 和 `run-evidence.md`

## 组件

| 文件 | 说明 |
|------|------|
| [`policy.json`](policy.json) | 8 条 gate 条件 + verdict 名映射 + 全局 invariant |
| [`schema/gate-result.schema.json`](schema/gate-result.schema.json) | gate 输出 JSON Schema |
| [`run-gate.sh`](run-gate.sh) | 只读 runner（`--self-test` / `--dry-run-current` / `--tool <slug>`）|
| [`fixtures/`](fixtures/) | 5 类 fixture（positive + 4 类 blocked）|
| [`outputs/current/`](outputs/current/) | 最近一次 dry-run 输出（gate-result.json / gate-result.md）|

## CLI 用法

```bash
# 跑全部 fixtures，期望全 pass（exit 0）
bash run-gate.sh --self-test

# 评估当前真实磁盘状态（exit 1 全 blocked）
bash run-gate.sh --dry-run-current

# 单工具诊断
bash run-gate.sh --tool cc-local-llm
```

退出码：`0` 有 eligible / `1` 全 blocked / `2` closure-manifest 缺失 / `3` invariant 违规 / `4` runner 错误

## 当前真实状态（2026-05-08）

`global_verdict = blocked_l2_verdict_blocked`

| 工具 | eval_level | 原因 |
|------|-----------|------|
| cc-executable-plan | L2 | blocked_no_l2_regression_evidence |
| cc-review | L2 | blocked_no_l2_regression_evidence |
| cc-local-llm | L1 | blocked_l1_floor |
| cc-skill-stocktake | L1 | blocked_l1_floor |
| cc-akb-search | L0 | blocked_l0_or_below |

## 8 条 Gate 条件（按评估顺序）

1. `eval_level == L2`（L0/L1 直接 floor）
2. `toolbox_status == active`
3. L2 regression `verdict == recommend_promote_L2`（当前 blocked → 不可过）
4. promotion evidence 存在
5. `automation_allowed == false`（hard fail if true）
6. `runtime_mutation_allowed == false`
7. rollback target（closure-manifest SHA256 完整）
8. human approval 标记

## 不做的事

- **不把任何工具改为 `automation_allowed: true`**
- 不创建定时任务、LaunchAgent、cron
- 不重跑 L2 regression、不修复 backend
- 不绕过现有 readonly contract

## L1 floor 不可被自动升 automation

`automation_allowed: false` invariant 受 `merge-baselines.sh` 守护（exit 1 if violated）。

## 链接

- L1 floor closure: `../../akb2-toolbox-closure/2026-05-07/`
- L2 regression v1: `../../akb2-toolbox-evals/l2-regression-v1/`
- Promotion Gate v0: `../../akb2-toolbox-promotion-gate/v0/`（precondition 未满足）
