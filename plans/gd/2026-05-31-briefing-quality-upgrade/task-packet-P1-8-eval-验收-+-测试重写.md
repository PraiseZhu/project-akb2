# Task Packet: p1-8-eval-验收-+-测试重写

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-task-packet

> 自包含合约：本 packet 可被子 agent 单独执行；不依赖会话上下文。

---

## 1. 标识

```yaml
task_id: p1-8-eval-验收-+-测试重写
agent_role: implementer
parent_step: P1-8
parent_plan: plans/gd/2026-05-31-briefing-quality-upgrade/step-P1-8-eval-验收-+-测试重写.md
created_at: 2026-05-31T11:00:00Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   把简报从「白名单熟人 + 关键词堆词」双窄门换成「语义 + 社区反响」，并上线调度
PHASE_GOAL:   正确性闸门
TASK_GOAL:    用 eval 量化新算法排序≥关键词版；重写 scoring 旧关键词/白名单测试 + 核对依赖分数排序的测试
```

---

## 3. 依赖与并发

```yaml
blocked_by:
  - P1-4
  - P1-5
  - P1-6
  - P1-7
can_parallel_with:
  []
required_context:
  - evals/briefing-scoring/
  - plans/gd/2026-05-31-briefing-quality-upgrade/step-P1-8-eval-验收-+-测试重写.md
```

---

## 4. 路径权限

```yaml
owned_paths:
  - tests/
  - reports/
forbidden_paths:
  - "/Users/praise/.claude/**"
  - 调度 plist / cron / LaunchAgent
  - 其他 step 的 owned_paths（未列入 required_context）
```

---

## 4b. 交付物（deliverables）

```yaml
deliverables:
  - reports/scoring-eval-<date>.md（新旧排序对比）
  - tests/briefing/test_scoring.py（重写后断言语义/降权）
```

---

## 5. 执行要求

- TDD：先写失败测试再实现（适用于代码 step）
- 改 scoring.py 的 step：必须先确认「改 scoring 本体」已获用户授权，否则停止
- 完成后跑 `pytest tests/` 全绿再交付

---

## 6. 成功标准（SC，1:1 可验收）

- [ ] SC-1：新版排序 ≥ 旧版
  - verify (path): `ls reports/scoring-eval-*.md`
  - expect: `新≥旧`
- [ ] SC-2：全仓库测试绿
  - verify (command): `pytest tests/`
  - expect: `all passed`

---

## 7. Hard-stop

- 未获 scoring 授权而本 task 改 scoring.py → 停止
- 写入越出 owned_paths → 停止
- 嵌入/网关失败 → 走降级（不让 pipeline 崩），不静默吞错
