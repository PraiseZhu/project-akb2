# 循环1 内容车道质量 — 满仓 0/18 收口 Master Plan v1

GD_STANDARD: /Users/praise/AI-Agent/Claude/projects/Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md + specs/content-lane-quality/spec.md + specs/multi-source-distill/spec.md
TEMPLATE_KIND: gd-master-plan

日期：2026-06-28
状态：draft
负责人：Claude 执行；Codex 异族 cross-review（判定级改动合 main 前必过）

> **GOAL_SOURCE 偏离说明（用户已授权）**：本套件不引用 GD 插件硬编码的 `Project GD/docs/gd-v7-project-goal.md`（GD v7 反填表目标，锚不上 AKB2 工作），改锚 AKB2 自身目标文档：工程宪法 `docs/constitution.md`（v1.2.0）+ feature spec `specs/content-lane-quality/spec.md` + `specs/multi-source-distill/spec.md`。
> **配套工件**：执行计划 `plans/gd/2026-06-28-loop1-execution-plan.md`、交接档 `plans/gd/2026-06-28-loop1-handoff.md`、记忆 `project_akb2_loop1_bottleneck_priority_2026-06-28`。

---

## 1. 目标链

```text
PROJECT_GOAL: （ref docs/constitution.md 原则 3/6/7/8）AKB2 是无人值守自动化知识库工程：fail-closed 质量门控 + 异族 Maker/Checker 分离 + 实测价值裁决，把达黄金样张级的合格知识入库、把不合格的拒在门外。
CHAIN_GOAL:   （ref specs/content-lane-quality/spec.md）循环1 是知识库唯一入库出口；按内容车道（概念型/操作型）分流质量标准，让概念型与操作型知识在不破完整性地板的前提下都能入库，使知识库从"命令清单库"回到"概念图谱知识库"。
PHASE_GOAL:   把满仓校准从正样本 0/18 推到"够格全过 + 该拒全拒"——P0 诚实化当前管线 + 补观测；P1 用真数据逐篇定性 12 篇失败；P2 据定性分类修复；v2 为"薄但有效"的源上多源提炼不丢料。可被 reports/calibration/factory.json 的 positive_pass_rate 从 0 动起、negative_reject_rate 维持 1.0、Codex 异族审 APPROVED 共同验证。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS（分号分隔）：fail-closed 门控不被绕过; Maker/Checker 异族分离不被破坏（grader 保留独立硬判）; 完整性地板不可被加权分补偿; 预算/失败可见（部分失败不报成功）; 测试断言绑可观测行为（收敛/门控/预算）非"能跑通"
- Domain-specific notes：判定级改动（grader/repair/admission/rubric）改完必过 Codex 异族审再合 main；不靠造假绿测试；不收窄 operations.scan；负样本必拒。

---

## 3. 成功标准（SC）

> Anti-fill：每条 SC 绑定命令/路径/输出断言/人工验收条件。SC-1..SC-6c 属 P0；SC-7 属 P1；SC-8 属 P2；SC-9a..SC-9e 属 v2。
> （Round 1 worker 审查修订：SC-6 拆出 content-lane 硬绑子项 SC-6a/6b/6c；SC-8 绑机器可读 decision-manifest + 真实 combined review 命令；SC-9 拆为 SC-9a..9e 真实命令；详见 reviews/ 与 merge-report.md §6。）

- [ ] SC-1：concept grader 每维**强制产出** 0~1 的 score（schema required + 0~1，prompt 钉尺度），weighted_score 可由各维 score 复算且非零；grader 未产出逐维 score 或 score 非数值/越界时 weighted_score fail-closed（不放行）。verify：黄金样张 #4 走 concept 通路后 `weighted_score >= 6.0` 且不再 `weighted_score_below_threshold`；离线锁"grader 缺逐维 score / score>1 → fail-closed"。
- [ ] SC-2：满仓 #0/#2/#8/#17 的 must-cover 卡点根因被定位并解除（**注意：`distill.py` 的 source_operation 直出渲染已存在且 `test_distill_must_cover.py` 现绿——不盲改绿路径**）。verify：先复验满仓 #0/2/8/17 是否仍卡 SG-must-cover；若仍卡，定位 scanner item / raw_content 传递 / section 抽取哪一环失配再改对应点；离线 `test_distill_must_cover.py` 保持绿。
- [ ] SC-3：raw_verify 命令形态判定区分真命令与散文/术语/表格碎片（单字中文、含 `|` 表格片段、纯术语不算命令）。verify：离线断言 `和`/表格碎片/术语不报、`pip install <fabricated>` 与 `deploy-preview --target staging` 类带参工具命令仍回查；满仓 #16 不再卡 distill_compose。
- [ ] SC-4：准入 topic/how/novelty/lane 判定由 gemini-flash 切到 **deepseek-v4-pro**（llm_router 实际支持的 family；字面 `deepseek` 会 RouterConfigError 让准入全 fail-closed），不破异族（准入判源不判提炼产物，grader 仍 gpt-5.4）。verify：离线全绿；满仓 ledger 的 admission 段 model_family 为 `deepseek-v4-pro`。
- [ ] SC-5：repair 逐轮 final_defects 落可读位置、admission pass/abort 两路 ledger 均写 lane_reason，且判定/收敛行为不变。verify：重跑后逐篇可读 repair 缺陷演化 + lane_reason；离线判定测试仍绿（观测不改判定）。
- [ ] SC-6：满仓以 active e2e 重跑完成（`AKB2_AUTO_PROMOTE=1` 真入库；若验收读 report `promotion_mode=active` 则一并设 `AKB2_AMENDMENT_APPLIED=1` 并说明修宪已应用，否则以 runner/promote/final_status 证据判真入库），positive_pass_rate 从 0.0 动起、negative_reject_rate 维持 1.0。verify：`reports/calibration/factory.json` 的 `positive_pass_rate > 0` 且 `negative_reject_rate == 1.0`；report 带 trace + lane_reason。
- [ ] SC-6a（content-lane SC-001）：纯概念 0-command 正样本入库。verify：`pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "concept_positive"` passed；factory.json 该 fixture `content_lane=="concept"`、`passed==true`、`weighted_score>=threshold`。
- [ ] SC-6b（content-lane SC-003）：三类低质负样本（机制糊清单/无据读后感/洗稿）分别 rejected 且能定位拦截项。verify：`pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "negatives"` passed；失败原因分别归 core_value_f4 / traceability / novelty 对应门。
- [ ] SC-6c（content-lane SC-004）：地板不可补偿对抗样本被拒（核心价值不足却堆高次要维度）。verify：`pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "adversarial"` passed；floor_failed 含 core_value_f4、weighted 未补偿放行。
- [ ] SC-7：P0 重跑后对剩余失败篇逐篇产出"救/调/拒"定性 + 依据，并落机器可读 `reports/loop1-audit/decision-manifest.json`（每条 `{fixture_id, verdict∈{救,调,拒}, expected_after_p2∈{ingest,reject}}`）。verify：`reports/loop1-audit/` 每篇一份记录（标签 ∈ {救,调,拒} + 三方 trace 引用）且 decision-manifest.json 覆盖全部剩余失败篇。
- [ ] SC-8：据 P1 定性分类修复后，满仓达"够格全过 + 该拒全拒"——以 decision-manifest 为机器可判目标集（`expected_after_p2=ingest` 全 `passed`、`=reject` 全 `rejected`、`negative_reject_rate==1.0`），且 Codex 异族审 APPROVED。verify：对照 decision-manifest 复验 factory.json（所有 ingest 项 passed、reject 项 rejected、负样本 100%）；Codex 异族审用真实命令 `gd-review-suite-controller.py --kind combined --target execution_artifact=<…> --target code_diff=<…> --out-dir <…> --live-transport --compat-v1`，断言 aggregate `GD_REVIEW_DECISION: APPROVED`。
- [ ] SC-9a（v2 spec SC-1）：薄源 ×K 合成出过门新页。verify：`pytest -k "multisource and synthesis" -q`；单源 floor fail、N 源合成后 F4 pass 且 `weighted_score>=threshold`。
- [ ] SC-9b（v2 spec SC-2）：不相干混料被 F4 拒。verify：`pytest -k "multisource and incoherent" -q`；`evaluate_integrity_floor` failed_floors 含 core_value_f4，未入库。
- [ ] SC-9c（v2 spec SC-3）：raw 门不误杀薄但有效源、零贡献拒。verify：`pytest -k "raw_gate" -q`；薄但 ≥1 合格贡献进池、新闻/营销/零贡献 rejected。
- [ ] SC-9d（v2 spec SC-4）：厚源走 1:1 快路径。verify：`pytest -k "fast_path_1to1" -q`；`route=="fast_1to1"` 不写 pending 槽。
- [ ] SC-9e（v2 spec SC-5）：v1 不改写已入库 wiki 页。verify：`pytest -k "multisource and no_wiki_mutation" -q`；`wiki/**/*.md` 既有页 mtime/content 快照不变。

---

## 4. 非目标（NON_GOALS）

- 不做 update（新 raw 并入已有 wiki 页 + merge + 重评分 + 版本）—— 属 v2 之后。
- 不做升星（wiki→wiki 无新料的二度/三度深化推高 How 密度）—— 独立未来，与多源提炼正交。
- 不做内容抓取层实时源失效修复（SC-1 验证可用 fixture/原文绕开实时抓取）—— 属独立工作项。
- 不放宽 concept F4 完整性地板、不豁免 novelty、不收窄 operations.scan。
- 不用 embedding 相似度做最终归组/聚类；embedding 仅作新概念去重召回，最终同一概念判定由 deepseek 按离散概念身份裁决（v2 决策）。
- v2 v1 仅 create 新页，不改写任何已入库 wiki 页；update/merge 已有页属后续版本。

---

## 5. Step 拆分

| Step | 名称 | owned_paths（实现期，execute 阶段细化） | blocked_by | can_parallel_with | 主要 SC |
|------|------|------------------------------------------|-----------|-------------------|---------|
| 1 | P0 当前管线诚实化 + 补观测 | src/akb2/quality/{weighted_score,grader,distill,checks_structural,admission,repair,runner}.py、src/akb2/quality/source_ops_scanners/raw_verify.py、skillpack/quality/rubric/、tests/、reports/calibration/ | — | — | SC-1..SC-6c |
| 2 | P1 12 篇语义审计（决策门） | reports/loop1-audit/ | step 1 | — | SC-7 |
| 3 | P2 据定性分类修复 | （P1 定）src/akb2/quality/{repair,admission}.py、skillpack/quality/rubric/、evals/main-pipeline-quality/corpus.yaml | step 2 | — | SC-8 |
| 4 | v2 多源提炼 | src/akb2/quality/（raw 门/概念路由/N:1 合成）、evals/、src/akb2/quality/calibration.py | step 1 | — | SC-9a..SC-9e |

> step 1 与 step 4 都触及 `src/akb2/quality/`，owned_paths 重叠 → 实现期（execute 阶段）**不可并行**，须各自独占 wave。本 master plan 的 Step 表是逻辑拆分；execute 阶段并行性以届时生成并通过 `gd-validate-dispatch.py` 的 execute dispatch map 为准。

---

## 5a. Dispatch Map / Wave Contract（MANDATORY — 本次为 PLAN 阶段 child_planner 派发）

> 本套件由 PLAN 阶段 2 个 child_planner 并行起草 P0 与 v2 的 step plan 草稿，主 agent 合并。下方 dispatch map 是 **PLAN 阶段**的派发证据（不是 execute 阶段的执行派发）。execute 阶段的实现派发 map 在 `/gd execute` 时另生成。

### Dispatch Map 引用

```
DISPATCH_MAP_PATH: plans/gd/2026-06-28-loop1-content-quality/dispatch-map.json
VALIDATE_CMD: python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-dispatch.py plans/gd/2026-06-28-loop1-content-quality/dispatch-map.json
```

### Wave Matrix（PLAN 阶段）

| Wave | Tracks（同 wave 可并行） | 并行前提验证 |
|------|--------------------------|-------------|
| 1 | t-p0-planner, t-v2-planner（child_agent_count=2） | can_parallel_with 对称声明；owned_paths（child-drafts/p0-draft.md vs v2-draft.md）不重叠；无 blocked_by |

> 规则：同 wave ≤2 track（max_parallel=2 硬上限）；主 agent 先跑 VALIDATE_CMD（exit 0）再按 wave dispatch；P1/P2 step 由主 agent 直接起草（P1 是需用户再拍的决策门、P2 内容由 P1 定性结果决定，不适合作为独立 child planner 草稿并行产出）。

---

## 6. 边界（修改 / 不修改）

修改（本计划套件目录内）：
- `plans/gd/2026-06-28-loop1-content-quality/**`

不修改：
- 旧 `/rev` 任何 artifact
- `/Users/praise/.claude/**`
- 实现期源码（本次只产计划，不动 src）

---

## 7. 风险与防护

| 风险 | 防护 |
|------|------|
| 把"达标"误设为硬凑 18 篇 → 把薄源硬塞入库 | SC-8 达标定义="够格全过 + 该拒全拒"；P1 审计后"该拒"的正确归类为成功，不是失败 |
| P0 修加权分后放松了完整性地板（加权分补偿地板） | 宪法原则 8：完整性地板二值硬判，不可被加权分补偿；P0-1 仅修"缺 score 静默当 0"，不动地板 |
| 准入切 deepseek 破坏异族（maker 与 checker 同族） | 准入判源不判提炼产物，grader 仍 gpt-5.4 独立硬判；保持 maker=deepseek/checker=gpt-5.4 两模型异族 |
| child planner 草稿越界写文件 | child_planner 契约禁写文件，只回草稿；主 agent review 越界打回 |

---

## 8. 测试计划

```bash
# PLAN 阶段产物校验（本次可执行）
GD=/Users/praise/AI-Agent/Claude/projects/Project\ GD
python3 "$GD/scripts/gd-validate-dispatch.py" plans/gd/2026-06-28-loop1-content-quality/dispatch-map.json
python3 "$GD/scripts/gd-validate-stage-dispatch-ledger.py" plans/gd/2026-06-28-loop1-content-quality/ledger/stage-dispatch-ledger.json
python3 "$GD/scripts/gd-validate-controller-report.py" plans/gd/2026-06-28-loop1-content-quality/reports/controller-report.json
test -f plans/gd/2026-06-28-loop1-content-quality/step-1-p0-pipeline-honesty.md
# 实现期验证（execute 阶段）：见各 step plan 与 task packet 的 verify 字段
```

---

## 9. Assumptions

- 已确认：worktree=`/Users/praise/AI-Agent/Claude/projects/akb2-integ-wt`，分支 `integration/loop1-content-quality`，HEAD=5dde324，本会话 4 commit 已落、离线 1633 绿。
- 已确认：满仓最新 positive_pass_rate=0.0、negative_reject_rate=1.0（reports/calibration/factory.json，calibration_ts 2026-06-28T02:46Z）。
- 已确认（用户锁定）：concept 阈值 6.5→6.0；满仓走 active e2e；准入用 deepseek 砍 gemini；raw 与 wiki 同标准差完成度。
- 已确认（用户授权）：GOAL_SOURCE 偏离为 AKB2 目标文档。
- P1 的逐篇"救/调/拒"是唯一需用户再拍一次的决策点（须 P0 重跑带 trace 后才有实据）。

---

## 10. 机器可读 step inventory + wave matrix（GD master-plan consistency preflight 消费）

> Round 1 worker 审查修订新增。preflight 跑法（**不传 --dispatch-map**：本套件 dispatch-map.json 是 PLAN 阶段 child_planner 派发，非 execute 阶段 step 派发；MASTER_PLAN_DISPATCH_DRIFT 检查留待 /gd execute 时对 execute dispatch map 跑）：
> `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-master-plan-consistency.py" plans/gd/2026-06-28-loop1-content-quality/master-plan.md` → 期望 `PREFLIGHT_PASSED`。

```json gd-step-plan-inventory
{
  "step_plans": [
    {
      "step_id": "step-1-p0",
      "status": "reviewed",
      "task_packet_mode": "file",
      "task_packets": ["p0-1-weighted-score", "p0-2-must-cover", "p0-3-command-form", "p0-4-admission-deepseek", "p0-5-observability", "p0-6-active-rerun"],
      "blocked_by": [],
      "owned_paths": ["src/akb2/quality/", "skillpack/quality/rubric/", "tests/", "reports/calibration/"],
      "forbidden_paths": ["/Users/praise/.claude/", "reports/loop1-audit/", "evals/"],
      "required_context": ["plans/gd/2026-06-28-loop1-execution-plan.md", "docs/constitution.md", "specs/content-lane-quality/spec.md"],
      "deliverables": [
        {"path": "reports/calibration/factory.json", "kind": "file", "must_exist": true},
        {"path": "skillpack/quality/rubric/rubric.yaml", "kind": "file", "must_exist": true},
        {"path": "tests/quality/test_grader_score_required.py", "kind": "file", "must_exist": true}
      ],
      "sc_refs": ["SC-1", "SC-2", "SC-3", "SC-4", "SC-5", "SC-6", "SC-6a", "SC-6b", "SC-6c"],
      "verify": [
        {"sc_ref": "SC-1", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_weighted_score.py tests/quality/test_grader_score_required.py", "expect": "passed; 缺逐维 score / score>1 → fail-closed; #4 weighted>=6.0"},
        {"sc_ref": "SC-2", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_distill_must_cover.py", "expect": "passed; 不盲改绿路径, 先复验满仓 #0/2/8/17 定位失配"},
        {"sc_ref": "SC-3", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/test_raw_verify_command_shape.py", "expect": "passed; 和/表格碎片/术语不报, 带参工具命令仍回查"},
        {"sc_ref": "SC-4", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/test_admission_lane.py tests/quality/test_admission_positive.py tests/quality/test_admission_negative.py", "expect": "passed; model_family==deepseek-v4-pro"},
        {"sc_ref": "SC-5", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_repair_loop.py tests/quality/test_admission_observability.py", "expect": "passed; lane_reason 可读, 判定不变"},
        {"sc_ref": "SC-6", "method": "assertion", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -c \"import json;d=json.load(open('reports/calibration/factory.json'));assert d['positive_pass_rate']>0 and d['negative_reject_rate']==1.0\"", "expect": "exit 0"},
        {"sc_ref": "SC-6a", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k concept_positive", "expect": "passed; concept 0-command 正样本入库"},
        {"sc_ref": "SC-6b", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k negatives", "expect": "passed; 三类负样本各被对应门拒"},
        {"sc_ref": "SC-6c", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k adversarial", "expect": "passed; 地板不可补偿对抗样本被拒"}
      ]
    },
    {
      "step_id": "step-2-p1",
      "status": "draft",
      "task_packet_mode": "deferred",
      "task_packets": ["p1-audit-batch-a", "p1-audit-batch-b"],
      "blocked_by": ["step-1-p0"],
      "owned_paths": ["reports/loop1-audit/"],
      "forbidden_paths": ["/Users/praise/.claude/", "src/", "skillpack/", "evals/"],
      "required_context": ["reports/calibration/factory.json"],
      "deliverables": [
        {"path": "reports/loop1-audit/decision-manifest.json", "kind": "file", "must_exist": true}
      ],
      "sc_refs": ["SC-7"],
      "verify": [
        {"sc_ref": "SC-7", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 scripts/verify_loop1_reachable_ceiling.py --mode coverage --factory reports/calibration/factory.json --decisions reports/loop1-audit/decision-manifest.json", "expect": "REACHABLE_CEILING_PASSED：verdict↔expected_after_p2 不变量(救/调→ingest、拒→reject) + manifest 覆盖 factory 全部失败正样本(不漏审)"}
      ]
    },
    {
      "step_id": "step-3-p2",
      "status": "draft",
      "task_packet_mode": "deferred",
      "task_packets": ["p2-a-repair-loc-tolerance", "p2-b-rubric-tuning", "p2-c-corpus-reject", "p2-d-lane-fix"],
      "blocked_by": ["step-2-p1"],
      "owned_paths": ["src/akb2/quality/repair.py", "src/akb2/quality/admission.py", "skillpack/quality/rubric/", "evals/main-pipeline-quality/", "reports/calibration/", "plans/gd/2026-06-28-loop1-content-quality/reports/p2/"],
      "forbidden_paths": ["/Users/praise/.claude/", "reports/loop1-audit/"],
      "required_context": ["reports/loop1-audit/decision-manifest.json", "reports/calibration/factory.json", "scripts/verify_loop1_reachable_ceiling.py"],
      "deliverables": [
        {"path": "plans/gd/2026-06-28-loop1-content-quality/reports/p2/execution-result.json", "kind": "file", "must_exist": true},
        {"path": "plans/gd/2026-06-28-loop1-content-quality/reports/p2/diff.patch", "kind": "file", "must_exist": true}
      ],
      "sc_refs": ["SC-8"],
      "verify": [
        {"sc_ref": "SC-8", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 scripts/verify_loop1_reachable_ceiling.py --factory reports/calibration/factory.json --decisions reports/loop1-audit/decision-manifest.json", "expect": "ingest 项全 passed; reject 项全 rejected; negative_reject_rate==1.0（verifier 脚本已建+--self-test 过；plan-time 可 --self-test 自证逻辑，完整运行待 P0 factory.json + P1 decision-manifest.json）"},
        {"sc_ref": "SC-8", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 /Users/praise/AI-Agent/Claude/projects/Project\\ GD/scripts/gd-review-suite-controller.py --target-set-id loop1-content-quality-p2 --kind combined --cwd /Users/praise/AI-Agent/Claude/projects/akb2-integ-wt --target execution_artifact=plans/gd/2026-06-28-loop1-content-quality/reports/p2/execution-result.json --target code_diff=plans/gd/2026-06-28-loop1-content-quality/reports/p2/diff.patch --out-dir plans/gd/2026-06-28-loop1-content-quality/reviews/codex-p2 --live-transport --compat-v1", "expect": "aggregate GD_REVIEW_DECISION==APPROVED（execution-result/diff 为 P2 执行产物）"}
      ]
    },
    {
      "step_id": "step-4-v2",
      "status": "draft",
      "task_packet_mode": "deferred",
      "task_packets": ["v2-1-raw-gate", "v2-2-concept-routing", "v2-3-nto1-synthesis", "v2-4-eval-harness"],
      "blocked_by": ["step-1-p0"],
      "owned_paths": ["src/akb2/quality/", "evals/main-pipeline-quality/", "tests/quality/test_multisource_distill.py", "tests/quality/test_raw_gate.py"],
      "forbidden_paths": ["/Users/praise/.claude/", "reports/loop1-audit/", "wiki/"],
      "required_context": ["specs/multi-source-distill/spec.md", "docs/constitution.md"],
      "deliverables": [
        {"path": "src/akb2/quality/multisource_distill.py", "kind": "file", "must_exist": true},
        {"path": "tests/quality/test_multisource_distill.py", "kind": "file", "must_exist": true},
        {"path": "tests/quality/test_raw_gate.py", "kind": "file", "must_exist": true}
      ],
      "sc_refs": ["SC-9a", "SC-9b", "SC-9c", "SC-9d", "SC-9e"],
      "verify": [
        {"sc_ref": "SC-9a", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -q tests/quality/test_multisource_distill.py -k synthesis", "expect": "passed; 单源 floor fail, N 源合成 F4 pass + weighted>=threshold（测试脚手架已建，selector 命中；v2 模块未建时 importorskip skip，建好转真）"},
        {"sc_ref": "SC-9b", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -q tests/quality/test_multisource_distill.py -k incoherent", "expect": "passed; 混料 F4 拒"},
        {"sc_ref": "SC-9c", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -q tests/quality/test_raw_gate.py", "expect": "passed; 薄但有效进池, 零贡献拒"},
        {"sc_ref": "SC-9d", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -q tests/quality/test_multisource_distill.py -k fast_path_1to1", "expect": "passed; route==fast_1to1 不写 pending"},
        {"sc_ref": "SC-9e", "method": "command", "cmd": "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -q tests/quality/test_multisource_distill.py -k no_wiki_mutation", "expect": "passed; wiki 既有页快照不变"}
      ]
    }
  ]
}
```

```json gd-wave-matrix
{
  "waves": [
    {"wave_id": "w1", "tracks": [
      {"track_id": "wt1", "step_ids": ["step-1-p0"], "max_parallel": 1, "mode": "execute"}
    ]},
    {"wave_id": "w2", "tracks": [
      {"track_id": "wt2", "step_ids": ["step-2-p1", "step-4-v2"], "max_parallel": 2, "mode": "execute"}
    ]},
    {"wave_id": "w3", "tracks": [
      {"track_id": "wt3", "step_ids": ["step-3-p2"], "max_parallel": 1, "mode": "execute"}
    ]}
  ]
}
```
