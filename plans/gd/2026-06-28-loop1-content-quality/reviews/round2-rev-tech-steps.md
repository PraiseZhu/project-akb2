VERDICT: REQUIRES_CHANGES

## 覆盖维度

- lens：技术正确性 + 自包含性；对象为 `step-1-p0-pipeline-honesty.md` 与 `task-packets/p0-1..p0-6`。
- 实际核验：打开真实代码 `grader.py`、`weighted_score.py`、`distill.py`、`checks_structural.py`、`source_ops_scanners/raw_verify.py`、`admission.py`、`repair.py`、`runner.py`、`llm_router.py`、`calibration.py`，并跑 `pytest --collect-only`/`rg`/路径存在性检查。
- 总体判断：真实代码 claim 大体成立；但 step/packet 层仍有 4 个阻塞级自包含/验收映射问题，需要改后再放执行。

## 逐 packet 结论

| packet | 结论 | 主要依据 |
|---|---|---|
| step-1 | 需改 | `SC-6a/6b/6c` 写在 step 成功标准，但 6 个 packet 没有承接这些 verify；执行 agent 只按 packet 可能漏跑 content-lane 核心验收。 |
| p0-1-weighted-score | 需小改 | 技术方向正确；`test_grader_score_required.py` 已标新增，concept-only threshold 正确。但 deliverables 未列新增测试与 `10-dimensions.md`，不够自包含。 |
| p0-2-must-cover | 需改 | §10 已写“不盲改、先复验”，但 TASK_GOAL/SC/deliverables/verify/handoff 仍是旧的“改渲染”口径；与真实代码已绿路径冲突。 |
| p0-3-command-form | 通过 | owned paths 存在，WHERE 与 `raw_verify.py` 现状一致，verify 测试路径真实可收集。 |
| p0-4-admission-deepseek | 需改 | §10 已写 `deepseek-v4-pro`，但 TASK_GOAL/SC/deliverables/verify/handoff 仍多处写泛称 `deepseek`；router 真实不支持字面 `deepseek`。 |
| p0-5-observability | 需小改 | ordered_shared 与测试拆分方向正确；但新增 `test_admission_observability.py` 只在 owned_paths 注释和 verify 出现，deliverables 未列，当前 collect-only 明确缺文件。 |
| p0-6-active-rerun | 需改 | `AKB2_AMENDMENT_APPLIED` 口径已落到 packet；但没有承接 step/master 的 `SC-6a/6b/6c` content-lane 验证命令。 |

## 代码 claim 核验表

