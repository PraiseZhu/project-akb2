VERDICT: APPROVED

## 覆盖维度

- lens：rev-tech re-confirm；复核 `round2-rev-tech-steps.md` 中 4 条阻塞 finding 是否已消解。
- 审查范围：`step-1-p0-pipeline-honesty.md` 与 `task-packets/p0-1..p0-6`，重点 `p0-1/p0-2/p0-4/p0-5/p0-6`；同时用真实代码/测试路径核对 router family、测试收集、owned/deliverables。
- 结论：4 条阻塞均已修到可执行状态；未发现新的 P1/P2 阻塞。

## 逐项复核表

| 原 finding | 状态 | 证据 | 结论 |
|---|---|---|---|
| P1：`SC-6a/6b/6c` 未落到任一 packet verify。 | 已消解 | `p0-6-active-rerun.md:48` 新增 SC-6a/6b/6c 关闭条件，要求 handoff 记录 factory 对应 fixture 的 `content_lane / passed / weighted_score / floor_failed`；`:70-81` 新增三条 `tests/quality/test_concept_judgment_e2e.py -k concept_positive|negatives|adversarial` verify。三条 collect-only 分别收集 `1/10`、`7/10`、`2/10` tests。 | P0 出口 packet 已承接 content-lane 核心验收。 |
| P2：p0-2 主体仍是旧“改渲染”口径，和“不盲改绿路径”冲突。 | 已消解 | `p0-2-must-cover.md:8` 明确直出渲染已存在且 `test_distill_must_cover.py` 现 4 passed；`:23` TASK_GOAL 改为先复验定位；`:48-51` SC 写先复验 #0/#2/#8/#17，不盲改 distill，定位 scanner/raw_content/section；`:56-59` `distill.py` deliverable `must_exist: false`；`:92-94` 范围禁令禁止盲改绿路径。 | p0-2 已从“补渲染”改为“复验定位/证据指向才改”。 |
| P2：p0-4 规范性字段仍泛称 `deepseek`，和 router 真实 family 冲突。 | 已消解 | `p0-4-admission-deepseek.md:8` 写明 router 只支持 `deepseek-v4-pro`、字面 `deepseek` 会 `RouterConfigError`；`:23` TASK_GOAL、`:48` SC、`:56/60` deliverables、`:69` verify、`:81-82` handoff 均使用 `deepseek-v4-pro`。`rg --pcre2` 检查旧规范写法无命中；真实 `llm_router` Python 检查确认 supported keys 为 `['deepseek-v4-pro', 'gemini-flash', 'gpt-5.4']`，`_validate_model_family('deepseek')` 抛 `RouterConfigError`。 | family 名与真实 router 一致，误用字面 `deepseek` 的阻塞风险已解除。 |
| P2：p0-1/p0-5 deliverables 漏新增测试/同步文件。 | 已消解 | `p0-1-weighted-score.md:69-76` 已补 `skillpack/quality/rubric/10-dimensions.md` 与新增 `tests/quality/test_grader_score_required.py`，且写明新增测试 `>=3` 断言；`p0-5-observability.md:67-70` 已补 `tests/quality/test_admission_observability.py`。路径检查显示 `test_admission_observability.py` 已存在，collect-only 收集 2 tests；`test_grader_score_required.py` 当前仍不存在，但 packet 已把它作为 P0-1 新增 deliverable，和计划阶段一致。 | deliverables 与 verify 已对齐。 |

## 技术核验摘要

- `p0-1`：concept-only threshold 约束仍在 `p0-1:107-109`；deliverables 已包含 `10-dimensions.md` 和新增 `test_grader_score_required.py`。当前缺少该测试文件是执行前状态，不再是计划 claim 错误。
- `p0-2`：`distill.py` 仍是正确 owned path；`compose.py` 不存在；packet 主体已禁止盲改现有绿路径。
- `p0-4`：四处 admission 模型切换目标统一为 `deepseek-v4-pro`；ordered_shared 仍要求 P0-4 先合、P0-5 后改 `admission.py`。
- `p0-5`：新增观测测试脚手架已建，collect-only 可收集；测试正文明确 P0-5 未实现时 skip、实现后转真。
- `p0-6`：`AKB2_AMENDMENT_APPLIED` 二选一口径仍在 `p0-6:46-47`；SC-6a/b/c 已落到 packet verify。

