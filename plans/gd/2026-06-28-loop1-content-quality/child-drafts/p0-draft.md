```yaml
template_kind: gd-child-plan-output
parent_dispatch_id: loop1-content-quality-plan
parent_track_id: t-p0-planner
agent_role: child_planner
output_status: completed
```

# GD Step Plan 草稿 — AKB2 循环1 P0「当前管线诚实化 + 补观测」

> 本文件是 child_planner `t-p0-planner` 的原始草稿（PLAN 阶段 deliverable，主 agent 已 review 并采纳，见 ../reports/merge-report.md）。最终化 step plan 见 ../step-1-p0-pipeline-honesty.md。

## 目标链（不可改写）
- PROJECT_GOAL: AKB2 无人值守自动化知识库工程：fail-closed 门控 + 异族 Maker/Checker + 实测价值裁决（宪法原则 3/6/7/8）
- CHAIN_GOAL: 循环1 按内容车道分流质量标准，让概念型与操作型知识在不破完整性地板前提下都能入库（specs/content-lane-quality/spec.md）
- PHASE_GOAL: P0 当前管线诚实化 + 补观测（地基，无悬念）
- TASK_GOAL: 把 P0-1..P0-6 落成自包含 step plan + 6 个候选 task packet

## Review 对齐
- REVIEW_DOMAIN = ai_infra
- 每个判定级改动（P0-1/P0-3/P0-4）交付前过 Codex 异族审（宪法原则 7：判定级改动改完过异族审再合 main）。
- 逐条核对宪法：原则 3（fail-closed 贯穿七段）、原则 6（量规输出可定位缺陷清单 + 加权分并存不取代）、原则 7（Maker/Checker 异族；准入判源不判产物，砍 gemini-flash 归 deepseek 不破异族）、原则 8（完整性地板硬判不可补偿 + 加权分仅过阈不排序；权重/阈值落 rubric.yaml）、原则 4（预算显式/失败可见，补观测不静默吞）、变更纪律 1（循环/门控/入库逻辑改动 MUST 带测试）。

## 前置条件
- blocked_by: 无
- baseline: HEAD = 5dde324（分支 `integration/loop1-content-quality`），离线 1633 绿（`pytest -m 'not integration'`，pyenv 3.13.12）。
- 环境复核已做：rubric.yaml 现 `rubric_version: "1.1.0"`、concept/procedural/top `threshold` 均为 6.5；grader schema 的 `score` 字段当前为**可选**（不在 `required`）且无 0~1 尺度约束；admission 四处判定均调 `gemini-flash`；repair 已写逐轮 trace（`_write_repair_trace`）；runner 准入 ledger 现记 `content_lane` 但**不记 `lane_reason`**。
- 边界确认：`compose.py` 不存在——组装逻辑在 `src/akb2/quality/distill.py`（`compose_candidate`），P0-2 的 owned_path 修正为 distill.py（不是 compose.py）。

## 本 step Success Criteria（每条绑 verify）

| SC | 断言 | method | cmd | expect |
|----|------|--------|-----|--------|
| SC-1 | concept grader 每维必带 0~1 score；缺 score → fail-closed（不静默当 0 过）；concept threshold=6.0；黄金样张 #4 复算加权分≥6.0 不再 weighted_score_below_threshold | command | `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_weighted_score.py tests/quality/test_grader_score_required.py` | passed（含「缺 score→fail-closed」「6.0 阈值」断言） |
| SC-2 | 含 source_operation 的 raw → distill compose 后该字面量字面渲染进「### 操作步骤」节、SG-must-cover 不报；#0/2/8/17 重跑不卡 must-cover | command | `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_distill_must_cover.py` | passed |
| SC-3 | 内联反引号「命令形态」判定：单字中文/含`|`表格碎片/纯术语不判 fabrication、真编造命令（pip install evil）仍报；#16 不卡 | command | `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/test_raw_verify_command_shape.py` | passed |
| SC-4 | admission topic/how/novelty/lane 调用切 deepseek；不破异族（准入判源不判产物） | command | `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/test_admission_lane.py tests/quality/test_admission_positive.py tests/quality/test_admission_negative.py` | passed |
| SC-5 | repair 逐轮 final_defects 落可读位置 + admission lane_reason 落 ledger（pass/reject 均可见）；判定行为不变 | command | `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_repair_loop.py tests/test_admission_lane.py` | passed |
| SC-6 | active e2e 满仓重跑完成：pass_rate 从 0 动起、负样本 100% 拒、黄金样张过、产物落 reports/calibration/factory.json | path | `test -f reports/calibration/factory.json && echo PASS` | PASS（且 factory.json 内 pass_rate>0、负样本全 reject） |

