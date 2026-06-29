# PLAN 阶段合并报告 — loop1-content-quality-plan

日期：2026-06-28
主 agent 角色：dispatch + merge + final gate（不执行实质规划工作，规划由 2 child_planner 产出）
dispatch_id：loop1-content-quality-plan

## 1. Capability probe
```yaml
CHILD_AGENT_CAPABILITY: available
probe_method: tool_list_inspection
evidence: "Agent (Task) 工具在当前 Claude Code 工具列表，可发起子 agent 并等待返回"
fallback_mode: n_a  # available → 正常 dispatch，未走 manual_packet
```

## 2. 派发结果（wave w1，2 child_planner 并行，max_parallel=2）
| track | agent | subagent_type | output_status | proposal 校验 | deliverable |
|-------|-------|---------------|---------------|---------------|-------------|
| t-p0-planner | child_planner | Plan (opus, 只读无写工具) | completed | gd-validate-child-proposal.py exit 0 | child-drafts/p0-draft.md |
| t-v2-planner | child_planner | Plan (opus, 只读无写工具) | completed | gd-validate-child-proposal.py exit 0 | child-drafts/v2-draft.md |

两 child 均真实派发（非 manual_packet_fallback）；用只读 Plan agent 从工具层强制"不写文件"契约；草稿由主 agent 落盘到各自 owned_path。

## 3. 合并裁决（merge gates G1-G6）
- **G1 格式合规**：两草稿均含唯一 `gd-child-plan-proposal-json` block，过 `gd-validate-child-proposal.py`（exit 0）。✅
- **G2 owned_paths（两层，Round 1 修正）**：(1) PLAN 阶段 child deliverable 仅写 `child-drafts/{p0,v2}-draft.md`（已核实只写这两个文件）✅；(2) child draft 内的 **execute packet proposals** owned_paths 引用工作区实现路径（`src/akb2/quality/**`、`skillpack/quality/rubric/**`、`tests/**`、`evals/**`）——这是**预期的**（它们是未来 execute 阶段要写的实现路径，非 PLAN 阶段越界），仅须禁 `~/.claude`/vault/`..` 穿越（已核实），且 execute 阶段重生成 dispatch-map 过 validator。✅
  > 修正说明：原 G2 文案"child task_packets owned_paths 全在本套件目录内"是误报——把"child deliverable 路径"与"未来 execute packet 路径"混成一条（rev-dispatch P1）。
- **G3 无 review verdict**：两草稿均为 plan 草稿，无 `GD_REVIEW_DECISION`/`REV_VERDICT` 字段。✅
- **G4 SC 绑 verify**：每条 sc_ref 有对应 verify（method ∈ command/path），cmd 可执行。✅
- **G5 并行 wave 全完才合并**：w1 两 child 均 completed 后才进合并。✅
- **G6 冲突仲裁**：两草稿 owned_paths 无重叠（P0 改 grader/distill/admission/raw_verify/repair/runner+rubric；v2 新增 raw_gate/concept_routing/multisource_distill）、无目标冲突。**无冲突，无需仲裁**。✅

## 4. 主 agent 最终化（采纳 + 2 处锚定 + 范围决策）
两 child 草稿质量高（真读源码、定位精确）。主 agent **采纳两者**，最终化为 step 计划：
- step-1-p0-pipeline-honesty.md ← 采纳 t-p0-planner，2 处最终化：
  1. P0-2 真实文件 `distill.py`（child 已核实 compose.py 不存在）。
  2. 顺序约束：P0-4 先合、P0-5 后做 admission 字段；P0-6 最后跑满仓。
  → materialize 6 个 execute-ready task packet（task-packets/p0-*.md）。
- step-4-v2-multisource-distill.md ← 采纳 t-v2-planner；v2 task packet **不预先 materialize**（待 P0 出口后派生，避免受 P0 落地结果漂移）。
- step-2-p1-semantic-audit.md / step-3-p2-classified-remediation.md ← 主 agent 直接起草（P1 是需用户拍的决策门、P2 内容由 P1 定性决定，均不适合 child planner 并行草稿）。

## 5. final_decision
```yaml
final_decision: APPROVED
blocking_buckets: []
rationale: 两 child proposal 校验通过、无冲突、已采纳并最终化为完整计划套件（master + 4 step + 6 P0 task packet + dispatch map）；PLAN 阶段 CAPABILITY_STATUS=local_only（计划生成非生产 final closure）。
```

> 说明：本 final_decision 是 **PLAN 阶段 child 派发合并**的裁决，不等于"计划已获生产批准"。计划的生产闭环（Codex 异族审 APPROVED）在 `/gd review plan`（下一 stage）与各判定级改动 execute 后的 `/gd review` 完成。

## 6. Round 1 worker 深审（3 骨折 gpt5.5 xhigh）findings 与处置

3 worker 全 REQUIRES_CHANGES（拒绝只读，真跑 validator + 开代码核验）。审查原文见 `reviews/worker-rev-{goal,tech,dispatch}-master-review.md`。处置（已落地到 master/step/packet/dispatch-map）：