| 核验项 | 状态 | 证据 | 结论 |
|---|---|---|---|
| owned_paths 对应真实文件 | 部分 | 路径检查显示所有既有源码/测试存在；`tests/quality/test_grader_score_required.py` 与 `tests/quality/test_admission_observability.py` 当前缺失。 | 缺失测试若作为新增测试可以接受，但 packet deliverables 需显式列出。 |
| P0-1 grader score 当前可选且无 0~1 | 证实 | `grader.py:332-336` score 只有 `type:number`；`grader.py:360` required 只有 `dimension/pass/defects`；`grader.py:229` prompt 仍写“可选”。 | P0-1 修法目标成立。 |
| P0-1 concept-only threshold | 证实计划方向正确 | `weighted_score.py:17-18` DEFAULT=6.5；`:58-71` lane threshold 覆盖 top；`rubric.yaml:57,66,107` 当前 top/procedural/concept 均 6.5；`p0-1:99-101` 写只改 `lanes.concept.threshold=6.0`。 | 不破 lane-aware；但 deliverables 要补齐新测试/10-dimensions。 |
| P0-2 compose.py 不存在、must-cover 渲染在 distill.py | 证实 | `rg --files | rg '(^|/)compose\.py$'` 无输出；`distill.py:2076` 为 `compose_candidate`；`distill.py:3726-3749` 和 `:4048-4061` 已直出 `source_operation`。 | §10 修正正确，但 packet 主体仍旧口径。 |
| P0-3 raw_verify 漏项现状 | 证实 | `raw_verify.py:130-147` 目前只豁免 numbered prose/repo slug；`:554-578` 内联反引号回查仍先走该豁免；当前测试 `tests/test_raw_verify_command_shape.py` 可收集。 | P0-3 packet 技术上成立。 |
| P0-4 admission 四处 gemini-flash | 证实 | `admission.py:413-416`、`:465-468`、`:528-531`、`:667-670` 四处 `route_llm_fn("gemini-flash",...)`。 | 四处切模型的 WHERE 准。 |
| P0-4 router family | 证实 packet 主体未完全同步 | `llm_router.py:61-69` 只支持 `deepseek-v4-pro`；`:164-168` 未知 family 抛错；Python 检查 `_validate_model_family('deepseek')` 抛 `RouterConfigError`。`p0-4:90-92` 正确，但 `p0-4:22,47,55,59,68,80,81` 仍写 `deepseek`。 | 必须把 packet 所有规范性位置统一为 `deepseek-v4-pro`。 |
| P0-4/P0-5 ordered_shared | 证实 | step `:47` 写 P0-4 先合、P0-5 后做 admission 字段；`p0-5:27` blocked_by p0-4；`p0-5:41,45,74` 新测试拆到 `test_admission_observability.py`，禁止共享 `test_admission_lane.py`。 | 顺序/拆分方向一致；deliverables 需补新增测试。 |
| P0-6 AKB2_AMENDMENT_APPLIED | 证实 | `calibration.py:81-89` 表明 report active 依赖 `AKB2_AMENDMENT_APPLIED=1`；`p0-6:47` 已写二选一口径。 | 已落到 packet。 |
| 不破护栏 | 部分通过 | 宪法 `docs/constitution.md:91-103` 要求异族/地板不可补偿；P0-1 `:94-96`、P0-3 `:84-86`、P0-4 `:84-86`、P0-6 `:80-84` 有护栏。 | 技术方向不破；但 P0-4 泛称 `deepseek` 仍会触发 fail-closed 风险。 |

## Findings

### P1 — step-1 / p0-6 漏承接 `SC-6a/6b/6c`，执行 packet 可能不跑 content-lane 核心验收

- 位置：`plans/gd/2026-06-28-loop1-content-quality/step-1-p0-pipeline-honesty.md:39`，`task-packets/p0-6-active-rerun.md:58-69`。
- 问题：step 明确把 `SC-6a/6b/6c` 绑定到 `tests/quality/test_concept_judgment_e2e.py` 的 `concept_positive/negatives/adversarial`，master 也在 `master-plan.md:180-182` 列了三条 verify；但 6 个 P0 packet 中没有任何一份承接这些命令。`p0-6` 只检查 `factory.json` 存在和 pass_rate/negative_rate。
- 影响：执行 agent 只按 packet 可以在未显式跑 content-lane 正样本、负样本、地板不可补偿对抗样本的情况下关闭 P0，漏掉循环1最核心的护栏验收。
- 可落地修法：在 `p0-6-active-rerun.md` 的 SC/verify 中追加三条：
  - `python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k concept_positive`
  - `python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k negatives`
  - `python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k adversarial`
  并在 handoff 要求同步记录 factory 中对应 fixture 的 lane/passed/weighted/floor_failed 证据。
- 验收：`pytest --collect-only -q tests/quality/test_concept_judgment_e2e.py` 当前能收集 10 tests；修后 `rg -n "SC-6a|SC-6b|SC-6c|test_concept_judgment_e2e" task-packets/p0-6-active-rerun.md` 应命中三条 verify。

### P2 — p0-2 主体仍是旧“改渲染”任务，和 §10 的“不盲改、先复验”冲突

