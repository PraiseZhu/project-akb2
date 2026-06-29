# Task Packet: p0-5-observability

GD_STANDARD: /Users/praise/AI-Agent/Claude/projects/Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-task-packet

> 自包含：精确 WHERE（repair.py / runner.py / admission.py 行号）见 ../child-drafts/p0-draft.md §步骤 5。仅加观测，判定行为零变化。

## 1. 标识
```yaml
task_id: p0-5-observability
agent_role: implementer
parent_step: step-1（P0）
parent_plan: plans/gd/2026-06-28-loop1-content-quality/step-1-p0-pipeline-honesty.md
```

## 2. 目标链
```text
PROJECT_GOAL: AKB2 预算显式/失败可见（docs/constitution.md 原则 4）
CHAIN_GOAL:   循环1 内容车道分流（specs/content-lane-quality/spec.md FR-008）
PHASE_GOAL:   P0 补观测，为 P1 语义审计提供逐篇可读 trace
TASK_GOAL:    repair 逐轮 final_defects 落可读位置 + admission lane_reason 落 ledger（pass 也可见），判定行为不变
```

## 3. 依赖与并发
```yaml
blocked_by: [p0-4-admission-deepseek]   # 共改 admission.py，p0-4 先合
can_parallel_with: []
required_context:
  - plans/gd/2026-06-28-loop1-content-quality/child-drafts/p0-draft.md
  - plans/gd/2026-06-28-loop1-execution-plan.md
```

## 4. 路径权限
```yaml
owned_paths:
  - src/akb2/quality/repair.py
  - src/akb2/quality/runner.py
  - src/akb2/quality/admission.py
  - tests/quality/test_repair_loop.py
  - tests/quality/test_admission_observability.py   # 新建独立观测测试，不再共享 p0-4 的 test_admission_lane.py
forbidden_paths:
  - "/Users/praise/.claude/**"
  - vault 内任何 .py/.sh
  - tests/test_admission_lane.py   # 归 p0-4 独占
  - 其他 P0 task 的 owned_paths（src/akb2/quality/admission.py 与 p0-4 为 ordered_shared：p0-4 先合，本 task 后改 lane_reason 字段）
```

## 5. 成功标准（SC）
- [ ] SC-5：AdmissionResult 透出 lane_reason → runner 准入 pass/abort 两处 `_log_event` details 补 lane_reason（每篇可读，不止 reject）；repair 逐轮 final_defects 演化落可读位置（trace_payload round-level 快照或 ledger 汇总）；**判定分支/收敛条件零改动**（离线判定测试不回归）。

## 6. 交付物
```yaml
deliverables:
  - path: src/akb2/quality/admission.py
    kind: file
    must_exist: true
    description: AdmissionResult 增/透出 lane_reason（pass 路径不丢弃）
  - path: src/akb2/quality/runner.py
    kind: file
    must_exist: true
    description: 准入 pass/abort 落 lane_reason 到 ledger details
  - path: src/akb2/quality/repair.py
    kind: file
    must_exist: true
    description: 逐轮 final_defects 演化可读
  - path: tests/quality/test_admission_observability.py
    kind: file
    must_exist: true
    description: 新增观测测试（脚手架已建，skip-until-impl），覆盖 admission pass/abort ledger lane_reason + repair 逐轮 final_defects；P0-5 实现后转真
```

## 7. 验证
```yaml
verify:
  - sc_ref: SC-5
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_repair_loop.py tests/quality/test_admission_observability.py"
    expect: "passed（admission pass 时 ledger 含 lane_reason；repair 逐轮可读；判定不变）"
  - sc_ref: SC-5
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -m 'not integration' -q"
    expect: "≥1633 passed（判定行为零回归）"
```

## 8. Handoff 输出
```yaml
handoff_output:
  result_path: <execution result 相对路径>
  status_field: 见 gd-execution-status.schema.json
  summary: lane_reason 全程可见 + repair 逐轮缺陷演化可读，判定不变
  blockers: <若观测改动意外改了判定，列具体测试>
```

## 9. 范围禁令
- 仅加观测字段，**禁改任何判定分支/收敛条件/门控阈值**——若离线判定测试转红即越界。
- 不写 ~/.claude/**、不碰 vault。
