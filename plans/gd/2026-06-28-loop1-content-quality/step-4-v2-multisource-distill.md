# Step 4: v2 多源提炼 + 概念图谱锚定 v1（finalized）

GD_STANDARD: /Users/praise/AI-Agent/Claude/projects/Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: specs/multi-source-distill/spec.md + docs/constitution.md
TEMPLATE_KIND: gd-step-plan

日期：2026-06-28
状态：reviewed（child t-v2-planner 草稿 → 主 agent 采纳并最终化）
负责人：Claude 执行；判定级改动（raw 门收/拒、路由归组、合成过门）过 Codex 异族审再合 main

> **来源/合并**：权威细节（WHERE/WHAT/WHY/VERIFY、FR↔SC 映射、风险防护）在 `child-drafts/v2-draft.md`（child_planner `t-v2-planner` 起草、已过 `gd-validate-child-proposal.py`）。主 agent review 见 `reports/merge-report.md`：**采纳**，无冲突。本文件为最终化索引 + 启动约束。

## 1. 目标链
```text
PROJECT_GOAL: AKB2 fail-closed 门控 + 异族 Maker/Checker + 实测价值裁决（ref docs/constitution.md 原则 3/6/7/8）
CHAIN_GOAL:   循环1 按内容车道分流（ref specs/content-lane-quality/spec.md）
PHASE_GOAL:   薄但有效的源不丢——挂概念图谱攒料，够 K 源再合成一页过门 wiki，保入库率不降质量（ref specs/multi-source-distill/spec.md）
TASK_GOAL:    落地 v2-1..v2-4，满足 spec FR-1..FR-8 与 SC-1..SC-5
```

## 2. Review 对齐
- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：raw 门与 wiki 同标准差完成度（不另立松阈值）; 路由锚定离散概念身份非 embedding 相似度归组; 合成过现有 F4+grader+加权门不放宽; 仅 create 不改写已入库页; raw 门/路由/合成全 deepseek、F4+grader 全 gpt-5.4 不破异族
- Domain-specific notes：v2 强依赖 P0；判定级改动过异族审。

## 3. 前置条件
- blocked_by：**Step 1（P0），尤其 P0-1 加权分修复**。理由：加权分=0 不修，合成多好也是 0 分（spec 前置依赖）。SC-1/SC-4 的 live verify 必须在 P0 出口（离线绿 + 满仓 weighted_score 非 0 可复算）后执行。
- 软依赖：P0-4（准入归 deepseek）—— v2-1 raw 门复用同一 deepseek 路由。
- 实现期可并行：起草 + 写离线单测 fixture（mock grader 逐维 score）不必等 P0 live。

## 4. 成功标准（SC，对齐 spec SC-1..SC-5；master 聚合为 SC-9）
权威 SC 表见 `child-drafts/v2-draft.md` §"本 step Success Criteria"。摘要：
- [ ] SC-1：薄源 ×K 合成出过门（F4+grader+加权）的新页。verify `pytest -k "multisource and synthesis" -q`（live 复算在 P0 后）。
- [ ] SC-2：不相干混料因 F4 因果边不连通被拒。verify `pytest -k "multisource and incoherent" -q`。
- [ ] SC-3：raw 门不误杀薄但有效源、零贡献拒。verify `pytest -k "raw_gate" -q`。
- [ ] SC-4：单篇够厚走 1:1 快路径。verify `pytest -k "fast_path_1to1" -q`。
- [ ] SC-5：v1 全程不改写已入库 wiki 页。verify `pytest -k "multisource and no_wiki_mutation" -q`。

## 5. 非目标
- update（并入已有页+merge+重评分+版本）→ 推后；升星 → 独立未来；embedding 做最终归组 → 仅去重召回；放宽地板/F4/novelty/负样本 → 禁。

## 6. 实现步骤
4 步（A raw 门 / B 概念路由 / C N:1 合成 / D eval harness）的 WHERE/WHAT/WHY/VERIFY 见 `child-drafts/v2-draft.md` §"实现步骤"。child 已核实可复用基建：`_load_vault_concept_index`（known_concepts + pending_queue 已装载）、promote.py 已写 pending 槽、`compose_candidate` 已接 known_concepts/pending_queue、calibration.py 为 eval harness。

