```yaml
template_kind: gd-child-plan-output
parent_dispatch_id: loop1-content-quality-plan
parent_track_id: t-v2-planner
agent_role: child_planner
output_status: completed
```

# v2 多源提炼 + 概念图谱锚定 — gd-step-plan 草稿

> 本文件是 child_planner `t-v2-planner` 的原始草稿（PLAN 阶段 deliverable，主 agent 已 review 并采纳，见 ../reports/merge-report.md）。最终化 step plan 见 ../step-4-v2-multisource-distill.md。

## 目标链
- **PROJECT_GOAL**: AKB2 无人值守自动化知识库工程：fail-closed 门控 + 异族 Maker/Checker + 实测价值裁决（constitution 原则 3/6/7/8）。
- **CHAIN_GOAL**: 循环1 按内容车道分流质量标准（specs/content-lane-quality/spec.md）。
- **PHASE_GOAL**: v2 多源提炼 + 概念图谱锚定——薄但有效的源不丢，攒够再合成一页 wiki；保入库率不降质量。
- **TASK_GOAL**: 把 v2 四任务（v2-1..v2-4）落成自包含 step plan + 4 个候选 task packet，严格对齐 specs/multi-source-distill/spec.md 的 FR-1..FR-8 与 SC-1..SC-5。

## Review 对齐
- **REVIEW_DOMAIN**: `ai_infra`（管线/门控/数据流改动，非纯前端/文档）。
- 判定级改动（raw 门收/拒口径、路由归组、合成是否过门）属准入与门控判定面 → 实现后 MUST 过 Codex 异族审 APPROVED 再合 main。
- 异族不变量：raw 门 + 路由 + 合成 maker 全用 **deepseek**（判源/提炼，maker 侧）；F4 完整性地板 + grader 守卫用 **gpt-5.4**（checker 侧）。v2 不得让同一族模型既造又判（constitution 原则 7）。

## 前置条件（blocked_by）
- **硬阻塞 = P0**（见执行计划 P0 段，特别是 P0-1 加权分必填 score + 尺度 0~1 + 阈值 6.0 + 缺 score fail-closed）。
  - 理由（spec 前置依赖原文）：N:1 合成出来照样过同一 grader/加权门；加权分=0 不修，合成多好也是 0 分。`src/akb2/quality/weighted_score.py:108-114` 当前缺维即 score=0.0 fail-closed——P0 未落地前，所有合成页加权分恒 0，SC-1 不可能为真。
  - **blocked_by**: P0 step（尤其 P0-1）。v2 实现期可并行起草/写单测 fixture，但 SC-1/SC-4 的 live verify MUST 在 P0 出口（离线全绿 + 满仓重跑 weighted_score 非 0 可复算）之后执行。
- **软依赖 = P0-4**（砍 gemini-flash，准入归 deepseek）。v2-1 raw 门复用准入的 `route_llm_fn` 调 deepseek；若 P0-4 已把 admission 的 topic/how/novelty/lane 切到 deepseek，v2-1 直接沿用同一路由，不另起模型。

## 本 step Success Criteria（绑 spec SC-1..SC-5）

