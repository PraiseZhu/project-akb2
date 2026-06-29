VERDICT: REQUIRES_CHANGES

## 覆盖维度

- 派发证据链：`dispatch-map.json`、`ledger/stage-dispatch-ledger.json`、`reports/controller-report.json`、`reports/merge-report.md`。
- 并行与 owned_paths：master §5/§5a、Step 1..4 §7/§8、P0 六个 task packet。
- GD 合约：`Project GD/commands/gd.md` 的 `/gd plan` dispatch/controller 约束、`gd-stage-dispatch-ledger.schema.json`、`gd-controller-report.schema.json`、三条 validator 脚本。
- 证据 hash：child draft、stage ledger、merge report、controller `batch_ledgers`。
- 缓发 packet 范围：P1/P2/v2 task packet 未 materialize 的理由与执行前置。

## validator 实跑结果

工作目录：`/Users/praise/AI-Agent/Claude/projects/akb2-integ-wt`

| 命令 | exit | 输出摘要 |
|---|---:|---|
| `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-dispatch.py" plans/gd/2026-06-28-loop1-content-quality/dispatch-map.json` | 0 | `校验通过：dispatch_id=loop1-content-quality-plan, tracks=2, waves=1` |
| `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-stage-dispatch-ledger.py" plans/gd/2026-06-28-loop1-content-quality/ledger/stage-dispatch-ledger.json` | 0 | `WARN: jsonschema unavailable ... manual fail-closed validator ...` 后 `LEDGER_VALID` |
| `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-controller-report.py" plans/gd/2026-06-28-loop1-content-quality/reports/controller-report.json` | 0 | `CONTROLLER_REPORT_VALID` |

## hash 核对结果

| artifact | 记录位置 | 记录 hash | 实算 hash | 结论 |
|---|---|---|---|---|
| `child-drafts/p0-draft.md` | `ledger/stage-dispatch-ledger.json:12-14` | `ade2513e7303519c1f3ad26dc03b06fdb1dce4b8987a8c21491766eaecbac400` | same | PASS |
| `child-drafts/v2-draft.md` | `ledger/stage-dispatch-ledger.json:18-20` | `20ddc854759a820699c5d6d7ed1e84748dc2bc0cd5bbaa747ae166a474cc357f` | same | PASS |
| `reports/merge-report.md` | `ledger/stage-dispatch-ledger.json:23-27` | `80679bb0ae6f5701656d0fad357f3aba8d0171470675919461e31666ad346cce` | same | PASS |
| `ledger/stage-dispatch-ledger.json` | `reports/controller-report.json:19-23` | `90b07e05d723b81ee34f3fde546d9ae8abe514742fa2fc6c5e83d43bd7c5ace4` | same | PASS |

## 已核实结论

- master §5 的 Step 1 与 Step 4 不可并行判断成立：master `master-plan.md:61-68` 明确 Step 1/4 都触及 `src/akb2/quality/`；细看 Step 4 还会接线 `runner.py`，与 Step 1/P0-5 的 `runner.py` 形成实际交叉。
- PLAN 阶段 dispatch-map 的两条 child track 可并行：`dispatch-map.json:13-22` 与 `dispatch-map.json:49-56` 的 owned_paths 分别是 `child-drafts/p0-draft.md` 和 `child-drafts/v2-draft.md`，互不重叠，`can_parallel_with` 对称。
- P0-1/P0-2/P0-3 可并行判断成立：三者 owned_paths 分别为 grader/weighted_score/rubric、distill、raw_verify/test，各自无精确重叠；我用脚本枚举 P0 六包，声明可并行的组合没有 owned_paths 交叉。
- P0-4 与 P0-5 的顺序约束方向正确：二者都改 `src/akb2/quality/admission.py`，`step-1-p0-pipeline-honesty.md:43-58`、`p0-5-observability.md:25-45` 已表达 p0-4 先、p0-5 后。但见 Finding P2-1，顺序共享路径漏了一个测试文件。
- controller-report 没有夸大成已 dual-review：`reports/controller-report.json:12-18` 用 `secondary_gate.verdict=not_run_plan_stage_local_only`、`gate_consistent=false`、`jobs=[]` 表示计划生成阶段未跑 Codex 二审；`reports/merge-report.md:40-47` 也说明该 `APPROVED` 只是 PLAN 阶段 child 派发合并裁决，不等于生产批准。
- P1/P2/v2 缓发 task packet 的范围决策总体合理：Step 2 `step-2-p1-semantic-audit.md:26-70` 依赖 P0 真实 trace，Step 3 `step-3-p2-classified-remediation.md:26-75` 依赖 P1 用户确认的分类，Step 4 `step-4-v2-multisource-distill.md:26-53` 依赖 P0 的 score/admission 签名。现在不 materialize 最终 execute packet 是防漂移，不是合约缺口，前提是执行前必须重新生成 execute dispatch map 并过 validator。

## findings

### [P1] PLAN merge gate G2 的证据描述与 child draft 实际内容不一致

