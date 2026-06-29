# Task Packet: p0-3-command-form

GD_STANDARD: /Users/praise/AI-Agent/Claude/projects/Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-task-packet

> 自包含：精确 WHERE 见 ../child-drafts/p0-draft.md §步骤 3（raw_verify.py 内联 recheck）。

## 1. 标识
```yaml
task_id: p0-3-command-form
agent_role: implementer
parent_step: step-1（P0）
parent_plan: plans/gd/2026-06-28-loop1-content-quality/step-1-p0-pipeline-honesty.md
```

## 2. 目标链
```text
PROJECT_GOAL: AKB2 fail-closed 门控（docs/constitution.md 原则 3）
CHAIN_GOAL:   循环1 内容车道分流（specs/content-lane-quality/spec.md）
PHASE_GOAL:   P0 当前管线诚实化
TASK_GOAL:    raw_verify 命令形态判定区分真命令与散文/术语/表格碎片，#16 不卡 distill_compose，真编造命令仍报
```

## 3. 依赖与并发
```yaml
blocked_by: []
can_parallel_with: [p0-1-weighted-score, p0-2-must-cover]
required_context:
  - plans/gd/2026-06-28-loop1-content-quality/child-drafts/p0-draft.md
  - plans/gd/2026-06-28-loop1-execution-plan.md
  - docs/constitution.md
```

## 4. 路径权限
```yaml
owned_paths:
  - src/akb2/quality/source_ops_scanners/raw_verify.py
  - tests/test_raw_verify_command_shape.py
forbidden_paths:
  - "/Users/praise/.claude/**"
  - vault 内任何 .py/.sh
  - 其他 P0 task 的 owned_paths
```

## 5. 成功标准（SC）
- [ ] SC-3：内联反引号回查前先做"命令形态"判定——单字/短词中文（如 `和`）、含 `|` 表格碎片、纯术语（无 CLI 动词/无 `$`/无路径参数形态）豁免回查；正向真命令识别（复用 CLI 动词白名单 npm/pip/python/git/docker...）命中才回查；危险黑名单 `_is_dangerous_command` 对豁免项仍跑（双重拦截）。

## 6. 交付物
```yaml
deliverables:
  - path: src/akb2/quality/source_ops_scanners/raw_verify.py
    kind: file
    must_exist: true
    description: 内联回查升级命令形态判定（正向真命令识别 + 非命令形态豁免）
  - path: tests/test_raw_verify_command_shape.py
    kind: file
    must_exist: true
    description: 单字中文/表格碎片/术语不报、pip install evil 仍报、fenced bash 不破
```

## 7. 验证
```yaml
verify:
  - sc_ref: SC-3
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/test_raw_verify_command_shape.py"
    expect: "passed（和/表格碎片/术语不报；pip install evil/危险命令仍报）"
  - sc_ref: SC-3
    method: command
    cmd: "PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -m 'not integration' -q"
    expect: "≥1633 passed"
```

## 8. Handoff 输出
```yaml
handoff_output:
  result_path: <execution result 相对路径>
  status_field: 见 gd-execution-status.schema.json
  summary: 命令形态判定补全，#16 命令残留误报解除，真编造命令仍拦
  blockers: <若豁免误放真命令，列具体 case>
```

## 9. 范围禁令
- 判定级改动：过 Codex 异族审。护栏：真编造命令（pip install evil）必须仍报；不放宽危险命令黑名单。
- 不写 ~/.claude/**、不碰 vault。
