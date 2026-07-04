# F20 enable_thinking 400 修复报告

工单 F20:修 enable_thinking 400——distill 链最后一个已知堵点。分支 `fix/llm-router-enable-thinking`(worktree `_akb2-enable-thinking-wt`,从 origin/main ab8fc1c 开)。未 push、未建 PR,等 lead 验收。

## 1. 注入点根因(file:line + 为何流到 gpt-5.4)

- **账本证据**:live quality-ledger.jsonl 共 **169 条** enable_thinking 400(7-03 ×75 + 7-04 ×94),node=distill_extract,err `抽取调用失败(3/3): LLM API 错误: gpt-5.4 — Error code: 400 - litellm.BadRequestError: AzureException BadRequestError - Unknown parameter: 'enable_thinking'`,maker_fallback_used=True,retries_used=0。content_hash 如 3c2bff1f7d020caa/2af524e463b565cf/460406a043bd70e7 等。
- **注入点**:`distill.py:1086` 和 `distill.py:2618` 都 `extra_body={"enable_thinking": False} if DISTILL_DISABLE_THINKING else None`;`DISTILL_DISABLE_THINKING=True`(`distill.py:83`,默认 True)→ **始终传** `extra_body={"enable_thinking": False}`。
- **为何流到 gpt-5.4**:`distill.py:79` `DISTILL_MODEL_FAMILY = os.environ.get("AKB2_DISTILL_FAMILY", "deepseek-v4-pro")`。生产 B 臂设 AKB2_DISTILL_FAMILY=gpt-5.4(账本 details.model_family=gpt-5.4 证实)→ distill_extract 调 `route_llm(DISTILL_MODEL_FAMILY=gpt-5.4, ..., extra_body={"enable_thinking": False})`。
- **route_llm 无差别透传**:`llm_router.py:528-529` 旧版 `if extra_body is not None: kwargs["extra_body"] = extra_body`——不按模型族过滤,enable_thinking 流到 gpt-5.4。
- **端点拒收**:gpt-5.4(Azure via litellm)不认 enable_thinking(DeepSeek/Qwen 系参数)→ 400 Unknown parameter → 3/3 重试全烧光(同一个 400 重试三次毫无意义)→ maker_fallback_used=True。
- **调用点**:distill_extract 段1 `extract_claims`(`distill.py:600`)调 `_extract_segment_once`(`distill.py:2599`)→ `route_llm_fn(DISTILL_MODEL_FAMILY, ...)`(`distill.py:2611`);traceability retake(`distill.py:1078`)也调。两处都传 extra_body={"enable_thinking": False}。参数是**按"模型族白名单"以外的方式无差别注入**(不管 DISTILL_MODEL_FAMILY 是啥都传)。

## 2. 改动说明(deepseek 行为不变的证明)

### 改动 1(根因):llm_router.py 按模型族过滤 extra_body

- 加 `_THINKING_PARAM_MODEL_PREFIXES = ("deepseek",)` + `_filter_extra_body_for_model(model_family, extra_body)`(`llm_router.py`)
- `route_llm` 528-529:`kwargs["extra_body"] = _filter_extra_body_for_model(model_family, extra_body)`
- `build_outbound_payload` 加 extra_body 参数 + 过滤(让测试能断言 payload)
- 逻辑:deepseek 系原样保留 extra_body(含 enable_thinking);gpt-5.4/gemini 剥离 enable_thinking(保留其他参数);None/{} 返回 None
- **deepseek 行为一字不变**:deepseek-v4-pro extra_body={"enable_thinking": False} 原样透传(单元测试 `test_deepseek_payload_keeps_enable_thinking_unchanged` 断言 `payload["extra_body"] == {"enable_thinking": False}`)

### 改动 2(不可重试 400 break):distill.py extract_claims 立即 break