- 位置：`task-packets/p0-2-must-cover.md:22`、`:47`、`:52-59`、`:67-68`、`:80-81`，以及修正段 `:88-91`。
- 问题：§10 已正确写明 `distill.py` source_operation 直出渲染已存在且 test 绿，应该先复验满仓 #0/#2/#8/#17，再定位 scanner/raw_content/section 失配。但 packet 的 TASK_GOAL、SC、deliverables、verify expect、handoff summary 仍要求“compose 让 source_operation 字面量进操作步骤节”。
- 影响：子 agent 可能按旧 SC 直接修改 `distill.py` 绿路径，违反“不盲改绿路径”，也可能绕过真正的满仓失配定位。
- 可落地修法：把 `p0-2` 的 TASK_GOAL/SC/deliverables/verify/handoff 全部改成“先复验 #0/#2/#8/#17；若仍卡 SG-must-cover，定位 scanner item / raw_content 传递 / section 抽取；只有证据指向 distill 渲染时才改 `distill.py`；`test_distill_must_cover.py` 保持绿”。`owned_paths` 可保留 `distill.py`，但交付物应允许“回归确认无源码改动”。
- 验收：`rg -n "不盲改|先复验|scanner item|raw_content|section 抽取" task-packets/p0-2-must-cover.md` 命中主体 SC/verify，而不只命中 §10。

### P2 — p0-4 的规范性字段仍写泛称 `deepseek`，和 router 真实 family 冲突

- 位置：`task-packets/p0-4-admission-deepseek.md:22`、`:47`、`:55`、`:59`、`:68`、`:80-81`；正确修正只在 `:90-92`。
- 问题：真实 router 只支持 `deepseek-v4-pro`，字面 `deepseek` 会 `RouterConfigError`。packet §10 已修正，但 TASK_GOAL/SC/deliverable/verify/handoff 这些执行者最先看的规范性字段仍写“deepseek/model=deepseek”。
- 影响：执行者若按 SC 或 deliverable 文案设置 `ADMISSION_MODEL="deepseek"`，admission 四路会 fail-closed，重现上一轮 P1。
- 可落地修法：把所有模型 family 规范性文本统一为 `deepseek-v4-pro`；只在解释性文字中可写“DeepSeek 族”。具体替换：`切 deepseek` → `切 deepseek-v4-pro`，`model=deepseek` → `model_family==deepseek-v4-pro`，`llm_router 不支持 deepseek family` → `不支持 deepseek-v4-pro 或误用字面 deepseek`。
- 验收：`rg -n '切 deepseek|model=deepseek|gemini-flash→deepseek|deepseek family' p0-4-admission-deepseek.md` 不再命中规范性字段；`rg -n 'deepseek-v4-pro' p0-4-admission-deepseek.md` 覆盖 SC/deliverables/verify/handoff。

### P2 — p0-1 / p0-5 新增测试和同步文件未进入 deliverables，packet 自包含性不足

- 位置：`task-packets/p0-1-weighted-score.md:54-69`、`:98-101`；`task-packets/p0-5-observability.md:52-67`、`:71-75`。
- 问题：P0-1 的 SC 要求同步 `10-dimensions.md` 版本头并新增 `tests/quality/test_grader_score_required.py`，但 deliverables 只列 `grader.py`、`weighted_score.py`、`rubric.yaml`。P0-5 的 verify 要跑新增 `tests/quality/test_admission_observability.py`，owned_paths 注释也说新建，但 deliverables 未列该测试。路径检查与 collect-only 均确认两个测试当前不存在。
- 影响：执行交付清单可能漏掉新增测试/版本同步文件；验收命令会因缺文件失败，或 execution result 无法证明这些新测试确实是任务产物。
- 可落地修法：P0-1 deliverables 补 `skillpack/quality/rubric/10-dimensions.md`、`tests/quality/test_grader_score_required.py`，并标明 `test_grader_score_required.py` 为新增且至少 3 断言。P0-5 deliverables 补 `tests/quality/test_admission_observability.py`，标明新增，覆盖 admission pass ledger lane_reason 与 abort lane_reason。
- 验收：`rg -n "test_grader_score_required.py|10-dimensions.md|test_admission_observability.py" task-packets/p0-1-weighted-score.md task-packets/p0-5-observability.md` 在 owned_paths、deliverables、verify 三处均命中。

