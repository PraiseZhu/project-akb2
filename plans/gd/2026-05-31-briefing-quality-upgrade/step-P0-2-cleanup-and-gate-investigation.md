# Step P0-2: 清理 06-01 残报 + 🔗 残留 + needs_repair 根因调查 v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-step-plan

日期：2026-05-31
状态：draft
负责人：Claude 执行

---

## 1. 目标链

```text
PROJECT_GOAL: 真实体现 AI 社区高质量内容，且第一时间被捕获
CHAIN_GOAL:   清掉旧产物干扰 + 查清被忽略的质量信号（不让 silent 退化）
PHASE_GOAL:   ref master-plan §1
TASK_GOAL:    删旧英文残报；清正文孤立 🔗；查清 gate 为何每次 needs_repair
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`app_code`
- REVIEW_FOCUS：删除操作可逆性/不误删; 🔗 清理不伤正文; gate 根因证据充分
- Domain notes：删 vault 文件需用户确认（外部产物）；needs_repair 属 NON_GOAL 的"修"，本 step 只"查清"。

---

## 3. 前置条件

- blocked_by：`—`（独立，不需 scoring 授权）
- 必须 artifact：vault 路径可写授权（用户已多次"入库"授权）
- Hard-stop：删 vault 文件前必须先确认目标确为旧残报（读 frontmatter 确认 evidence=dry_run/源是英文 fallback），不误删正式简报

---

## 4. 成功标准（SC）

- [ ] SC-P0-2a：06-01 英文残报清理（确认是旧代码英文 fallback 产物后删除/归档）
  - verify (command): `ls "<vault>/AI TRENDS/Social/" | grep 2026-06-01` 无残报，或已移入 archive
  - expect: 不在正式目录
- [ ] SC-P0-2b：正文孤立 🔗 残留清零（`_strip_inline_links` 顺带去掉剥链接后残留的孤立 🔗/连接词尾巴）
  - verify (test): 含 "🔗 https://t.co/x" 的推文渲染后无孤立 🔗
  - expect: 绿
- [ ] SC-P0-2c：needs_repair 根因调查报告
  - verify (path): `reports/gate-needs-repair-diagnosis.md` 含「gate 哪条规则触发 + 为何 + 修/不修建议」
  - expect: 根因明确

---

## 5. 非目标

- 不修 gate 规则本体（只查清根因）
- 不动 scoring（本 step 与 P1 授权无关）

---

## 6. 边界与失败模式

- 删 vault 文件不可逆 → 先 archive 再删，或仅移动到 `archive/`，并向用户确认
- 🔗 清理可能误删正文中有意义的 🔗（如本就是内容的一部分）→ 仅清"紧邻被剥链接位置"的孤立符号
- needs_repair 调查若涉及读 gate CLI 内部 → 只读不改（gate 本体 NON_GOAL）