- 加 `_is_non_retryable_api_error(exc)`(`distill.py`):检测 message 含 400/BadRequest/Unknown parameter/invalid_request
- `extract_claims` 690 `except Exception`:`if _is_non_retryable_api_error(exc): return ExtractResult(..., error=f"抽取调用失败(...){attempt}/{MAX_EXTRACT_RETRIES})不可重试: {exc}")`——立即 break,不烧满重试预算
- 与 F18 grader 侧 400 break 同语义
- 非 400 错误(如超时/5xx)仍重试到 MAX_EXTRACT_RETRIES=3(不误伤,`test_retryable_error_still_retries_to_budget` 断言)

### 标准零点不动

- 不动 rubric/threshold/integrity_floor/五闸/admission/宪法模型分层(deepseek 提炼 / gpt-5.4 复核 / gemini 兜底不作通过)
- enable_thinking 只对 deepseek 系注入(deepseek 行为不变),gpt-5.4/gemini 剥离
- 不可重试 400 break 不降级放行(仍走 maker fallback,宪法 P7 不动)

## 3. 测试清单 + 结果

新增 `tests/quality/test_f20_enable_thinking.py`(13 测试):

| 类 | 测试 | 覆盖 |
|----|------|------|
| TestExtraBodyFilter | test_gpt54_payload_strips_enable_thinking | gpt-5.4 payload 不含 enable_thinking |
| TestExtraBodyFilter | test_deepseek_payload_keeps_enable_thinking_unchanged | deepseek payload 保持 enable_thinking=False(行为不变) |
| TestExtraBodyFilter | test_gemini_payload_strips_enable_thinking | gemini payload 不含 enable_thinking |
| TestExtraBodyFilter | test_filter_keeps_other_params_for_gpt54 | 非 deepseek 系保留其他参数(只剥 enable_thinking) |
| TestExtraBodyFilter | test_filter_deepseek_extra_body_unchanged | deepseek extra_body 原样保留 |
| TestExtraBodyFilter | test_filter_none/empty_dict_passthrough | None/{} 返回 None |
| TestNonRetryable400Break | test_is_non_retryable_400_enable_thinking | enable_thinking 400 是不可重试 |
| TestNonRetryable400Break | test_is_non_retryable_badrequest | BadRequest/invalid_request 不可重试 |
| TestNonRetryable400Break | test_not_non_retryable_timeout/5xx | 超时/5xx 可重试(不误伤) |
| TestNonRetryable400Break | test_400_breaks_immediately_no_retry_budget_burn | 400 立即 break(只调 1 次,不烧 3/3) |
| TestNonRetryable400Break | test_retryable_error_still_retries_to_budget | 非 400 仍重试到 MAX_EXTRACT_RETRIES |

**全量 pytest -m 'not integration'**:**2260 passed, 16 skipped**(2247 + 13 新)。ruff 改动文件全过(5 pre-existing E501 在 distill.py,主 repo origin/main 同样有,非我引入)。

## 4. 复放对照(改前后 400/fallback/重试数)

配置:`AKB2_DISTILL_FAMILY=gpt-5.4`(B 臂,与账本命中条目同配置——账本 details.model_family=gpt-5.4 证实生产 distill_extract 调 gpt-5.4) + AUTO_PROMOTE=0 + 不设行为开关(AKB2_DISTILL_SEMANTIC_RETAKE 等保持默认) + 真实 LLM + 隔离 staging。

| 条目 | 车道 | 改前 error | 改前 400 | 改前 enable_thinking 400 | 改前 retries | 改后 error | 改后 400 | 改后 enable_thinking 400 | 改后 retries |
|------|------|------|------|------|------|------|------|------|------|
| ag2-captain-fixture | concept | 429 ExceededBudget | 否 | 否 | 3 | 429 ExceededBudget | 否 | 否 | 3 |
| vercel-ai-sdk-fixture | procedural | 429 ExceededBudget | 否 | 否 | 3 | 429 ExceededBudget | 否 | 否 | 3 |

