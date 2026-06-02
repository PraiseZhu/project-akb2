# Task Packet: p2-1-接入-zh-抓取合并

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-task-packet

> 自包含合约：本 packet 可被子 agent 单独执行；不依赖会话上下文。

---

## 1. 标识

```yaml
task_id: p2-1-接入-zh-抓取合并
agent_role: implementer
parent_step: P2-1
parent_plan: plans/gd/2026-05-31-briefing-quality-upgrade/step-P2-1-接入-zh-抓取合并.md
created_at: 2026-05-31T11:00:00Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   把简报从「白名单熟人 + 关键词堆词」双窄门换成「语义 + 社区反响」，并上线调度
PHASE_GOAL:   覆盖中文社区（不需 scoring 授权）
TASK_GOAL:    抓取层 fetch en+zh 合并，_DEFAULT_TIER 不再仅 en；与已做的 lang 分流配套
```

---

## 3. 依赖与并发

```yaml
blocked_by:
  []
can_parallel_with:
  - P0-1
required_context:
  - config/curator-accounts.yaml
  - plans/gd/2026-05-31-briefing-quality-upgrade/step-P2-1-接入-zh-抓取合并.md
precondition: 首步先真跑复验 fetch_tier.zh 11 账号产量非空（即 SC-2 的 zh 探测）；产量为 0 则先修账号配置再做合并，不直接改抓取层（Codex F1 修订）
```

---

## 4. 路径权限

```yaml
owned_paths:
  - src/akb2/briefing/intake/account_collector.py
  - src/akb2/briefing/pipeline.py
  - src/akb2/briefing/synthesis.py  # 执行中发现的隐藏硬依赖：synthesis 的 _merge_with_input/_fallback_synthesis 用字段白名单重建 tweet，会丢弃 collector 打的 lang 标 → translate 读不到 → P3a per-tweet 分流失效（静默失败）。lang 透传是 P2-1 端到端目标的必需部分，故正式纳入 owned_paths（2026-06-02 L3 review finding 1 修订）
forbidden_paths:
  - "/Users/praise/.claude/**"
  - 调度 plist / cron / LaunchAgent
  - 其他 step 的 owned_paths（未列入 required_context）
```

---

## 4b. 交付物（deliverables）

```yaml
deliverables:
  - src/akb2/briefing/intake/account_collector.py（en+zh 合并）
  - src/akb2/briefing/pipeline.py
  - tests/briefing/test_account_collector.py -k zh 用例
```

---

## 5. 执行要求

- TDD：先写失败测试再实现（适用于代码 step）
- 改 scoring.py 的 step：必须先确认「改 scoring 本体」已获用户授权，否则停止
- 完成后跑 `pytest tests/` 全绿再交付

---

## 6. 成功标准（SC，1:1 可验收）

- [ ] SC-1：collect 同时含 en+zh tier
  - verify (test): `pytest tests/briefing/test_account_collector.py -k zh`
  - expect: `绿`
- [ ] SC-2：真跑产出含中文大号内容
  - verify (command): `PYTHONPATH=src python3 -m akb2.briefing.intake.account_collector --output /tmp/akb2_zh_probe.json && grep -c '"lang": "zh"' /tmp/akb2_zh_probe.json`
  - expect: `命中`

---

## 7. Hard-stop

- 未获 scoring 授权而本 task 改 scoring.py → 停止
- 写入越出 owned_paths → 停止
- 嵌入/网关失败 → 走降级（不让 pipeline 崩），不静默吞错
