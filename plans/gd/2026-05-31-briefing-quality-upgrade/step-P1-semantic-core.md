# Step P1: 质量内核换语义（topic 语义化 + authority 降权 + banned 精确化）v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-step-plan

日期：2026-05-31
状态：draft
负责人：Claude 执行；Codex 可选 cross-review

---

## 1. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容
CHAIN_GOAL:   把简报从「白名单熟人 + 关键词堆词」双窄门换成「语义 + 社区反响」
PHASE_GOAL:   ref master-plan §1
TASK_GOAL:    用嵌入语义相关度替换 16 词关键词；降 author_authority 权重；BANNED 精确化；全程 eval 量化优于旧版
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`app_code`
- REVIEW_FOCUS：语义相关度正确性; 嵌入失败降级; 权重和=1; 误杀防护; eval 量化闸门
- Domain notes：P1-3..P1-6 都改 `scoring.py`，强制串行同 PR；嵌入走网关 text-embedding-3-small（已验证 1536 维多语言）。

---

## 3. 前置条件

- blocked_by 链（**已修排序**）：`P1-1`（嵌入客户端）→ `P1-2`（离线标定）→ `P1-3`（ScoreEngine 批量化）→ `P1-4`（语义 relevance）→ `P1-5`（authority 降权）→ `P1-6`（BANNED）
- **硬排序**：P1-4（语义 relevance）必须在 P1-3（批量预嵌入）之后，否则逐条联网慢到不可用
- **硬捆绑**：P1-4 与 P1-5 必须同 PR（只做一半效果互相抵消）
- 必须 artifact：`embeddings.py`、离线对比报告（标定归一化区间）、eval harness（P1-7，验收用）
- Hard-stop：未获「改 scoring 本体」授权 → 停止；离线对比未证明语义优于关键词 → 停止不硬上

---

## 4. 成功标准（SC）

- [ ] SC-P1-a：嵌入客户端批量+缓存+失败抛错（master SC-2/6）
  - verify (test): `pytest tests/briefing/test_embeddings.py`
  - expect: 绿
- [ ] SC-P1-b：语义 topic_relevance，中文/无 buzzword AI 内容 relevance > 离题（master SC-4）
  - verify (test): `pytest tests/briefing/test_scoring.py -k semantic`
  - expect: 绿
- [ ] SC-P1-c：author_authority 权重 ≤0.10 且 DEFAULT_WEIGHTS 和=1（master SC-5）
  - verify (assertion): sum + 阈值断言
  - expect: True
- [ ] SC-P1-d：嵌入失败回退关键词版不崩（master SC-6）
  - verify (test): 注入失败单测
  - expect: 不抛异常
- [ ] SC-P1-e：BANNED 不误杀 AI 安全内容（master SC-7）
  - verify (test): 含 exploit 的 AI 推文 safety≠0
  - expect: 绿
- [ ] SC-P1-f：eval 量化新版排序 ≥ 旧版（master SC-8）+ 旧测试重写全绿（master SC-9）
  - verify (path+command): `reports/scoring-eval-*.md` + `pytest tests/`
  - expect: 新≥旧 且 全绿
- [ ] SC-P1-g：新建打分排序 eval harness + 标注集（master SC-8a，P1-7，可早于 scoring 改动开工）
  - verify (path): `evals/briefing-scoring/` 含 runner + 标注 fixtures
  - expect: 可一键跑排序指标
- [ ] SC-P1-h：嵌入缓存被 relevance(P1-4) 与 novelty(P2-2) 共享，每条推文只嵌一次
  - verify (test): 同一批推文跑 relevance+novelty，嵌入客户端调用次数 == 去重后文本数
  - expect: 无重复嵌入

---

## 5. 非目标

- novelty 语义去重 / engagement velocity / zh 抓取 归 P2
- 不动 gate / 调度 / schema

---

## 6. 边界与失败模式

- P1-3 与 P1-4 只做一半 → 互相抵消，必须同 PR（盘查结论）
- 嵌入网关超时 → 降级关键词版（SC-P1-d）
- 归一化区间 [0.12,0.40] 是 5 样例估值 → 必须 P1-2 离线标定后再写死
- TDD：每步先写失败测试再实现
