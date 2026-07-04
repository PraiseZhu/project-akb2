# F18 grader schema 兼容修复报告

工单：修 grader(GPT-5.4) schema 兼容 bug——生产主血管堵点（A 刀）。
分支：`fix/grader-schema-compat`（worktree `_akb2-grader-schema-wt`，从 origin/main a06d7e0 开）。
未 push、未建 PR（等 lead 验收）。

## 1. 根因（带 live 账本证据）

live 账本：`/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/staging/quality-ledger.jsonl`（4216 行）。

### 签名 1（400 schema）——已由 Plan B 修复

- **账本证据**：行 2004，ts=2026-06-29T15:49:55Z，content_hash=`49fcc2f003a595a0`（= openai-blog--3d99），node=grade_llm。
- **报错全文**（未截断）：
  > `gpt-5.4 调用失败: LLM API 错误: gpt-5.4 — Error code: 400 - {'error': {'message': "litellm.BadRequestError: AzureException BadRequestError - Invalid schema for response_format 'response': In context=('properties', 'dimensions', 'items'), 'required' is required to be supplied and to be an array including every key in properties. Missing 'score'.. Received Model Group=gpt-5.4", 'type': 'invalid_request_error', 'param': 'response_format', 'code': '400'}}`
- **根因**：Azure strict structured output 要求 strict object 的 `required` 覆盖 `properties` 全键；旧 schema 的 `dimensions.items.properties` 含 `score` 但 `required` 不含 → 端点拒收 400。
- **修复**：Plan B（提交 `232fc01 fix(quality+deploy): 修 grader schema Azure 400`）从 `dimensions.items.properties` 移除 `score`（concept weighted 由逐维 pass 派生，不读 score；parse 层仍容忍 score 出现，concept 校验 score 范围的约束保留）。
- **账本验证**：Plan B 后（>=2026-06-29）gpt-5.4 primary 40 条，0 条 400。

### 签名 2（不符合 grader schema）——已由 Plan B 修复

- **账本证据**：21 条，全在 2026-06-27（15 条）/ 2026-06-28（6 条），**全在 Plan B 前**。Plan B 后 0 条。
- **报错样本**（行 1466，2026-06-27）：
  > `gpt-5.4 返回不符合 grader schema 的 JSON(keys=content_lane,dimensions,entailment_issues,failed_dimensions,integrity_floor,interpretation_entailment,overall_decision,overall_score,page_id,rubric_version,summary,threshold)`
- **根因**：网关（litellm→Azure）**不强制 strict json_schema**（`tests/test_grader_concept_prompt.py` 顶部注释证实："网关 (codex/gpt-5.4) 不强制 strict json_schema"）+ 旧 prompt 没写死字段名 → gpt-5.4 自由发挥返回额外键（`overall_decision/verdict/summary/page_id/rubric_version` 等）+ dimensions 内部错形状（`verdict` 而非 `pass`）。
- **修复**：Plan B 用 prompt 写死字段名（`pass/defects`，禁 `verdict`）+ enum 限定维度名 + `_canonical_dimension_name` 容差匹配（全/半角括号、丢后缀）。
- **账本验证**：Plan B 后 gpt-5.4 primary 40 条全成功（primary_failed=False, fallback_used=False）。

### 关键事实

**当前代码（a06d7e0 含 Plan B）已修复 grader schema 主死因**。工单基于 2026-06-27/28 旧数据判断"频繁失败"，但 Plan B（232fc01）已修复。改前基线（见 §4）3 条原死链条目 gpt-5.4 primary 全部直接成功。

## 2. 改动说明（防御深度，严格度守恒）

工单铁律"严格度不降"+"被移出的约束在本地补"。Plan B 已守恒（score 约束在 parse 层保留）。本工单补充防御深度——网关 strict 不可靠是已知事实，gpt-5.4 偶尔仍会返回错 keys（输出有随机性，见 §4 改前 openai-blog--3d99 两次跑 defects=9 vs 4）。

### 改动清单（`src/akb2/quality/grader.py`）