> **Round 1 worker 审查修正（rev-tech P1）— 接线顺序必须改**：child 草稿把 raw_gate 接在**现有 admission 之后**（v2-draft §步骤A 写 `runner.py:1262` admission 之后）。但现有 admission 在 `admission.py:183-196` 已先跑 topic/how/novelty/lane，且 `all_passed`（`:211-213`）含 novelty 与 lane——薄但有效的源会在到达 raw_gate 前就被 novelty/lane 拒，违背 v2"raw 门不判 novelty、薄源先入池"的语义。**正确做法**：raw pool 前置门必须在现有 admission **之前**（只判"可用 + 对某概念 ≥1 合格贡献"）；厚源/成页路径再跑当前 admission/novelty/质量门。或把 admission 拆为 `raw_relevance`（前置，不判 novelty/how/lane）与 `page_admission`（成页时）两函数，v2 pending 路径只走前者。v2-1 task packet 实现时按此重排，不照 child 草稿的"admission 之后"接线。

## 7. Task Packet 拆分
| task_id | agent_role | owned_paths | blocked_by | can_parallel_with |
|---------|-----------|------------|-----------|-------------------|
| v2-1-raw-gate | implementer | raw_gate.py, runner.py, **tests/quality/test_raw_gate.py** | P0 | — |
| v2-2-concept-routing | implementer | concept_routing.py, runner.py | v2-1 | — |
| v2-3-nto1-synthesis | implementer | multisource_distill.py, runner.py, **tests/quality/test_multisource_distill.py** | v2-2, P0 | — |
| v2-4-eval-harness | implementer | evals/main-pipeline-quality/, calibration.py | v2-3 | — |

> 候选 packet 定义见 `child-drafts/v2-draft.md` §"候选 task packets"。**最终化 task packet 文件待 v2 启动时（P0 出口后）派生**——本计划阶段不预先 materialize v2 packets，因 v2 实现细节会受 P0 落地结果（grader score 形态、admission deepseek 路由签名）影响，过早固化会漂移。

> **SC-9 验收承载（Round 2 worker rev-goal/rev-tech 修正）**：v2-3/v2-1 实现**必须产出可命中的测试** `tests/quality/test_multisource_distill.py`（用例含 `synthesis`/`incoherent`/`fast_path_1to1`/`no_wiki_mutation` selector）+ `tests/quality/test_raw_gate.py`，作为 SC-9a..9e 的承载（master §10 已列为 v2 deliverable + owned_path）。**已按用户决策（Option B）先建脚手架**：`tests/quality/test_multisource_distill.py`（synthesis/incoherent/fast_path_1to1/no_wiki_mutation 四用例）+ `tests/quality/test_raw_gate.py`（3 用例）已创建并 ruff 干净；现在 `pytest -k <selector>` **命中**（exit 0），因 v2 模块未建而**函数级 importorskip 诚实 skip**（"1 skipped" 非 0-selected）。v2 实现把 `akb2.quality.{multisource_distill,raw_gate}` 建好后，同一测试自动转真。skip≠pass，是先行验收契约。

## 8. 边界
修改：`src/akb2/quality/{raw_gate,concept_routing,multisource_distill}.py`（新增）、`src/akb2/quality/runner.py`（接线）、`src/akb2/quality/calibration.py`、`evals/main-pipeline-quality/`、`tests/quality/test_multisource_distill.py` + `tests/quality/test_raw_gate.py`（SC-9 验收测试，脚手架已建、v2 实现期转真；归 v2-3/v2-1 owned，与 master §10 step-4 deliverable/owned 对齐）。
不修改：已入库 `wiki/**/*.md`（SC-5）、其他 quality 模块判定逻辑（只复用调用）、`~/.claude/**`、vault .py/.sh。

## 9. 风险与防护
见 `child-drafts/v2-draft.md` §"风险与防护"（新增有状态 pending 槽 / 新概念去重防碎片 / 不混不相干双保险 / P0 阻塞 / eval 口径漂移 / 异族被破）。

## 10. 测试计划
离线单测（注入 route_llm_fn、mock 逐维 score）覆盖 SC-1..5 → ruff → calibration 离线 N:1 → live 复算（P0 出口后）→ 异族审。

## 11. Assumptions
- 已确认（child 实测）：现有基建支持 v2（known_concepts/pending_queue 已装载、compose_candidate 已接参、calibration 可扩 N:1）。
- K 值（攒几源触发）/ TTL 超时 / N:1 harness 细节在 v2 实现时定（spec 未固定）。
- v2 不在本会话执行——本 step 是 P0 之后的下一阶段计划。