| SC | 命题（spec 原文） | verify method | cmd | expect |
|----|------|------|------|------|
| SC-1 | 一篇单独 1:1 会失败的薄源，挂概念槽、与 ≥K 个同概念源合成后，能出一页过质量门（F4+grader+加权）的 wiki | command | `pytest -k "multisource and synthesis" -q` | 绿；含 1 例「单源 floor fail → N 源合成后 F4 pass + weighted_score ≥ threshold」断言 |
| SC-2 | 不相干源被合进同一页时，因 F4 因果边不连通被拒（连贯性守门生效） | command | `pytest -k "multisource and incoherent" -q` | 绿；喂跨概念混料 → `evaluate_integrity_floor` 返回 fail（因果边不连通），合成页被拒不入库 |
| SC-3 | raw 门只挡不可用，不误杀「暂时单薄但领域相关」的源（它们进池等攒） | command | `pytest -k "raw_gate" -q` | 绿；薄但对某概念 ≥1 贡献样本 → raw 门 passed=True 进池；新闻/营销/零贡献 → rejected（与 wiki 负样本同口径） |
| SC-4 | 单篇够厚仍走 1:1 快路径，时效不退化 | command | `pytest -k "fast_path_1to1" -q` | 绿；机制组件候选 ≥3 的厚源 → 路由判定 `route=fast_1to1`，不进 pending 槽、不等攒 |
| SC-5 | v1 全程不改写任何已入库 wiki 页 | command | `pytest -k "multisource and no_wiki_mutation" -q` | 绿；合成全程仅写 staging/candidate + pending 槽 + promote create，对 `wiki/**/*.md` 既有页 0 次写（断言 mtime/内容快照不变） |

> 注：上表 SC-1..SC-5 = spec 验收。机器可读 proposal block 用聚合标识 **SC-9** 指代「v2 全套验收通过」（见 4.5），其 verify `pytest -k multisource -q` 覆盖上述全部用例。

## 实现步骤（每步 WHERE / WHAT / WHY / VERIFY）

### 步骤 A — raw 暂存 + 与 wiki 对齐的 raw 门（v2-1，FR-1/FR-2，SC-3）
- **WHERE**: 新增 `src/akb2/quality/raw_gate.py`；接线点在 `src/akb2/quality/runner.py:1262`（admission segment 之后、distill segment `1296` 之前）。复用 `admission.run_admission` 的 `route_llm_fn` 注入模式（`admission.py:148-172`）。
- **WHAT**:
  1. 机判前置（确定性，先挡不可用）：抽取成功（`ExtractResult.success`）+ 去重（沿用 runner 的 `content_hash` / `_is_processed`，`runner.py:164-244`）+ 非噪音/导航/付费墙（抽取层已做，raw_gate 只校验标志位，不重判）。
  2. deepseek 轻判：用 **wiki 同一套 taxonomy + 知识/机制定义**（复用 admission 的 lane/knowledge 判据，不另立松阈值旋钮）判这篇「对 wiki 标准里某个概念是否提供 ≥1 合格知识贡献（命名机制组件 / 可溯源论断）」。有 ≥1 → 收进 raw 等攒；对任何概念零贡献（新闻/营销/纯观点/噪音）→ 拒，口径与 wiki 负样本一致。
  3. raw 门 **不判**：how 密度阈值 / 车道分类 / 新颖度（spec 决策 2「移出 raw 门，成页时才判」）。
  4. 返回结构含 `passed` + `reason` + `contribution_evidence`（命中哪个概念/哪条贡献，供步骤 B 路由复用，省一次 LLM 调用）。
- **WHY**: spec FR-1（同标准差完成度，不另立松标准）、FR-2（deepseek 判源不判产物，不破异族）。机判前置满足「先挡不可用」。
- **VERIFY**: `pytest -k "raw_gate" -q` 绿；离线 fixture 含「薄但有效→收」「零贡献→拒」「抽取失败/重复→机判前置拒」三类；ruff 干净。

