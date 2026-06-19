# 底座修宪草案 · 内容质量循环主链路上线前置

> **状态:DRAFT —— 待 praise 审批后才应用到 vault。本文件在工程 repo,不是 vault。**
> 日期:2026-06-17
> 范围:3 项底座改动(F1 自动升格 / F2a 星级名分 / F3 阶段对齐),为循环1 主链路上线扫清底座障碍。
> 依据:F4 schema 对账(子代理,2026-06-17),靶点行号均经只读核对。

---

## 0. Sync Impact Report(供 CONTENT-STANDARD §16 修订历史登记)

- **触发**:内容质量循环(循环1)主链路即将上线,底座需(F1)授权五闸自动升格、(F2a)给入库页定义 `star_level` 名分、(F3)把阶段模型对齐到"链路已建"。
- **受影响不变量**:
  - INTAKE-GATE §1 不变量 3(没有自动 active)
  - CONTENT-LIFECYCLE §1.3 不变量 1/2(没有自动 active / active 必须有 reviewer)、§5.2 禁止转换、§6 actor 委托、§7 升级硬条件
  - CONTENT-STANDARD §9.2 当前阶段、§12.3 phase gate、§3.8.1 frontmatter 白名单、§3.6.4 字段决策矩阵
- **核心设计**:不取消"没有未授权自动 active"这条底线,而是**新增一类合规写者——"机器 reviewer(五闸)"**:五闸全过 = 用户预批(2026-06-13)的常驻委托所授权的机器 reviewer 路径,经 manifest 单写者写 active。仍然禁止"入口直写"和"Lint 越权提升"。
- **下游代码影响(属主链路,不在本草案)**:
  - promote.py `finalize_promotion` 必须写 `reviewer: akb2-quality-loop-v1` + `reviewed_at` + `gate_reason`(F4 发现当前不写)。
  - distill.py 必须补 6 个缺失必填字段 + `star_level: 0`(F4 发现当前缺)。
- **版本**:CONTENT-STANDARD §16 修订历史 +1;新增 docs/AKB-2.0-STAR-RATING.md。

---

## F1 · 自动升格修宪(机器 reviewer 路径)

### 靶点与现状(F4 核对)
| 文件 · 位置 | 现状原文(摘) | 冲突 |
|---|---|---|
| INTAKE-GATE §1 不变量 3(line 26) | "**没有"自动 active"**:即使来源可信、5 项硬指标全过,也不直接 active" | 挡死五闸自动升格 |
| CONTENT-LIFECYCLE §1.3 不变量 1(line 41) | "**没有自动 active**:任何 actor(人/Claude/Codex/Lint/pipeline)都不能跳过门控直接产出 active" | 同上 |
| CONTENT-LIFECYCLE §1.3 不变量 2(line 42) | "active 由 `gate_decision: active` 产生:且必须有 reviewer + reviewed_at + gate_reason" | 需定义机器 reviewer |
| CONTENT-LIFECYCLE §6(line 171-182) | "Claude/Codex 仅当被用户明确委托为 reviewer 时可批准 active;自动化不可跳过门控" | 需把五闸定义为"预批的常驻委托" |

### 拟改(草案文本)

**机器 reviewer 定义(新增,放 CONTENT-LIFECYCLE §6.1 委托机制内)**:
> 机器 reviewer `akb2-quality-loop-v1` 是一类受**用户常驻委托**的自动 reviewer。其委托记录 = praise 2026-06-13 预批决策(五闸=黄金样张标准机判,满足即自动 active)。委托可随时撤销(设 `AKB2_AUTO_PROMOTE=0` 即回退提议模式)。该 reviewer 仅在**五闸全过**时有权写 active,且必须经 manifest 单写者、写全 `reviewer/reviewed_at/gate_reason`。
> **五闸** = gate 10/10 + decision_confidence high + graph_readiness real_link + lint 0 finding + GPT-5.4 异族复核 PASS。

**INTAKE-GATE §1 不变量 3 改为**:
> 没有**未经授权的**自动 active:任何入口不得绕过门控直写 active。**例外**:受用户常驻委托的机器 reviewer(`akb2-quality-loop-v1`)在五闸全过时,经 manifest 写 active —— 这不是"绕过门控",五闸本身就是门控的机器执行。

**CONTENT-LIFECYCLE §1.3 不变量 1 改为**:
> 没有**未授权**自动 active:跳过门控直接产出 active 仍永久禁止;受委托机器 reviewer 经五闸 + manifest 的升格不属此列。

