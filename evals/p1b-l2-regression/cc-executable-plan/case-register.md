# cc-executable-plan — P1b L2 Regression Case Register

Phase:P1b L2 regression(承接 C-5b active)
工具:`cc-executable-plan`
SKILL.md sha256(P1b 全程不动):`5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a`
Workspace path:`~/.codex/skills/cc-executable-plan/SKILL.md`
Runner:**claude**(Option A — 用户 2026-05-04 override)
Runner override reason:Codex CLI 在本 Claude Code session 内不可直接调用;Claude 严格按 cc-executable-plan SKILL.md + plan-template.md 11 节模板自驱产出
Run started:2026-05-06
Run finished:2026-05-06

## 5 case 矩阵

| # | case_id | 类型 | REVIEW_DOMAIN(预期) | 关键 assertion | fixture path | output path | output sha256 |
|---|---|---|---|---|---|---|---|
| 1 | mandatory-docs-content-plan | mandatory | docs_content | 11 节齐 + Edit 仅 1 文件 + 不越界到代码 + reserved prefix 列表完整 | `cc-executable-plan/fixtures/01-mandatory-docs-content-plan.md` | `cc-executable-plan/outputs/01-mandatory-docs-content-plan.md` | `dd51650cd76e` |
| 2 | mandatory-ai-infra-plan | mandatory | app_code | 11 节齐 + `--strict-mode` flag 默认 off + exit 3 新增 + 不解锁 automation_allowed + 4-6 单测覆盖矩阵 | `cc-executable-plan/fixtures/02-mandatory-ai-infra-plan.md` | `cc-executable-plan/outputs/02-mandatory-ai-infra-plan.md` | `6a703550876e` |
| 3 | mandatory-mutation-boundary-plan | mandatory | ai_infra | 11 节齐 + §不修改 显式列旧 AKB 全链路 + 不接 cron / LaunchAgent / hook + Project AKB/** 严格只读 | `cc-executable-plan/fixtures/03-mandatory-mutation-boundary-plan.md` | `cc-executable-plan/outputs/03-mandatory-mutation-boundary-plan.md` | `5b1d8ebcb83c` |
| 4 | edge-ambiguous-input-plan | edge | other | 11 节齐 + 不编造 file path / 函数名 + §待确认 ≥ 5 条 + §代码执行方向 显式"待用户明示" | `cc-executable-plan/fixtures/04-edge-ambiguous-input-plan.md` | `cc-executable-plan/outputs/04-edge-ambiguous-input-plan.md` | `9ef97c8017b2` |
| 5 | edge-review-handoff-plan | edge | ai_infra | 11 节齐 + Bash 实现(不写 Python)+ 8 类 lint 规则齐 + reviewer handoff 段含 3 抽查项 + 验收 evidence ≥ 4 条对账命令 | `cc-executable-plan/fixtures/05-edge-review-handoff-plan.md` | `cc-executable-plan/outputs/05-edge-review-handoff-plan.md` | `5cfa4f097411` |

## 11 节模板出现矩阵

模板必备 11 节:Title(H1)/ Review 对齐 / 目标与验收 / 当前状态 / 代码执行方向 / 接口与数据合约 / 实现步骤 / 边界与失败模式 / 测试计划 / 待确认 / 实施前 Review Checklist

实测每个 output 的 H2 节命中数(目标 = 10,Title 是 H1 不计):

| case_id | H2 节命中(/10) |
|---|---|
| 01-mandatory-docs-content-plan | 10 |
| 02-mandatory-ai-infra-plan | 10 |
| 03-mandatory-mutation-boundary-plan | 10 |
| 04-edge-ambiguous-input-plan | 10 |
| 05-edge-review-handoff-plan | 10 |

**汇总**:5/5 case 输出 11 节齐(10 H2 + 1 H1 Title),100% 模板兼容。
