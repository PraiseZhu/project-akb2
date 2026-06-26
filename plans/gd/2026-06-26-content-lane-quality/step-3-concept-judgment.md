# Step 3: concept 判定核心（三级 how + 结构化机制字段 + 完整性地板四成员 + F3 两层 + 加权分 + 图谱 resolve + grader lane 维度集）

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md v1.2.0 + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-step-plan

日期：2026-06-26 ｜ 状态：draft（child_planner t2 起草 + 主 agent 合并；round-3 真异族 Codex 审后修订：A3-1/2/5/6/8 + Decision-1 F3 两层）

## 1. 目标链

```text
PROJECT_GOAL: 让循环1 从命令清单库变成能同时收录概念型与操作型知识的概念图谱知识库（spec: specs/content-lane-quality/spec.md）
CHAIN_GOAL:   把宪法 v1.2.0「完整性地板（硬 pass/fail）+ 车道感知加权质量分（过阈不排序）」实现为循环1 判定代码（原则 6/8）
PHASE_GOAL:   产出包 A『改标准』计划套装
TASK_GOAL:    实现 concept 判定核心——①三级 how（H1/H2/H3）抽取 + 结构化机制字段（mechanism_components/causal_edges）+ lane-aware extract 校验（concept 允许 H1=0、改判 H3≥3，真实 worker 路径生效）；②完整性地板四成员（论断锚定率/证据诚实/解读分离/F4 车道核心价值，二值硬卡不可补偿，pre-grader early-return）；③F3 两层（确定性 traces_to 锚定 + 异族 grader 推导成立硬判，拒 poisoned-trace）；④concept 加权质量分过 6.5（H3 深度独立于地板）；⑤图谱接入 A 层确定性 resolve + 生产 loader 接线；⑥grader 按 lane 选维度集（从 rubric.yaml 动态派生、不硬编码）。fixture 验收正样本过/操作不回归/负样本拒/对抗假阳性拒
```

前提依赖（blocked_by）：step-1 已把 content_lane 穿到全链各层形参（默认 procedural 兼容）+ 穿过抽取子进程边界（A3-1）+ 产出 `_load_vault_concept_index()`（known_concepts/pending_queue/wiki_references，Decision-2/A3-8 共用）；step-2 已把维度集/权重/阈值/地板（含 `evaluated_by` 标签）外置成机器可读 `rubric.yaml` + `_load_rubric_spec()` loader。本 step 在已穿好的形参上**填判定逻辑**、消费 profile，不重拉线/不重设计 profile schema。

## 2. Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：`F4 地板不可补偿性（concept H3 严格线/procedural H1≥1，二值硬卡，绝不被加权分顶过）; 完整性地板四成员齐全（论断锚定率/证据诚实/解读分离/F4 都有 A 层硬门接，不是只实现 F4——A3-2）; 结构化机制字段（F4 只消费 mechanism_components/causal_edges 结构字段，不从自然语言反解 H3 标签——A3-5）; F3 两层（层① 确定性 traces_to 锚定真实存在；层② 异族 grader 判推导成立、拒 poisoned-trace，生产模型不自证——Decision-1）; 图谱 resolve 真实性（A 层确定性查 + 生产 loader 真传 known_concepts/pending_queue，非 fixture 掩盖；纯代码经 monkeypatch-raise 证明——A3-8）; grader 维度集从 rubric.yaml 动态派生不硬编码（A3-6）`
- 重点反向风险（round-3 加固）：①地板被加权补偿（须先地板 early-return、地板 fail 加权分不算）②地板只实现 F4、锚定/诚实没硬门（A3-2）③F4 数 H3 标签糊编（须消费结构字段 A3-5）④F3 退化成只数引用不验推导（须 grader entailment 硬判 Decision-1）⑤图谱 resolve 用 LLM 猜 / 生产恒不 resolve 被 fixture 掩盖（A3-8）⑥grader 维名硬编码破坏外置（A3-6）

## 3. 前置条件

