# Step P1-2: 离线对比 + 标定 v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-step-plan

日期：2026-05-31
状态：draft
负责人：Claude 执行；Codex 可选 cross-review

---

## 1. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   把简报从「白名单熟人 + 关键词堆词」双窄门换成「语义 + 社区反响」，并上线调度
PHASE_GOAL:   改 scoring 前用真实数据去风险
TASK_GOAL:    对今日真实 30 条推文跑关键词版 vs 语义版排序并排，标定归一化区间写入常量
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`app_code`
- REVIEW_FOCUS：本 step 改动正确性; 失败降级/边界; 不越 owned_paths
- Domain notes：ref master-plan §2 / §5a 串行约束 / §6 维度 note

---

## 3. 前置条件

- blocked_by：P1-1
- can_parallel_with：—
- 必须 artifact：见 §4 verify 引用
- Hard-stop：ref master-plan §6/§7（改 scoring 需授权；嵌入失败降级；不并行同改 scoring.py）

---

## 4. 成功标准（SC，anti-fill 绑定）

- [ ] SC-P1-2-1：输出关键词 vs 语义排序差异表
  - verify (method: path): `ls reports/scoring-semantic-offline-compare-*.md`
  - expect: `存在含两列`
- [ ] SC-P1-2-2：标定归一化区间写入常量（替换估值 [0.12,0.40]）
  - verify (method: assertion): `grep 归一化常量 in scoring/embeddings`
  - expect: `命中标定值`

---

## 5. 非目标

- 不做本 step 之外的 Phase 任务
- 不改 gate / schema / 调度时间 / secrets

---

## 6. 边界与失败模式

- ref master-plan §6（嵌入失败降级、P1-4 须在 P1-3 后并与 P1-5 捆绑、嵌入缓存共享）
- owned_paths 越界 → 停止；改 scoring.py 的多 step 必须串行（§5a）
