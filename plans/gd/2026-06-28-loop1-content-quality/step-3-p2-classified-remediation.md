# Step 3: P2 据定性分类修复 v1

GD_STANDARD: /Users/praise/AI-Agent/Claude/projects/Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-step-plan

日期：2026-06-28
状态：draft（内容由 P1 定性结果决定——本 step 是条件性框架，待 P1 落地后细化具体改动）
负责人：Claude 执行；判定级改动（repair/grader/rubric/corpus）过 Codex 异族审再合 main

> **条件性**：P2 的**具体改动内容由 Step 2（P1）的逐篇 {救|调|拒} 定性决定**，现在无法预先固定到 task packet 级。本文件给出四条修复路径的触发条件 + 解法 + 验收框架；P1 出口后按定性分布 materialize 具体 task packet。

## 1. 目标链
```text
PROJECT_GOAL: AKB2 fail-closed 门控 + 异族 Maker/Checker + 实测价值裁决（ref docs/constitution.md 原则 3/7/8）
CHAIN_GOAL:   循环1 按内容车道分流（ref specs/content-lane-quality/spec.md SC-001..005）
PHASE_GOAL:   据 P1 定性把"救"的修收敛、"调"的调 rubric、"拒"的正确归类，使满仓达"够格全过 + 该拒全拒"
TASK_GOAL:    按 P1 四类定性分类处理 + 顺带修 lane 误判/reject 后门/测试债，达满仓可达上限 + Codex 异族审 APPROVED
```

## 2. Review 对齐
- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：调 rubric 不放水（负样本仍拒）; 改 repair 不绕过完整性地板; "拒"的归类不靠造假; 权重/阈值只改 rubric.yaml 不改判定代码（spec FR-010）; 判定级改动过异族审
- Domain-specific notes：P2 改判定面最重，每类改动过 Codex 异族审 + 满仓回归。

## 3. 前置条件
- blocked_by：**Step 2（P1）出口——用户已确认 12 篇（或剩余）逐篇 {救|调|拒}**。
- 必须 artifact：`reports/loop1-audit/*.md`（P1 定性记录）+ P0 重跑 factory.json 基线。
- Hard-stop：P1 未拍板 → 停，不在无定性下盲改 rubric/repair。

## 4. 成功标准（SC，对齐 master SC-8）
- [ ] SC-8：据 P1 分类修复后满仓达"够格全过 + 该拒全拒"、negative_reject_rate==1.0、Codex 异族审 APPROVED。
  - verify-1 (method: command)：以 P1 产出的机器可读 `reports/loop1-audit/decision-manifest.json` 为目标集复验——`PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 scripts/verify_loop1_reachable_ceiling.py --mode outcome --factory reports/calibration/factory.json --decisions reports/loop1-audit/decision-manifest.json`，断言所有 `expected_after_p2=ingest` 的 fixture `passed==true`、`=reject` 的 fixture `passed==true 且 final_status∈{rejected_correctly,rejected,non_knowledge_rejected}`（**非** `passed==false`，见 §7 reject 判据修正）、`negative_reject_rate==1.0`。（`verify_loop1_reachable_ceiling.py` **已按 Option B 建好**，归共享工具，本 step 以 required_context 引用、非待建 P2 交付物——见 §7。）
  - verify-2 (method: command)：Codex 异族审用真实命令 `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-review-suite-controller.py" --target-set-id loop1-content-quality-p2 --kind combined --cwd /Users/praise/AI-Agent/Claude/projects/akb2-integ-wt --target execution_artifact=<P2 execution result.json> --target code_diff=<P2 diff.patch> --out-dir plans/gd/2026-06-28-loop1-content-quality/reviews/codex-p2 --live-transport --compat-v1`，断言 aggregate `GD_REVIEW_DECISION: APPROVED`（`/gd review execution-plus-code` 是路线名，实际入口是此 controller）。
  - expect：达可达上限（非硬凑 18）+ 负样本 100% + 异族 APPROVED。

## 5. 非目标
- 不为提通过率放宽完整性地板/F4/novelty；不把"源真薄"硬凑入库（那是污染）。
- 不在本 step 做 v2 多源提炼（薄源的根治在 v2，P2 只是先正确"拒"并记账）。