### 步骤 B — 概念图谱锚定路由（v2-2，FR-3/FR-4，SC-2 输入侧）
- **WHERE**: 新增 `src/akb2/quality/concept_routing.py`；接线在 runner raw_gate 之后。**复用现成基建**：`known_concepts` + `pending_queue` 由 `runner.py:_load_vault_concept_index`（`runner.py:394-495`）已装载（concepts/sources glob + staging/pending/concept-backlinks.jsonl）；pending 槽落盘格式沿用 promote.py 已有的 `concept-backlinks.jsonl`（`promote.py:842-857`，字段 `concept_slug` / `status:"pending"`）。
- **WHAT**:
  1. 厚度预判（确定性，FR-7 快路径分流）：对 raw 抽出机制组件候选；≥3 即「厚」→ 标 `route=fast_1to1`，直接走现有 distill 1:1（步骤 D 快路径），**不进 pending 槽**。
  2. 薄源：deepseek 判「这篇讲图谱里哪个概念」→ 命中 `known_concepts` 里某离散概念身份 → 挂到对应 **pending 槽**（v1 只挂 pending，不并入已有节点——并入=update 属 v2-后续）。锚定来自图谱拓扑（讲这个概念），不来自向量距离。
  3. 新概念去重召回（embedding 仅辅助，FR-4）：判不出已有概念时，用 embedding 找近义候选 → 仍由 **deepseek 判是否同一概念**（embedding 不做最终归组，只做召回防碎片化）。保守提名，命中既有则归既有槽。
  4. 写 pending 槽时带 `source_slug` / `content_hash` / `contribution_evidence`（来自步骤 A），使「来自 ≥K 个不同源」可统计。
- **WHY**: FR-3（raw 不丢，deepseek 路由挂具体概念）、FR-4（锚定离散概念身份，embedding 仅去重召回不做归组）。
- **VERIFY**: `pytest -k "concept_routing" -q` 绿；含「薄源命中已有概念→挂对应槽」「厚源→fast_1to1 不挂槽」「近义新概念→deepseek 判同一→不开新槽」三类离线断言。

### 步骤 C — pending 槽攒够 N:1 合成（v2-3，FR-5/FR-6/FR-7/FR-8，SC-1/SC-2/SC-4/SC-5）
- **WHERE**: 新增 `src/akb2/quality/multisource_distill.py`（N:1 合成编排薄壳）；底座复用 `distill.extract_claims`（`distill.py:558`）+ `distill.compose_candidate`（`distill.py:2076`，**已接受 `known_concepts`/`pending_queue` 参数**）。门控复用 `integrity_floor.evaluate_integrity_floor`（runner.py:652 调用点）+ `grader.run_grader_detailed`（`grader.py:676`）+ `weighted_score.compute_weighted_score`（`weighted_score.py:74`）+ `promote`（create 路径）。触发调度接线在 runner 主循环 `_process_one`（`runner.py:1481`）或独立攒料触发器。
- **WHAT**:
  1. 触发判定：某 pending 概念槽来自 **≥K 个不同源**（按 `source_slug` 去重计数；K 为锁定参数，落到与 rubric 同级的配置而非硬编码）料够 → 只对**这一个概念**触发合成。超时（槽内最旧条目超 TTL）仍未攒够 → 标 `source_insufficient` 告警落账，不无限囤（spec 决策 4）。
  2. 多源合成（deepseek maker，FR-5）：收 N 个 raw 的抽取片段（机制点/claims，**非全文硬拼**）→ 命名机制组件跨源凑齐、因果边跨源建立、**每条论断标注来自哪个源**（多源 provenance `[raw §X@source-k]`，沿用 distill 现有 provenance-split 机制 `distill.py:875-968`）。
  3. 输入侧 scoped（FR-5 / 双保险其一）：**只喂挂在该概念槽上的料，不卷图谱邻居**（routing 已 scoped 到单节点）。
  4. 过现有质量门（FR-6，不放宽地板）：合成页 → F4 连贯地板 → grader 逐维 → 加权过阈（≥6.0）。混了不相干内容则因果边连不起来 → F4 自动拒（输出侧兜底，SC-2）。不过不丢 → 退回槽等更多源。
  5. 快路径（FR-7）：步骤 B 标 `fast_1to1` 的厚源直接走现有 1:1 distill，不等攒。
  6. 仅 create（FR-8 / SC-5）：命中概念无 wiki 页 → 第一次合成出新页；**v1 MUST NOT 触碰/改写已入库 wiki 页**（合成只写 staging/candidate + promote create；update 属后续）。
