# Task Packet: p0-6-active-rerun

GD_STANDARD: /Users/praise/AI-Agent/Claude/projects/Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-task-packet

> 自包含：满仓命令/env 见 ../child-drafts/p0-draft.md §步骤 6 + ../../2026-06-28-loop1-handoff.md §1。无源码改动，仅运行 + 读结果。

## 1. 标识
```yaml
task_id: p0-6-active-rerun
agent_role: implementer
parent_step: step-1（P0）
parent_plan: plans/gd/2026-06-28-loop1-content-quality/step-1-p0-pipeline-honesty.md
```

## 2. 目标链
```text
PROJECT_GOAL: AKB2 fail-closed 门控 + 实测价值裁决（docs/constitution.md 原则 3/8）
CHAIN_GOAL:   循环1 内容车道分流（specs/content-lane-quality/spec.md SC-001/002/003）
PHASE_GOAL:   P0 出口——active e2e 满仓重跑出基线
TASK_GOAL:    AKB2_AUTO_PROMOTE=1 真入库满仓重跑，pass_rate 从 0 动起、负样本 100% 拒、黄金样张过
```

## 3. 依赖与并发
```yaml
blocked_by: [p0-1-weighted-score, p0-2-must-cover, p0-3-command-form, p0-4-admission-deepseek, p0-5-observability]
can_parallel_with: []
required_context:
  - plans/gd/2026-06-28-loop1-content-quality/child-drafts/p0-draft.md
  - plans/gd/2026-06-28-loop1-execution-plan.md
  - plans/gd/2026-06-28-loop1-handoff.md
```

## 4. 路径权限
```yaml
owned_paths:
  - reports/calibration/
forbidden_paths:
  - "/Users/praise/.claude/**"
  - vault 内任何 .py/.sh
  - src/**（本 task 不改源码）
```

## 5. 成功标准（SC）
- [ ] SC-6：P0-1..P0-5 全合入 + 离线 ≥1633 绿 + ruff 后，设 env（AI_GATEWAY_BASE_URL / AI_GATEWAY_API_KEY=取 ANTHROPIC_API_KEY 值 / OPENAI_MODEL_ID=codex/gpt-5.4 / PYTHONPATH=src / AKB2_AUTO_PROMOTE=1）串行跑满仓（~50min），factory.json 落盘，pass_rate>0、negative_reject_rate==1.0、黄金样张 #4 pass、report 带 trace+lane_reason。
  - **Round 1 worker 修正（rev-tech P2）**：`AKB2_AUTO_PROMOTE=1` 控制 runner 真入库，但 calibration report 的 `promotion_mode` 还受 `AKB2_AMENDMENT_APPLIED` 影响（`calibration.py:81-89`）。二选一：(a) 若验收要读 report `promotion_mode=active`，**追加 `AKB2_AMENDMENT_APPLIED=1`** 并在交付说明修宪已应用；(b) 否则验收**不以 report `promotion_mode` 判真入库**，改以 runner/promote/final_status 证据判。
- [ ] SC-6a/6b/6c（content-lane 核心验收，Round 2 rev-tech P1 补承接）：满仓重跑同时跑 `tests/quality/test_concept_judgment_e2e.py` 的 concept_positive / negatives / adversarial（见 §7 verify），并在 handoff 记录 factory 对应 fixture 的 content_lane / passed / weighted_score / floor_failed 证据——P0 不得在未跑这三条 content-lane 验收的情况下关闭。

## 6. 交付物
```yaml
deliverables:
  - path: reports/calibration/factory.json
    kind: file
    must_exist: true
    description: active e2e 满仓重跑结果（pass_rate / per_segment / 负样本 / lane_reason / trace）
```

## 7. 验证
```yaml
verify:
  - sc_ref: SC-6
    method: path
    cmd: "test -f reports/calibration/factory.json && echo PASS"
    expect: "PASS"
  - sc_ref: SC-6
    method: assertion
    cmd: "python3 -c \"import json;d=json.load(open('reports/calibration/factory.json'));print(d['positive_pass_rate'],d['negative_reject_rate'])\""
    expect: "positive_pass_rate>0 且 negative_reject_rate==1.0"
  - sc_ref: SC-6a
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k concept_positive"
    expect: "passed；factory 中该 fixture content_lane==concept、passed==true、weighted_score>=threshold"
  - sc_ref: SC-6b
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k negatives"
    expect: "passed；三类负样本各归 core_value_f4 / traceability / novelty 对应门拒"
  - sc_ref: SC-6c
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k adversarial"
    expect: "passed；floor_failed 含 core_value_f4、weighted 未补偿放行"
```

## 8. Handoff 输出
```yaml
handoff_output:
  result_path: <execution result 相对路径>
  status_field: 见 gd-execution-status.schema.json
  summary: P0 出口满仓基线（pass_rate 从 0 动起、负样本 100%、样张过）
  blockers: <若 pass_rate 仍 0 或负样本漏，列 per_segment 分布>
```

## 9. 范围禁令
- 必须在 p0-1..p0-5 全合入后最后执行；离线未全绿不得跑满仓。
- **env key 走环境变量、网关 key 优先官方 key、绝不打印任何 key 值**（安全约束）。
- 不用 `git --no-verify`；不改源码（本 task 只运行 + 落 reports/calibration/）。
- 部分失败（如 pass_rate 仍 0）必须如实报，不得报成功（原则 4）。