全局出口闸（每个 implementer 收尾必跑）：`PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -m 'not integration' -q` → 全绿 + `ruff check src/akb2/quality`。

## 实现步骤（WHERE / WHAT / WHY / VERIFY）

### 步骤 1 — P0-1 加权分必填 score + 0~1 尺度钉 + 阈值 6.0
- WHERE:
  - `src/akb2/quality/grader.py::_build_grader_schema`（行 307-401）——`score` 现是可选 number、无范围；`dimensions.items.required`（行 360）只含 `dimension/pass/defects`。
  - `src/akb2/quality/grader.py::_build_grader_prompt` 内 `_build_grader_weight_floor_note`（行 195-230，第 229 行「可选：每个维度可附 score 数值」）+ system_prompt（行 266-295）。
  - `src/akb2/quality/grader.py::_parse_grader_output`（行 446-543，`_dim_allowed` 行 486）与 `_parse_grader_extensions`（行 590-658，dim_scores 收集行 630-635）。
  - `src/akb2/quality/weighted_score.py::compute_weighted_score`（行 74-126；concept 缺维 fail-closed 已在行 105-118，但**缺 score**未 fail-closed）。
  - `skillpack/quality/rubric/rubric.yaml`：`lanes.concept.threshold`、`lanes.procedural.threshold`、顶层 `threshold`、`rubric_version`；`src/akb2/quality/weighted_score.py::DEFAULT_THRESHOLD`（行 18）。
- WHAT:
  1. concept 车道 grader schema 把 `score` 设为 `required`（与 `dimension/pass/defects` 并列），并加 `minimum:0, maximum:1`（procedural 保持可选零回归，或全车道 0~1 但仅 concept required）。
  2. prompt 明确要求「每维必出 0~1 的 score（0=完全不达该维、1=完全达标），不是 0~10」；`_dim_allowed` 仍含 score。
  3. `_parse_grader_output` 对 concept：解析每维必须有合法 0~1 score，缺失/越界 → 整批 parse 失败（走重试再 fail-closed），杜绝静默当 0。
  4. `_parse_grader_extensions`：dim_scores 收集时校验 0~1 范围。
  5. `compute_weighted_score`：concept 分支在缺维 fail-closed 之上，补「present 维但 score 缺/非数值」→ fail-closed（与现行缺维同口径 return score=0/passed=False）。
  6. rubric.yaml：concept `threshold` 6.5→6.0；同步 `weighted_score.py::DEFAULT_THRESHOLD` 兜底值（仅兜底，真相以 yaml 为准）；按 spec FR-010 不在 .py 写死数值，故只改 yaml threshold + bump `rubric_version`（同步 grader 的版本断言会触发 `_load_rubric_text` 版本校验，需一并更新 10-dimensions.md 头「版本: X.Y.Z」否则离线红）。
- WHY: 现状 grader 不吐逐维 score → `weighted_total=None` → concept 加权分恒为 0 → 黄金样张 #4 死于 weighted_score_below_threshold（handoff §3 实锤 bug①）。score 不钉 0~1，模型可能吐 0~10 让 6.0 阈值形同虚设=误放（核心决策 2）。缺 score 静默当 0 违反原则 3/8 fail-closed。
- VERIFY: SC-1 命令；并新增离线锁「concept 缺 score → fail-closed」「score 越界（>1）→ fail-closed」「rubric threshold=6.0 生效」。

### 步骤 2 — P0-2 must-cover 字面量渲染修（救 #0/2/8/17）
- WHERE:
  - 校验侧：`src/akb2/quality/checks_structural.py::run_candidate_self_gate` 的 SG-must-cover 块（行 1273-1331），`source_operation` 映射到 `how_text = _how_section_text(text)`（行 1059-1063：截 `### 操作步骤` 之后文本）。
  - 渲染侧（真因）：`src/akb2/quality/distill.py::compose_candidate` 与 How 节渲染（`_provenance_split_summary_comment` 行 928 起、how_steps 渲染、`must_cover_operation_provenance_split` 行 901）——must_cover 的 `source_operation` 字面量（如 `1. Pick the next…`）当前没被字面写进「### 操作步骤」节。
