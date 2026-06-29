VERDICT: REQUIRES_CHANGES

覆盖维度：技术正确性；计划 claim 与当前代码现实一致性；修法是否会破 fail-closed / 异族 / 完整性地板不可补偿；测试与验证命令是否真实可执行。

## claim 核验表

| # | 计划 claim | 核验状态 | 证据 | 结论 |
|---|---|---|---|---|
| 1 | grader 逐维 `score` 当前可选、无 0~1 约束，是 concept 加权分恒 0 的真因 | 部分证实 | `src/akb2/quality/grader.py:229` prompt 写“可选”；`grader.py:332-336` schema 只声明 number、无 minimum/maximum；`grader.py:360` required 不含 `score`；`grader.py:484-486` parse 允许但不要求 `score`；`grader.py:630-642` 只收集存在的 score。`reports/calibration/factory.json` #4 为 `weighted_score_below_threshold`，score=0.0。 | 真因应表述为“grader schema/prompt/parse 没强制产出逐维 score，导致 runner 用空/不完整 `dimension_scores` 进入 concept 加权门并 fail-closed 为 0”。不是“缺 score 静默通过”。 |
| 2 | `compose.py` 不存在，must-cover 渲染在 `distill.py::compose_candidate` | 证实 | `find src/akb2/quality -name compose.py` 无结果；`distill.py:2076-2086` 为 `compose_candidate`；`distill.py:2160-2174` 扫描并传入 must_cover；`checks_structural.py:1290-1325` SG-must-cover 消费对应节。 | 文件定位修正正确。 |
| 2b | 当前 source_operation 字面量没有渲染进 “### 操作步骤” | 证伪/过时 | `distill.py:3726-3749` 已有 `_render_must_cover_operations`；`distill.py:4048-4061` 将 `source_operation` 直出到 How；`pytest -q tests/quality/test_distill_must_cover.py` = 4 passed。 | P0-2 不应再按“当前缺渲染”设计盲改；应改成“保留现有直出逻辑 + 用满仓样本复验 #0/#2/#8/#17 是否仍卡 must-cover”。 |
| 3 | admission 有 4 处 `gemini-flash` 调用（topic/how/novelty/lane） | 证实 | `src/akb2/quality/admission.py:414`, `:466`, `:529`, `:668`；`rg -n "gemini-flash" src/akb2/quality/admission.py` 仅这 4 处调用。 | 数量与位置正确。 |
| 4 | `weighted_score.compute_weighted_score` 缺维 fail-closed，但缺 score 未 fail-closed | 部分证伪 | `weighted_score.py:108-118` concept 缺维直接返回 score=0/passed=False；`weighted_score.py:96-103` 对存在维度的 `sc` 只做 `isinstance`，非数值当 0，数值越界未拒。 | “缺 score”若表现为缺 key，当前已经 fail-closed；真正缺口是 grader 不强制 score，以及 `compute_weighted_score` 未拒绝非数值/越界 score。 |
| 5 | raw_verify R1 只修“编号散文 + repo slug”，单字中文/表格碎片/术语仍漏 | 证实 | `raw_verify.py:130-147` 只豁免 numbered prose 与 repo slug；`tests/test_raw_verify_command_shape.py:105-115` 仅覆盖该谓词边界。函数探针显示 `和`、`Conv \| ReLU`、`GraphRAG` 都被报“原文不存在此命令字面量”。 | 计划对现状判断正确；P0-3 需要新增命令形态判定和对应测试。 |
| 6 | P0-1 修法：score required + 0~1 + 缺/越界 fail-closed | 部分成立 | schema/parse/prompt 收紧方向正确；宪法原则 8 要求地板不可补偿，runner 已在 `runner.py:987-1016` 地板 fail early-return。 | 必须修正子草稿里“同步 `DEFAULT_THRESHOLD`/top/procedural threshold”的指令；concept 阈值应只改 `lanes.concept.threshold=6.0`。 |
| 6 | P0-2 修法：must-cover 渲染 | 部分成立 | 渲染路径已存在且测试绿；当前计划仍把它当缺失 bug。 | 改为复验/补满仓回归；避免重复改动绿色路径。 |
| 6 | P0-3 修法：命令形态判定 | 成立但需加边界 | 当前探针证实误报；危险命令层在 `raw_verify.py:475-481` 先跑。 | 豁免必须基于“非命令形态”，不能只靠少量 CLI 白名单；保留 `deploy-preview --target staging` 这类带参数工具命令回查。 |
| 6 | P0-4 修法：admission 切 deepseek | 需修改 | `llm_router.py:61-69` 支持的 family 是 `deepseek-v4-pro`，没有 `"deepseek"`；`llm_router.py:164-168` 未知 family 会抛 `RouterConfigError`。 | task packet 必须写死或定义 `ADMISSION_MODEL = "deepseek-v4-pro"`，或者先加 alias；“deepseek”字面实现会使准入 fail-closed。 |
| 6 | P0-5 修法：repair trace + admission lane_reason | 成立 | `admission.py:196` 拿到 `lane_reason`，但 `AdmissionResult` 只有 `content_lane`（`:125-140`），pass 返回丢弃（`:235-241`）；runner pass ledger 只写 `content_lane`（`runner.py:1293-1294`）。repair trace 已写每轮 `input_defects/a_defects_after/b_defects_after/status`（`repair.py:1527-1539`, `:1562`, `:1603`, `:1714`, `:1766`）。 | 方向正确；需明确 pass/abort 两条 runner ledger 都写 `lane_reason`，repair 只补汇总可读性，不改收敛条件。 |
| 6 | P0-6 active e2e | 部分成立 | runner `auto_promote` 取 `AKB2_AUTO_PROMOTE`（`runner.py:1845-1858`）；calibration 报告的 `promotion_mode` 还受 `AKB2_AMENDMENT_APPLIED` 影响（`calibration.py:81-89`）。 | 真入库可由 runner env 触发，但报告验收若要求 `promotion_mode=active`，计划需显式设置/解释 `AKB2_AMENDMENT_APPLIED=1`。 |
| 6 | v2 raw gate after admission | 证伪修法正确性 | v2 草稿把 raw_gate 接在 admission 后（`child-drafts/v2-draft.md:45`），但当前 admission 先跑 topic/how/novelty/lane（`admission.py:183-196`），且 `all_passed` 含 novelty 与 lane（`:211-213`）。 | 这不能满足 v2 “raw 门不判 novelty、薄但有效先入池”的语义；需要重排/拆分准入。 |
| 7 | 计划 SC 引用的测试文件真实存在或合理新增 | 部分 | `rg --files tests` 证实存在：`tests/quality/test_concept_weighted_score.py`, `tests/quality/test_distill_must_cover.py`, `tests/test_raw_verify_command_shape.py`, `tests/test_admission_lane.py`, `tests/quality/test_admission_positive.py`, `tests/quality/test_admission_negative.py`, `tests/quality/test_repair_loop.py`。`tests/quality/test_grader_score_required.py` 不存在，`pytest --collect-only` 对该路径报 file not found。 | `test_grader_score_required.py` 是合理新增，但当前不能作为已存在验证；计划应显式标注新增测试。 |

