# Task Packet: p2-3-engagement-velocity

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-task-packet

> 自包含合约：本 packet 可被子 agent 单独执行；不依赖会话上下文。

---

## 1. 标识

```yaml
task_id: p2-3-engagement-velocity
agent_role: implementer
parent_step: P2-3
parent_plan: plans/gd/2026-05-31-briefing-quality-upgrade/step-P2-3-engagement-velocity.md
created_at: 2026-05-31T11:00:00Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   把简报从「白名单熟人 + 关键词堆词」双窄门换成「语义 + 社区反响」，并上线调度
PHASE_GOAL:   时效信号（捞刚冒头的爆款）
TASK_GOAL:    engagement 加 velocity（赞/小时）维度，高速新帖优先于同总量老爆款
```

---

## 3. 依赖与并发

```yaml
blocked_by:
  - P2-2
can_parallel_with:
  []
required_context:
  - plans/gd/2026-05-31-briefing-quality-upgrade/step-P2-3-engagement-velocity.md
```

---

## 4. 路径权限

```yaml
owned_paths:
  - src/akb2/briefing/scoring.py
forbidden_paths:
  - "/Users/praise/.claude/**"
  - 调度 plist / cron / LaunchAgent
  - 其他 step 的 owned_paths（未列入 required_context）
```

---

## 4b. 交付物（deliverables）

```yaml
deliverables:
  - src/akb2/briefing/scoring.py（engagement velocity）
  - tests/briefing/test_scoring.py -k velocity 用例
```

---

## 5. 执行要求

- TDD：先写失败测试再实现（适用于代码 step）
- 改 scoring.py 的 step：必须先确认「改 scoring 本体」已获用户授权，否则停止
- 完成后跑 `pytest tests/` 全绿再交付

---

## 6. 成功标准（SC，1:1 可验收）

- [ ] SC-1：高速新帖 engagement > 同总量老爆款
  - verify (test): `pytest tests/briefing/test_scoring.py -k velocity`
  - expect: `绿`
- [ ] SC-2：缺 created_at 时回退累计值不崩
  - verify (test): `pytest tests/briefing/test_scoring.py -k velocity_missing_created_at`
  - expect: `不抛异常`

---

## 7. Hard-stop

- 未获 scoring 授权而本 task 改 scoring.py → 停止
- 写入越出 owned_paths → 停止
- 嵌入/网关失败 → 走降级（不让 pipeline 崩），不静默吞错
