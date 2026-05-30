```yaml
template_kind: gd-child-plan-output
parent_dispatch_id: akb2-briefing-rebuild
parent_track_id: t-structure
agent_role: child_planner
output_status: completed
```

---

# Step 2: P1 结构精炼 + P2 验收

## 目标链

```
PROJECT_GOAL: 在 Claude Code 中建设 /gd Goal-Driven 多 Agent 主链路，提升复杂任务的计划、审查、执行、验收效率，并通过 Codex 作为 cross-review sidecar 降低填表式计划与执行遗漏风险。
CHAIN_GOAL: 用 shared core 固定目标链、SC、任务包、review contract 和 anti-fill 标准，保证后续 /gd command、multi-agent dispatch、execution review、Codex cross-review 都引用同一套契约。
PHASE_GOAL: AKB2 社交日报结构精炼——核心讨论由模型不限字数提炼、每条挂 2-3 条高分支撑推文带原文链接+全文500字截断去折叠；并完成测试与真实链路验收。
TASK_GOAL: 产出 P1 结构精炼 + P2 验收的 GD step plan + task packets，覆盖 SC-7~SC-13，每条 SC 绑定可执行 verify。
```

## Review 对齐

- **REVIEW_DOMAIN**：app_code
- **PLAN_SOURCE**：approved_plan（`docs/briefing-rebuild-plan.md` P2 + 全局 SC-12/13）
- **REVIEW_FOCUS**（3-5 项）：
  1. **去折叠 + 500 字截断正确性**：`_render_tweet` 不再产出 `<details>`/`[!quote]- 展开英文原文` 块；推文正文 >500 字截断并附「…（已截断）」标记；原文链接 `url` 始终渲染。
  2. **每主题 2-3 条高分支撑推文**：`_collect_display_tweets`（或 `_render_tweet` 调用点）按 score 倒序对每主题 `supporting_tweet_ids` 取 top 2-3，不再全量渲染。
  3. **synthesis 去字数下限对齐 prompt + 代码**：`docs/deepseek-synthesis-prompt-v1.md` 删「≥3 句 / 3-5 句」硬约束 + 「禁编造 supporting_tweet_ids」强化；`_validate_quality` 确认无残留长度门控（现状已无，需测试钉住）。
  4. **translate 去 core_excerpt 不崩**：移除 `core_excerpt_en/zh` 抽段链路；展示推文保留 `text_zh`/`zh_point`；LLM 主+备模型双失败时返回原 synth 不抛异常。
  5. **audit 去句数检查 + 保留有效检查**：删 `_MIN_SUMMARY_SENTENCES`/`_count_sentences`/句数 issue；保留 `zh_point` 中文检查、`supporting_tweet_ids` 存在性检查、`core_excerpt` 英文检查随 SC-10 一并清理。

**Domain-specific notes（app_code）**：
- changed files：`synthesis.py`、`docs/deepseek-synthesis-prompt-v1.md`、`pipeline.py`（仅渲染段 `_render_tweet`/`_collect_display_tweets`/`build_briefing_markdown`）、`translate.py`、`audit.py`、`tests/briefing/test_*.py`。
- API contracts：对接 dict 形状不变；synth payload schema（social_trends_v1_1）不变；渲染输出形态变化（截断替代折叠）。
- data schema：无 schema 变更。
- tests：反转 v2 已有的折叠/句数/core_excerpt 断言；新增去折叠/500 截断/top2-3/链接存在测试。
- security/performance：本 Step 无网络抓取；LLM 调用沿用现有降级；无 secret 落盘。
- deployment runtime：不改 `daily_briefing.sh`/LaunchAgent/调度。

## 前置条件

```
blocked_by: Step 1（P0 抓取重建 — account_collector + curator fetch tier + pipeline.run_intake 切账号 collector，SC-1~SC-6）
```

- 依赖说明：T9/T10 验收要求 `pipeline.run_intake` 已切到账号 collector（Step 1 SC-6），否则真实链路（SC-13）跑出来仍是关键词广搜内容。T5~T8 的纯结构 delta **不**硬依赖 Step 1（可与 Step 1 并行起草/实现），但 T9 全绿 + T10 真实跑必须在 Step 1 合入后执行。
- **pipeline.py 冲突**：本 Step 改 `pipeline.py` 的**渲染段**，Step 1 改 `pipeline.py` 的 `run_intake`。同文件不同函数，为避免 merge 冲突，T6 不可与 Step 1 的 pipeline 任务并行写盘。

## 成功标准（SC，每条带 verify）