## findings

### P1 — P0-4 的 deepseek family 名称不精确，按字面实现会让 admission 全部 fail-closed

- 精确位置：`plans/gd/2026-06-28-loop1-content-quality/master-plan.md:41`、`task-packets/p0-4-admission-deepseek.md:47`；代码事实为 `src/akb2/quality/llm_router.py:61-69` 和 `:164-168`。
- 问题：计划多处写“切 deepseek”，但当前路由层只接受 `deepseek-v4-pro`。如果 implementer 直接把四处 `"gemini-flash"` 改成 `"deepseek"`，`_validate_model_family` 会抛 `RouterConfigError`，admission topic/how/novelty/lane 都会 fail-closed。
- 可落地修法：在 p0-4 packet 中明确 `ADMISSION_MODEL = "deepseek-v4-pro"`；如想使用 `"deepseek"`，先在 `llm_router._MODEL_CONFIG` 加受测 alias。测试里断言 mock 收到的 `model_family == "deepseek-v4-pro"`，ledger 观测字段也写实际 `model_family/model_used`。

### P1 — v2 raw_gate 接在现有 admission 后，无法实现“raw 门不判 novelty、薄源入池”

- 精确位置：`plans/gd/2026-06-28-loop1-content-quality/child-drafts/v2-draft.md:45-50`；当前 admission 顺序在 `src/akb2/quality/admission.py:183-196`，判定汇总在 `:211-213`。
- 问题：v2 说 raw 门不判 how/车道/novelty，薄但有效源应进入 pending pool；但计划把 raw_gate 放在 admission 后，现有 admission 已经跑 novelty 和 lane 并可拒收，薄源可能根本到不了 raw_gate。
- 可落地修法：v2 需要拆两条路径：raw pool 前置门在现有 admission 之前，只判“可用 + 对某概念 ≥1 合格贡献”；厚源/成页路径再跑当前 admission/novelty/质量门。或者把 current admission 拆为 raw relevance 与 page admission 两个函数，v2 pending 路径不运行 novelty/how/lane。

### P1 — P0-1 子草稿要求同步 `DEFAULT_THRESHOLD`/全局阈值，和当前 lane-aware 代码语义冲突

