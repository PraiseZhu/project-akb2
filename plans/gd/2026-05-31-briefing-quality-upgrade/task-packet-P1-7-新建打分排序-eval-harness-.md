# Task Packet: p1-7-新建打分排序-eval-harness-+-标注

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-task-packet

> 自包含合约：本 packet 可被子 agent 单独执行；不依赖会话上下文。

---

## 1. 标识

```yaml
task_id: p1-7-新建打分排序-eval-harness-+-标注
agent_role: implementer
parent_step: P1-7
parent_plan: plans/gd/2026-05-31-briefing-quality-upgrade/step-P1-7-新建打分排序-eval-harness-.md
created_at: 2026-05-31T11:00:00Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   把简报从「白名单熟人 + 关键词堆词」双窄门换成「语义 + 社区反响」，并上线调度
PHASE_GOAL:   验收前提（可早于改 scoring 开工）
TASK_GOAL:    建 evals/briefing-scoring/ runner + 标注集（~30 AI + ~15 离题），一键跑排序指标
```

---

## 3. 依赖与并发

```yaml
blocked_by:
  []
can_parallel_with:
  - P1-1
  - P1-2
  - P1-3
  - P1-4
  - P1-5
  - P1-6
required_context:
  - plans/gd/2026-05-31-briefing-quality-upgrade/step-P1-7-新建打分排序-eval-harness-.md
```

---

## 4. 路径权限

```yaml
owned_paths:
  - evals/briefing-scoring/
forbidden_paths:
  - "/Users/praise/.claude/**"
  - 调度 plist / cron / LaunchAgent
  - 其他 step 的 owned_paths（未列入 required_context）
```

---

## 4b. 交付物（deliverables）

```yaml
deliverables:
  - evals/briefing-scoring/run.py（eval runner）
  - evals/briefing-scoring/fixtures/（~30 AI + ~15 离题标注集）
```

---

## 5. 执行要求

- TDD：先写失败测试再实现（适用于代码 step）
- 改 scoring.py 的 step：必须先确认「改 scoring 本体」已获用户授权，否则停止
- 完成后跑 `pytest tests/` 全绿再交付

---

## 6. 成功标准（SC，1:1 可验收）

- [ ] SC-1：eval runner + 标注 fixtures 就位
  - verify (path): `ls evals/briefing-scoring/`
  - expect: `含 runner+fixtures`
- [ ] SC-2：一键跑出排序质量指标（nDCG 或 top-k）
  - verify (command): `python3 evals/briefing-scoring/run.py`
  - expect: `输出指标`

---

## 7. Hard-stop

- 未获 scoring 授权而本 task 改 scoring.py → 停止
- 写入越出 owned_paths → 停止
- 嵌入/网关失败 → 走降级（不让 pipeline 崩），不静默吞错