| SC | 描述 | verify method | cmd | expect |
|----|------|---------------|-----|--------|
| SC-7 | synthesis 去字数下限：`_validate_quality` 无长度/句数校验（仅非空+非空洞词）；prompt 去「≥3 句/3-5 句」 | command (test) | `python -m pytest tests/briefing/test_synthesis.py -q -k "validate_quality"` | 全绿；新测 `test_validate_quality_short_summary_passes`（单句 summary 不 raise）通过 |
| SC-7b | prompt 文档不再含句数硬约束 | command (grep) | `! grep -E "≥3 句|3 至 5 句|必须 ≥3|3-5 句|3至5句" "docs/deepseek-synthesis-prompt-v1.md"` | grep 无命中（退出码 0，因 `!` 取反） |
| SC-8 | 每主题渲染按 score 取 top 2-3 支撑推文 | command (test) | `python -m pytest tests/briefing/test_pipeline_render.py -q -k "top3 or top_n or cap"` | 新测 `test_render_topic_caps_2_to_3`：主题有 5 个 supporting id 时渲染 ≤3 条且为最高分 |
| SC-9 | 支撑推文显示 `url` 原文链接 + 全文 500 字截断 + 无折叠 | command (test) | `python -m pytest tests/briefing/test_pipeline_render.py -q -k "no_fold or truncate or link"` | `test_render_no_fold`、`test_render_truncate_500`、`test_render_link_present` 全绿 |
| SC-10 | translate 去 `core_excerpt`；展示推文有 `zh_point`；LLM 双失败降级不崩 | command (test) | `python -m pytest tests/briefing/test_translate.py -q` | 全绿；`_build_translate_prompt` 输出无 `core_excerpt_en` 字段；`test_translate_degrades_without_llm` 仍通过 |
| SC-11 | audit 去句数检查；保留 `zh_point` 中文 + `supporting_tweet_ids` 存在性 | command (test) | `python -m pytest tests/briefing/test_audit.py -q` | 全绿；新测 `test_audit_no_sentence_count` 通过；`test_audit_flags_missing_zh_point`/`test_audit_flags_nonexistent_supporting_tweet` 仍通过 |
| SC-12 | `pytest tests/briefing/` 全绿，含结构 delta 新测 | command (test) | `python -m pytest tests/briefing/ -q` | exit 0，0 failed |
| SC-13 | 真实 key+cookie 单跑：核心讨论挂支撑推文带链接全文、无折叠 | assertion (human-read + grep) | `AKB2_INTAKE_MODE=account python -m akb2.briefing.pipeline --date $(date +%F) --vault-root <tmp-vault>` 后 grep | 产出 md 无折叠标记、含原文链接；人读确认内容为策展账号真实推文 |

## 非目标

- 不改 scoring 评分算法本体（`scoring.py`）。
- 不改 P0 抓取层（`intake/`、`account_collector`、`curator-accounts.yaml`、`run_intake`）——属 Step 1。
- 不动 gate / validator / schema（social_trends_v1_1 不变）。
- 不动调度（`daily_briefing.sh`/LaunchAgent）。
- 不调优截断长度/支撑条数最终参数（默认 500 字、top 2-3，env 可覆盖）。
- 不引入 L1/L2 信任传播、新评分维度、自进化清单。

## 实现步骤

### 步骤 T5：synthesis 去字数下限 + prompt 修订（SC-7, SC-7b）
- **WHERE**：`src/akb2/briefing/synthesis.py` `_validate_quality`（行 109-130）；`docs/deepseek-synthesis-prompt-v1.md`（行 33-34, 44, 49, 56）。
- **WHAT**：(a) 代码侧——`_validate_quality` 现状**已无**数字长度门控（只校验 topic 非空/非空洞 + summary 非空），保持「非空」语义，确认不残留任何句数/字数下限。(b) prompt 侧——删 summary 的「必须写满 3 至 5 句」「3句未满不得停止」、硬约束第 2 条「summary 必须 ≥3 句」、反面案例；改为「据支撑推文充分提炼，不限字数」；强化「supporting_tweet_ids 必须真实、禁止编造」（置顶）。
- **WHY**：用户要模型自由提炼不限字数；prompt 句数硬约束是「填表式凑句」根因。
- **VERIFY**：`python -m pytest tests/briefing/test_synthesis.py -q -k validate_quality` 全绿 + 新增 `test_validate_quality_short_summary_passes`；`! grep -E "≥3 句|3 至 5 句|3句未满|3-5 句" docs/deepseek-synthesis-prompt-v1.md`。