> 术语：本 step 所称「profile / rubric spec」**即 step-2 产出的单文件 `skillpack/quality/rubric/rubric.yaml`**（含 `lanes.{concept,procedural}` 的维度集+权重+阈值 + `integrity_floor` 含 `evaluated_by` 标签）+ `_load_rubric_spec()` loader；无独立 `profiles/` 目录。
- blocked_by：`step-1`（content_lane 穿全链 + 抽取子进程过界 + `_load_vault_concept_index` loader）、`step-2`（维度集/权重/阈值/地板外置进 rubric.yaml + loader）
- baseline：
  - step-1 deliverable：admission content_lane + 各层形参 + 抽取子进程携 lane（`distill_worker.py` 解析 JSON payload）+ `runner._load_vault_concept_index()`（known_concepts/pending_queue/wiki_references）
  - step-2 deliverable：`rubric.yaml`（concept/procedural 维度集+逐维权重+阈值 + integrity_floor 四成员含 evaluated_by）+ `_load_rubric_spec` loader
  - 现状锚点（round-3/round-4 实读）：distill（`_EXTRACT_SCHEMA` how_steps 仅 instruction/command/executable/anchor/provenance，**无 mechanism_components/causal_edges**，:156-181；`_validate_extract_output` :1448-1458 硬卡 executable≥3；compose 内第一道 `run_candidate_self_gate(full_page, raw_content)` 现未传 known_concepts/pending_queue）；checks_structural（`MAX_AKB_INTERPRETATION_RATIO`=0.40 :104；`check_a3_raw_anchors` :431 只验「被引用锚点存在」≠「每条来源论断都有锚」；`SG-raw-anchor` :998 只验「正文有任意 [raw §]」；`check_a4_graph_edges(text)` :470 **只收 text**；`run_structural_checks` :876 喂 a3、:878 喂 a5/known_concepts，**未喂 a4**；`run_candidate_self_gate(text, raw_content, pending_queue, known_concepts)` :979 已具形参，但 runner :672 调用时**未传** known_concepts/pending_queue）；grader（`RUBRIC_DIMENSIONS` 含 `How 密度`/`How 可执行性`，带空格 :50-61；维度集精确匹配断言）
- Hard-stop：step-1 的 content_lane 未穿到 grader/self-gate/抽取子进程，或 step-1 `_load_vault_concept_index` 不存在，或 step-2 的 `rubric.yaml`/loader 不存在 → 停止返回 blocked_missing_context（本 step 不补 step-1/2 工作）

## 4. 成功标准（SC）

