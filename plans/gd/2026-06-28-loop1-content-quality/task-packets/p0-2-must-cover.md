# Task Packet: p0-2-must-cover

GD_STANDARD: /Users/praise/AI-Agent/Claude/projects/Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-task-packet

> 自包含：子 agent 只读本 packet + required_context；精确 WHERE 见 ../child-drafts/p0-draft.md §步骤 2。真实文件是 distill.py（compose.py 不存在）。
> **前提（rev-tech 实测）**：`distill.py:3726-3749 _render_must_cover_operations` + `:4048-4061` 的 source_operation 直出渲染**已存在且 `test_distill_must_cover.py` 现 4 passed**。本 task 是"先复验定位、不盲改绿路径"，不是"补缺失渲染"。

## 1. 标识
```yaml
task_id: p0-2-must-cover
agent_role: implementer
parent_step: step-1（P0）
parent_plan: plans/gd/2026-06-28-loop1-content-quality/step-1-p0-pipeline-honesty.md
```

## 2. 目标链
```text
PROJECT_GOAL: AKB2 fail-closed 门控 + 实测价值裁决（docs/constitution.md 原则 3/6）
CHAIN_GOAL:   循环1 内容车道分流（specs/content-lane-quality/spec.md）
PHASE_GOAL:   P0 当前管线诚实化
TASK_GOAL:    定位并解除满仓 #0/2/8/17 的 SG-must-cover 卡点——source_operation 直出渲染已存在且 test 绿，故先复验定位失配环节，仅在证据指向 distill 渲染时才改，不盲改绿路径
```

## 3. 依赖与并发
```yaml
blocked_by: []
can_parallel_with: [p0-1-weighted-score, p0-3-command-form]
required_context:
  - plans/gd/2026-06-28-loop1-content-quality/child-drafts/p0-draft.md
  - plans/gd/2026-06-28-loop1-execution-plan.md
  - specs/content-lane-quality/spec.md
```

## 4. 路径权限
```yaml
owned_paths:
  - src/akb2/quality/distill.py
  - tests/quality/test_distill_must_cover.py
forbidden_paths:
  - "/Users/praise/.claude/**"
  - vault 内任何 .py/.sh
  - 其他 P0 task 的 owned_paths
```

## 5. 成功标准（SC）
- [ ] SC-2：**先复验**满仓 #0/2/8/17 是否仍卡 SG-must-cover（用 fixture / calibration slice，**不盲改 distill 绿路径**）。
  - 若仍卡 → 定位 scanner item / raw_content 传递 / section 抽取**哪一环失配**，只对失配点改；
  - 若已被其他 P0 修复连带解决（不再卡）→ 本 task 退化为回归确认，**不改 distill 渲染**；
  - 全程 `test_distill_must_cover.py` 保持绿；不收窄 operations.scan、不改 SG-must-cover 校验侧判定逻辑。

## 6. 交付物
```yaml
deliverables:
  - path: src/akb2/quality/distill.py
    kind: file
    must_exist: false   # 仅当复验证据指向 distill 渲染失配时才改；否则不动（回归确认型 task）
    description: 仅在定位到 distill 渲染失配时才改对应点；否则保持不动
  - path: tests/quality/test_distill_must_cover.py
    kind: file
    must_exist: true
    description: 保持绿；如定位到新失配类型，补对应回归断言
  - path: <复验记录>
    kind: report
    must_exist: true
    description: 记录 #0/2/8/17 复验结果（仍卡/已解）+ 若仍卡则失配环节定位
```

## 7. 验证
```yaml
verify:
  - sc_ref: SC-2
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_distill_must_cover.py"
    expect: "passed（保持绿；若因证据改了 distill，字面量仍在操作步骤节、SG-must-cover 无 Defect）"
  - sc_ref: SC-2
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -m 'not integration' -q"
    expect: "≥1633 passed"
```

## 8. Handoff 输出
```yaml
handoff_output:
  result_path: <execution result 相对路径>
  status_field: 见 gd-execution-status.schema.json
  summary: 复验 #0/2/8/17 must-cover 卡点；定位失配点（若有）并修，或回归确认无 distill 改动；test 保持绿
  blockers: <若仍卡且失配定位不明，列具体 scanner item / raw_content / section 证据>
```

## 9. 范围禁令
- **不盲改 distill 绿路径**（直出渲染已存在且 test 绿）；不收窄 operations.scan（护栏）；不改 SG-must-cover 校验侧判定逻辑。
- 不写 ~/.claude/**、不碰 vault。
