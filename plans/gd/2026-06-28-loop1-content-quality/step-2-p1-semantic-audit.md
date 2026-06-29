# Step 2: P1 12 篇语义审计（决策门，需用户再拍一次）v1

GD_STANDARD: /Users/praise/AI-Agent/Claude/projects/Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-step-plan

日期：2026-06-28
状态：draft（主 agent 直接起草——P1 是决策门，不适合作为 child planner 并行草稿）
负责人：Claude 派审计 worker；裁决由用户拍

> **为何主 agent 直接起草**：P1 的产物是"对 12 篇逐篇定性（救/调/拒）"，这是**需用户拍板的决策**且**强依赖 P0 重跑的真实 trace**——不是可由独立 child planner 离线起草的实现计划。故不进 PLAN 阶段 child dispatch。

## 1. 目标链
```text
PROJECT_GOAL: AKB2 fail-closed 门控 + 异族 Maker/Checker + 实测价值裁决（ref docs/constitution.md 原则 8 实测价值裁决）
CHAIN_GOAL:   循环1 按内容车道分流（ref specs/content-lane-quality/spec.md SC-001/003/004）
PHASE_GOAL:   用 P0 重跑后的真实数据（trace + lane_reason）给每篇失败稿定性，区分"机制可修 / grader 太苛 / 源真薄"，为 P2 提供分类依据
TASK_GOAL:    对 P0 重跑后剩余失败篇（≤12）逐篇产出 {救|调|拒} 标签 + 三方比对依据
```

## 2. Review 对齐
- REVIEW_DOMAIN：`ai_infra`（实为分析/裁决任务，归 ai_infra）
- REVIEW_FOCUS：定性基于真实 trace 非臆测; "该拒"是正确归类不是失败; 不预设结论硬凑通过率; 审计 worker 只读不改判定
- Domain-specific notes：本 step 不改任何代码/判定；产物是审计记录 + 用户裁决。

## 3. 前置条件
- blocked_by：**Step 1（P0）必须出口完成**——P0-6 满仓重跑产出带 trace + lane_reason 的 factory.json + ledger，否则无真实数据可审（handoff §7：现在拍是瞎猜）。
- 必须的 baseline/artifact：`reports/calibration/factory.json`（P0 重跑后）、`~/Obsidian/Praise Space/AKB2.0/staging/quality-ledger.jsonl`（abort 行带 final_defects + repair_trace_paths）、各篇 repair trace。
- Hard-stop：P0 重跑未完成 / trace 缺失 → 停，不在无 trace 下审计。

## 4. 成功标准（SC，对齐 master SC-7）
- [ ] SC-7：P0 重跑后剩余失败篇逐篇产出定性标签 + 依据，并落机器可读 `reports/loop1-audit/decision-manifest.json`（供 P2 SC-8 复验消费）。
  - verify-1 (method: path)：`test -d reports/loop1-audit && ls reports/loop1-audit/*.md | wc -l` ≥ 剩余失败篇数；每份含 `verdict: 救|调|拒` + raw 原文/候选页/grader 指控三方引用。
  - verify-2 (method: command)：用 SC-8 同一验收器的 **coverage 模式**机判 manifest 合法性 + 覆盖度（对 P0 重跑 factory）——`python3 scripts/verify_loop1_reachable_ceiling.py --mode coverage --factory reports/calibration/factory.json --decisions reports/loop1-audit/decision-manifest.json`，断言 `REACHABLE_CEILING_PASSED`。它强制两条不变量（rev-goal Round 2）：(a) **verdict↔expected_after_p2 一致**（救/调→ingest、拒→reject，防用户拍的 verdict 被字段绕过）；(b) **manifest 覆盖 factory 全部失败正样本**（expected=ingest 且 passed=False，防漏审）。
  - expect：每篇一份审计记录（标签 ∈ {救,调,拒} + trace 引用）+ verifier coverage 模式 PASS（不变量 + 全覆盖）。
  - manifest 契约（机判前提）：每条 `{fixture_id（factory index 或 url）, verdict∈{救,调,拒}, expected_after_p2∈{ingest,reject}}`，且 **拒↔reject、救/调↔ingest** 为硬不变量（verifier 拒绝违反者）。