## Findings

无 P1/P2 阻塞。

## 剩余风险

- P3：`p0-2-must-cover.md:64` 的复验记录 deliverable 仍写 `path: <复验记录>` 占位。已有 `handoff_output.result_path` 可承接，不阻塞执行；执行 dispatch 时最好落成具体 execution-result/report 路径，避免交付物清单不可机器检查。
- P3：`tests/quality/test_admission_observability.py` 当前是 skip-until-impl 验收脚手架；这是计划期可接受状态，P0-5 实现后必须转成真实断言通过，不能用 skip 当实现完成证据。

## 实际跑过的命令清单

- `sed -n '1,220p' /Users/praise/.codex/skills/cc-review/SKILL.md && sed -n '1,220p' /Users/praise/.codex/skills/cc-review/references/structured-review-templates.md`
- `pwd && git branch --show-current && git status --short`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/step-1-p0-pipeline-honesty.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-1-weighted-score.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-2-must-cover.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-3-command-form.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-4-admission-deepseek.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-5-observability.md`
- `nl -ba plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-6-active-rerun.md`
- `rg --pcre2 -n '切 deepseek(?!-v4-pro)|model=deepseek(?!-v4-pro)|gemini-flash ?[→-]+ ?deepseek(?!-v4-pro)|deepseek family|ADMISSION_MODEL = "deepseek"|model_family==deepseek(?!-v4-pro)' plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-4-admission-deepseek.md`
- `rg -n 'deepseek-v4-pro|字面 \`deepseek\`|ADMISSION_MODEL|model_family' plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-4-admission-deepseek.md`
- `rg -n 'SC-6a|SC-6b|SC-6c|test_concept_judgment_e2e|content_lane|weighted_score|floor_failed|handoff' plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-6-active-rerun.md`
- `rg -n '不盲改|先复验|scanner item|raw_content|section 抽取|must_exist: false|直出渲染已存在' plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-2-must-cover.md`
- `for p in tests/quality/test_grader_score_required.py tests/quality/test_admission_observability.py tests/quality/test_concept_judgment_e2e.py tests/quality/test_distill_must_cover.py tests/test_admission_lane.py tests/quality/test_admission_positive.py tests/quality/test_admission_negative.py; do test -e "$p"; done`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_admission_observability.py`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_concept_judgment_e2e.py -k 'concept_positive or negatives or adversarial'`
- `nl -ba tests/quality/test_admission_observability.py | sed -n '1,220p'`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_concept_weighted_score.py tests/quality/test_grader_score_required.py`（预期失败：P0-1 新增测试尚未实现）
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_distill_must_cover.py tests/test_raw_verify_command_shape.py tests/test_admission_lane.py tests/quality/test_admission_positive.py tests/quality/test_admission_negative.py tests/quality/test_repair_loop.py tests/quality/test_admission_observability.py`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 - <<'PY' ... llm_router family check ... PY`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_concept_judgment_e2e.py -k concept_positive`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_concept_judgment_e2e.py -k negatives`
- `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest --collect-only -q tests/quality/test_concept_judgment_e2e.py -k adversarial`
- `rg -n '<复验记录>|must_exist: false|result_path|factory 对应 fixture|content_lane / passed / weighted_score / floor_failed' plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-2-must-cover.md plans/gd/2026-06-28-loop1-content-quality/task-packets/p0-6-active-rerun.md`

纠错说明：首次 `rg` 旧写法检查使用默认 regex lookahead，ripgrep 报不支持；已用 `--pcre2` 重跑并以重跑结果为证据。