- 精确位置：`child-drafts/p0-draft.md:51`、`:58`；代码事实为 `src/akb2/quality/weighted_score.py:17-18`、`:58-71`，现有测试 `tests/quality/test_concept_weighted_score.py:93-97` 断言默认阈值等于顶层 spec。
- 问题：当前 `compute_weighted_score` 优先读 `lanes.<lane>.threshold`，`DEFAULT_THRESHOLD` 只是顶层/未知 lane 兜底。P0 目标是 concept 6.5→6.0，不是 procedural/top/default 全部降到 6.0。按草稿改兜底会破坏 procedural/global 语义和现有测试。
- 可落地修法：最终计划明确只改 `skillpack/quality/rubric/rubric.yaml` 的 `lanes.concept.threshold` 到 6.0；保留顶层 `threshold`、`lanes.procedural.threshold`、`weighted_score.DEFAULT_THRESHOLD` 为 6.5，除非另有独立校准决策。

### P2 — P0-2 对 must-cover 的“当前未渲染”判断已经过时，继续盲改会扰动绿色路径

- 精确位置：`child-drafts/p0-draft.md:62-68`；代码事实为 `src/akb2/quality/distill.py:3726-3749`、`:4048-4061`；验证 `pytest -p no:cacheprovider -q tests/quality/test_distill_must_cover.py` 为 4 passed。
- 问题：当前代码已经把 `source_operation` 直出到 “### 操作步骤”，且离线测试覆盖 raw 文件缺失时传入 `raw_content` 的场景。计划仍把此作为缺失 bug，会诱导实现者重复改动 distill 渲染侧。
- 可落地修法：把 P0-2 改成“保留现有 must-cover 直出实现，新增/复用满仓 #0/#2/#8/#17 fixture 或 calibration slice 复验”。若满仓仍报 must-cover，应先定位 scanner item、raw_content 传递、section extraction 哪一环失配，再改对应点。

### P2 — P0-6 的报告验收条件漏掉 `AKB2_AMENDMENT_APPLIED` 影响

- 精确位置：`task-packets/p0-6-active-rerun.md:46`；代码事实为 `src/akb2/quality/calibration.py:81-89`、`:806-810`。
- 问题：runner 入库由 `AKB2_AUTO_PROMOTE=1` 控制，但 calibration 报告 `promotion_mode` 会在未设置 `AKB2_AMENDMENT_APPLIED=1` 时显示 `proposal/not_applied`。如果验收读取 report 的 active 字段，会产生“实际 auto_promote 开了但报告不 active”的混乱。
- 可落地修法：P0-6 明确二选一：若本轮要报告 `promotion_mode=active`，增加 `AKB2_AMENDMENT_APPLIED=1` 并说明修宪已应用；若不设置，则验收不以 report `promotion_mode` 判断真入库，而以 runner/promote/final_status 证据判断。

### P3 — `test_grader_score_required.py` 当前不存在，计划应标注为新增测试

- 精确位置：`task-packets/p0-1-weighted-score.md:44`, `:76`。
- 问题：`pytest --collect-only tests/quality/test_grader_score_required.py` 当前报 `file or directory not found`。
- 可落地修法：在 packet 交付物与 verify 中写明“新增 `tests/quality/test_grader_score_required.py`”，并列出至少 3 个断言：concept 缺 score parse fail、score>1 parse/compute fail、procedural 旧响应不回归。

## 实际跑过的命令清单

- `pwd && git status --short --branch`
- `find plans/gd/2026-06-28-loop1-content-quality -maxdepth 3 -type f | sort`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/master-plan.md | sed -n '1,260p'`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/child-drafts/p0-draft.md | sed -n '1,320p'`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/child-drafts/v2-draft.md | sed -n '1,360p'`
- `rg -n "score|dimensions|required|_build_grader_schema|_parse_grader_output|_parse_grader_extensions|dim_scores" src/akb2/quality/grader.py`
- `nl -ba src/akb2/quality/{grader,weighted_score,distill,checks_structural,admission,repair,runner}.py ...`
- `nl -ba src/akb2/quality/source_ops_scanners/raw_verify.py | sed -n '1,220p;430,590p'`
- `nl -ba skillpack/quality/rubric/rubric.yaml | sed -n '1,220p'`
- `nl -ba skillpack/quality/rubric/10-dimensions.md | sed -n '1,160p'`
- `nl -ba docs/constitution.md | sed -n '1,220p'`
- `rg -n "gemini-flash|deepseek|route_llm_fn" src/akb2/quality/admission.py src/akb2/quality/llm_router.py`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_concept_weighted_score.py tests/quality/test_distill_must_cover.py tests/test_raw_verify_command_shape.py tests/test_admission_lane.py tests/quality/test_admission_positive.py tests/quality/test_admission_negative.py tests/quality/test_repair_loop.py`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_grader_score_required.py`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_distill_must_cover.py`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_weighted_score.py tests/test_raw_verify_command_shape.py`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH PYTHONPATH=src python3 - <<'PY' ... verify_commands_in_raw probes ... PY`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m src.akb2.quality.calibration --help`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 - <<'PY' ... read reports/calibration/factory.json summary ... PY`