- **WHY**: FR-5/6/7/8 + SC-1/2/4/5 的核心载体。直接解「单源凑不齐 ≥3 机制组件」的 F4 卡点。
- **VERIFY**: `pytest -k "multisource and (synthesis or incoherent or fast_path_1to1 or no_wiki_mutation)" -q` 绿；含 SC-1（单源 floor fail→N 源 F4 pass+加权≥阈）、SC-2（混料 F4 拒）、SC-4（厚源快路径）、SC-5（既有 wiki 页 0 写）四组断言。**SC-1 的 live 复算 verify 必须在 P0 出口后执行**。

### 步骤 D — eval harness 改 1:1→N:1（v2-4，SC-1 可评测）
- **WHERE**: `evals/main-pipeline-quality/`（corpus.yaml schema 扩展 + fixtures 增 N:1 组）+ `src/akb2/quality/calibration.py`（`_process_corpus_entry` `calibration.py:144`、报告聚合 `_init_report` `calibration.py:79`）。
- **WHAT**:
  1. corpus schema 扩展：现每条 entry 是「1 url → 1 页」（`expected: ingest|reject`）。新增多源条目类型，可声明「N 个 fixture 源 → 同一概念槽 → 1 合成页 expected: ingest」，并保留 1:1 旧条目兼容。
  2. calibration runner 支持多源→一页：`_process_corpus_entry` 识别多源 entry → 喂 N 源走 routing→攒够→合成→门控，统计「合成页是否过门」作为该 entry 的 pass/fail；报告新增 N:1 维度指标（不破坏现有 positive_pass_rate / negative_reject_rate 混池口径）。
  3. fixture：构造 ≥1 组「单篇 1:1 必失败的薄源 ×K → 同概念合成后过门」黄金多源样张（SC-1 的可评测锚）。
- **WHY**: spec 前置依赖「eval harness 要改：现满仓是 1 URL→1 页假设；N:1 改变『通过』定义」。
- **VERIFY**: `python -m src.akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out <tmp>` 跑通且报告含 N:1 段；`pytest -k "calibration and multisource" -q` 绿（离线 fixture，不依赖 live 网络）。

## 非目标（明确不做）
- **update**（新 raw 并入已有 wiki 页 + merge + 重评分 + 版本）→ 推后，v1 只做 create。
- **升星**（wiki→wiki 无新料的二度/三度深化）→ 独立未来，与多源提炼正交。
- **embedding 做最终归组/聚类** → 只做新概念去重召回，deepseek 做最终归组判定。
- **放宽 F4 完整性地板 / novelty 豁免 / 负样本放水 / 加权补偿地板**（constitution 原则 8：地板不可补偿；护栏全程不破）。
- raw 门**不**判 how 密度/车道/新颖度（成页时才判）。

## 边界
- 改动集中在 `src/akb2/quality/`（新增 raw_gate / concept_routing / multisource_distill 三模块 + runner 接线 + calibration 扩展）。不动 `briefing/` / `feishu_sync/` / `video/` / `toolbox_scan/`。
- pending 槽存储沿用 `<staging>/pending/concept-backlinks.jsonl`（promote.py 既有写入处），路径经 `loops/paths.get_staging_root` 解析，不硬编码。
- 仅 maker 侧（deepseek）扩 raw 门/路由/合成；checker 侧（gpt-5.4 grader + F4）零改判逻辑（只被复用调用），保异族。