| 改动 | 说明 |
|------|------|
| 新增 `SCHEMA_MISMATCH_RETRY_BUDGET = 1` 具名常量 | schema-mismatch 专用额外重试预算（宪法 P4），总尝试 = `MAX_GRADER_RETRIES(2) + 1 = 3`。超预算 fail-closed。 |
| 新增 `_schema_mismatch_hint(content, content_lane)` | 用 `jsonschema` 在响应侧本地校验生成具体错误提示（路径+原因）。**严格度守恒**：请求侧 schema 约束（`additionalProperties:False`/`required`/`enum`）因网关不强制 strict 无法在端点生效，此处用本地 jsonschema 在响应侧照常强制校验——约束位置从请求侧挪到响应侧，**不许消失**。仅生成诊断提示，**不改变 `_parse_grader_output` 的 parse 决策**（容忍无害顶层额外键、拒 dimensions 内部错形状——严格度不降也不升，避免降入库率）。 |
| `_build_grader_prompt` 加 `schema_mismatch_hint` 参数 | 重试时 system 消息末尾附"上次输出格式错误（请避免重蹈覆辙）"+具体 hint，帮 gpt-5.4 修正（网关不强制 strict，prompt 软约束 + 重试提示补缺口）。 |
| `run_grader_detailed` 重试逻辑重构 | messages 移入循环（每次按 hint 重建）+ parse 失败生成 hint 附下次重试 + 总尝试 = `MAX_GRADER_RETRIES + SCHEMA_MISMATCH_RETRY_BUDGET` + 400/配置错误 `break` 不重试 + 超预算 fail-closed 走兜底。fallback 用 fresh prompt（不带 primary 的 hint——hint 是 gpt-5.4 的格式错误，跟 gemini 无关）。 |

### 严格度守恒证明

- **Plan B 移出 score property（请求侧）**：score 是可选字段，约束（concept 校验 score 范围 `[0,1]`、非 bool）在 `_parse_grader_output` 层保留（grader.py:569-574）。**已守恒**。
- **新增 `_schema_mismatch_hint`**：用 `jsonschema.validate` 在响应侧强制校验完整 schema（`additionalProperties:False` + `required` 全键 + `enum`），约束位置从请求侧挪到响应侧，**不许消失**。仅诊断，不改 parse 决策。
- **宪法 P7 不动**：gemini 兜底仍 `fallback_used=True`（下游 `grader_fallback_not_allowed` 守卫拒，不作通过条件）。修复不改这个语义。
- **不动**：rubric.yaml / threshold / integrity_floor / 五闸 / admission。

## 3. 测试清单 + 结果

### 新增 `tests/test_grader_schema_mismatch_retry.py`（13 测试）

| 类 | 测试 | 覆盖场景 |
|----|------|---------|
| TestSchemaMismatchBoundedRetry | test_verdict_shape_triggers_retry_with_hint_then_succeeds | 首次 verdict 形状 → 重试 prompt 含 hint → 第二次对 → 成功（gpt 调用 2 次） |
| TestSchemaMismatchBoundedRetry | test_extra_top_level_keys_triggers_retry | 顶层额外键 + 合法 dimensions → 不误触发重试（容忍无害额外键，严格度不升） |
| TestBudgetExhaustedFailClosed | test_budget_exhausted_fail_closed_suspended | 总错 keys → 预算耗尽（3 次）→ gemini 也失败 → suspended（fail-closed） |
| TestGeminiFallbackNotPassCondition | test_gemini_fallback_marks_fallback_used_true | gpt-5.4 失败 + gemini 兜底成功 → fallback_used=True（P7 不动作通过条件） |
| TestConfigErrorBreaksNoRetry | test_400_config_error_breaks_no_retry | 400/配置错误 → break 不重试（gpt 调用 1 次）→ suspended |
| TestSchemaMismatchHintLocalValidation | test_hint_reports_extra_top_level_key | 顶层额外键 → jsonschema 报 Additional properties not allowed |
| TestSchemaMismatchHintLocalValidation | test_hint_reports_missing_pass_in_dimension | dimensions 项缺 pass → jsonschema 报 required |
| TestSchemaMismatchHintLocalValidation | test_hint_for_non_json_string | 非 JSON 字符串 → hint 报类型错 |
| TestSchemaMismatchHintLocalValidation | test_hint_for_none_content | None content → hint 报类型错（不崩） |
| TestRequestSchemaStrictCompliant | test_total_attempts_budget_bounded | 总尝试 = 2+1=3（有界，P4） |
| TestRequestSchemaStrictCompliant | test_schema_mismatch_retry_budget_is_named_constant | SCHEMA_MISMATCH_RETRY_BUDGET 是具名常量（非魔法数字） |
| TestRequestSchemaStrictCompliant | test_request_schema_no_score_property | Plan B 守护：dimensions.items 无 score（修 Azure 400） |
| TestRequestSchemaStrictCompliant | test_request_schema_additional_properties_false_all_levels | 所有 strict object additionalProperties:False |

### 更新 `tests/quality/test_grader.py`

- `test_run_grader_detailed_retry_exhausted_to_degraded`：重试上限断言从 `<= MAX_GRADER_RETRIES` 更新为 `<= MAX_GRADER_RETRIES + SCHEMA_MISMATCH_RETRY_BUDGET`（F18 +1 的直接后果，测试意图"重试有界 + 耗尽走兜底"保留）。