- 精确位置：`dispatch-map.json:89-95`，`reports/merge-report.md:23-30`，`child-drafts/p0-draft.md:127-177`，`child-drafts/v2-draft.md:116-154`。
- 问题：dispatch-map 的 G2 写的是“child 草稿 task_packets owned_paths 全在本套件目录内、不越界 ~/.claude”，merge-report 也在 G2 标了通过。但两个 child draft 的候选/最终 task packet owned_paths 明确包含 `src/akb2/quality/**`、`skillpack/quality/rubric/**`、`tests/**`、`evals/main-pipeline-quality/**` 等工作区实现路径，并不“全在本套件目录内”。如果 G2 本意是 PLAN 阶段 child 只能写自己的 draft deliverable，那么当前文字把“child deliverable owned_paths”和“未来 execute packet owned_paths”混成了一条证据。
- 影响：这是派发证据链诚实性问题。validator 只校验 dispatch-map schema，不会发现 G2 语义误报；后续 reviewer 可能基于 merge-report 的 ✅ 误以为 execute packet 也被限制在计划目录内，或反向认为 child proposal 越界。
- 可落地修法：把 `dispatch-map.json` 的 G2 改成两层：1. PLAN 阶段 child deliverable 仅允许写 `child-drafts/{p0,v2}-draft.md`；2. child draft 内的 execute packet proposals 允许引用工作区实现路径，但必须禁止 `~/.claude/**`、vault、`..` 穿越，并在 execute 阶段重新生成 dispatch-map 校验。同步改 `reports/merge-report.md:23-30` 的 G2/G6 文案。
- 验收：修改后重算 `reports/merge-report.md` hash，更新 `ledger/stage-dispatch-ledger.json` 的 `merge_report_hash`，再重算 ledger hash 更新 `reports/controller-report.json.batch_ledgers[0].hash`；三条 GD validator 重新 exit 0，`shasum -a 256` 四项仍全部对齐。

### [P2] P0-4/P0-5 顺序共享路径只豁免了 `admission.py`，漏掉共同测试文件

- 精确位置：`step-1-p0-pipeline-honesty.md:43-58`，`task-packets/p0-4-admission-deepseek.md:35-44`，`task-packets/p0-5-observability.md:34-46`。
- 问题：p0-4 和 p0-5 不只共享 `src/akb2/quality/admission.py`，还都声明 owned `tests/test_admission_lane.py`。但 step-1 的顺序说明只写“共改 admission.py”，p0-5 的 forbidden_paths 只豁免“admission.py 与 p0-4 顺序共享”。这会让 p0-5 自己的 owned_paths 与“其他 P0 task 的 owned_paths”禁令冲突。
- 影响：执行阶段若按 packet 严格验边界，p0-5 修改 `tests/test_admission_lane.py` 可能被判越界；若执行者为避开越界不改该测试，又会削弱 lane_reason 观测断言。
- 可落地修法：二选一。方案 A：把 `tests/test_admission_lane.py` 明确列入 p0-4/p0-5 的 ordered_shared_paths，并在 p0-5 forbidden_paths 里写“除 `src/akb2/quality/admission.py` 与 `tests/test_admission_lane.py` 与 p0-4 顺序共享”。方案 B：拆分测试 ownership，p0-4 保留 model 切换测试，p0-5 新建独立 `tests/quality/test_admission_observability.py`，不再共享同一测试文件。
- 验收：重新枚举 P0 packet owned_paths，除显式 ordered_shared_paths 外无重叠；execute dispatch map 对 p0-4/p0-5 串行且边界不报冲突。

### [P2] `gd.md` 的 controller-report prose 与当前 schema/validator 已漂移，主 agent “以 validator 为准”方向正确但未消除合约矛盾

- 精确位置：`Project GD/commands/gd.md:195-208`，`Project GD/schema/gd-controller-report.schema.json:8-20`、`:147-190`，`Project GD/scripts/gd-validate-controller-report.py:93-159`、`:269-289`，`reports/controller-report.json:1-25`。
- 问题：`gd.md:208` 仍写 controller report 含 `jobs[]` 的 result path/hash/status、`final_decision`、`main_agent_merge.merge_report_path`；但当前 schema v1.1 的 required fields 是 `primary_gate`、`secondary_gate`、`gate_consistent`、`jobs`，v1.1 额外要求 `batch_ledgers` 和 `suite_target_closure`，并不要求 `final_decision` 或 `main_agent_merge`。validator 自测还显式接受 `jobs: []` 的 v1.1 live report。
- 影响：当前 controller-report 用 `jobs: [] + secondary_gate=not_run_plan_stage_local_only + gate_consistent=false` 是诚实的 plan-stage local_only 表达；但 gd.md prose 会误导后续执行者以为当前 report 缺 `final_decision/main_agent_merge` 或 job hash，从而把 validator 通过的工件误判为缺证，或反过来在未来生成不符合 schema 的 controller-report。
- 可落地修法：更新 `gd.md:208`：controller-report 的权威字段以 `schema/gd-controller-report.schema.json` 为准；plan-stage local_only 可用 `jobs: []`，但必须通过 `batch_ledgers` 绑定 stage ledger hash，并用 `secondary_gate=not_run...` + `gate_consistent=false` 明示未做 Codex 二审；生产 closure/dual-review 另由 `/gd review plan` 处理。
- 验收：`gd.md` prose 与 schema required fields 一致；当前 controller-report 仍过 `gd-validate-controller-report.py`；不得再声称 PLAN 阶段已经 dual-review。

## P3 / residual risk

- P0 task packet 的 `forbidden_paths` 已覆盖 lead 要求的 `~/.claude/**`、vault、其他 P0 owned_paths，但“其他 P0 task 的 owned_paths”是语义引用，不是展开列表。因为每个 P0 packet 的 required_context 都包含 `child-drafts/p0-draft.md`，而该 draft 列了所有 P0 owned_paths，所以当前可执行；更稳的做法是在 execute dispatch map 生成时展开为具体 forbidden_paths 或由 validator 做 owned_paths 交叉审计。
- stage ledger schema/validator 当前不校验 `result_hash` 是否等于 `result_path` 文件内容，只校验 SHA-256 格式和 child count。我已手工核对通过；若要长期防漂移，应把 result_path hash cross-check 加进 `gd-validate-stage-dispatch-ledger.py`。