- [ ] SC-3a（三级 how + 结构化机制字段 + lane-aware extract，A3-1/A3-5）：distill `_EXTRACT_SCHEMA` 加 `mechanism_components`(命名组件数组) + `causal_edges`(from→to 指向命名组件的因果边数组)；how_steps 每条带 how_level enum{H1,H2,H3}；`_validate_extract_output(data, content_lane)` 按 lane——procedural 保留 `executable≥3` extract 门（零回归），concept 改判 `len(mechanism_components)≥3` 且 concept 允许 H1==0。**测的是 lane-aware 校验真生效、不被绕过**：测 `extract_claims(raw, content_lane="concept", route_llm_fn=<fake 返 H1=0/mechanism≥3>)`（worker 子进程内调的同一可调用，注入 LLM 绕开真网关）+ `build_deterministic_extract_result(raw, raw_path, content_lane="concept")` 两条 in-process 路径均 success；子进程边界本身由 step-1 SC-8 担保（worker 把 content_lane 转给 extract_claims）。verify(test): `pytest tests/quality/test_three_level_how.py -q` → concept(content_lane="concept", H1==0, mechanism_components≥3) 两路径 extract success；procedural(content_lane="procedural") executable≥3 仍 success、executable<3 仍 fail
- [ ] SC-3b（F4 地板二值硬卡 + 不可补偿 + 消费结构字段，A3-5）：concept F4 = `len(mechanism_components)≥3` 且每个组件在 `causal_edges` 中至少有一条出边或入边指向另一命名组件（**只读结构字段、不从 candidate_text/H3 标签反解**）；procedural F4=H1≥1；地板 fail 直接拒、加权分不参与。verify(test): `pytest tests/quality/test_integrity_floor.py -k "f4" -q` → 纯名词清单（3 组件但 0 causal_edges）floor_failed；组件无出/入边 floor_failed；对抗样本（F4 fail + 辅助维满分）整体 reject 且日志含 `floor_failed` + `weighted_score_not_computed`
- [ ] SC-3h（完整性地板另两成员有 A 层硬门，A3-2）：concept 地板含 `claim_anchor_rate_floor`（每条 source_grounded claim 都带真实 anchor，覆盖率<100% 即 fail，区别于 `SG-raw-anchor` 的「有任意锚点」）+ `evidence_honesty_floor`（证据地图含「未覆盖」列且 ≥1 行标注真实盲区，裸来源断言 fail）；二值、fail-closed、不进加权池。verify(test): `pytest tests/quality/test_integrity_floor.py -k "claim_anchor_rate or evidence_honesty" -q` → 缺锚来源论断 floor_failed；证据地图无未覆盖列 floor_failed；两者 fail 时 grader_not_called + weighted_score_not_computed
- [ ] SC-3c（F3 两层：确定性溯源 + 异族 grader entailment，Decision-1）：层① distill/checks 删 concept ≤40% 比例上限，改逐条 akb_interpretation 必带 `traces_to` 指向 ≥1 条本页 source_grounded 锚定论断、且该 anchor 在 claims 真实存在（确定性 pre-grader 地板）；层② 异族 grader(GPT-5.4) 判每条 interpretation 是否真从 cited anchor 推出，**拒 poisoned-trace（anchor 真实存在但与 interpretation 无关）**——deepseek maker 不自证；procedural 保留原 ≤40% cap（不回归）。fixture 单测只覆盖 parser/early-return 回归；SC-7 pass 还必须有隔离 canary：固定 poisoned-trace prompt 走真实（或 golden-recorded）GPT-5.4，断言模型族 == `PRIMARY_GRADER_MODEL` 且 `interpretation_entailment=false`，不可用时 fail-closed/skip-not-run 不计 SC-7 pass。verify(test): `pytest tests/quality/test_interpretation_traceability.py -q` → 「>40% 解读但全溯源且 fixture entailment=true 的 concept 候选过 / 无据飘浮读后感（层①）拒 / poisoned-trace fixture entailment=false 时 early-return 拒 / procedural >40% 仍按原 cap 拒」四例全绿；verify(canary): `pytest tests/quality/test_interpretation_traceability.py -k "poisoned_trace_real_canary or entailment_model_is_gpt54" -q` → GPT-5.4 canary passed；verify(assertion): `grep -n "MAX_AKB_INTERPRETATION_RATIO" src/akb2/quality/checks_structural.py | wc -l` → ≥1（procedural cap 保留、零回归）
- [ ] SC-3d（concept 加权分过 6.5 + H3 深度独立）：读 step-2 `lanes.concept.dimensions`（恰 7 加权维，和=10：概念机制清晰度(H3)3.5/实现机制说明(H2)0.5/图谱可调用性2.0/适用边界1.5/TLDR密度1.0/Future Questions质量1.0/新颖增量0.5），过 6.5 pass；最小合规机制 H3 深度 ≤1.0；**地板维不在加权池、dim→weight 按全名 join**。verify(test): `pytest tests/quality/test_concept_weighted_score.py -q` → 权重和==10、维名⊆grader concept 维名集（无 KeyError/静默零权重）、正样本≥6.5、最小合规 H3 深度≤1.0 且总分<6.5 拒
- [ ] SC-3e（图谱接入 A 层 resolve + 生产 loader 接线 + 纯代码硬证，A3-8）：`check_a4_graph_edges` 增 known_concepts/pending_queue 形参；`run_structural_checks` 把这两参喂 check_a4；step-1 loader 产出的 known_concepts/pending_queue 必须同时传入两处 self-gate：①compose 内第一道 `run_candidate_self_gate(full_page, raw_content, known_concepts=..., pending_queue=...)`，②runner 质量段第二道 `run_candidate_self_gate(candidate_text, raw_content, known_concepts=..., pending_queue=...)`；边 resolve 到真实节点或 pending ≥3 共指才计图谱分(2.0)、假边/未 resolve ≤0.5。verify(test): `pytest tests/quality/test_graph_resolvability.py -q` → 真实节点边计分、假边≤0.5、两处 self-gate 均收非 None known/pending（分别 monkeypatch compose/runner 调用点断言）；**纯代码硬证**：monkeypatch `src.akb2.quality.llm_router.route_llm` 与一切 router 入口为 `raise AssertionError` 后 resolve 仍正确（不靠「源码不含 route_llm 字符串」弱检查），并 import allowlist 断言 `_resolve_graph_node` 模块不 import llm_router
- [ ] SC-3f（grader 按 lane 选维度集 + 动态派生不硬编码，A3-6）：`RUBRIC_DIMENSIONS_BY_LANE` **从 `_load_rubric_spec()` 动态派生**（不在 .py 手写维名）；concept grader B 层维度集 == step-2 `lanes.concept.dimensions` 的 7 个加权维全名（逐字含 `(H3)`/`(H2)` 后缀）、不含 How 两维/不含地板维；procedural 逐字等于现有 10 维。**层级澄清**：完整性地板维（锚定/诚实/F3/F4）= A 层 checks/runner 地板（二值、不进 grader B 层维度 enum、不加权）；F3 层② entailment 是 grader 端**地板字段**（非加权维）；边强度匹配 concept 下并入 A 层图谱检查（Step.5）——均**不**在 grader concept 维度 enum 里。verify(test): `pytest tests/quality/test_grader_lane_dimensions.py -q` → concept grader 维度集 == step-2 7 维全名集、不含 How/地板维；procedural 逐字；verify(assertion): `python3 -c "import ast,pathlib; names={'概念机制清晰度(H3)','实现机制说明(H2)','图谱可调用性','适用边界','TLDR密度','Future Questions质量','新颖增量'}; t=ast.parse(pathlib.Path('src/akb2/quality/grader.py').read_text()); vals={n.value for n in ast.walk(t) if isinstance(n, ast.Constant) and isinstance(n.value,str)}; assert not (names & vals), sorted(names & vals)"` → no output；verify(behavior): `pytest tests/quality/test_grader_lane_dimensions.py -k tmp_rubric_rename_changes_schema -q` → `_build_grader_schema`/parse 维度集随 tmp rubric 改名变化
- [ ] SC-3g（E2E + 对抗 + 零回归，对齐 spec SC-001~004）：concept 正样本 proposal_passed；操作样本不回归；负样本（机制糊清单/无据读后感/洗稿/缺锚/无未覆盖列/无 causal_edges/poisoned-trace）拒；对抗假阳性（地板 fail+辅助满分）拒。verify(test): `pytest tests/quality/test_concept_judgment_e2e.py -q && pytest -m 'not integration' -q` → 全绿（测试函数命名含 `concept_positive`/`negatives`/`adversarial` 子串，锚定 master `-k` 选择器防 deselect-all 假绿）

