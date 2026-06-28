# Task Packet: p0-1-weighted-score

GD_STANDARD: /Users/praise/AI-Agent/Claude/projects/Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-task-packet

> 自包含：子 agent 只读本 packet + required_context；精确 WHERE 行号见 ../child-drafts/p0-draft.md §步骤 1。

## 1. 标识
```yaml
task_id: p0-1-weighted-score
agent_role: implementer
parent_step: step-1（P0）
parent_plan: plans/gd/2026-06-28-loop1-content-quality/step-1-p0-pipeline-honesty.md
```

## 2. 目标链
```text
PROJECT_GOAL: AKB2 fail-closed 门控 + 异族 Maker/Checker + 实测价值裁决（docs/constitution.md 原则 3/8）
CHAIN_GOAL:   循环1 内容车道分流（specs/content-lane-quality/spec.md FR-006/008）
PHASE_GOAL:   P0 当前管线诚实化
TASK_GOAL:    concept grader 每维必出 0~1 score、缺 score fail-closed、concept 阈值 6.5→6.0，使加权分非 0 可复算
```

## 3. 依赖与并发
```yaml
blocked_by: []
can_parallel_with: [p0-2-must-cover, p0-3-command-form]
required_context:
  - plans/gd/2026-06-28-loop1-content-quality/child-drafts/p0-draft.md
  - plans/gd/2026-06-28-loop1-execution-plan.md
  - docs/constitution.md
  - specs/content-lane-quality/spec.md
```

## 4. 路径权限
```yaml
owned_paths:
  - src/akb2/quality/grader.py
  - src/akb2/quality/weighted_score.py
  - skillpack/quality/rubric/rubric.yaml
  - skillpack/quality/rubric/10-dimensions.md
  - tests/quality/test_concept_weighted_score.py
  - tests/quality/test_grader_score_required.py
forbidden_paths:
  - "/Users/praise/.claude/**"
  - vault 内任何 .py/.sh
  - 其他 P0 task 的 owned_paths
```

## 5. 成功标准（SC）
- [ ] SC-1：concept grader schema `score` 设 required + minimum:0/maximum:1（仅 concept 收紧，procedural 不回归）；prompt 钉"0~1 非 0~10"；parse + compute_weighted_score 缺/越界 score → fail-closed（不静默当 0）；rubric concept threshold 6.5→6.0 + bump rubric_version（同步 10-dimensions.md 版本头）；黄金样张 #4 加权分≥6.0 可复算。

## 6. 交付物
```yaml
deliverables:
  - path: src/akb2/quality/grader.py
    kind: file
    must_exist: true
    description: concept score required+0~1、prompt 钉尺度、parse 缺 score fail-closed
  - path: src/akb2/quality/weighted_score.py
    kind: file
    must_exist: true
    description: present 维缺/越界 score → fail-closed（同缺维口径）
  - path: skillpack/quality/rubric/rubric.yaml
    kind: file
    must_exist: true
    description: concept threshold=6.0 + version bump
  - path: skillpack/quality/rubric/10-dimensions.md
    kind: file
    must_exist: true
    description: 同步版本头（与 rubric_version bump 对齐，否则 _load_rubric_text 版本校验红）
  - path: tests/quality/test_grader_score_required.py
    kind: file
    must_exist: true
    description: 新增测试，≥3 断言（concept 缺逐维 score→fail / score>1→fail / procedural 不回归）
```

## 7. 验证（Anti-fill 硬约束）
```yaml
verify:
  - sc_ref: SC-1
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_weighted_score.py tests/quality/test_grader_score_required.py"
    expect: "passed（含 缺 score→fail-closed / score>1→fail-closed / threshold=6.0 三断言）"
  - sc_ref: SC-1
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -m 'not integration' -q"
    expect: "≥1633 passed（procedural 零回归）"
```

## 8. Handoff 输出
```yaml
handoff_output:
  result_path: <execution result 相对路径>
  status_field: 见 gd-execution-status.schema.json
  summary: concept 评分诚实化（每维 0~1 score 必填 + fail-closed + 阈值 6.0）
  blockers: <若 rubric 版本断言连锁红，列具体测试>
```

## 9. 范围禁令
- 判定级改动：合 main 前过 Codex 异族审。
- 不改 procedural 车道评分行为（仅 concept 收紧）；不动完整性地板判定（不可被加权分补偿）。
- 不写 ~/.claude/**、不碰 vault、不收窄 operations.scan。

## 10. Round 1 worker 审查修正（rev-tech P1/P3）
- rubric **只改 `lanes.concept.threshold=6.0`**，保留顶层 `threshold` / `lanes.procedural.threshold` / `weighted_score.DEFAULT_THRESHOLD`=6.5（child 草稿"同步 DEFAULT_THRESHOLD 兜底值"的指令作废——会破 procedural/global lane-aware 语义和现有 `test_concept_weighted_score.py:93-97` 断言）。
- 真因精确表述：grader schema/prompt/parse **未强制产出**逐维 score → runner 用空/不完整 dimension_scores 进 concept 加权门 fail-closed 为 0（不是"缺 score 静默当 0 通过"——当前缺 key 已 fail-closed）。本 task 修的是"强制产出 + 拒非数值/越界"。
- `tests/quality/test_grader_score_required.py` 为**新增**测试（当前不存在），须含 ≥3 断言：concept 缺逐维 score→parse fail；score>1→parse/compute fail；procedural 旧响应不回归。