## 风险与防护
| 风险 | 防护 |
|------|------|
| **新增有状态**（循环1 从逐篇无状态变 pending 概念槽 + 路由 + 触发调度） | 槽落盘复用既有 concept-backlinks.jsonl 格式（append-only + 幂等读 `_read_pending_concepts`）；触发器读槽时按 `source_slug` 去重计数；超时标 `source_insufficient` 不无限囤；与每日 briefing 自动化衔接点显式落 ledger。 |
| **新概念去重不足→碎片化**（同一概念开成多槽） | 提名新概念保守：先 embedding 召回近义候选 → deepseek 判是否同一 → 命中既有则归既有槽，判不出才开新槽；离线 fixture 锁「近义不开新槽」。 |
| **混入不相干内容**（路由把相邻但不同概念混进一页） | **双保险**：输入侧 scoped（只喂该节点料，不卷图谱邻居，routing 强制单节点 scope）+ 输出侧 F4 兜底（≥3 命名机制组件 + 因果边连通；混料因果边连不起来→F4 自动拒，SC-2）。 |
| **P0 未落地→合成页加权恒 0**（weighted_score 缺维 fail-closed） | blocked_by=P0 硬阻塞；SC-1/SC-4 live verify 在 P0 出口后执行；实现期可先写单测 fixture（mock grader 逐维 score）。 |
| **eval 口径漂移**（N:1 改变「通过」定义打乱现有 18 正/2 负混池） | calibration 新增 N:1 独立维度指标，不改现有 positive_pass_rate/negative_reject_rate 计算；旧 1:1 条目全兼容。 |
| **异族被破**（raw 门/路由误用 checker 族模型，或合成 maker 兼判） | raw 门/路由/合成全 deepseek（判源/提炼）；F4+grader 全 gpt-5.4（守卫）；接线点不引入跨族调用；判定级改动过 Codex 异族审。 |

## 测试计划
1. **离线单测（无网络/无 LLM live）**：每模块 fixture 注入 `route_llm_fn`，覆盖 SC-1..SC-5 全部断言（见各步骤 VERIFY）；mock grader 逐维 score 以在 P0 未落地时也能锁合成逻辑。
2. **ruff**：三新模块 + 改动文件 lint 干净。
3. **calibration 离线跑**：N:1 fixture 组跑通，报告含 N:1 段。
4. **live 复算（P0 出口后）**：满仓重跑，验证薄源攒够合成页 weighted_score 非 0 可复算且 ≥ threshold（SC-1）；负样本仍 100% 拒；既有 wiki 页 0 写（SC-5 快照对比）。
5. **异族审**：判定级改动提交 Codex 交叉审 APPROVED 再合 main。

---

## 候选 task packets（4 个，最终化文件待 v2 启动时由 v2-draft 派生）

### packet 1 — `v2-1-raw-gate`
- **owned_paths**: `src/akb2/quality/raw_gate.py`（新增）；接线编辑 `src/akb2/quality/runner.py`。
- **required_context**: `specs/multi-source-distill/spec.md`；`docs/constitution.md`；`src/akb2/quality/admission.py`；`src/akb2/quality/runner.py:164-244,1262-1294`。
- SC-3 → `pytest -k "raw_gate" -q`（薄但有效收 / 零贡献拒 / 机判前置拒）。

### packet 2 — `v2-2-concept-routing`
- **owned_paths**: `src/akb2/quality/concept_routing.py`（新增）；接线编辑 `src/akb2/quality/runner.py`。
- **required_context**: `specs/multi-source-distill/spec.md`；`src/akb2/quality/runner.py:394-546`；`src/akb2/quality/promote.py:842-857`。
- SC-4 → `pytest -k "fast_path_1to1" -q`（厚源 fast_1to1 不挂槽；薄源挂槽；近义新概念不开新槽）。

### packet 3 — `v2-3-nto1-synthesis`
- **owned_paths**: `src/akb2/quality/multisource_distill.py`（新增）；接线编辑 `src/akb2/quality/runner.py`。
- **required_context**: `specs/multi-source-distill/spec.md`；`docs/constitution.md`；`src/akb2/quality/distill.py:558,875-968,2076`；`src/akb2/quality/integrity_floor.py` + `grader.py:676` + `weighted_score.py:74`。
- SC-1 → `pytest -k "multisource and synthesis" -q`（单源 floor fail → N 源 F4 pass + 加权≥阈）；附 SC-2/SC-5。**blocked_by P0**。