### 步骤 T6：渲染去折叠 + 500 字截断 + 每主题 top 2-3 + 原文链接（SC-8, SC-9）
- **WHERE**：`src/akb2/briefing/pipeline.py` `_render_tweet`（行 260-287）、`_collect_display_tweets`（行 226-257）、`build_briefing_markdown`（行 290-369）。
- **WHAT**：
  1. `_render_tweet`：删 `core_excerpt` 折叠分支（含 `[!quote]- 展开英文原文` + `folded_lines`）；正文统一渲染 `text`（中文译文），>500 字时截断为 `text[:500] + "…（已截断）"`；`zh_point` 保留；`url` 始终渲染 `[原文](url)`。截断阈值读 `AKB2_RENDER_TRUNCATE`（默认 500）。
  2. 每主题取 top 2-3：在 `_collect_display_tweets` 内对每个主题的 `supporting_tweet_ids` 命中的推文按 `score` 倒序，截 `[:AKB2_SUPPORT_PER_TOPIC]`（默认 3；不足 2 不强补——交 audit 存在性检查）。
  3. `build_briefing_markdown` 调用点同步。
- **WHY**：落地用户锁定展示偏好——去折叠、全文截断、每主题 2-3 条带链接。
- **VERIFY**：`python -m pytest tests/briefing/test_pipeline_render.py -q`，含新 `test_render_no_fold`/`test_render_truncate_500`/`test_render_link_present`/`test_render_topic_caps_2_to_3`；反转旧 `test_long_tweet_rendered_with_callout`、`test_long_tweet_multiline_original_stays_in_callout`。

### 步骤 T7：translate 去 core_excerpt（SC-10）
- **WHERE**：`src/akb2/briefing/translate.py` `_build_translate_prompt`、`_apply_translation_inner`、`_translate_snippet`、`LONG_TWEET_THRESHOLD`。
- **WHAT**：prompt 不再要求 `core_excerpt_en/zh`；展示推文只产 `text_zh` + `zh_point`；删 `_apply_translation_inner` 长推核心段分支；删 `_translate_snippet` 及调用；删/弃用 `LONG_TWEET_THRESHOLD`；主模型→备模型→双失败返回原 synth（现有降级链保留）。
- **WHY**：去折叠后展示走「译文全文 + 渲染层截断」，core_excerpt 抽段冗余且是漂移/崩溃来源。
- **VERIFY**：`python -m pytest tests/briefing/test_translate.py -q` 全绿；反转 core_excerpt 相关测试；`test_translate_degrades_without_llm` 仍通过；`_build_translate_prompt` 输出不含 `core_excerpt_en`。

### 步骤 T8：audit 去句数检查（SC-11）
- **WHERE**：`src/akb2/briefing/audit.py` 常量 `_MIN_SUMMARY_SENTENCES`、`_count_sentences`、`_check_standard` 句数分支；以及随 T7 一并清理 core_excerpt 英文检查。
- **WHAT**：删 `_MIN_SUMMARY_SENTENCES` 与句数 issue 分支（summary 仅保留「非空 + 含中文」）；删 `_count_sentences`；删 core_excerpt 英文检查；**保留** `zh_point` 中文检查、`supporting_tweet_ids` 存在性 + top-3 覆盖、exec_summary/topic 中文检查。
- **WHY**：与 SC-7 去字数下限一致；audit 不应再用句数门控卡模型自由提炼。
- **VERIFY**：`python -m pytest tests/briefing/test_audit.py -q` 全绿；反转 `test_audit_flags_short_summary`、删/改 `test_audit_count_sentences`、删 `test_audit_checks_core_excerpt_english`；新增 `test_audit_no_sentence_count`；保留检查仍通过。

### 步骤 T9：测试收尾全绿（SC-12）
- **WHERE**：`tests/briefing/`。
- **WHAT**：跑 `pytest tests/briefing/ -q`，修所有因 T5~T8 失效的旧断言；确认新增结构 delta 测试入列；mock 所有 LLM/subprocess，tmp_path 隔离。
- **WHY**：确认全链路结构 delta 自洽，无回归。
- **VERIFY**：`python -m pytest tests/briefing/ -q` exit 0，0 failed。

### 步骤 T10：真实链路验收（SC-13）
- **WHERE**：worktree 内手动单跑（真实 key + cookie），tmp vault。
- **WHAT**：`AKB2_INTAKE_MODE=account python -m akb2.briefing.pipeline --date $(date +%F) --vault-root <tmp-vault>`（依赖 Step 1 已合入）；读产出 md：核心讨论挂 2-3 条支撑推文、带 `[原文](http...)` 链接、>500 字截断有标记、无 `[!quote]`/`展开英文原文`/`<details>`。
- **WHY**：真实产出是用户验收的最终标准。
- **VERIFY**：`! grep -E "展开英文原文|\[!quote\]|<details>" <output>.md` 且 `grep -E "\[原文\]\(http" <output>.md`；人读确认内容来源。