**不变量 2、§5.2、§7 保持不变**(它们要求 active 必须有 reviewer+reviewed_at+gate_reason、必经门控——机器 reviewer 路径**全部满足**,无需改;只需 promote.py 真的写这三个字段)。

---

## F2a · 星级名分(只定契约,升星引擎留循环3/Plan 4)

### 新建 `docs/AKB-2.0-STAR-RATING.md`(草案内容)
> **§1 目的**:定义知识页的实测价值星级(constitution 原则 8),与"能否入库"的门控判定正交。
> **§2 星级语义(L4 红线)**:
> - 0 星 = raw 提炼的基础 wiki(**全部新入库页默认 0 星**)
> - 1 星 = 同层 ≥3 篇高频共现合成(落 `wiki/synthesis/`)
> - 2 星 = 同机制作用于 1 星层
> - 3 星 = 封顶,不再向上合成
> **§3 字段**:`star_level`(整数 0-3,默认 0,机器索引短标量)。
> **§4 不变量**:同一知识永远只有一份正本在长大(混层共现只喂养不升格);合成品不豁免任何门控(走完整循环1 + 五闸)。
> **§5 参数(L3,可默许调)**:共现 K/N/窗口、降星归档阈值 —— 由循环3 实现,本标准只占位。
> **§6 范围声明**:本次仅锁"名分"(字段 + 语义 + 0 星基线 + synthesis 落位)。**升星引擎(共现检测/合成/降星)= 循环3 / Plan 4,不在本标准实现范围。**

### `PAGE-TEMPLATES` 改动(F4 精确锚点)
- §3.8.1 frontmatter 白名单"# 状态(短)"分组:在 `evidence_provenance`(line 456)与 `reviewer`(line 457)之间**插入 `star_level`**;白名单字段数 17→18。
- §3.6.4 字段决策矩阵(line 308)"短状态/标签"列举:补 `star_level`。
- §4.2 source 模板字段集:`star_level` 默认 0(active 非硬必填,缺失视同 0)。
- **不放进** `quality_dimensions`/`active_quality_score`(那是连续分预留字段;星级是 0-3 离散,语义不同,constitution 原则 8 明禁连续分排序)。

### `CONTENT-STANDARD` 改动
- §3 或 §4 加一句引用:"知识页实测价值分级见 [[docs/AKB-2.0-STAR-RATING|STAR-RATING]];入库页默认 0 星,升星机制由循环3 实现。"

---

## F3 · 阶段模型对齐

### 靶点与现状(F4 核对)
| 文件 · 位置 | 现状 | 冲突 |
|---|---|---|
| CONTENT-STANDARD §9.2 | "当前阶段(A)原则:不接链路" | 工程侧循环1 全链已建 |
| CONTENT-STANDARD §12.2 | B 阶段 checklist:"不引入 fetch/translator/synth 自动化代码""不开任何 cron""不动旧内容" | 与 7×24 循环1 直接矛盾 |
| CONTENT-STANDARD §12.3 | B→C→D→E→F 准入全是"占位" | 未授权链路上线 |

### 拟改(草案文本)
**§9.2 当前阶段更新为**:
> 当前阶段:**F(链路落地)**。Schema 层(root + 14 份 docs/)已 ready 并锁定;内容质量循环(循环1)主链路已建,授权"接链路 + 开 cron + 自动入库",受循环7 watchdog + 五闸 fail-closed 约束。A-E 阶段的"不接链路"原则为历史阶段口径,F 阶段起不再适用。

**§12.3 phase gate 填实(F→稳态)**:
> F 阶段准入(本次满足):循环1 七段链端到端跑通(SC-1/SC-3)+ 五闸自动升格修宪应用(F1)+ 前 50 条快照回滚兜底(FR-015)+ watchdog 自检绿。

---

## 应用清单(审批后执行)
1. INTAKE-GATE.md:改 §1 不变量 3
2. CONTENT-LIFECYCLE.md:改 §1.3 不变量 1 + §6.1 加机器 reviewer 委托定义
3. 新建 docs/AKB-2.0-STAR-RATING.md
4. PAGE-TEMPLATES.md:§3.8.1 白名单 + §3.6.4 矩阵 + §4.2 加 star_level
5. CONTENT-STANDARD.md:§9.2 + §12.3 + §3/§4 加 STAR-RATING 引用 + §16 修订历史登记 Sync Impact
6. (主链路阶段,非本草案)promote.py 写 reviewer/reviewed_at/gate_reason;distill.py 补 6 字段 + star_level:0
