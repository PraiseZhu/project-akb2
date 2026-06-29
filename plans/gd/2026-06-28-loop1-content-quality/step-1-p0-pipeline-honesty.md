# Step 1: P0 当前管线诚实化 + 补观测 v1（finalized）

GD_STANDARD: /Users/praise/AI-Agent/Claude/projects/Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-step-plan

日期：2026-06-28
状态：reviewed（child t-p0-planner 草稿 → 主 agent 采纳并最终化）
负责人：Claude 执行；P0-1/P0-3/P0-4 判定级改动过 Codex 异族审再合 main

> **来源/合并**：本 step 的权威细节（精确 WHERE 行号、WHAT/WHY/VERIFY）在 `child-drafts/p0-draft.md`（child_planner `t-p0-planner` 起草、已过 `gd-validate-child-proposal.py`）。主 agent review 结论见 `reports/merge-report.md`：**采纳**，无冲突；仅做两处最终化锚定（见下"主 agent 最终化"）。本文件为最终化的索引 + 出口契约，不重复粘贴草稿全文。

## 1. 目标链
```text
PROJECT_GOAL: AKB2 fail-closed 门控 + 异族 Maker/Checker + 实测价值裁决（ref docs/constitution.md 原则 3/6/7/8）
CHAIN_GOAL:   循环1 按内容车道分流，概念型与操作型在不破完整性地板前提下都能入库（ref specs/content-lane-quality/spec.md）
PHASE_GOAL:   把满仓从 0/18 的失败根因（加权分恒=0 等代码 bug）诚实化，并补 P1 所需观测
TASK_GOAL:    落地 P0-1..P0-6，使 concept 通路能真出分、4 类卡点（加权分/must-cover/命令形态/准入误杀）解除、repair/lane 可观测、active 满仓重跑出基线
```

## 2. Review 对齐
- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：grader 缺 score → fail-closed（不静默当 0）; 完整性地板不被加权分补偿; 准入切 deepseek 不破异族（判源不判产物，grader 仍 gpt-5.4）; 命令形态豁免不放行真编造命令; 观测改动判定行为零变化
- Domain-specific notes：P0-1/P0-3/P0-4 改判定面，合 main 前过 Codex 异族审。

## 3. 前置条件
- blocked_by：`—`
- baseline：HEAD=5dde324，离线 1633 绿（pyenv 3.13.12，`pytest -m 'not integration'`）。
- Hard-stop：任一 P0 子任务离线测试转红且非预期 → 停，不带红进满仓。

## 4. 成功标准（SC，对齐 master SC-1..SC-6）
权威 SC 表见 master-plan §3（Round 1 已扩为 SC-1..6 + 6a/6b/6c）。摘要：
- [ ] SC-1：concept grader 每维强制产出 0~1 score、缺逐维 score/越界 fail-closed、concept 阈值 6.0、#4 加权分≥6.0 可复算。
- [ ] SC-2：满仓 #0/2/8/17 must-cover 卡点根因定位解除（直出渲染已存在且 test 绿，不盲改）。
- [ ] SC-3：命令形态判定区分真命令/散文/术语/表格碎片，#16 不卡，带参工具命令仍回查。
- [ ] SC-4：准入四处切 `deepseek-v4-pro`、离线绿、不破异族。
- [ ] SC-5：repair 逐轮 final_defects + admission pass/abort 两路 lane_reason 可读，判定行为不变。
- [ ] SC-6：active e2e 满仓重跑完成，pass_rate>0、负样本 100% 拒。
- [ ] SC-6a/6b/6c（content-lane）：概念正样本入库 / 三类负样本各被对应门拒 / 地板不可补偿对抗样本被拒（绑 `tests/quality/test_concept_judgment_e2e.py` 的 concept_positive/negatives/adversarial）。

## 5. 非目标
- 不做 P1 定性、P2 修复、v2、update、升星；不放宽地板/F4/novelty/负样本（护栏）。