- WHAT: 在 distill compose 渲染「### 操作步骤」时，确保 `_scan_source_evidence` 产出的 `must_cover_class=="source_operation"` 字面量被字面渲染进该节（与校验侧 `_normalize_must_cover_text` 归一口径一致——锚点/markdown/编号前缀容差，但实质内容必须在）。优先复用既有 how_steps/must_cover 渲染零件，不新造（变更纪律 2）。不收窄 operations.scan（护栏）。
- WHY: handoff §3 bug② + 执行计划 P0-2：source_operation 字面量没进操作步骤节 → SG-must-cover 报「字面量未在对应节渲染」→ #0/2/8/17 卡 must-cover。
- VERIFY: SC-2 命令；新增/扩 `test_distill_must_cover.py`：构造含 source_operation 的 raw → compose 后断言字面量在「### 操作步骤」节 + SG-must-cover 无 Defect。

### 步骤 3 — P0-3 #16 命令形态判定补全
- WHERE: `src/akb2/quality/source_ops_scanners/raw_verify.py`：`_is_prose_or_reference_literal`（行 130-147，现仅排除「编号散文 + repo slug」）、内联 recheck 循环（行 554-578）、`_classify_command_violation`（行 464-501）。已有测试 `tests/test_raw_verify_command_shape.py`。
- WHAT: 把内联反引号回查升级成「命令形态」判定——在 fabrication recheck（`literal not in raw_text`）之前，扩展「非命令形态」豁免：(a) 单字/短词中文（无 shell 关键字、纯术语如 `和`）；(b) 含 `|` 的 markdown 表格碎片（如 `Conv | …`）；(c) 纯术语/概念名（无 CLI 动词、无 `$`、无路径/参数形态）。判据可复用 admission.py 的 `_NUMBERED_WITH_CMD_RE` CLI 动词白名单思路（npm/pip/python/git/docker/...）做正向「真命令」识别：命中真命令形态才回查，否则豁免。危险黑名单（`_is_dangerous_command`）对豁免项仍跑（保持双重拦截，不破 SC-4）。
- WHY: handoff §2/§3 bug③ + 执行计划 P0-3：#16 死于 `和`/表格碎片/术语被当命令回查报 fabrication。R1 已修「编号散文 + repo slug」，但单字中文/表格碎片/纯术语仍漏。护栏：真编造命令（pip install evil）仍须报。
- VERIFY: SC-3 命令；扩 `test_raw_verify_command_shape.py`：单字中文 `和`、表格碎片 `Conv |`、纯术语不判 fabrication；`pip install evil`/危险命令仍报；fenced ```bash 块不破。

### 步骤 4 — P0-4 砍 gemini-flash，准入归 deepseek
- WHERE: `src/akb2/quality/admission.py` 四处 `route_llm_fn("gemini-flash", ...)`：`_check_topic_relevance`（行 413）、`_check_how_tendency`（行 465）、`_check_novelty`（行 528）、`_classify_content_lane`（行 667）。
- WHAT: 把这四处的 model_family 从 `"gemini-flash"` 切到 `"deepseek"`（用既有 llm_router 的 deepseek family id，调用前确认 llm_router 支持该 family）。提取为一个具名常量（如 `ADMISSION_MODEL = "deepseek-..."`）避免四处魔数（变更纪律 2/原则 4 显式）。不改判定逻辑/schema。
- WHY: 核心决策 4 + 执行计划 P0-4：gemini-flash 做准入是 2026-06-13 旧代码漂移，误杀官方文档（#12 crewai/#13 sub-agents）。准入判「源」不判「产物」，故 maker(deepseek 提炼) 与准入(deepseek 判源) 不构成自评自批——异族分离针对的是「产物的生产者 vs 裁决者」（grader=gpt-5.4 不变），准入是入口过滤不裁决产物质量，回到 maker=deepseek/checker=gpt-5.4 两族不破原则 7。需在交付说明显式论证此点并过 Codex 异族审。
- VERIFY: SC-4 命令（admission 测试全绿）；满仓（SC-6）后 factory/ledger 的 admission 段 model 含 deepseek。

### 步骤 5 — P0-5 补观测：repair trace + admission lane_reason（不改判定）
- WHERE:
  - repair：`src/akb2/quality/repair.py::run_repair_loop`（行 1445 起；逐轮 `trace_payload` 已写 `b_defects_after`/`status`/`a_defects_after`，`_write_repair_trace` 行 1232，返回 `trace_paths`）；`final_defects` 在返回 dict（行 1775/1831）。
  - runner：`src/akb2/quality/runner.py` 准入段 `_log_event(config,"admission",...)`（行 1293-1294 pass 分支、行 1275-1276 abort 分支）现仅写 `content_lane`，**不写 `lane_reason`**；`repair_trace_paths` 已落 ledger（行 1145/1425-1427/1445-1446）。
  - admission：`src/akb2/quality/admission.py::run_admission`（行 196 拿到 `lane_reason`，但 pass 时丢弃；只在 `_log_rejection` 行 750-802 写 reject lane_reason）。
- WHAT:
  1. admission：`AdmissionResult` 增 `lane_reason` 字段（或在 pass 路径把 lane_reason 透出），让 runner 能在「准入 pass」时也拿到。
  2. runner：准入 pass/abort 两处 `_log_event` 的 details 补 `lane_reason`，使每篇 ledger 可读 lane_reason（不止 reject）。
  3. repair：确保逐轮 final_defects/缺陷演化落到可读位置——现 trace 已逐轮写 `b_defects_after`，补「逐轮 final_defects 汇总」可读性（如 trace_payload 增 round-level final_defects 快照 or runner 把 repair_trace_paths + 逐轮缺陷数汇总进 ledger details）。仅加观测字段，判定分支/收敛条件零改动。
- WHY: 执行计划 P0-5 + handoff §6：P1 语义审计 12 篇需逐篇可读 repair 缺陷演化 + lane_reason（gap1「光看 ledger 判不了」）。原则 4 失败可见。必须「判定行为不变（离线绿）」。
- VERIFY: SC-5 命令；新增断言：admission pass 时 ledger details 含 lane_reason；repair 逐轮 trace 可读 final_defects 演化；既有判定测试不回归。

### 步骤 6 — P0-6 active e2e + 满仓重跑
- WHERE: 运行态，无源码改动。`src/akb2/quality/runner.py` 行 1845（`AKB2_AUTO_PROMOTE` 读取）；满仓入口 `python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json`（handoff §1）。owned_path 为产物目录 `reports/calibration/`。
- WHAT: 步骤 1-5 全绿 + ruff 后，设 4 个 env（`AI_GATEWAY_BASE_URL`/`AI_GATEWAY_API_KEY`/`OPENAI_MODEL_ID=codex/gpt-5.4`/`PYTHONPATH=src`）+ `AKB2_AUTO_PROMOTE=1` 真入库，串行跑满仓（~50min），产物落 `reports/calibration/factory.json`。读结果：pass_rate、per_segment、负样本拒、样张过。
- WHY: 核心决策 3 + 执行计划 P0-6：验证 concept 通路修通（#4 极可能转过）、pass_rate 从 0 动起、负样本 100% 拒。这是 P0 出口、P1 的输入基线。
- WHY-NOTE: 此步依赖 1-5 全部合入，必须最后执行；env key 不打印（安全约束①②），不用 `git --no-verify`。
- VERIFY: SC-6 path 命令；人工读 factory.json：pass_rate>0、负样本全 reject、黄金样张 pass。

## 非目标（明确不做）
- 不做 P1 语义审计 12 篇定性（救/调/拒）——P0 重跑+trace 之后才拍。
- 不做 P2 repair 机制改造（location 容差/A-B 拉扯）、不调 rubric 维度权重数值（仅 P0-1 改 threshold 6.5→6.0，不动 concept 维权重——那是 P2-b 校准）。
- 不做 v2 多源提炼、update、升星。
- 不放宽完整性地板/F4/novelty/负样本判定（护栏）。

## 边界（owned/forbidden）
- 允许修改：`src/akb2/quality/{grader,weighted_score,distill,checks_structural,admission,repair,runner}.py`、`src/akb2/quality/source_ops_scanners/raw_verify.py`、`skillpack/quality/rubric/{rubric.yaml,10-dimensions.md}`、`tests/**`、产物 `reports/calibration/`。
- 禁止触碰：`~/.claude/`、vault（`Praise Space/AKB2.0/`）任何 .py/.sh（原则 5 工程-vault 解耦）、operations.scan 收窄、judge 判定级护栏放宽。

## 风险与防护
- R1（异族误判）：P0-4 把准入切 deepseek 可能被误读为破异族 → 防护：交付文档显式论证「准入判源不判产物，grader 仍 gpt-5.4」，过 Codex 异族审再合。
- R2（rubric 版本断言连锁红）：bump `rubric_version` 会触发 `_load_rubric_text` 版本校验 → 防护：同步改 10-dimensions.md 头部「版本:」并先跑离线全量。
- R3（P0-1 score required 破 procedural 零回归）：防护：required/0~1 只对 concept 车道收紧，procedural 保持可选。
- R4（P0-3 命令形态豁免误放真命令）：防护：豁免基于「正向真命令识别 + 危险黑名单仍跑」，测试锁 `pip install evil` 仍报。
- R5（满仓 ~50min 串行 + 网关 key）：防护：key 走环境变量、网关优先官方 key、不打印 key；满仓只在 1-5 全绿后跑一次。
- R6（顺序依赖）：P0-6 必须在 P0-1..P0-5 合入后；P0-1/P0-2 同改 grader/distill+rubric 有相邻面，建议串行或明确 owned_paths 分离避免 patch 冲突。

## 测试计划（可执行）
1. 单任务回归（各 implementer 收尾）：见各 SC cmd。
2. 全量离线门（出口闸）：`PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -m 'not integration' -q` → 期望 ≥1633 passed。
3. lint：`ruff check src/akb2/quality src/akb2/quality/source_ops_scanners`。
4. 满仓 active e2e（P0-6）：设 5 env（含 AKB2_AUTO_PROMOTE=1）跑 calibration，读 factory.json。

---

# 6 个候选 Task Packet（最终化文件见 ../task-packets/p0-*.md）

### p0-1-weighted-score
- owned_paths: `src/akb2/quality/grader.py`, `src/akb2/quality/weighted_score.py`, `skillpack/quality/rubric/rubric.yaml`, `skillpack/quality/rubric/10-dimensions.md`, `tests/quality/test_concept_weighted_score.py`, `tests/quality/test_grader_score_required.py`
- required_context: `plans/gd/2026-06-28-loop1-execution-plan.md`, `docs/constitution.md`, `specs/content-lane-quality/spec.md`
- SC-1 verify: `pytest -q tests/quality/test_concept_weighted_score.py tests/quality/test_grader_score_required.py`

### p0-2-must-cover
- owned_paths: `src/akb2/quality/distill.py`, `tests/quality/test_distill_must_cover.py`（真实文件 distill.py 非 compose.py）
- SC-2 verify: `pytest -q tests/quality/test_distill_must_cover.py`

### p0-3-command-form
- owned_paths: `src/akb2/quality/source_ops_scanners/raw_verify.py`, `tests/test_raw_verify_command_shape.py`
- SC-3 verify: `pytest -q tests/test_raw_verify_command_shape.py`

### p0-4-admission-deepseek
- owned_paths: `src/akb2/quality/admission.py`, `tests/test_admission_lane.py`
- SC-4 verify: `pytest -q tests/test_admission_lane.py tests/quality/test_admission_positive.py tests/quality/test_admission_negative.py`

### p0-5-observability
- owned_paths: `src/akb2/quality/repair.py`, `src/akb2/quality/runner.py`, `src/akb2/quality/admission.py`, `tests/quality/test_repair_loop.py`, `tests/test_admission_lane.py`
- SC-5 verify: `pytest -q tests/quality/test_repair_loop.py tests/test_admission_lane.py`
- note: 与 p0-4 共改 admission.py，p0-4 先合再做 p0-5 的 admission 字段。

### p0-6-active-rerun
- owned_paths: `reports/calibration/`
- SC-6 verify: `test -f reports/calibration/factory.json && echo PASS`
- note: 依赖 p0-1..p0-5 全合入；key 走 env 不打印；不用 git --no-verify。

---

## 中文结论摘要

P0 六任务全部定位到精确代码位（grader.py 评分 score 字段当前可选且无 0~1 尺度是 concept 加权分恒为 0 的真因；must-cover 渲染真因在 distill.py 的 compose 而非不存在的 compose.py；命令形态豁免补在 raw_verify.py 内联回查；准入四处 gemini-flash 调用集中在 admission.py；lane_reason 在 admission pass 路径被丢弃、runner 准入 ledger 只记 content_lane），baseline HEAD=5dde324 离线 1633 绿，已落成一份自包含 step plan + 六个边界清晰的候选 task packet，每条 SC 绑可执行 verify，判定级改动（P0-1/3/4）标注须过 Codex 异族审，满仓 active 重跑作为 P0 出口闸最后执行。

<!-- gd-child-plan-proposal-json:start -->
```json
{
  "proposal_id": "loop1-cq-plan-t-p0-planner",
  "parent_dispatch_id": "loop1-content-quality-plan",
  "parent_track_id": "t-p0-planner",
  "agent_role": "child_planner",
  "output_status": "completed",
  "summary_cn": "P0 六任务全部定位到精确代码位并落成自包含 step plan 加六个候选 task packet，每条 SC 绑可执行 verify，判定级改动标注须过 Codex 异族审，满仓 active 重跑作为出口闸最后执行。",
  "task_packets": [
    {"task_id": "p0-1-weighted-score", "owned_paths": ["src/akb2/quality/grader.py", "src/akb2/quality/weighted_score.py", "skillpack/quality/rubric/rubric.yaml", "skillpack/quality/rubric/10-dimensions.md", "tests/quality/test_concept_weighted_score.py"], "required_context": ["plans/gd/2026-06-28-loop1-execution-plan.md", "docs/constitution.md", "specs/content-lane-quality/spec.md"]},
    {"task_id": "p0-2-must-cover", "owned_paths": ["src/akb2/quality/distill.py", "tests/quality/test_distill_must_cover.py"], "required_context": ["plans/gd/2026-06-28-loop1-execution-plan.md", "specs/content-lane-quality/spec.md"]},
    {"task_id": "p0-3-command-form", "owned_paths": ["src/akb2/quality/source_ops_scanners/raw_verify.py", "tests/test_raw_verify_command_shape.py"], "required_context": ["plans/gd/2026-06-28-loop1-execution-plan.md", "docs/constitution.md"]},
    {"task_id": "p0-4-admission-deepseek", "owned_paths": ["src/akb2/quality/admission.py", "tests/test_admission_lane.py"], "required_context": ["plans/gd/2026-06-28-loop1-execution-plan.md", "docs/constitution.md"]},
    {"task_id": "p0-5-observability", "owned_paths": ["src/akb2/quality/repair.py", "src/akb2/quality/runner.py", "src/akb2/quality/admission.py", "tests/quality/test_repair_loop.py"], "required_context": ["plans/gd/2026-06-28-loop1-execution-plan.md"]},
    {"task_id": "p0-6-active-rerun", "owned_paths": ["reports/calibration/"], "required_context": ["plans/gd/2026-06-28-loop1-execution-plan.md", "plans/gd/2026-06-28-loop1-handoff.md"]}
  ],
  "sc_refs": ["SC-1", "SC-2", "SC-3", "SC-4", "SC-5", "SC-6"],
  "verify": [
    {"sc_ref": "SC-1", "method": "command", "cmd": "python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_weighted_score.py"},
    {"sc_ref": "SC-2", "method": "command", "cmd": "python3 -m pytest -p no:cacheprovider -q tests/quality/test_distill_must_cover.py"},
    {"sc_ref": "SC-3", "method": "command", "cmd": "python3 -m pytest -p no:cacheprovider -q tests/test_raw_verify_command_shape.py"},
    {"sc_ref": "SC-4", "method": "command", "cmd": "python3 -m pytest -p no:cacheprovider -q tests/test_admission_lane.py tests/quality/test_admission_positive.py tests/quality/test_admission_negative.py"},
    {"sc_ref": "SC-5", "method": "command", "cmd": "python3 -m pytest -p no:cacheprovider -q tests/quality/test_repair_loop.py tests/test_admission_lane.py"},
    {"sc_ref": "SC-6", "method": "path", "cmd": "test -f reports/calibration/factory.json && echo PASS"}
  ],
  "blocked_reason": null
}
```
<!-- gd-child-plan-proposal-json:end -->
