# Task Packet: p0-2-清理-06-01-残报-+-🔗-+-needs_

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-task-packet

> 自包含合约：本 packet 可被子 agent 单独执行；不依赖会话上下文。

---

## 1. 标识

```yaml
task_id: p0-2-清理-06-01-残报-+-🔗-+-needs_
agent_role: implementer
parent_step: P0-2
parent_plan: plans/gd/2026-05-31-briefing-quality-upgrade/step-P0-2-*.md
created_at: 2026-05-31T11:00:00Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   把简报从「白名单熟人 + 关键词堆词」双窄门换成「语义 + 社区反响」，并上线调度
PHASE_GOAL:   防 silent 退化
TASK_GOAL:    删旧英文残报；清正文孤立 🔗；查清 gate 为何每次 needs_repair
```

---

## 3. 依赖与并发

```yaml
blocked_by:
  []
can_parallel_with:
  - P0-1
required_context:
  - plans/gd/2026-05-31-briefing-quality-upgrade/step-P0-2-cleanup-and-gate-investigation.md
```

---

## 4. 路径权限

```yaml
owned_paths:
  - vault 清理（archive）
  - src/akb2/briefing/pipeline.py
  - reports/
forbidden_paths:
  - "/Users/praise/.claude/**"
  - 调度 plist / cron / LaunchAgent
  - 其他 step 的 owned_paths（未列入 required_context）
  - gate 规则本体（只查不改）
```

---

## 5. 执行要求

- TDD：先写失败测试再实现（适用于代码 step）
- 改 scoring.py 的 step：必须先确认「改 scoring 本体」已获用户授权，否则停止
- 完成后跑 `pytest tests/` 全绿再交付

---

## 6. 成功标准（SC，1:1 可验收）

- [ ] SC-1：06-01 残报移出正式目录
  - verify (command): `ls Social/ | grep 06-01`
  - expect: `不在正式目录`
- [ ] SC-2：正文孤立 🔗 清零
  - verify (test): `含🔗推文渲染单测`
  - expect: `无孤立🔗`
- [ ] SC-3：needs_repair 根因报告
  - verify (path): `ls reports/gate-needs-repair-diagnosis.md`
  - expect: `存在含根因`

---

## 7. Hard-stop

- 未获 scoring 授权而本 task 改 scoring.py → 停止
- 写入越出 owned_paths → 停止
- 嵌入/网关失败 → 走降级（不让 pipeline 崩），不静默吞错