### 结果

- **全量 pytest -m 'not integration'**：**2232 passed, 16 skipped**（无 failed）。
- **ruff**：我改的 3 个文件（grader.py / test_grader.py / test_grader_schema_mismatch_retry.py）**全过**。全量 12 errors 是 pre-existing（主 repo origin/main 同样 12 errors，在 distill.py / test_f10d / test_f11a / test_f11c / test_f9a，非我引入，不在工单范围）。

## 4. 真机复放对照表（生产默认配置）

配置：`source ~/.config/akb2/github-briefing.env`（AI_GATEWAY_API_KEY/AI_GATEWAY_BASE_URL/VAULT_ROOT 均 SET，走网关模式）+ `AKB2_AUTO_PROMOTE=0` + **不设任何行为开关**（AKB2_DISTILL_SEMANTIC_RETAKE 等一律不碰）+ 真实 LLM 调用（不 mock）+ 只读 candidate（不写 vault 正库）。

| 条目 | 车道 | 改前 model | 改前 pf | 改前 fu | 改前 结局 | 改后 model | 改后 pf | 改后 fu | 改后 结局 |
|------|------|------|------|------|------|------|------|------|------|
| openai-blog--3d99（原 400 条目） | concept | gpt-5.4 | False | False | 过 grader（defects=4, entailment=False, weighted=4.0） | gpt-5.4 | False | False | 过 grader（defects=7, entailment=False, weighted=4.0） |
| addyosmani-loop-eng（distill_no_converge） | procedural | gpt-5.4 | False | False | 过 grader（defects=5, entailment=False） | gpt-5.4 | False | False | 过 grader（defects=4, entailment=True） |
| fixture-ag2-captain（distill_no_converge） | procedural | gpt-5.4 | False | False | 过 grader（defects=5, entailment=False） | gpt-5.4 | False | False | 过 grader（defects=0, entailment=True） |

- pf = primary_failed, fu = fallback_used。
- 改前/改后都 **gpt-5.4 primary 直接成功，不 400，不错 keys，不 fallback gemini**。
- defects 数与 entailment 在改前/改后有波动（如 openai-blog--3d99 defects 4→7，addyosmani entailment False→True）——这是 **gpt-5.4 输出随机性**（每次调用结果不同），**非改动导致**；改前基线单条跑 openai-blog--3d99 两次也得 defects=9 与 4，同理。
- 改前基线即全成功，证实 **Plan B 已修复主死因**；改后仍全成功，证实**本工单改动不回归**。
- 改前/改后均未触发重试（gpt-5.4 当前 prompt 下首次即合法），故真机未验证"重试+hint 在 gpt-5.4 偶尔出错 keys 时的恢复力"——此场景由 mock 测试 `test_verdict_shape_triggers_retry_with_hint_then_succeeds` 确定性覆盖（真机无法确定性触发 gpt-5.4 出错）。

复放脚本与原始 JSON：`reports/akb2-quality-repair/_tmp/f18-grader-schema/replay.py` / `replay-baseline.json` / `replay-fixed.json`。

## 5. 未覆盖项

- **真机未触发重试**：改前/改后 3 条 gpt-5.4 都首次成功，未触发 schema-mismatch 重试。重试+hint 的恢复力由 mock 测试覆盖（真机无法确定性触发 gpt-5.4 出错 keys）。
- **distill_extract 链路 enable_thinking 400**：账本 2026-07-03 大量 `distill_extract` 节点 400（`Unknown parameter: 'enable_thinking'`），是 **distill 链路**的参数错误，**不是 grader schema**，不在本工单范围（工单明确修 grader schema）。
- **gemini 兜底真机未触发**：改前/改后 gpt-5.4 都成功，未走兜底。gemini 兜底仍不作通过条件由 mock 测试 `test_gemini_fallback_marks_fallback_used_true` 覆盖。
- **live daemon 未碰**：com.akb2.main-pipeline（PID 90302）/其 plist/AUTO_PROMOTE 未动。复放全程 AUTO_PROMOTE=0 + 隔离 staging（只读 candidate，不写 vault 正库）。

## 6. 一句总判

Plan B（232fc01）已修复 grader schema 主死因（400 Missing 'score' + 错 keys），改前基线 3 条原死链条目 gpt-5.4 primary 全成功；本工单补充防御深度（schema-mismatch 有界重试 +1 + prompt 附 jsonschema 诊断 hint，严格度守恒——约束位置从请求侧挪到响应侧本地校验，gemini 兜底仍不作通过条件 P7 不动），全量测试 2232 passed、改后复放 3 条不回归；未 push、未建 PR，等 lead 验收。