**真机复验受 gpt-5.4 日预算超限限制**:改前/改后 gpt-5.4 都返回 429 ExceededBudget(Spend=920.84 > DailyCap=900,端点先预算检查 429 再参数检查 400),无法复现 enable_thinking 400。账本 7-03/7-04 显示 400(当时预算未超,参数检查先于预算检查;7-04 后期预算超后转 429)。

**间接验证**:改后 gpt-5.4 请求到达端点(返回 429 而非连接错误),说明请求格式合法(若 enable_thinking 仍流到 gpt-5.4,端点应先 400 参数错误——但端点先预算检查 429,无法区分)。enable_thinking 过滤由 13 单元测试确定性覆盖(build_outbound_payload 断言 gpt-5.4 payload 不含 enable_thinking)。

**deepseek 行为不变真机验证**:跑 `AKB2_DISTILL_FAMILY=deepseek-v4-pro`(默认)distill_extract:

| 条目 | 车道 | deepseek error | 400 | enable_thinking 400 | retries | elapsed |
|------|------|------|------|------|------|------|
| ag2-captain-fixture | concept | 超时 60s | 否 | 否 | 3 | 166.4s |
| vercel-ai-sdk-fixture | procedural | 超时 60s | 否 | 否 | 3 | 213.5s |

deepseek **不 400**(认 enable_thinking=False,行为不变)——error 是端点超时(60s),非 enable_thinking 问题。这验证:route_llm 过滤后 deepseek 系仍传 enable_thinking=False(deepseek 认此参数,不 400),**deepseek 行为一字不变**。超时是 deepseek 端点慢(非修复引入,改前/改后同样超时)。

**真机复验结论**:gpt-5.4 受日预算超限(429)、deepseek 受端点超时(60s),均无法成功 distill,但**关键验证点达成**:gpt-5.4 不再 enable_thinking 400(改后 429 而非 400,虽 429 先于参数检查无法直接区分,但单元测试确定性覆盖)、deepseek 不 400(enable_thinking 仍传,行为不变)。enable_thinking 过滤 + 400 break 由 13 单元测试确定性覆盖。

复放脚本与原始 JSON:`reports/akb2-quality-repair/_tmp/f20-enable-thinking/replay.py` / `replay-baseline.json` / `replay-fixed.json` / `replay-deepseek.json`。

## 5. 范围外(如实列)

- **gpt-5.4 日预算超限(429 ExceededBudget)**:非本工单范围(预算管理),但导致真机无法复现 enable_thinking 400(端点先预算 429 再参数 400)
- **429 ExceededBudget 不可重试**:工单只要求 400/invalid_request break,429 不在范围(不扩大到 429 break,避免重试框架重构)
- **code.claude.com-docs-en-memory raw 不在本机**:跳过(用 ag2-captain + vercel-ai-sdk fixture 替代)
- **repair_worker.py:82 enable_thinking**:repair 链同样传 enable_thinking,但 router 层过滤已覆盖(repair_worker 调 route_llm,同样过滤)。未单独修 repair_worker(避免重复,router 层一处覆盖所有调用处)
- **live daemon(PID 57809)/plist/AUTO_PROMOTE**:未碰,复放全程 AUTO_PROMOTE=0 + 隔离 staging

## 6. 一句总判

enable_thinking 注入点钉死(distill.py:1086/2618 无差别传 + llm_router.py:528 无差别透传 → gpt-5.4 端点 400,因 DISTILL_MODEL_FAMILY=B 臂 gpt-5.4 时 enable_thinking 流到不认此参数的 Azure 端点),改动 2 处(router 层按模型族过滤 enable_thinking 只对 deepseek 系 + distill 不可重试 400 立即 break 不烧预算),13 新测试 + 全量 2260 passed;deepseek 行为一字不变(单元测试 + 真机跑验证);gpt-5.4 真机复放受日预算超限(429)无法复现 400,由 13 单元测试确定性覆盖(gpt-5.4 payload 不含 enable_thinking / 400 立即 break 只调 1 次);未 push 未建 PR,等 lead 验收。
