# Task Packet: p2-2-novelty-语义去重

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-task-packet

> 自包含合约：本 packet 可被子 agent 单独执行；不依赖会话上下文。

---

## 1. 标识

```yaml
task_id: p2-2-novelty-语义去重
agent_role: implementer
parent_step: P2-2
parent_plan: plans/gd/2026-05-31-briefing-quality-upgrade/step-P2-2-*.md
created_at: 2026-05-31T11:00:00Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   把简报从「白名单熟人 + 关键词堆词」双窄门换成「语义 + 社区反响」，并上线调度
PHASE_GOAL:   防同义重复挤占展示位（共用 P1-1 缓存）
TASK_GOAL:    novelty 改语义近似去重（cosine>阈值判重），复用 P1-1 嵌入缓存
```

---

## 3. 依赖与并发

```yaml
blocked_by:
  - P1-1
can_parallel_with:
  []
required_context:
  - src/akb2/briefing/scoring.py
  - src/akb2/briefing/embeddings.py
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

- [ ] SC-1：改写转发的同义推文 novelty=0
  - verify (test): `pytest tests/briefing/test_scoring.py -k novelty_semantic`
  - expect: `绿`
- [ ] SC-2：异质同主题内容不误判重
  - verify (test): `保守阈值单测`
  - expect: `novelty>0`

---

## 7. Hard-stop

- 未获 scoring 授权而本 task 改 scoring.py → 停止
- 写入越出 owned_paths → 停止
- 嵌入/网关失败 → 走降级（不让 pipeline 崩），不静默吞错