## 非阻塞核验备注

- P0-3 packet 当前可执行：`raw_verify.py` WHERE 与现状一致，`tests/test_raw_verify_command_shape.py` 可收集。
- P0-4/P0-5 ordered_shared 方向正确：P0-5 `blocked_by: [p0-4-admission-deepseek]`，且禁止改 `tests/test_admission_lane.py`，改用新增观测测试。
- P0-6 的 `AKB2_AMENDMENT_APPLIED` 二选一口径已落到 packet；当前已有 `reports/calibration/factory.json` 只代表旧/当前产物，检查输出为 `positive_pass_rate=0.0, negative_reject_rate=1.0, promotion_mode=proposal, amendment_status=not_applied`，不能作为 P0-6 通过证据。

## 实际跑过的命令清单

- `pwd && git branch --show-current && git status --short`
- `rg -n ... step-1-p0-pipeline-honesty.md task-packets/p0-*.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/step-1-p0-pipeline-honesty.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-1-weighted-score.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-2-must-cover.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-3-command-form.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-4-admission-deepseek.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-5-observability.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-6-active-rerun.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/child-drafts/p0-draft.md | sed -n '1,280p'`
- `for p in ...; do test -e "$p"; done`
- `nl -ba src/akb2/quality/grader.py | sed -n '190,235p;300,405p;440,545p;590,660p'`
- `nl -ba src/akb2/quality/weighted_score.py | sed -n '1,130p'`
- `nl -ba skillpack/quality/rubric/rubric.yaml | sed -n '1,120p'; nl -ba skillpack/quality/rubric/10-dimensions.md | sed -n '1,40p'`
- `nl -ba src/akb2/quality/distill.py | sed -n '900,940p;3720,3760p;4040,4070p'`
- `nl -ba src/akb2/quality/checks_structural.py | sed -n '1050,1070p;1270,1335p'`
- `nl -ba src/akb2/quality/source_ops_scanners/raw_verify.py | sed -n '120,155p;455,505p;545,585p'`
- `nl -ba src/akb2/quality/admission.py | sed -n '130,215p;405,475p;520,535p;660,675p;740,805p'`
- `nl -ba src/akb2/quality/repair.py | sed -n '1225,1240p;1440,1465p;1768,1838p'`
- `nl -ba src/akb2/quality/runner.py | sed -n '1138,1150p;1268,1300p;1418,1450p;1838,1852p'`
- `nl -ba src/akb2/quality/llm_router.py | sed -n '59,75p;154,170p'`
- `nl -ba src/akb2/quality/calibration.py | sed -n '79,90p;805,811p'`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_concept_weighted_score.py`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_grader_score_required.py`（预期失败：当前新增测试未建）
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_distill_must_cover.py tests/test_raw_verify_command_shape.py tests/test_admission_lane.py tests/quality/test_admission_positive.py tests/quality/test_admission_negative.py tests/quality/test_repair_loop.py`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_admission_observability.py`（预期失败：当前新增测试未建）
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_concept_judgment_e2e.py`
- `rg --files | rg '(^|/)compose\.py$' || true`
- `rg -n "def compose_candidate|_render_must_cover_operations|source_operation" src/akb2/quality/distill.py tests/quality/test_distill_must_cover.py`
- `rg -n "AKB2_AMENDMENT_APPLIED|promotion_mode|AKB2_AUTO_PROMOTE|runner/promote|final_status" ...`
- `rg -n "SC-6a|SC-6b|SC-6c|concept_judgment|concept_positive|negatives|adversarial" ...`
- `test -f reports/calibration/factory.json && echo PASS; PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -c "import json; ..."`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 - <<'PY' ... llm_router family check ... PY`