## 6. 实现步骤（四条路径，按 P1 定性触发）
```text
P2-a 改 repair 机制   触发: P1 判"机制可修"
  WHERE: src/akb2/quality/repair.py（location 匹配、A-B 拉扯）
  WHAT:  location 容差匹配（grader 自由文本 location → 映射真实小节）；缓解 A/B 层拉扯（repair 改 A 层时不复发 B 层）
  WHY:   gap2：repair 只能改 location 命中的小节，命名对不上→patch 被丢→3 轮空转
  VERIFY: 该批"救"的篇 repair 收敛（满仓重跑该批不再 distill_no_converge）

P2-b 调 grader 参数   触发: P1 判"太苛"
  WHERE: skillpack/quality/rubric/rubric.yaml（维度权重/判据/阈值，不改判定代码）
  WHAT:  调对应车道某维权重或判据，使被误杀的"调"批达标
  WHY:   spec FR-010：标准与判定逻辑分离，调参不改代码
  VERIFY: 该批达标 + 负样本仍 100% 拒（不放水）；改 rubric 后离线 + 满仓回归

P2-c 承认该拒/换源    触发: P1 判"源真薄"
  WHERE: evals/main-pipeline-quality/corpus.yaml
  WHAT:  把"源真薄"篇标 expected=reject 或移出；达标定义改为"够格全过 + 该拒全拒"
  WHY:   handoff §7：90% 可能不可达且不是 bug；分层标准正确拒它们是对的
  VERIFY: 这些篇正确归类为 reject；满仓达"可达上限"

P2-d lane误判+reject后门+测试债  触发: 并行（有 lane_reason 后）
  WHERE: src/akb2/quality/admission.py（lane 判定，#6/#15）+ reject 后门（wA 发现）+ 测试诚实（wB 发现）
  WHAT:  有 lane_reason 后定根修 lane 误判（概念文判成 procedural）；堵 reject 后门；补诚实测试
  WHY:   #6/#15 死于 lane 误判；wA/wB 历史 finding
  VERIFY: lane 误判篇正确分车道；后门堵住；测试断言绑行为
```

## 7. Task Packet 拆分
最终化 task packet **待 P1 定性后 materialize**——四条路径各自的篇集、改动范围由 P1 结果决定。占位：
| task_id | agent_role | owned_paths | blocked_by |
|---------|-----------|------------|-----------|
| p2-a-repair-loc-tolerance | implementer | src/akb2/quality/repair.py | P1（"救"批非空时启用） |
| p2-b-rubric-tuning | implementer | skillpack/quality/rubric/rubric.yaml | P1（"调"批非空时启用） |
| p2-c-corpus-reject | implementer | evals/main-pipeline-quality/corpus.yaml | P1（"拒"批非空时启用） |
| p2-d-lane-fix | implementer | src/akb2/quality/admission.py | P1 lane_reason 可读后 |
| p2-e-verify-ceiling | implementer | plans/gd/2026-06-28-loop1-content-quality/reports/p2/ | P1（decision-manifest 就绪后） |

> **SC-8 验收承载（Round 2 worker rev-goal 修正）**：验收器 `scripts/verify_loop1_reachable_ceiling.py` **已按 Option B 建好**（不再是待造交付物，归共享工具，step-3 以 required_context 引用）——`--self-test` **7 例全过**（含 outcome/coverage 两模式 + reject 正确拒/repair 失败误判拦/verdict↔expected 不变量）、ruff 干净。p2-e 的工作是：(1) 跑 `--mode outcome` 对 P2 重跑 factory + decision-manifest 验"够格全过+该拒全拒"（**reject 判据已修正为 `passed==True 且 final_status==rejected_correctly`**，不是早先错误的 `passed==False`）；(2) 产出 `reports/p2/execution-result.json` + `diff.patch` 供 SC-8 第二条 verify（`gd-review-suite-controller.py --kind combined`）消费（master §10 列为 step-3 deliverable）。

## 8. 边界
修改（按 P1 定性激活的子集）：`src/akb2/quality/{repair,admission}.py`、`skillpack/quality/rubric/rubric.yaml`、`evals/main-pipeline-quality/corpus.yaml`、`tests/**`。
不修改：完整性地板判定逻辑、F4/novelty/负样本判据、`~/.claude/**`、vault。

## 9. 风险与防护
| 风险 | 防护 |
|------|------|
| 调 rubric 放水→负样本漏入 | 每次 rubric 改动后满仓重跑验 negative_reject_rate==1.0；对抗性假阳样本仍被拒（spec SC-004） |
| 把"该拒"硬改成"该过" | P2-c 只标 reject 不改判定使其通过；达标定义="够格全过+该拒全拒" |
| repair location 容差过宽→误改无关小节 | 容差匹配加约束 + 该批 repair 收敛验证 + 异族审 |

## 10. 测试计划
每类改动：离线全绿 + ruff → 满仓重跑（active）验 SC-8 → `/gd review` execution-plus-code 三段闭环 Codex 异族审 APPROVED → 合 main。

## 11. Assumptions
- P2 内容完全由 P1 定性决定，本文件是框架；具体 task packet 在 P1 出口后生成。
- "够格全过 + 该拒全拒"是达标定义，不硬凑 18（handoff §7）。
- 薄源的根治在 v2（多源提炼），P2 对薄源只做正确"拒"+记账。