| # | 来源 | 严重度 | finding | 处置 |
|---|------|--------|---------|------|
| 1 | rev-goal | P1 | master 缺 `gd-step-plan-inventory`/`gd-wave-matrix` 机器块 → consistency preflight `SKIPPED_LEGACY` | master §10 补两块；preflight（不传 --dispatch-map）→ `PREFLIGHT_PASSED` 0 error |
| 2 | rev-goal | P1 | content-lane SC-001/003/004 未硬绑，聚合通过率不足证目标 | 新增 SC-6a/6b/6c 绑 `test_concept_judgment_e2e.py` |
| 3 | rev-goal | P1 | SC-8 "可达上限"无机器可判目标集、Codex 审非真命令 | SC-8 绑 `decision-manifest.json` + 真实 `gd-review-suite-controller.py --kind combined`；P1 SC-7 产出 manifest |
| 4 | rev-goal | P1 | SC-9 聚合无真命令 | 拆 SC-9a..9e 绑真实 pytest |
| 5 | rev-tech | P1 | P0-4 family 名 `deepseek` 不存在（只 `deepseek-v4-pro`），字面改会 RouterConfigError 全 fail-closed | master SC-4 + p0-4 packet + step-1 全改 `deepseek-v4-pro` |
| 6 | rev-tech | P1 | v2 raw_gate 接在 admission 后 → 薄源被 novelty/lane 先拒，违 v2 语义 | step-4 §6 改"raw 前置门必须在现有 admission 之前/拆 raw_relevance+page_admission" |
| 7 | rev-tech | P1 | P0-1 child 草稿要求同步 DEFAULT/global 阈值，破 lane-aware | p0-1 packet + step-1 改"只 lanes.concept=6.0，保留其余 6.5" |
| 8 | rev-dispatch | P1 | dispatch-map G2 语义误报（混 child deliverable 与 execute packet 路径） | dispatch-map + 本报告 G2 改两层 |
| 9 | rev-goal/tech | P2 | master Step1 owned_paths 含不存在 compose.py、漏 raw_verify.py | master §5 表已改 distill.py + raw_verify.py |
| 10 | rev-tech | P2 | P0-2 must-cover 渲染"已存在且 test 绿"，前提过时 | p0-2 packet + step-1 改"不盲改，先复验满仓定位失配" |
| 11 | rev-tech | P2 | P0-6 漏 `AKB2_AMENDMENT_APPLIED` 对 report promotion_mode 影响 | p0-6 packet + master SC-6 + step-1 补口径二选一 |
| 12 | rev-dispatch | P2 | P0-4/P0-5 共享 `test_admission_lane.py` 漏豁免 | p0-5 改用新建 `test_admission_observability.py`，不共享 |
| 13 | rev-goal | P3 | NON_GOALS 漏 embedding 不归组 / v1 不改写已入库页 | master §4 补两条 |
| — | rev-dispatch | P2（跨库，不在本 loop 改） | `gd.md` controller-report prose↔schema 漂移 | **surface 不改**：gd.md 属 Project GD 框架库，非本 AKB2 计划包边界；本套件 controller-report 已 schema 合规。交 GD 维护者 |

**已核实通过（worker 确认无误）**：三条 GD validator + master-consistency preflight 全 exit 0；4 项 hash 链全对齐；controller-report `jobs:[]+secondary_gate=not_run+gate_consistent:false` 诚实表达 plan 阶段无 Codex 二审，无夸大；P1/P2/v2 缓发 packet 范围决策合理（防漂移，非合约缺口）。

> Round 1 修正后本报告内容变更 → 其 sha256 已更新到 `ledger/stage-dispatch-ledger.json.main_agent_merge.merge_report_hash`，ledger sha256 同步更新到 `reports/controller-report.json.batch_ledgers[0].hash`（见证据链重算）。

## 7. Round 2/3：rev-goal 残留 + 用户裁决（Option B：先建脚手架）

R2 后 rev-tech APPROVED；rev-dispatch 仅余 SC-5 verify 未跟上拆分（已修：SC-5 verify 指向新建 `tests/quality/test_admission_observability.py`）；rev-goal 余 2 条结构性 P1：SC-8 verifier 脚本不存在、SC-9 pytest selector 0-selected。我已把其"可落地修法"全做完（声明 deliverable + 创建动作 + 具体命令），唯"未来产物 plan-time 不存在"无法消除——遂上交用户裁决。