## Task Packet 拆分表

| task_id | 范围 | owned_paths | blocked_by | can_parallel_with |
|---------|------|-------------|------------|-------------------|
| t5-synthesis-drop-length-floor | SC-7 | synthesis.py, deepseek-synthesis-prompt-v1.md | — | t7, t8 |
| t6-render-unfold-truncate-toptweets | SC-8, SC-9 | pipeline.py(渲染段) | — | t7, t8（**不**与 Step1 pipeline 任务并行） |
| t7-translate-drop-core-excerpt | SC-10 | translate.py | — | t5, t6, t8 |
| t8-audit-drop-sentence-count | SC-11 | audit.py | t7（core_excerpt 检查随 T7 清理） | t5, t6 |
| t9-tests-green | SC-12 | tests/briefing/ | t5, t6, t7, t8 | — |
| t10-real-run-acceptance | SC-13 | plans/gd/2026-05-31-briefing-rebuild/acceptance-report.md（验收报告） | t9, Step1(SC-6) | — |

## 边界与失败模式

必须处理：
- 推文 >500 字 → 渲染层截断 + 「…（已截断）」；原文链接保留。
- 主题 `supporting_tweet_ids` < 2 → 不强补（交 audit 存在性检查报 issue）。
- 主题命中推文 < 2-3 → 渲染实际命中数，不报错。
- translate LLM 主+备双失败 → 返回原 synth（英文），不抛异常。
- `text_zh` 缺失 → 渲染回退原 `text`（仍走截断）。
- T6 与 Step 1 同改 `pipeline.py` → 串行写盘。

明确不处理：抓取层改动（Step 1）；截断/条数参数最终调优；gate/schema/调度。

风险与防护：
- 风险：去折叠后长推刷屏 → 防护：500 字截断 + 每主题 2-3 条。
- 风险：删 core_excerpt 漏改 audit 的 core_excerpt 英文检查导致测试挂 → 防护：T8 与 T7 绑定（blocked_by），同批清理。
- 风险：T6/Step1 同文件 merge 冲突 → 防护：can_parallel_with 显式排除，串行写盘。
- 风险：prompt 改后模型给超长 summary → 防护：synthesis 调用本就有 top-N 输入约束；真实跑观测后调。

## 测试计划

新增（mock LLM / mock subprocess / tmp_path）：
- `test_validate_quality_short_summary_passes`（synthesis）
- `test_render_no_fold` / `test_render_truncate_500` / `test_render_link_present` / `test_render_topic_caps_2_to_3`（render）
- `test_audit_no_sentence_count`（audit）

反转/删除（v2 已有，本轮 supersede）：
- `test_long_tweet_rendered_with_callout`、`test_long_tweet_multiline_original_stays_in_callout`（render）
- `test_translate_long_tweet_excerpt_and_original`、`test_core_excerpt_substring_guard`、`test_missing_core_excerpt_zh_triggers_snippet_translate`、`test_snippet_translate_failure_no_english_core_excerpt`（translate）
- `test_audit_flags_short_summary`、`test_audit_count_sentences`、`test_audit_checks_core_excerpt_english`（audit）

保留必须仍通过：
- `test_translate_degrades_without_llm`、`test_audit_flags_missing_zh_point`、`test_audit_flags_nonexistent_supporting_tweet`、`test_audit_flags_top3_tweet_not_covered`、`test_validate_quality_empty_summary_raises`、`test_validate_quality_banned_topic_raises`。

集成：`python -m pytest tests/briefing/ -q` 全绿；真实单跑（SC-13）。
隔离：tmp_path 造 vault；mock 全部 subprocess/LLM；绝不写真实 vault；不提交 secret。

## Assumptions

1. **synthesis `_validate_quality` 现状无数字长度门控**（已核对：只校验 topic 非空/非空洞 + summary 非空）。SC-7 代码 delta 极小——主要工作在 prompt 文档。诚实标注：不假装代码层有大量「去字数下限」工作。
2. **截断发生在渲染层（pipeline `_render_tweet`），不在 translate**。translate 删 core_excerpt 后产「译文全文」。
3. **env 覆盖默认值**：`AKB2_RENDER_TRUNCATE=500`、`AKB2_SUPPORT_PER_TOPIC=3`。
4. **Step 1 SC-6 提供 `AKB2_INTAKE_MODE` 开关**；T10 真实跑用 `account` 模式。
5. **同 `pipeline.py` 文件 T6 与 Step 1 串行**——packet `can_parallel_with` 排除 Step 1 pipeline 任务。