### packet 4 — `v2-4-eval-harness`
- **owned_paths**: `evals/main-pipeline-quality/`；接线编辑 `src/akb2/quality/calibration.py`。
- **required_context**: `specs/multi-source-distill/spec.md`；`plans/gd/2026-06-28-loop1-execution-plan.md`；`src/akb2/quality/calibration.py:79,144`；`evals/main-pipeline-quality/corpus.yaml`。
- SC-1（可评测面）→ `pytest -k "calibration and multisource" -q` + calibration 报告含 N:1 段。

## 中文结论摘要
v2 的四个任务可在现有 `src/akb2/quality/` 基建上以三新模块（raw_gate / concept_routing / multisource_distill）加 runner 接线与 calibration 扩展落地：raw 门用 deepseek 按 wiki 同一套标准只判「对某概念是否≥1 合格贡献」、机判前置先挡不可用；路由复用已装载的 known_concepts 与 pending_queue 把薄源锚定到离散概念槽、embedding 仅做新概念去重召回；攒够 ≥K 个不同源后只对该概念做 scoped 多源合成并过现有 F4 加权门、厚源走 1:1 快路径、超时标源不足、全程仅 create 不碰已入库页；eval harness 把 corpus 与 calibration 从一源一页扩成多源一页可评测。整链硬阻塞于 P0 加权分修复——加权门未诚实化前合成页恒零分，故 v2-3 的实测验收必须排在 P0 出口之后；判定级改动需过 Codex 异族审再合并主干。

<!-- gd-child-plan-proposal-json:start -->
```json
{
  "proposal_id": "loop1-cq-plan-t-v2-planner",
  "parent_dispatch_id": "loop1-content-quality-plan",
  "parent_track_id": "t-v2-planner",
  "agent_role": "child_planner",
  "output_status": "completed",
  "summary_cn": "v2 四任务可在现有 quality 基建上以 raw_gate/concept_routing/multisource_distill 三新模块加 runner 接线与 calibration 扩展落地，复用 known_concepts 与 pending_queue 锚定概念槽、攒够 K 源 scoped 合成过现有 F4 加权门，全程仅 create 不碰已入库页，硬阻塞于 P0 加权分修复且判定级改动需过异族审。",
  "task_packets": [
    {"task_id": "v2-1-raw-gate", "owned_paths": ["src/akb2/quality/raw_gate.py", "src/akb2/quality/runner.py"], "required_context": ["specs/multi-source-distill/spec.md", "docs/constitution.md", "src/akb2/quality/admission.py", "src/akb2/quality/runner.py"]},
    {"task_id": "v2-2-concept-routing", "owned_paths": ["src/akb2/quality/concept_routing.py", "src/akb2/quality/runner.py"], "required_context": ["specs/multi-source-distill/spec.md", "src/akb2/quality/runner.py", "src/akb2/quality/promote.py"]},
    {"task_id": "v2-3-nto1-synthesis", "owned_paths": ["src/akb2/quality/multisource_distill.py", "src/akb2/quality/runner.py"], "required_context": ["specs/multi-source-distill/spec.md", "docs/constitution.md", "src/akb2/quality/distill.py", "src/akb2/quality/weighted_score.py"]},
    {"task_id": "v2-4-eval-harness", "owned_paths": ["evals/main-pipeline-quality/", "src/akb2/quality/calibration.py"], "required_context": ["specs/multi-source-distill/spec.md", "plans/gd/2026-06-28-loop1-execution-plan.md", "src/akb2/quality/calibration.py", "evals/main-pipeline-quality/corpus.yaml"]}
  ],
  "sc_refs": ["SC-9"],
  "verify": [
    {"sc_ref": "SC-9", "method": "command", "cmd": "pytest -k multisource -q"}
  ],
  "blocked_reason": null
}
```
<!-- gd-child-plan-proposal-json:end -->
