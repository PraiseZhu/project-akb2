# Task Packet: p1-5-author_authority-降权

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-task-packet

> 自包含合约：本 packet 可被子 agent 单独执行；不依赖会话上下文。

---

## 1. 标识

```yaml
task_id: p1-5-author_authority-降权
agent_role: implementer
parent_step: P1-5
parent_plan: plans/gd/2026-05-31-briefing-quality-upgrade/step-P1-5-*.md
created_at: 2026-05-31T11:00:00Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   把简报从「白名单熟人 + 关键词堆词」双窄门换成「语义 + 社区反响」，并上线调度
PHASE_GOAL:   拆「看脸」窄门（与 P1-4 捆绑）
TASK_GOAL:    author_authority 权重 0.30→≤0.10，engagement 升权，DEFAULT_WEIGHTS 和=1.0
```

---

## 3. 依赖与并发

```yaml
blocked_by:
  - P1-4
can_parallel_with:
  []
required_context:
  - src/akb2/briefing/scoring.py
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

## 5. 执行要求

- TDD：先写失败测试再实现（适用于代码 step）
- 改 scoring.py 的 step：必须先确认「改 scoring 本体」已获用户授权，否则停止
- 完成后跑 `pytest tests/` 全绿再交付

---

## 6. 成功标准（SC，1:1 可验收）

- [ ] SC-1：权重 ≤0.10 且和=1.0
  - verify (assertion): `python3 -c 断言 sum==1 and authority<=0.1`
  - expect: `True`
- [ ] SC-2：与 P1-4 同 PR 提交（只做一半互相抵消）
  - verify (assertion): `git 同提交含 relevance+weight 改动`
  - expect: `同 PR`

---

## 7. Hard-stop

- 未获 scoring 授权而本 task 改 scoring.py → 停止
- 写入越出 owned_paths → 停止
- 嵌入/网关失败 → 走降级（不让 pipeline 崩），不静默吞错
