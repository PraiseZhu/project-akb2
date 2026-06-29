VERDICT: APPROVED

## 覆盖维度

- lens：技术正确性；复核 Round 1 rev-tech P1/P2/P3 finding 是否被 Round 2 计划修正真实消解。
- 核验范围：`master-plan.md`、`step-1-p0-pipeline-honesty.md`、`step-4-v2-multisource-distill.md`、`task-packets/p0-1-weighted-score.md`、`p0-2-must-cover.md`、`p0-4-admission-deepseek.md`、`p0-6-active-rerun.md`；并打开真实代码 `llm_router.py`、`weighted_score.py`、`admission.py`、`distill.py`、`calibration.py`、`rubric.yaml`、`docs/constitution.md` 与相关测试。
- 结论：上一轮阻塞项已按代码现实修正；本轮未发现新的 P1/P2/P3 阻塞。

## Claim 核验表

| Round 1 claim / finding | R2 处置核验 | 证据 | 结论 |
|---|---|---|---|
| P1：P0-4 不能写字面 `deepseek`，真实 router family 是 `deepseek-v4-pro`；字面 `deepseek` 会 `RouterConfigError`。 | 证实已修正。 | `src/akb2/quality/llm_router.py:61-69` 仅配置 `deepseek-v4-pro`；`:164-168` 未知 family 抛 `RouterConfigError`。干净导入检查输出 supported keys 为 `['deepseek-v4-pro', 'gemini-flash', 'gpt-5.4']`，`_validate_model_family('deepseek')` 抛 `RouterConfigError`。计划侧：`master-plan.md:42`、`task-packets/p0-4-admission-deepseek.md:90-92`、`step-1-p0-pipeline-honesty.md:48` 均改为 `deepseek-v4-pro` 并显式警告字面 `deepseek`。 | 已消解。SC-4 与 router 真实约束一致；不再存在会把准入整体打成 fail-closed 的 family 名错误。 |
| P1：P0-1 若同步改 `DEFAULT_THRESHOLD`/top/procedural 会破 lane-aware 语义；只应改 `lanes.concept.threshold=6.0`。 | 证实已修正。 | `src/akb2/quality/weighted_score.py:17-18` 当前 `DEFAULT_THRESHOLD=6.5`；`:58-71` 读取 `lanes.<lane>.threshold -> top threshold -> DEFAULT_THRESHOLD`。`skillpack/quality/rubric/rubric.yaml:57,66,107` 当前 top/procedural/concept 均为 6.5（实现前基线）。`tests/quality/test_concept_weighted_score.py:93-97` 仍断言 default 与 top spec 同步，`pytest -q tests/quality/test_concept_weighted_score.py` 为 10 passed。计划侧：`task-packets/p0-1-weighted-score.md:99-101`、`step-1-p0-pipeline-honesty.md:49` 写明只改 `lanes.concept.threshold=6.0`，保留 top/procedural/DEFAULT=6.5，且真因改为 grader 未强制产出逐维 score。 | 已消解。不会破 procedural/global 阈值语义，也符合完整性地板不可补偿前提。 |
| P1：v2 raw_gate 接在现有 admission 后会让薄源先被 novelty/lane 拒，违背 pending raw pool 语义。 | 证实已修正。 | 真实代码 `src/akb2/quality/admission.py:183-196` 现有 admission 先跑 topic/how/novelty/lane；`:211-213` `all_passed` 仍受 novelty 与 lane_failed/lane_rejected 影响。R2 计划 `step-4-v2-multisource-distill.md:45` 明确 raw pool 前置门必须在现有 admission 前，或拆 `raw_relevance` 与 `page_admission`，pending 路径不跑 novelty/how/lane。 | 已消解。修法能避免“薄但有效源在到达 raw_gate 前被 novelty/lane 拒”的接线错误。 |
| P2：P0-2 must-cover 的“直出渲染缺失”前提过时；真实代码已有 source_operation 直出且单测绿，不应盲改绿路径。 | 证实已修正。 | 真实代码 `src/akb2/quality/distill.py:3726-3749` 已实现 `_render_must_cover_operations`；`:4048-4061` 将 `source_operation` 直出排在 LLM how_steps 前。`pytest -q tests/quality/test_distill_must_cover.py` 为 4 passed。计划侧：`master-plan.md:40`、`step-1-p0-pipeline-honesty.md:50`、`task-packets/p0-2-must-cover.md:88-91` 均写明直出渲染已存在且 test 绿，先复验满仓 #0/#2/#8/#17 并定位 scanner/raw_content/section 失配。 | 已消解。`p0-2` 顶部 SC 仍用“渲染 outcome”描述，但 §10 明确改为先复验/定位，不构成阻塞。实现者必须以 §10 为执行约束。 |
| P2：P0-6 active rerun 若验收读 report `promotion_mode=active`，必须处理 `AKB2_AMENDMENT_APPLIED`。 | 证实已修正。 | 真实代码 `src/akb2/quality/calibration.py:81-89` 表明 `AKB2_AUTO_PROMOTE=1` 且未设 `AKB2_AMENDMENT_APPLIED=1` 时 report 仍为 `proposal/not_applied`；`:806-810` 会打印该 report 状态。计划侧：`master-plan.md:44`、`step-1-p0-pipeline-honesty.md:51`、`task-packets/p0-6-active-rerun.md:47` 已补二选一口径：读 report active 就同设 amendment；否则不以 report `promotion_mode` 判真入库。 | 已消解。验收口径与 calibration 真实行为一致。 |
| P3：`tests/quality/test_grader_score_required.py` 当前不存在，不能当既有测试引用。 | 证实已修正。 | `python3 -m pytest --collect-only -q tests/quality/test_grader_score_required.py` 返回 file not found；计划侧 `task-packets/p0-1-weighted-score.md:101` 已标为新增测试且要求 >=3 断言，`master-plan.md:170,174` 将其列为实现后 deliverable/verify。 | 已消解。当前不存在不再是 claim 错误；它是 P0-1 应新增的验收物。 |
| SC 引用的现有测试路径需真实可收集。 | 证实。 | `python3 -m pytest --collect-only -q tests/quality/test_concept_weighted_score.py tests/quality/test_distill_must_cover.py tests/test_raw_verify_command_shape.py tests/test_admission_lane.py tests/quality/test_admission_positive.py tests/quality/test_admission_negative.py tests/quality/test_repair_loop.py` 收集 93 tests。`test_grader_score_required.py` 单独 collect 失败，已按新增测试处理。 | 已消解。现有路径真实；新增路径标注合理。 |
| admission 当前确有 4 处 `gemini-flash` 调用，P0-4 目标是四处切到 `deepseek-v4-pro`。 | 证实。 | `rg -n "gemini-flash|route_llm_fn|model_family" src/akb2/quality/admission.py` 显示四个 `route_llm_fn("gemini-flash", ...)` 调用点：`:413`、`:466`、`:529`、`:668`，对应 topic/how/novelty/lane。 | R2 修法成立；实施时应抽 `ADMISSION_MODEL = "deepseek-v4-pro"` 并让测试断言实际 `model_family`。 |
| 修法不能破宪法 P7 异族与 P8 地板不可补偿。 | 证实未破。 | `docs/constitution.md:91-103` 约束 maker/checker 异族与完整性地板不可补偿；`master-plan.md:42` 明确 admission 判源、grader 仍 `gpt-5.4`；`task-packets/p0-1-weighted-score.md:94-96` 明确不改 procedural、不动完整性地板、不收窄 operations.scan。 | 未发现护栏破坏。 |

