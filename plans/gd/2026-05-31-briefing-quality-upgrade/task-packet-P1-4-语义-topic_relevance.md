# Task Packet: p1-4-语义-topic_relevance

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-task-packet

> 自包含合约：本 packet 可被子 agent 单独执行；不依赖会话上下文。

---

## 1. 标识

```yaml
task_id: p1-4-语义-topic_relevance
agent_role: implementer
parent_step: P1-4
parent_plan: plans/gd/2026-05-31-briefing-quality-upgrade/step-P1-4-语义-topic_relevance.md
created_at: 2026-05-31T11:00:00Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   把简报从「白名单熟人 + 关键词堆词」双窄门换成「语义 + 社区反响」，并上线调度
PHASE_GOAL:   替换 16 词关键词窄门
TASK_GOAL:    topic_relevance 改锚点 max cosine 归一化，签名不变；嵌入失败回退关键词版
```

---

## 3. 依赖与并发

```yaml
blocked_by:
  - P1-2
  - P1-3
can_parallel_with:
  []
required_context:
  - src/akb2/briefing/embeddings.py
  - reports/（P1-2 标定输出）
  - plans/gd/2026-05-31-briefing-quality-upgrade/step-P1-4-语义-topic_relevance.md
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
  - src/akb2/briefing/scoring.py（语义 topic_relevance + 关键词回退）
  - tests/briefing/test_scoring.py -k semantic 用例
```

---

## 5. 执行要求

- TDD：先写失败测试再实现（适用于代码 step）
- 改 scoring.py 的 step：必须先确认「改 scoring 本体」已获用户授权，否则停止
- 完成后跑 `pytest tests/` 全绿再交付

---

## 6. 成功标准（SC，1:1 可验收）

- [ ] SC-1：中文/无buzzword AI 内容 relevance > 离题
  - verify (test): `pytest tests/briefing/test_scoring.py -k semantic`
  - expect: `绿`
- [ ] SC-2：嵌入失败回退 _topic_relevance_keywords 不崩
  - verify (test): `pytest tests/briefing/test_scoring.py -k semantic_fallback`
  - expect: `不抛异常`

---

## 7. Hard-stop

- 未获 scoring 授权而本 task 改 scoring.py → 停止
- 写入越出 owned_paths → 停止
- 嵌入/网关失败 → 走降级（不让 pipeline 崩），不静默吞错