## 5. 非目标
- 不在本 step 改任何代码、rubric、corpus（那是 P2）。
- 不预先决定通过率目标（"该拒"的正确归类即成功）。

## 6. 实现步骤
```text
Step.1
  WHERE: reports/calibration/factory.json（P0 重跑后）+ quality-ledger.jsonl
  WHAT:  读 P0 重跑结果——通过率、剩几篇失败、各卡哪段（per_segment）、每篇 lane_reason + repair 逐轮 final_defects 演化
  WHY:   建立 P1 的现状基线（master SC-7 的输入）；P0 修完可能已救活若干篇，先看真实剩余集
  VERIFY: 能列出"P0 后仍失败的具体篇号 + 各自卡点段 + lane_reason"清单

Step.2
  WHERE: 派 ≤2 个只读审计 worker（per 批分摊剩余失败篇）；产物落 reports/loop1-audit/<篇号>.md
  WHAT:  逐篇三方比对：raw 原文（讲了什么机制/知识）vs 候选页（提炼出什么）vs grader 指控（扣在哪维/哪条 defect）→ 裁决：
         - 救（机制可修）：raw 里有真机制、候选页因 location 对不上/A-B 拉扯/补回填没补上而没收敛 → P2-a 改 repair
         - 调（grader 太苛）：raw 机制够、候选页其实达标，但 grader 某维判据/权重过严误杀 → P2-b 调 rubric 参数
         - 拒（源真薄）：raw 本身对任何概念零合格贡献/机制讲不清 → 正确归类为拒（P2-c corpus 标 reject）
  WHY:   gap1「光看 ledger 判不了，需独立语义审计」；区分三类决定 P2 各篇走哪条修复路径
  VERIFY: reports/loop1-audit/ 下每篇一份，含 verdict + 三方引用 + 依据段落

Step.3
  WHERE: 本 step 产物汇总 → 提交用户
  WHAT:  把 12 篇（或 P0 后剩余数）的 {救|调|拒} 分布 + 依据汇总，**请用户逐篇/分组确认**
  WHY:   handoff §6：这是唯一需用户再拍一次的点；P2 内容完全由此定性决定
  VERIFY: 用户确认每篇归类（救/调/拒），形成 P2 输入
```

## 7. Task Packet 拆分
| task_id | agent_role | owned_paths | blocked_by | can_parallel_with |
|---------|-----------|------------|-----------|-------------------|
| p1-audit-batch-a | child_validator（只读） | reports/loop1-audit/ | step 1(P0) | p1-audit-batch-b |
| p1-audit-batch-b | child_validator（只读） | reports/loop1-audit/ | step 1(P0) | p1-audit-batch-a |

> 两批审计 worker owned_paths 同为 reports/loop1-audit/ 但写不同篇号文件——execute 阶段 dispatch map 须按"不同篇号文件"细分 owned_paths 避免重叠告警，或单 worker 串行审。最终化 packet 待 P0 出口后按真实剩余失败集生成。

## 8. 边界
修改：`reports/loop1-audit/`（审计记录）。
不修改：任何源码/rubric/corpus（P2 才改）、`~/.claude/**`、vault。

## 9. 风险与防护
| 风险 | 防护 |
|------|------|
| 审计 worker 预设结论硬凑"该救" | worker 只读 + 三方比对强制引用 raw 原文；"拒"是合法且常见结论（handoff §7：90% 可能不可达且不是 bug） |
| 无 trace 下臆测 | blocked_by P0 硬阻塞；Hard-stop trace 缺失即停 |

## 10. 测试计划
```bash
test -d reports/loop1-audit && ls reports/loop1-audit/*.md
grep -l 'verdict:' reports/loop1-audit/*.md | wc -l   # 每篇有裁决
```

## 11. Assumptions
- P0 修完后剩余失败篇可能 < 12（#4 等若干篇 P0 后转过）。
- "该拒"的正确归类是成功，不是失败——达标定义="够格全过 + 该拒全拒"（master SC-8）。
- 用户在本 step 末拍板，是整条链唯一需再次决策点。
