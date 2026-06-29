VERDICT: REQUIRES_CHANGES

## 覆盖维度

- Round 1 rev-dispatch 三项 finding 复审：G2 语义误报、P0-4/P0-5 测试路径共享、`gd.md` controller-report prose 与 schema 漂移。
- 实跑 GD validator：dispatch / stage-dispatch-ledger / controller-report。
- 实跑 master consistency preflight。
- 重算并核对 `merge-report.md` 与 `stage-dispatch-ledger.json` hash 链。

## 实跑结果

工作目录：`/Users/praise/AI-Agent/Claude/projects/akb2-integ-wt`

| 命令 | exit | 输出摘要 |
|---|---:|---|
| `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-dispatch.py" plans/gd/2026-06-28-loop1-content-quality/dispatch-map.json` | 0 | `校验通过：dispatch_id=loop1-content-quality-plan, tracks=2, waves=1` |
| `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-stage-dispatch-ledger.py" plans/gd/2026-06-28-loop1-content-quality/ledger/stage-dispatch-ledger.json` | 0 | `WARN: jsonschema unavailable ... manual fail-closed validator ...` 后 `LEDGER_VALID` |
| `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-controller-report.py" plans/gd/2026-06-28-loop1-content-quality/reports/controller-report.json` | 0 | `CONTROLLER_REPORT_VALID` |
| `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-master-plan-consistency.py" plans/gd/2026-06-28-loop1-content-quality/master-plan.md` | 0 | `PREFLIGHT_RESULT: PREFLIGHT_PASSED (0 errors, 0 warning(s))` |

## hash 核对结果

| artifact | 实算 hash | 记录位置 | 记录 hash | 结论 |
|---|---|---|---|---|
| `reports/merge-report.md` | `e671729482a7ea94af68f633843921301c04dfd872fbd5b0b01b539de228d0ba` | `ledger/stage-dispatch-ledger.json:23-27` | `e671729482a7ea94af68f633843921301c04dfd872fbd5b0b01b539de228d0ba` | PASS |
| `ledger/stage-dispatch-ledger.json` | `fdb344177ec1602819cb267e7ddb11df13970a6941b310f9b9f0e76e2916eb34` | `reports/controller-report.json:19-23` | `fdb344177ec1602819cb267e7ddb11df13970a6941b310f9b9f0e76e2916eb34` | PASS |

## 已关闭项

- R1 P1 G2 语义误报：已关闭。`dispatch-map.json:89-95` 和 `reports/merge-report.md:23-30` 均改成两层语义：PLAN child deliverable 只写 `child-drafts/{p0,v2}-draft.md`；child draft 内 execute packet proposals 引用工作区实现路径是预期，禁 `~/.claude`/vault/`..`，execute 阶段重生成 dispatch map 校验。
- hash 链：已关闭。`merge-report.md` hash 已写入 ledger，ledger hash 已写入 controller-report，实算一致。
- controller-report dual-review 诚实性：仍然通过。`reports/controller-report.json` 继续用 `jobs: []`、`secondary_gate=not_run_plan_stage_local_only`、`gate_consistent=false` 表示 PLAN 阶段无 Codex 二审，没有夸大成 dual-review。
- `gd.md` prose 与 schema 漂移：认同本轮边界判断，不作为本 AKB2 plan package blocker。理由：`gd.md` 在 Project GD 框架库，不属于本套件 owned surface；本套件 controller-report 已 schema 合规，并且 `reports/merge-report.md:69` 已把该问题登记为跨库残留交 GD 维护者。残余风险是后续 GD 维护仍应修 prose，否则会继续误导其他计划包。

## findings

### [P2] P0-5 测试路径拆分未完全落到 verify，当前 packet 自相矛盾

- 精确位置：`task-packets/p0-5-observability.md:34-46`、`task-packets/p0-5-observability.md:69-79`、`master-plan.md:173-179`、`step-1-p0-pipeline-honesty.md:44-51`。
- 问题：Round 2 已把 p0-5 owned_paths 改为新建 `tests/quality/test_admission_observability.py`，并把 `tests/test_admission_lane.py` 列为 forbidden、归 p0-4 独占。但 p0-5 的 verify 命令仍是 `tests/quality/test_repair_loop.py tests/test_admission_lane.py`，master inventory 的 SC-5 verify 也仍跑 `tests/test_admission_lane.py`，没有跑新建的 `tests/quality/test_admission_observability.py`。
- 影响：上一轮 P2“共享 test_admission_lane.py 漏豁免”只修了 owned/forbidden 表，未修验证面。执行者按 packet 做 p0-5 时，要么跑 forbidden 的 p0-4 测试文件，要么新观测测试不进入 SC-5 验收，lane_reason pass/abort 可见性的断言仍可能缺失。
- 可落地修法：把 p0-5 packet §7 和 master §10 `SC-5` verify 改为运行 `tests/quality/test_repair_loop.py tests/quality/test_admission_observability.py`；如仍需要 `tests/test_admission_lane.py` 作回归，只能在 p0-4 或全量离线回归中跑，不应作为 p0-5 的 owned-scope 验收命令。同步补 p0-5 deliverables（若本套件要求 test deliverable 显式列出）包含 `tests/quality/test_admission_observability.py`。
- 验收：`rg -n "SC-5|test_admission_lane|test_admission_observability" master-plan.md task-packets/p0-5-observability.md` 显示 p0-5 的 SC-5 command 只跑新观测测试；`gd-validate-master-plan-consistency.py master-plan.md` 仍 exit 0；P0 owned_paths 枚举中除 `src/akb2/quality/admission.py` ordered_shared 外不再有 p0-4/p0-5 共享测试路径。

## residual risk

- master preflight 目前没有抓到“packet forbidden path 出现在 verify command”这种语义矛盾，本次由人工 `rg` 发现。若这类边界错误常见，建议后续增强 `gd-validate-master-plan-consistency.py` 或 execute dispatch validator。
