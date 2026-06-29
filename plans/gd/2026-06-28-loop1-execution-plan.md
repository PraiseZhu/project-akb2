# AKB2 循环1 执行计划(任务优先级 + 解法 + 验收 + 阶段)— 2026-06-28 定稿

配套:[[project_akb2_loop1_bottleneck_priority_2026-06-28]](记忆)、`specs/multi-source-distill/spec.md`(v2 设计)。
分支 `integration/loop1-content-quality`,HEAD=5dde324(本会话已提交 R1/R2/B1/wA#3)。

## 阶段依赖总览
```
P0 当前管线诚实化+补观测(地基,无悬念) 
   └→ P1 真数据定性 12 篇(唯一要你再拍一次的地方)
        └→ P2 据定性分类处理 → 异族审 → 合 main
v2 多源提炼(P0 后启动,spec 已落) → later: update / 升星(推后)
```
锁定参数:concept 阈值 6.5→6.0;满仓 active e2e(AKB2_AUTO_PROMOTE=1);准入用 deepseek 砍 gemini;raw 与 wiki 同标准差完成度。

---

## P0 — 当前管线诚实化 + 补观测(无悬念,先做)

| 任务 | 类型 | 解法 | 验收 |
|------|------|------|------|
| **P0-1 加权分必填 score+尺度0~1+阈值6** | 代码+参数 | grader prompt 要求每维输出 score(0~1);schema 把 score 设 required;`_parse_grader_extensions` 缺 score → fail-closed(不静默当0过);rubric.yaml concept threshold 6.5→6.0 | live 探针:concept grader 返回每维带 0~1 score、weighted_score 非0可复算;黄金样张 #4 加权分 ≥6.0 不再 weighted_score_below_threshold;离线锁"缺score→fail-closed" |
| **P0-2 must-cover 字面量渲染修**(救#0/2/8/17) | 代码 | 查 compose 为何 source_operation 字面量(`1. Pick the next…`)没进"操作步骤"节;修渲染/口径让字面量真出现在对应节 | 离线:含 source_operation 的 raw → compose 后该字面量在"操作步骤"出现、SG-must-cover 不报;#0/2/8/17 重跑不卡 must-cover |
| **P0-3 #16 命令形态判定补全** | 代码 | raw_verify 的 R1 denylist 升级成"命令形态"判定:单字(中文/短词)、表格碎片(含\|)、纯术语不算命令;真命令仍回查 | 离线:`和`/表格碎片/术语不报;真编造命令(pip install evil)仍报;#16 重跑不卡 |
| **P0-4 砍 gemini-flash,准入归 deepseek** | 代码 | admission 的 topic/how/novelty/lane 调用 gemini-flash → deepseek;确认不破异族(准入判源不判产物) | 离线全绿;live:准入用 deepseek;满仓 admission 段 model=deepseek |
| **P0-5 补观测:repair trace + lane_reason** | 代码(观测,不改判定) | repair 逐轮 final_defects 默认写可读位置;admission 落 lane_reason 到 ledger | 重跑后逐篇可读 repair 每轮缺陷演化 + 每篇 lane_reason;判定行为不变(离线绿) |
| **P0-6 active e2e + 满仓重跑** | 参数+验证 | 满仓设 AKB2_AUTO_PROMOTE=1 真入库;重跑 | 满仓跑完;通过率有数(从0动起);负样本仍100%拒;黄金样张过 |

**P0 出口**:离线全绿 + ruff + 满仓重跑完成(带 trace + lane_reason + active)。

---

## P1 — 用真数据定性那 12 篇(唯一需你再拍一次)

| 任务 | 类型 | 解法 | 验收 |
|------|------|------|------|
| P1-1 读 P0 重跑结果 | 分析 | 看 P0 修完通过率、剩几篇、各卡哪 | 知道现状基线 |
| **P1-2 语义审计 12 篇**(派 worker,有 trace) | 调查(决策前提) | 逐篇比对 raw 原文 vs 候选页 vs grader 指控 → 裁决:机制可修(location锁/A-B拉扯) / grader太苛(参数) / 源真薄(该拒) | 12 篇每篇有定性标签+依据 |

**P1 出口**:12 篇逐篇定性 → 据此定 P2 具体内容。**这是你要再拍一次的点:确认每篇归"救/调/拒"。**

---

## P2 — 据定性分类处理(内容由 P1 决定)

| 任务 | 触发条件 | 解法 | 验收 |
|------|---------|------|------|
| P2-a 改 repair 机制 | P1 判"机制可修" | location 容差匹配(grader自由文本 location→映射真实小节);缓解 A-B 拉扯 | 该批篇 repair 收敛 |
| P2-b 调 grader 参数 | P1 判"太苛" | 调对应维度权重/判据(rubric.yaml,不改判定代码) | 该批达标且无放水(负样本仍拒) |
| P2-c 承认该拒/换源 | P1 判"源真薄" | corpus 标 expected=reject/移出;达标定义="够格全过+该拒全拒" | 这些篇正确归类;满仓达"可达上限" |
| P2-d lane误判(#6/15)+reject后门(wA)+测试债(wB) | 并行 | 有 lane_reason 后定根修 lane;堵 reject 后门;补测试诚实 | 各自修+测试 |

**P2 出口**:满仓达"够格全过 + 该拒全拒" + 负样本100%;Codex 异族审 APPROVED → 合 main。

---

## v2 — 多源提炼(P0 后启动,spec 已落)

| 任务 | 解法 | 验收(对应 spec SC) |
|------|------|------|
| v2-1 raw 暂存 + 与 wiki 对齐的 raw 门 | deepseek 判"对某概念 ≥1 合格贡献"收、零贡献拒(同 wiki 负样本口径)+ 机判前置 | SC-3 raw门不误杀薄但有效源 |
| v2-2 概念图谱锚定路由 | deepseek 判讲哪个概念→挂 pending 槽(复用 known_concepts+pending_queue);embedding 仅做新概念去重召回 | 路由准、不混 |
| v2-3 pending 槽攒够 N:1 合成(create) | ≥K 源料够→只对该概念合成出新页;单篇够厚走1:1快路径;超时标"源不足" | SC-1 薄源攒够出过门页;SC-2 不相干被F4拒;SC-4 快路径不退化 |
| v2-4 eval harness 改 1:1→N:1 | 校准框架支持多源→一页 | 满仓能评 N:1 |

**v2 出口**:spec SC-1~5 全过;v1 不碰已有页(SC-5)。

---

## later(明确推后,不在当前范围)
- **update**(新 raw 并入已有页 + merge + 重评分 + 版本):v2 之后。
- **升星**(wiki→wiki 无新料的二度/三度深化,推高 How 密度/可执行性):独立未来,单独设计。

## 护栏(全程不破)
concept F4不放宽 / novelty不豁免 / 负样本必拒 / fail-closed / operations.scan不收窄 / Maker-Checker异族分离 / 二值地板不可用加权补偿 / 不靠造假绿测试。判定级改动过 Codex 异族审再合。
