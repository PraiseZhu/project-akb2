# Plan: AKB2.0 知识库后续闭环 v2.1（缩水版）

日期：2026-05-08
状态：ready-for-implementation
负责人：Claude Code / Codex
源 plan：v2（5 阶段）→ 收缩为 2 阶段闭环；3 项剥离为后续独立计划

## 与 v2 的差异

| v2 阶段 | v2.1 处置 | 理由 |
|---------|-----------|------|
| Stage 1 P2 清零 | **保留为 Stage 1** | 真闭环；v0.3 / 后续工作的硬前置 |
| Stage 2 baseline freeze | **并入 Stage 1 收尾** | 本质是 Stage 1 完成后的状态报告，不需要独立编号 |
| Stage 3 LINT v0.3 | **重塑为 Stage 2: standards/concept census** | v0.3 规则是否落地依赖 census 结果；与 BL-D-014 concept census 合并 |
| Stage 4 内容扩展 | **撤出 → 后续独立计划** | 持续工作流，非闭环动作 |
| Stage 5 自动化 dry-run | **撤出 → 后续独立计划** | 不 live-enable 时，dry-run 设计的产出物用途模糊 |
| Stage 5 `cc-akb-search` MCP 挂载 | **撤出 → 后续独立计划** | MCP 阻塞从 BL-D 阶段就在，挂着不解决=空话 |

## Review 对齐

REVIEW_DOMAIN：docs_content

REVIEW_FOCUS：
- 维护记录 P2 清理是否只压缩审计行，不改正文论点
- v0.2 clean baseline freeze 报告是否准确反映清零后状态
- standards census 是否覆盖完整 standards 文档集，不漏检
- concept census 是否如实列出 BL-D-014 待建概念的证据缺口

Domain-specific notes：
- docs_content：阶段 1 改 active 页 `## 维护记录`；阶段 2 仅产出 census 报告，不改 active
- ai_infra：toolbox matrix 已 APPROVED，本计划不触
- app_code：无
- other：撤出项后续单独立项

## 目标与验收

用户目标：
- 把 AKB2.0 从 "active P0/P1 干净，但仍有 P2 和未明确的 v0.3 / 概念欠账" 推进到 "P0/P1/P2 全清零 + 后续动作有据可依"

成功标准：
- [ ] 阶段 1：清零 21 个 `BODY-MAINTENANCE-LOG-VERBOSE` P2，v0.2 lint 达到 `P0=0 / P1=0 / P2=0`，并在同一份 cleanup report 末尾写 baseline freeze 段
- [ ] 阶段 2：产出 standards census 报告（列出可机器检查 + 有 citation 的候选规则）+ concept census 报告（列出 BL-D-014 待建概念及其证据来源 / graph role）
- [ ] census 结果用于决定是否启动 v0.3 / 内容扩展独立计划

非目标：
- 不修旧 AKB1
- 不接 fetch/synth/translate/NLM/每日简报
- 不改 toolbox 正文或 SKILL.md
- 不在本计划内实施 v0.3 规则、内容扩展、自动化 dry-run、MCP 挂载

## 当前状态

相关事实：
- 最新 lint report：`docs/phase-c-reports/lint-cli-v0.2-2026-05-08T001657.md`，15 active，`P0=0 / P1=0 / P2=21`
- 21 个 P2 全部是 `BODY-MAINTENANCE-LOG-VERBOSE`；当前 5 个 `wiki/tools` 已无该 P2
- `GRAPH-LINKING` standards 示例旧 stem 已轻量修正（2026-05-08 APPROVED）
- toolbox matrix follow-up review 已 APPROVED
- `cc-akb-search` 仍是 wiki active / toolbox candidate，原因 `mcp__akb-search not mounted`

现有入口：
- AKB root：`/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0`
- Lint CLI：`.akb-runtime/lint/akb2-lint.py`
- Standards 集：`docs/AKB-2.0-*.md`（含 GRAPH-LINKING、ACTIVE-PAGE-CONSTITUTION、CHARTER 等）
- BL-D-014 入口：`docs/phase-d-reports/`（待 census）

约束：
- active 正文必须 source-grounded
- 维护记录只留短审计；长解释指向 phase report
- 标准不清时不批量修 active
- 不 live-enable 自动化

## 代码执行方向

新增：
- `docs/phase-c-reports/maintenance-log-p2-cleanup-2026-05-08.md`（含末尾 baseline freeze 段）
- `docs/phase-d-reports/standards-rule-census-2026-05-08.md`（Stage 2 输出）
- `docs/phase-d-reports/concept-census-bl-d-014-2026-05-08.md`（Stage 2 输出）

修改：
- 阶段 1：只改含 P2 的 active 页 `## 维护记录` 段
- 阶段 2：只产出 report，不改 active

不修改：
- `toolbox/**`
- `staging/**`
- `~/.claude/**`
- 旧 AKB、抓取链路、外部服务配置
- 任何 active 页正文论点 / 证据 / graph relation

## 接口与数据合约

CLI 合约：
- 命令：`python3 .akb-runtime/lint/akb2-lint.py`
- 阶段 1 验收：exit 0，`P0=0 / P1=0 / P2=0`