## 5. 非目标

- 不动 step-1 拉线/抽取子进程协议/loader 实现（只消费）；不动 step-2 rubric.yaml schema（只读维度集/权重/阈值/地板）；不切 grader 模型（gpt-5.4 不动）；不动 10-dimensions.md procedural 锁定文本；不碰 briefing/**；不做语料层修复（用本地 fixture）；不做升星聚合（另案 [[project_akb2_star_rating_deferred]]）
- 不在本 step 重做 novelty 接线（Decision-2 属 step-1）——本 step 的洗稿负样本（SC-3g）走 step-1 已接好的生产 novelty 路径

## 6. 实现步骤（编码顺序 t1→t7，每步独立 commit）

```text
Step.1 三级 how + 结构化机制字段 + lane-aware extract  WHERE: distill.py(_EXTRACT_SCHEMA / _HOW_PARAMS_SCHEMA / _build_extract_segment_messages / _normalize_how_step / _validate_extract_output / build_deterministic_extract_result)
  WHAT:【A3-5】_EXTRACT_SCHEMA 顶层加 `mechanism_components: [{name, role}]`（命名机制组件数组）+ `causal_edges: [{from, to, relation}]`（from/to 必须是 mechanism_components 中的 name），concept 必填、procedural minItems=0；how_steps 每条增 how_level enum{H1,H2,H3}；prompt 加 H2(数据流/状态/接口)/H3(命名机制组件+因果边) 抽取指令。【A3-1】_validate_extract_output 增 content_lane 消费：procedural 保留 `executable≥HOW_STEPS_MIN_EXECUTABLE(3)` extract 门（零回归），concept 改判 `len(mechanism_components)≥3`（允许 H1==0、不卡 executable）；build_deterministic_extract_result 同 lane-aware。**concept 抽取校验必须能在真实子进程路径过**（step-1 已让 lane 过界）
  VERIFY: pytest tests/quality/test_three_level_how.py -q → concept(H1==0,mechanism_components≥3) 经 _run_distill_segment 路径 extract success；procedural executable<3 仍 fail
Step.2 完整性地板四成员（含 A3-2 + A3-5 + early-return）  WHERE: 新增 integrity_floor.py（或并入 grader）；checks_structural.py（claim_anchor_rate_floor + evidence_honesty_floor + SG-how-density 按 lane gate_min(concept=0)）；runner.py（distill 段评估地板 + 评分段消费 FloorResult early-return）
  WHAT: evaluate_integrity_floor(extract_data, content_lane)→FloorResult{passed, failed_floors:[...], reason}，concept 评四成员（全 evaluated_by=structural 的）：①claim_anchor_rate=每条 source_grounded claim 带真实 anchor（覆盖率<100% fail，A3-2，区别于「有任意锚点」）②evidence_honesty=证据地图含「未覆盖」列且 ≥1 行标真实盲区（A3-2）③解读分离层①=每条 akb_interpretation traces_to 指向 claims 中真实存在的 source_grounded anchor（Step.3 共用）④F4 车道核心价值=len(mechanism_components)≥3 且每组件在 causal_edges 有出/入边（**只读结构字段，A3-5，不反解 H3 标签**）；procedural F4=H1≥1。**数据流穿线（A-R4 保留）**：地板在 distill 段评估（持有结构化 extract）；`_run_distill_segment` 返回追加 FloorResult（仅小对象）；pipeline 解包 → `_run_quality_segments` 增 floor_result 形参。评分段 MUST 先看 FloorResult，任一成员 fail→直接 reject、加权分函数 NOT 调用（显式 early-return + 日志 floor_failed + weighted_score_not_computed）
  VERIFY: pytest tests/quality/test_integrity_floor.py -q（runner 真实数据流、非孤立 stub）→ 机制糊清单 F4 fail；缺锚 claim_anchor_rate fail；无未覆盖列 evidence_honesty fail；对抗样本 reject + 日志 weighted_score_not_computed
Step.3 F3 两层（确定性 + 异族 grader entailment，Decision-1）  WHERE: distill.py(_validate_extract_output akb_interpretation>40% 校验 + prompt + traces_to)；checks_structural.py(MAX_AKB_INTERPRETATION_RATIO、SG-provenance-ratio)；grader.py(entailment 地板字段，见 Step.6)
  WHAT: 层①（确定性，checks/distill）：concept 车道删 ≤40% 比例上限，改 verify_interpretation_traceability——每条 akb_interpretation MUST 带 traces_to 指向 ≥1 条本页 source_grounded 锚定论断的 anchor 且该 anchor 在 claims 真实存在（判溯源标注存在）；procedural 保留原 ≤40%。层②（异族硬判，grader）：grader 输出新增二值地板字段 `interpretation_entailment`（per-interpretation 或聚合）——GPT-5.4 判每条 interpretation 是否真从 cited anchor 推出、拒 poisoned-trace（anchor 真实但与 interpretation 语义无关）；**deepseek maker 不评此项**（P7 异族）。entailment fail = 地板 fail（二值、不进加权），promote 不放行。新增 isolated canary：固定 poisoned-trace prompt 走真实（或 golden-recorded）GPT-5.4，断言模型族==PRIMARY_GRADER_MODEL 且 `interpretation_entailment=false`；fixture 单测不能替代 canary。
  VERIFY: pytest tests/quality/test_interpretation_traceability.py -q → 有据高解读过、无据读后感拒（层①）、poisoned-trace parser/early-return 回归、procedural >40% 拒；pytest tests/quality/test_interpretation_traceability.py -k "poisoned_trace_real_canary or entailment_model_is_gpt54" -q → GPT-5.4 canary passed；grep concept 路径无残留 40%
Step.4 concept 加权质量分  WHERE: 新增 weighted_score.py（或 grader 内）；消费 step-2 concept profile 权重；grader run_grader_detailed 评分聚合段
  WHAT: compute_weighted_score(grader_dimensions, content_lane, profile)→ScoreResult{score,pass}：读 profile 权重加权合成单一分，过阈(6.5)=pass；分数仅用于过阈二值裁决 NOT 排序（无 sort/rank）；H3 深度评分独立于地板，最小合规（3 组件 1 因果边）H3 深度 ≤1.0；dim→weight 按全名 join（维名⊆grader concept 维名集，缺则 KeyError 不静默零权重）
  VERIFY: pytest tests/quality/test_concept_weighted_score.py -q → 权重和==10、正样本≥6.5、最小合规 H3 深度≤1.0 且总分<6.5 拒
Step.5 图谱接入 A 层 resolve + 生产 loader 接线（A3-8）  WHERE: checks_structural.py(check_a4_graph_edges **增 known_concepts/pending_queue 形参**（现仅收 text,:470）+ run_structural_checks 调用点把这两参喂 check_a4（现仅喂 a5,:878）+ 新增 _resolve_graph_node)；distill.py(compose_candidate 内第一道 run_candidate_self_gate) + runner.py(第二道 run_candidate_self_gate 调用点 :672) 都必须接 step-1 _load_vault_concept_index() 产出的 known_concepts/pending_queue **实参**，现两处都会因传 None/缺参导致 production 边 resolve 被 fixture 掩盖
  WHAT: 合并旧⑧边强度匹配+新图谱可调用性为单一 A 层确定性检查（边强度匹配 concept 下并入此检查、不再作 grader B 层维，与 SC-3f 一致）：边 target MUST resolve（命中 known_concepts/已有 source，或 pending_queue ≥3 共指）；resolve 的边计图谱接入得分(2.0)，假边/未 resolve ≤0.5；**纯代码 NOT 调 LLM**——_resolve_graph_node 不 import llm_router。两处 self-gate 的语义必须一致：compose 第一闸先挡假边，runner 第二闸复核同一 known/pending 输入。
  VERIFY: pytest tests/quality/test_graph_resolvability.py -q → 真实节点边计分、假边≤0.5、compose 与 runner 两处 self-gate 均传实参（非 None）；monkeypatch 一切 router 入口 raise 后 resolve 仍正确 + import allowlist 断言
Step.6 grader lane 维度集（动态派生，A3-6）+ entailment 地板字段（Decision-1 层②）  WHERE: grader.py(RUBRIC_DIMENSIONS_BY_LANE、_load_rubric_text/_load_rubric_spec、_build_grader_prompt/schema enum、维度完整性断言、run_grader_detailed)
  WHAT:【A3-6】`RUBRIC_DIMENSIONS_BY_LANE` = 从 `_load_rubric_spec()` 动态派生（procedural=spec procedural 维名逐字；concept=spec `lanes.concept.dimensions` 7 维全名逐字含后缀），**不在 .py 手写维名列表**；按 lane 加载/建 schema/断言 seen_dimensions==set(by_lane[lane])。run_grader_detailed 消费 content_lane（step-1 Step.5 已加同形参→此处只消费不重加）；模型常量不动。【Decision-1 层②】grader 输出 schema 加二值地板字段 `interpretation_entailment`（evaluated_by=grader，不进加权维 enum）；prompt 注入 entailment 判定指令（判推导成立、拒 poisoned-trace）
  VERIFY: pytest tests/quality/test_grader_lane_dimensions.py -q → concept 不含 How 两维/地板维、procedural 逐字；AST 断言 grader.py 非测试/fixture 字符串常量不含 concept 7 维任一字面量；pytest tests/quality/test_grader_lane_dimensions.py -k tmp_rubric_rename_changes_schema -q → tmp rubric 改名会驱动 schema/parse 维度集变化
Step.7 E2E 收口+对抗 fixture  WHERE: 新建 tests/quality/test_concept_judgment_e2e.py + fixtures/concept/（正样本 + 负样本 mechanism-mush/ungrounded-reflection/rewrite/missing-anchor/no-uncovered-col/no-causal-edges/poisoned-trace + 对抗 adversarial-floor-edge）
  WHAT: 注入式 fixture LLM 跑 admission→compose→self-gate→grader 薄切片：concept 正样本 proposal_passed；负样本各被对应判定拦下定位（机制糊→F4 / 无据读后感→F3 层① / 缺锚→claim_anchor_rate / 无未覆盖列→evidence_honesty / 无 causal_edges→F4 结构字段 / poisoned-trace→F3 层② grader / 洗稿→step-1 已接的准入 novelty）；对抗样本（地板 fail+辅助满分）拒且日志证明未补偿；随后 pytest -m 'not integration' 全绿（procedural 零回归）。**测试函数命名含 `concept_positive`/`negatives`/`adversarial` 子串，锚定 master SC-1/SC-3/SC-4 的 -k 选择器，防 deselect-all 假绿**
  VERIFY: pytest tests/quality/test_concept_judgment_e2e.py -q && pytest -m 'not integration' -q → 全绿
```

Anti-fill：所有动作为具体函数/字段/判定改动，非「完善/优化/系统性/全面/增强」。

## 7. Execution Checkpoint：concept-judgment-core

- agent_role: implementer ｜ blocked_by: [step-1, step-2] ｜ can_parallel_with: [] ｜ execution_packet: `content-lane-quality-execution`（本节是单一 execution packet 内部 checkpoint，不单独作为 §6 task packet 调度；7 步跨文件共享 how_level/content_lane/mechanism_components/profile/floor 契约，单 packet 串行内部 checkpoint commit）
- owned_paths: `src/akb2/quality/{distill,checks_structural,grader,runner,integrity_floor,weighted_score}.py`、`tests/quality/test_{three_level_how,integrity_floor,interpretation_traceability,concept_weighted_score,graph_resolvability,grader_lane_dimensions,concept_judgment_e2e}.py`、`tests/quality/fixtures/concept/`、`docs/allowed-residuals.md`（仅为 concept 放宽 universal 门时登记）
- forbidden_paths: `src/akb2/briefing/**`、`src/akb2/quality/distill_worker.py`（checkpoint step-1 已改，step-3 只读）、`src/akb2/quality/admission.py`（checkpoint step-1 已改，step-3 只读）、`skillpack/quality/rubric/10-dimensions.md`（procedural 锁定）、`skillpack/quality/rubric/rubric.yaml`（checkpoint step-2 已改，step-3 只读消费）、旧 /rev artifacts、`/Users/praise/.claude/**`、vault 路径
- required_context: `specs/content-lane-quality/spec.md`、`docs/constitution.md`、`skillpack/quality/rubric/10-dimensions.md`、`skillpack/quality/rubric/rubric.yaml`（step-2 产出，读概念维度集/权重/阈值/地板 evaluated_by）、`src/akb2/quality/admission.py`（step-1 产出，读 content_lane 来源）、`src/akb2/quality/distill_worker.py`（step-1 产出，确认 lane 已过界）、`src/akb2/quality/source_ops_scanners/operations.py`（owned 的 distill/checks_structural/grader/runner.py 为本 packet 自有，执行时直接读改，按标准 §6 不重列入 required_context）
- deliverables: `src/akb2/quality/{distill,checks_structural,grader,runner}.py`、新建 `integrity_floor.py`+`weighted_score.py`（或并入 grader）、7 个 `tests/quality/test_*.py`、`tests/quality/fixtures/concept/`、step-3-execution-result.md
- verify: 本 step §4 SC-3a~3h 的 verify 命令（三级 how+结构字段+lane-aware extract / F4 二值不可补偿消费结构字段 / 地板另两成员硬门 / F3 两层 / concept 加权过 6.5+H3 深度独立 / 图谱 A 层 resolve+生产接线+纯代码硬证 / lane 维度集动态派生 / E2E+对抗+零回归）
- handoff: result → `plans/gd/2026-06-26-content-lane-quality/results/step-3-execution-result.md`

## 8. 边界

修改：distill.py（三级 how + mechanism_components/causal_edges 结构字段 + lane-aware _validate_extract_output + F3 层① traces_to）/ checks_structural.py（claim_anchor_rate_floor + evidence_honesty_floor + SG-how-density lane gate + SG-provenance concept 改溯源 + check_a4 图谱 resolve 增 known_concepts/pending_queue 形参）/ grader.py（RUBRIC_DIMENSIONS_BY_LANE 动态派生 + lane-aware 加载/schema/断言 + 加权聚合 + interpretation_entailment 地板字段）/ runner.py（评分段先地板 early-return + :672 self-gate 传 known_concepts/pending_queue 实参）/ 新建 integrity_floor.py + weighted_score.py（或并入 grader）/ 7 个新测试 + fixtures/concept/。
不修改：briefing/**、distill_worker.py（checkpoint step-1 已改）、admission.py（checkpoint step-1 已改）、10-dimensions.md（procedural 锁定逐字）、step-1 拉线/loader/step-2 rubric.yaml 格式与 loader（只消费）、vault、/Users/praise/.claude/**。

## 9. 风险与防护

| 风险 | 防护 |
|------|------|
| 地板被加权分补偿 | runner 强制先地板 early-return、任一地板成员 fail 加权分不调用；SC-3b 断言日志 weighted_score_not_computed |
| 地板只实现 F4、锚定/诚实无硬门（A3-2 原洞） | Step.2 显式实现 claim_anchor_rate_floor + evidence_honesty_floor，SC-3h 负样本各被对应成员拦 |
| F4 数 H3 标签糊编 3 条假机制（A3-5 原洞） | F4 只消费 mechanism_components/causal_edges 结构字段，不反解自然语言；SC-3b 无 causal_edges/组件无边 fail |
| F3 退化成只数 provenance marker 不验推导（Decision-1） | 层① 校验 traces_to 指向真实存在 anchor；层② 异族 grader 判 entailment 拒 poisoned-trace；SC-3c poisoned-trace 拒 |
| 图谱 resolve 用 LLM 猜 / 生产恒不 resolve 被 fixture 掩盖（A3-8） | check_a4 纯代码查；compose 第一闸与 runner 第二闸都真传 step-1 loader 实参；SC-3e monkeypatch router raise 后仍正确 + import allowlist + 两处 self-gate 传实参断言 |
| grader 维名硬编码破坏外置（A3-6） | RUBRIC_DIMENSIONS_BY_LANE 从 _load_rubric_spec 派生；SC-3f 用 AST 字符串常量负断言 + tmp rubric 改名行为测试，防常量键硬编码漏检 |
| 对抗踩线（最小合规靠辅助分过 6.5） | H3 深度独立于地板、最小合规 ≤1.0；SC-3d 断言总分<6.5 拒 |
| concept 退化成全绿后门 | universal 门全保留（source_boundary/conclusion must-cover、raw-anchor、command-recheck、F3 两层、F4 + 锚定/诚实地板）；SC-3g 多负+对抗拒 |
| procedural 被 lane 改动破坏 | 所有 lane 形参默认 procedural；_validate_extract_output procedural 保留 executable≥3；SC-3f 维度集逐字 + SC-3g golden_replay/negative 零回归 |
| 三级 how 把 concept 机制误标 H1 致地板误判 | how_level prompt 明确 H1/H2/H3 边界 + normalize 兜底（concept 无 command 的 how 不标 H1）；SC-3a 断言 concept H1==0 |

## 10. Assumptions

- step-1 已穿 content_lane（默认 procedural 兼容）+ 过抽取子进程边界 + 产出 `_load_vault_concept_index`；step-2 已外置 profile + loader（SC-005 由 step-2 担保，本 step 读 profile 值不硬编码权重/维名）；concept 正样本沿用既有黄金样张锚定（首篇通过的 concept 页作后续修复爬坡标杆）；E2E 用注入式 fixture LLM 不依赖真网关/抓取；integrity_floor/weighted_score 是否独立成文件按 import 环依赖定（不影响判定语义）；mechanism_components/causal_edges 是 F4 的结构真相源（A3-5），F4 不从自然语言反解；F3 层② entailment 由 grader(GPT-5.4) 异族硬判（Decision-1，P7），maker 不自证；权重数值/阈值以 step-2 profile 为外置真相源，测试读 profile 不硬编码。