## 6. 实现步骤
6 步（P0-1..P0-6）的 WHERE/WHAT/WHY/VERIFY 见 `child-drafts/p0-draft.md` §"实现步骤"。**主 agent 最终化锚定 + Round 1 worker 审查修正**（已并入各 task packet，与 child 草稿冲突处以本节为准）：
1. **owned_path 修正**：P0-2 真实文件是 `src/akb2/quality/distill.py`（`compose.py` 不存在，child 已核实）。
2. **顺序约束**：P0-5 与 P0-4 共改 `admission.py` → P0-4 先合、P0-5 后做其 admission 字段；P0-5 的观测测试改用新建 `tests/quality/test_admission_observability.py`（不再共享 p0-4 的 `test_admission_lane.py`）；P0-6（满仓）必须在 P0-1..P0-5 全合入后最后执行。
3. **P0-4 family 名修正（rev-tech P1）**：准入切 `deepseek-v4-pro`（不是字面 `deepseek`——会 `RouterConfigError` 让准入全 fail-closed）。
4. **P0-1 阈值范围修正（rev-tech P1）**：rubric 只改 `lanes.concept.threshold=6.0`，保留 `DEFAULT_THRESHOLD`/顶层/`procedural`=6.5（child 草稿"同步 DEFAULT"指令作废）；真因是"grader 未强制产出逐维 score"非"缺 score 静默当 0"。
5. **P0-2 前提修正（rev-tech P2）**：`distill.py` 的 source_operation 直出渲染**已存在且 test 绿**——P0-2 不盲改，先复验满仓 #0/2/8/17 是否仍卡再定位失配点。
6. **P0-6 报告口径（rev-tech P2）**：若验收读 report `promotion_mode=active`，须同设 `AKB2_AMENDMENT_APPLIED=1`；否则以 runner/promote/final_status 判真入库。

## 7. Task Packet 拆分
| task_id | agent_role | owned_paths | blocked_by | can_parallel_with |
|---------|-----------|------------|-----------|-------------------|
| p0-1-weighted-score | implementer | grader.py, weighted_score.py, rubric/ | — | p0-2, p0-3 |
| p0-2-must-cover | implementer | distill.py | — | p0-1, p0-3 |
| p0-3-command-form | implementer | source_ops_scanners/raw_verify.py | — | p0-1, p0-2 |
| p0-4-admission-deepseek | implementer | admission.py | — | — |
| p0-5-observability | implementer | repair.py, runner.py, admission.py | p0-4 | — |
| p0-6-active-rerun | implementer | reports/calibration/ | p0-1,p0-2,p0-3,p0-4,p0-5 | — |

> 最终化 task packet 文件：`task-packets/p0-1-weighted-score.md` … `task-packets/p0-6-active-rerun.md`（6 份，execute-ready）。execute 阶段并行波次以届时生成并过 `gd-validate-dispatch.py` 的 execute dispatch map 为准（p0-1/2/3 可同波；p0-4 单独；p0-5 在 p0-4 后；p0-6 最后）。

## 8. 边界
修改：`src/akb2/quality/{grader,weighted_score,distill,checks_structural,admission,repair,runner}.py`、`src/akb2/quality/source_ops_scanners/raw_verify.py`、`skillpack/quality/rubric/{rubric.yaml,10-dimensions.md}`、`tests/**`、`reports/calibration/`。
不修改：`~/.claude/**`、vault 内任何 .py/.sh、operations.scan 收窄、judge 判定级护栏。

## 9. 风险与防护
见 `child-drafts/p0-draft.md` §"风险与防护"（R1 异族误判 / R2 rubric 版本断言连锁 / R3 procedural 零回归 / R4 命令豁免误放 / R5 满仓 key / R6 顺序依赖）。

## 10. 测试计划
出口闸：`PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -m 'not integration' -q`（期望 ≥1633 passed）+ `ruff check src/akb2/quality src/akb2/quality/source_ops_scanners`；满仓见 P0-6。

## 11. Assumptions
- 已确认（child 实测）：grader `score` 字段当前可选无 0~1 约束；admission 四处 gemini-flash 在 admission.py 精确行；rubric 三 threshold 均 6.5。
- 加权分恒=0 修复后黄金样张 #4 极可能转过（handoff §3 正面信号）。