Census 合约：
- standards census 每条 candidate rule 必含：rule id (proposed)、severity、target object、check method (regex/AST/manual)、standards citation (file:section)、estimated finding count
- concept census 每条待建 concept 必含：proposed slug、source evidence (file path)、graph role (concept hub / source / design / N-A)、blocker note

## 实现步骤

### Stage 1: P2 清零 + baseline freeze

1.1 从最新 v0.2 lint report 提取 21 个 `BODY-MAINTENANCE-LOG-VERBOSE` 命中，按文件分组
1.2 对每条超长维护记录压缩到 ≤200 chars，保留：
   - 日期
   - 动作类别（rewrite / cleanup / rename / etc.）
   - 依据报告路径
1.3 不改 frontmatter、正文观点、source evidence、graph relation
1.4 跑 v0.2 lint，目标 `P0=0 / P1=0 / P2=0`
1.5 写 cleanup report：
   - before/after 对比表（按文件）
   - 触碰文件清单
   - lint 前后状态对照
   - **末尾 baseline freeze 段**：15 active 清单 + template 分布 + lint v0.2 状态 + toolbox matrix 状态 + `cc-akb-search` split + known deferred

### Stage 2: standards census + concept census

2.1 standards census：
   - 全量扫描 `docs/AKB-2.0-*.md` standards 文档集
   - 提取所有"应该 / 必须 / 不得 / required" 性质条款
   - 对每条标记：是否可机器检查 / 是否已被 v0.2 覆盖 / 是否有可执行 check
   - 输出 candidate rule list（仅候选，不实现）
   - 空集时如实写 "0 standards-backed candidate rule"，不强凑
2.2 concept census：
   - 从 BL-D-014 phase report 提取所有提到的待建概念
   - 对每个：proposed slug + source evidence + graph role + blocker note
   - 输出 concept candidate list（仅候选，不建页）
2.3 census 报告交付后，本计划完结
2.4 是否启动 v0.3 / 内容扩展独立计划，由用户基于 census 结果决定

## 边界与失败模式

必须处理：
- 阶段 1 若压缩会丢失审计依据 → 改为指向 phase report，不删除事实
- 阶段 1 若 lint 不是 `P2=0` → 不写 baseline freeze 段，标记 stage failed
- 阶段 2 若 standards census 出 0 条可执行规则 → 如实记录，本计划仍算完结（用户拿这个结果决定 v0.3 是否撤掉）
- 阶段 2 若 concept census 出 0 个待建概念 → 同上

明确不处理：
- 不在本计划修正文质量
- 不实现任何新 lint 规则
- 不建任何新概念页
- 不写任何 automation 设计文档
- 不处理 MCP 挂载

风险与防护：
- 风险：维护记录压缩后不可追溯
  防护：保留报告链接 / 依据，细节放 cleanup report
- 风险：census 范围漂移（顺手实现规则 / 顺手建页）
  防护：census 输出严格只是 list，不含实现 / 不含建页动作

## 测试计划

集成验证：
- 阶段 1：`python3 .akb-runtime/lint/akb2-lint.py`
  - 期望：`P0=0 / P1=0 / P2=0`
- 阶段 1：grep 验证维护记录字符数 ≤200
- 阶段 2：standards census 报告与 standards 文件总数一致性核对
- 阶段 2：concept census 报告与 BL-D-014 phase report 提及概念一致性核对

测试隔离：
- 阶段 1 只改 active 页 `## 维护记录` 段
- 阶段 2 只产出 phase-d-reports，不污染 active

暂不执行：
- 不验证外部 MCP
- 不跑抓取链路
- 不跑旧 AKB 迁移
- 不实现 v0.3 规则的 fixtures（撤出后单独立项时再做）

## 撤出项 → 后续独立计划

以下 3 项不在本 v2.1 计划内，但记录为后续独立计划，待 Stage 2 census 完成后由用户决策是否启动：

| 撤出项 | 启动条件 | 备注 |
|--------|---------|------|
| LINT CLI v0.3 实现 | Stage 2 standards census 出 ≥3 条 standards-backed 候选规则 | 候选数 < 3 → 撤掉，不勉强 |
| 内容扩展（补样 + BL-D-014 概念建页）| Stage 2 concept census 完成 + 用户确认补样优先级 | candidate-only，正文 source-grounded |
| 自动化 dry-run + `cc-akb-search` MCP 挂载 | 用户明确启用意图 | dry-run 不做无消费者的设计文档 |

## 实施顺序

```
Stage 1 (P2 清零 + baseline freeze)
  ↓ review APPROVED
Stage 2 (standards census + concept census)
  ↓ review APPROVED
本计划完结 → 用户基于 census 决定后续独立计划是否启动
```

每阶段完成后 review，再进入下一阶段。

## 待确认

- 无。按 1→2 顺序推进。

## 实施前 Review Checklist

- [ ] 目标、成功标准、非目标清楚
- [ ] REVIEW_DOMAIN 和 REVIEW_FOCUS 清楚
- [ ] 新增/修改/不修改的文件范围清楚
- [ ] CLI / census 输出合约清楚
- [ ] 边界情况和失败模式清楚（含 census 出空集时的处理）
- [ ] 撤出项的启动条件清楚

---

> **审查提醒**: 计划完成后可手动 `/review plan` 触发 Codex 审查。不自动触发。
