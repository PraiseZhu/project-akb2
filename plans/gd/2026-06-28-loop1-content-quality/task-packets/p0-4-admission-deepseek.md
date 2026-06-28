# Task Packet: p0-4-admission-deepseek

GD_STANDARD: /Users/praise/AI-Agent/Claude/projects/Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-task-packet

> 自包含：精确 WHERE（admission.py 四处 gemini-flash 调用行号）见 ../child-drafts/p0-draft.md §步骤 4。
> **family 名硬约束（rev-tech 实测）**：`llm_router._MODEL_CONFIG`（`llm_router.py:61-69`）只支持 **`deepseek-v4-pro`**，字面 `deepseek` 会在 `:164-168` 抛 `RouterConfigError` → 准入四路全 fail-closed。本 packet 所有规范性字段一律用 `deepseek-v4-pro`，"DeepSeek 族"仅用于解释性文字。

## 1. 标识
```yaml
task_id: p0-4-admission-deepseek
agent_role: implementer
parent_step: step-1（P0）
parent_plan: plans/gd/2026-06-28-loop1-content-quality/step-1-p0-pipeline-honesty.md
```

## 2. 目标链
```text
PROJECT_GOAL: AKB2 异族 Maker/Checker 分离（docs/constitution.md 原则 7）
CHAIN_GOAL:   循环1 内容车道分流（specs/content-lane-quality/spec.md FR-002）
PHASE_GOAL:   P0 当前管线诚实化
TASK_GOAL:    admission topic/how/novelty/lane 四处判定由 gemini-flash 切 deepseek-v4-pro，解除官方文档误杀（#12/#13），不破异族
```

## 3. 依赖与并发
```yaml
blocked_by: []
can_parallel_with: []
required_context:
  - plans/gd/2026-06-28-loop1-content-quality/child-drafts/p0-draft.md
  - plans/gd/2026-06-28-loop1-execution-plan.md
  - docs/constitution.md
```

## 4. 路径权限
```yaml
owned_paths:
  - src/akb2/quality/admission.py
  - tests/test_admission_lane.py
forbidden_paths:
  - "/Users/praise/.claude/**"
  - vault 内任何 .py/.sh
  - 其他 P0 task 的 owned_paths
```

## 5. 成功标准（SC）
- [ ] SC-4：admission 四处 `route_llm_fn("gemini-flash",...)`（topic/how/novelty/lane）切 **`deepseek-v4-pro`**，提具名常量 `ADMISSION_MODEL = "deepseek-v4-pro"`（**不可写字面 `deepseek`——router 不支持会 RouterConfigError**）避免四处魔数；不改判定逻辑/schema；离线全绿；交付文档显式论证不破异族（准入判源不判提炼产物，grader 仍 gpt-5.4，maker=DeepSeek 族 / checker=gpt-5.4 族 两族异族）。

## 6. 交付物
```yaml
deliverables:
  - path: src/akb2/quality/admission.py
    kind: file
    must_exist: true
    description: 四处 gemini-flash → deepseek-v4-pro（具名常量 ADMISSION_MODEL="deepseek-v4-pro"），判定逻辑不变
  - path: tests/test_admission_lane.py
    kind: file
    must_exist: true
    description: 断言 mock 收到 model_family=="deepseek-v4-pro"、正负样本判定行为不变
```

## 7. 验证
```yaml
verify:
  - sc_ref: SC-4
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/test_admission_lane.py tests/quality/test_admission_positive.py tests/quality/test_admission_negative.py"
    expect: "passed（准入 model_family==deepseek-v4-pro、正负样本判定不变）"
  - sc_ref: SC-4
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -m 'not integration' -q"
    expect: "≥1633 passed"
```

## 8. Handoff 输出
```yaml
handoff_output:
  result_path: <execution result 相对路径>
  status_field: 见 gd-execution-status.schema.json
  summary: 准入四判定切 deepseek-v4-pro，官方文档误杀解除，异族不破（附论证）
  blockers: <若误用字面 deepseek 触发 RouterConfigError（router 仅支持 deepseek-v4-pro），列具体>
```

## 9. 范围禁令
- 判定级改动：合 main 前过 Codex 异族审，交付必须含"准入判源不判产物 → 不破异族"的显式论证。
- 规范性字段一律 `deepseek-v4-pro`，禁字面 `deepseek`（会 RouterConfigError 让准入全 fail-closed）。
- p0-5 也改 admission.py：本 task（p0-4）先合，p0-5 在其后做 lane_reason 字段，避免 patch 冲突。
- 不写 ~/.claude/**、不碰 vault。
