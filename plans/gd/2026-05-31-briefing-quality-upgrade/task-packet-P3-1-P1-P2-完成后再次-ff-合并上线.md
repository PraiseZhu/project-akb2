# Task Packet: p3-1-P1-P2-完成后再次-ff-合并上线

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-task-packet

> 自包含合约：本 packet 可被子 agent 单独执行；不依赖会话上下文。

---

## 1. 标识

```yaml
task_id: p3-1-P1-P2-完成后再次-ff-合并上线
agent_role: implementer
parent_step: P3-1
parent_plan: plans/gd/2026-05-31-briefing-quality-upgrade/step-P3-1-P1-P2-完成后再次-ff-合并上线.md
created_at: 2026-05-31T11:00:00Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   把简报从「白名单熟人 + 关键词堆词」双窄门换成「语义 + 社区反响」，并上线调度
PHASE_GOAL:   部署 cadence
TASK_GOAL:    P1/P2 新改动再次 ff 合并到调度分支，触发真跑验证语义/覆盖改动生产生效
```

---

## 3. 依赖与并发

```yaml
blocked_by:
  - P1-8
  - P2-1
  - P2-3   # P2-3 已 transitively 含 P2-2 → blocked_by 覆盖全部 P2（Codex F2 修订：不在 P2 未上线前合并）
can_parallel_with:
  []
required_context:
  - plans/gd/2026-05-31-briefing-quality-upgrade/master-plan.md
```

---

## 4. 路径权限

```yaml
owned_paths:
  - git 分支操作
forbidden_paths:
  - "/Users/praise/.claude/**"
  - 调度 plist / cron / LaunchAgent
  - 其他 step 的 owned_paths（未列入 required_context）
  - src/**（本 step 只合并不改源码）
```

---

## 4b. 交付物（deliverables）

```yaml
deliverables:
  - 再次 ff 合并上线的 feature/automation-pipeline-v2 分支
  - 上线后真跑产出验证记录（语义排序 + zh 内容）
```

---

## 5. 执行要求

- TDD：先写失败测试再实现（适用于代码 step）
- 改 scoring.py 的 step：必须先确认「改 scoring 本体」已获用户授权，否则停止
- 完成后跑 `pytest tests/` 全绿再交付

---

## 6. 成功标准（SC，1:1 可验收）

- [ ] SC-1：P1/P2 commit ff 合入调度分支且全绿
  - verify (command): `合并后 pytest tests/`
  - expect: `all passed`
- [ ] SC-2：真跑产出体现语义排序+zh内容
  - verify (command): `合并后真跑，grep -E "account_collector|evidence_paths" "<vault>/AI TRENDS/Social/<date> Daily Briefing.md"`
  - expect: `命中新行为`

---

## 7. Hard-stop

- 未获 scoring 授权而本 task 改 scoring.py → 停止
- 写入越出 owned_paths → 停止
- 嵌入/网关失败 → 走降级（不让 pipeline 崩），不静默吞错