**用户选 Option B：现在就写可跑脚手架**（任务从"出计划"扩为"出计划 + 验收脚手架"）。已建并验证（守住"不造假 pass"诚实边界）：
- `scripts/verify_loop1_reachable_ceiling.py`：SC-8 第一条 verify 的真实验收器（纯 JSON 比对，不依赖未建模块）。`--self-test` 5 例全过（1 正 + 4 负：ingest 项 fail / reject 漏放 / nrr≠1 / fixture 找不到）、`--help` exit 0、ruff 干净。完整运行待 P0 factory.json + P1 decision-manifest.json。
- `tests/quality/test_multisource_distill.py`（4 用例：synthesis/incoherent/fast_path_1to1/no_wiki_mutation）+ `tests/quality/test_raw_gate.py`（3 用例）：SC-9 验收锚。函数级 `importorskip("akb2.quality.{multisource_distill,raw_gate}")` → `pytest -k <selector>` 现**命中**（exit 0 "1 skipped"，不再 0-selected/exit5）；v2 模块未建时诚实 skip，建好自动转真。ruff 干净、concept_judgment_e2e 10 passed 不回归。
- `tests/quality/test_admission_observability.py`（2 用例：lane_reason 透出 / repair 逐轮 final_defects）：**SC-5 验收锚**（rev-dispatch R3 额外查到此文件原未落盘——SC-5 verify 引用它，留着会与 SC-8/SC-9 同病 file-not-found；为 Option B 口径一致补建）。能力探测 + skip：P0-5 未实现时 skip。SC-5 声明命令实跑 `pytest tests/quality/test_repair_loop.py tests/quality/test_admission_observability.py` = **43 passed, 2 skipped, exit 0**、ruff 干净。

诚实声明：skip ≠ pass；这些是"先写验收契约、实现期转真"的脚手架，不是 fake 绿测试（宪法变更纪律 4 / 编码规则 9）。SC-8 第二条 combined-review 命令仍需 P2 执行产物 + Codex transport，属未来验收。

## 8. Round 2：步骤子计划深审（3 worker 各审一片）findings 与处置

按并行串行原则把步骤子计划分三片：rev-tech=step-1+6 P0 packet；rev-goal=step-2+step-3+verifier；rev-dispatch=step-4+v2 脚手架+跨步骤一致性。

**rev-tech（step-1 + P0 packet，4 条已全修，re-confirm 中）**：
- P1：SC-6a/6b/6c 没 packet 承接 → p0-6 §5+§7 补三条 content-lane 验收命令 + handoff 证据要求。
- P2：p0-2 主体仍"改渲染"口径 → 整篇重写为"先复验 #0/2/8/17、不盲改绿路径、只在证据指向 distill 失配才改"。
- P2：p0-4 规范字段泛称 deepseek → 整篇重写为 `deepseek-v4-pro`（grep 确认无裸规范 deepseek）。
- P2：p0-1/p0-5 deliverables 漏新增测试/同步文件 → 补 10-dimensions.md + test_grader_score_required.py（p0-1）、test_admission_observability.py（p0-5）。

**rev-goal（step-2/3 + verifier，3 条已修，含一条真 bug，re-confirm 中）**：
- **P1（verifier 真 bug）**：`verify_loop1_reachable_ceiling.py` 原对 `expected=reject` 只断言 `passed==False`——但 factory 实测 schema 里"正确拒"是 `passed==True + final_status=="rejected_correctly"`，`passed==False` 反而匹配"正样本 repair 不收敛失败"。已修：reject → 要求 `passed==True 且 final_status∈{rejected_correctly,...}`；新增 self-test 锁"repair 失败被误当 reject 通过"必 FAIL。
- P1：manifest 缺 verdict↔expected_after_p2 不变量 → verifier 加硬不变量（拒↔reject、救/调↔ingest，违反即 fail）；step-2 manifest 契约 + SC-7 verify-2 落地。
- P2：SC-7 未机判 manifest 覆盖 → verifier 加 `--mode coverage`（断言 factory 全部失败正样本被 manifest 覆盖）；step-2 SC-7 verify-2 + master §10 SC-7 verify 改用 `verify_loop1_reachable_ceiling.py --mode coverage`。
- 修后 verifier `--self-test` = SELF_TEST_PASSED（7 例，含 reject 正确拒 / repair 失败误判拦 / 不变量 / coverage）、ruff 干净。

**rev-dispatch（step-4 + v2 脚手架 + 跨步骤，2 条已修，re-confirm 中）**：
- P2：step-4 §7/§8 没同步 master §10 已列的 `tests/quality/test_multisource_distill.py` + `test_raw_gate.py` → step-4 §7 task 表把两测试归入 v2-3/v2-1 owned、§8 边界补列（与 master §10 对齐，execute 边界不漂移）。
- P2：master §10 verify 的裸 `python3 -m pytest` 在裸 shell 解析到 /usr/bin/python3（无 pytest）exit 1 → master §10 全部 17 条 verify cmd 统一加 `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH` 前缀（与 packet 一致）；抽 SC-9a 原样跑 exit 0。
- 因 verifier 已 Option B 建好，从 step-3 deliverable/owned 摘除（改 required_context），消除 step-2 SC-7 引用它的 VERIFY_FUTURE_REFERENCE；preflight 复跑 0 error。
- rev-dispatch 还核实通过：scaffolds 函数级 importorskip 诚实 skip、selector 与 SC-9 对齐、raw_gate 前置表达清楚、wave 顺序隔离 step-1/step-4 重叠。

> rev-dispatch 另一条 R3 master 额外观察（test_admission_observability.py 未落盘）已在 §7 处置：补建为 SC-5 脚手架。