## Findings

无 P1/P2/P3 阻塞 finding。

非阻塞执行注意：
- `task-packets/p0-2-must-cover.md:22,47,55` 仍以“source_operation 渲染进操作步骤节”描述目标/交付物；结合同文件 `:88-91` 的 Round 1 修正，这应理解为结果验收和回归确认，不是要求盲改 `distill.py` 绿路径。实现者执行时必须先复验满仓 #0/#2/#8/#17，再按 scanner item / raw_content 传递 / section 抽取定位。

## 实际跑过的命令清单

有效证据命令：
- `pwd && git branch --show-current && git status --short`
- `nl -ba src/akb2/quality/llm_router.py | sed -n '55,75p;154,172p'`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 - <<'PY' ... _MODEL_CONFIG / _validate_model_family ... PY`
- `nl -ba src/akb2/quality/weighted_score.py | sed -n '1,80p;90,130p'`
- `rg -n "threshold:|concept:|procedural:|top:" skillpack/quality/rubric/rubric.yaml`
- `rg -n 'deepseek-v4-pro|RouterConfigError|字面|lanes\.concept\.threshold|DEFAULT_THRESHOLD|top/procedural|grader 未强制|raw 前置门|pending 路径|不跑 novelty|直出渲染|test_grader_score_required|AKB2_AMENDMENT_APPLIED' ...`
- `rg -n "gemini-flash|route_llm_fn|model_family" src/akb2/quality/admission.py`
- `nl -ba src/akb2/quality/admission.py | sed -n '183,213p;270,335p'`
- `nl -ba src/akb2/quality/calibration.py | sed -n '79,90p;800,812p'`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_distill_must_cover.py`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_concept_weighted_score.py tests/quality/test_distill_must_cover.py tests/test_raw_verify_command_shape.py tests/test_admission_lane.py tests/quality/test_admission_positive.py tests/quality/test_admission_negative.py tests/quality/test_repair_loop.py`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_grader_score_required.py`（预期失败：当前文件不存在；计划已标新增）
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-2-must-cover.md | sed -n '15,95p'`
- `nl -ba src/akb2/quality/distill.py | sed -n '3720,3760p;4040,4070p'`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_weighted_score.py`
- `nl -ba tests/quality/test_concept_weighted_score.py | sed -n '80,112p'`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-1-weighted-score.md | sed -n '94,104p'`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/master-plan.md | sed -n '36,46p;166,178p'`
- `rg -n "原则 7|原则 8|异族|地板|补偿|不可补偿" docs/constitution.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-4-admission-deepseek.md | sed -n '86,94p'`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/step-4-v2-multisource-distill.md | sed -n '42,48p'`
- `rg -n "test_grader_score_required|test_distill_must_cover|test_concept_weighted_score|test_raw_verify_command_shape|test_admission_lane|test_admission_positive|test_admission_negative|test_repair_loop" plans/gd/2026-06-28-loop1-content-quality/master-plan.md plans/gd/2026-06-28-loop1-content-quality/task-packets/*.md`

纠错说明：
- 首次 `rg` 模式中含未转义反引号，触发 zsh command substitution，已用单引号模式重跑；首次输出未作为证据。
- 首次 Python 路由检查误把 `_validate_model_family()` 返回结构当作 `_MODEL_CONFIG` 原始配置读取，触发 `KeyError`；已用直接读取 `_MODEL_CONFIG['deepseek-v4-pro']` 的命令重跑并通过。