## 中文结论摘要

把重建计划 P2（SC-7~SC-11）+ 全局验收（SC-12/13）拆成 6 个自包含 task packet（T5~T10），每条 SC 绑定可执行 pytest/grep verify。已核对代码确认三处关键事实：(1) synthesis `_validate_quality` 现状已无字数门控，SC-7 真正工作在 prompt 去「≥3 句」；(2) 500 字截断应落渲染层 `_render_tweet`，translate 删 core_excerpt 后只产译文全文；(3) audit 的 core_excerpt 英文检查须随 T7 一并清理，故 T8 `blocked_by: T7`。已知缺口：T10 硬依赖 Step 1 合入与 `AKB2_INTAKE_MODE=account`；T6 与 Step 1 同改 `pipeline.py`（不同函数）需串行写盘。

<!-- gd-child-plan-proposal-json:start -->
```json
{
  "proposal_id": "akb2-briefing-rebuild-t-structure-p1b1",
  "parent_dispatch_id": "akb2-briefing-rebuild",
  "parent_track_id": "t-structure",
  "agent_role": "child_planner",
  "output_status": "completed",
  "summary_cn": "P2 结构精炼+验收拆为 T5~T10 六个自包含 task packet，覆盖 SC-7~SC-13；已核实 synthesis 现状无字数门控（SC-7 主战场在 prompt）、500字截断应落渲染层、audit 的 core_excerpt 检查须随 T7 清理故 T8 依赖 T7。",
  "task_packets": [
    {"task_id": "t5-synthesis-drop-length-floor", "owned_paths": ["src/akb2/briefing/synthesis.py", "docs/deepseek-synthesis-prompt-v1.md"], "required_context": ["src/akb2/briefing/synthesis.py", "docs/deepseek-synthesis-prompt-v1.md", "tests/briefing/test_synthesis.py"]},
    {"task_id": "t6-render-unfold-truncate-toptweets", "owned_paths": ["src/akb2/briefing/pipeline.py"], "required_context": ["src/akb2/briefing/pipeline.py", "tests/briefing/test_pipeline_render.py"]},
    {"task_id": "t7-translate-drop-core-excerpt", "owned_paths": ["src/akb2/briefing/translate.py"], "required_context": ["src/akb2/briefing/translate.py", "tests/briefing/test_translate.py"]},
    {"task_id": "t8-audit-drop-sentence-count", "owned_paths": ["src/akb2/briefing/audit.py"], "required_context": ["src/akb2/briefing/audit.py", "tests/briefing/test_audit.py"]},
    {"task_id": "t9-tests-green", "owned_paths": ["tests/briefing/"], "required_context": ["tests/briefing/", "src/akb2/briefing/"]},
    {"task_id": "t10-real-run-acceptance", "owned_paths": ["plans/gd/2026-05-31-briefing-rebuild/acceptance-report.md"], "required_context": ["src/akb2/briefing/pipeline.py", "docs/briefing-rebuild-plan.md"]}
  ],
  "sc_refs": ["SC-7", "SC-8", "SC-9", "SC-10", "SC-11", "SC-12", "SC-13"],
  "verify": [
    {"sc_ref": "SC-7", "method": "command", "cmd": "python -m pytest tests/briefing/test_synthesis.py -q -k validate_quality"},
    {"sc_ref": "SC-8", "method": "command", "cmd": "python -m pytest tests/briefing/test_pipeline_render.py -q -k 'cap or top'"},
    {"sc_ref": "SC-9", "method": "command", "cmd": "python -m pytest tests/briefing/test_pipeline_render.py -q -k 'no_fold or truncate or link or callout'"},
    {"sc_ref": "SC-10", "method": "command", "cmd": "python -m pytest tests/briefing/test_translate.py -q"},
    {"sc_ref": "SC-11", "method": "command", "cmd": "python -m pytest tests/briefing/test_audit.py -q"},
    {"sc_ref": "SC-12", "method": "test", "cmd": "python -m pytest tests/briefing/ -q"},
    {"sc_ref": "SC-13", "method": "command", "cmd": "OUT=$(find <tmp-vault> -name '*Daily Briefing.md' | head -1) && ! grep -E '展开英文原文|\\[!quote\\]|<details>' \"$OUT\" && grep -E '\\[原文\\]\\(http' \"$OUT\""}
  ],
  "blocked_reason": null
}
```
<!-- gd-child-plan-proposal-json:end -->
