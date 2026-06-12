# Step 3: 准入与提炼 v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-step-plan

日期:2026-06-12 | 状态:draft | 负责人:Claude 执行;Codex cross-review

## 1. 目标链(继承 + 当前 task goal)

```text
PROJECT_GOAL: 在 Claude Code 中建设 /gd Goal-Driven 多 Agent 主链路,提升复杂任务的计划、审查、执行、验收效率,并通过 Codex 作为 cross-review sidecar 降低填表式计划与执行遗漏风险。
CHAIN_GOAL:   用 shared core 固定目标链、SC、任务包、review contract 和 anti-fill 标准,保证后续 /gd command、multi-agent dispatch、execution review、Codex cross-review 都引用同一套契约。
PHASE_GOAL:   ref master plan(plans/gd/2026-06-12-content-quality-loop/master-plan.md §1)
TASK_GOAL:    交付准入三硬判定(admission.py)+ OpenAI 兼容路由(llm_router.py)+ DeepSeek 两段式提炼(distill.py)+ skillpack/quality/ 量规包;负样本 100% 死在准入段(承载 master SC-2),候选页规避黄金样张已知坑 1-3,全部单测走 fixture LLM。
```

## 2. Review 对齐

- REVIEW_DOMAIN:`ai_infra`
- REVIEW_FOCUS:`准入二值判定无连续分数(FR-004/宪法P8); 负样本 fail-closed 且拒收落账(P3/P4); 路由禁 -preview+缺 key 显式报错(P7); 抽取重试有界+预算具名常量(P1/P4); frontmatter 硬字段对齐样张步 3-4 与已知坑 1-3(P6)`
- Domain-specific notes:SC 验收对照 `docs/main-pipeline-golden-sample.md` 步 3-4 "标准"列;判定可由轻量 LLM 实现但输出必须 schema 化为布尔+理由;重试/校验/预算全确定性代码,不进 prompt。

## 3. 前置条件

- blocked_by:`step-1`(账本/故障签名 schema,拒收落账依赖);`step-2` 仅为运行时数据依赖——build 期以黄金样张锁定的 raw 格式 fixture 解耦(master §5a e2 声明可并行)
- 必须的 baseline / artifact:`schema/loop-ledger.schema.json`、`schema/failure-signature.schema.json`(step-1 deliverable);`docs/main-pipeline-golden-sample.md`;vault 黄金样张 raw/wiki 文件(只读 fixture 来源)
- Hard-stop 条件:step-1 schema 不存在或 `python3 -m json.tool` 不过 → 停止,不得自建临时 schema 顶替

## 4. 成功标准(SC,本 step 内的)

- [ ] SC-3.1(承载 master SC-2):2 个负样本(洗稿型/新闻型)100% 拒于准入段,拒收原因落账,账本零提炼调用
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python3 -m pytest tests/quality/test_admission_negative.py -q`
  - expect: exit 0
- [ ] SC-3.2:正样本(黄金样张 raw fixture)三硬判定全 pass,输出为 schema 化布尔+理由,无任何连续分数字段
  - verify (method: test): `python3 -m pytest tests/quality/test_admission_positive.py -q`
  - expect: exit 0
- [ ] SC-3.3:llm_router 缺 key 显式抛错不静默、拒绝 `-preview` 模型、单篇调用上限与超时为具名常量
  - verify (method: test): `python3 -m pytest tests/quality/test_llm_router.py -q`
  - expect: exit 0
- [ ] SC-3.4:段 1 抽取 JSON 经 jsonschema 校验,失败走有界重试(达上限 → 显式失败不出候选)
  - verify (method: test): `python3 -m pytest tests/quality/test_distill_extract_retry.py -q`
  - expect: exit 0
- [ ] SC-3.5:段 2 成稿 frontmatter 硬字段全检——`gate_decision: null` 写死 + `source_refs` 含 raw 路径 + `gate_reason`≤150 字符 + `future_questions` 2-3 条 + `related_concepts`≥2,且全部论断带 `[raw §X]` 锚点
  - verify (method: test): `python3 -m pytest tests/quality/test_distill_frontmatter.py -q`
  - expect: exit 0
- [ ] SC-3.6:skillpack/quality/ 四件齐(样张引用清单 / 负样本对照 / 概念索引注入 / 已知坑 5 条)且每文件头带 version 字段（rubric/ 目录归 step-4 owned，本 step 不交付）
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && for f in skillpack/quality/golden-sample-refs.md skillpack/quality/negative-sample-notes.md skillpack/quality/concept-index-injection.md skillpack/quality/known-pitfalls.md; do test -f "$f" || { echo "MISSING $f"; exit 1; }; done && for f in skillpack/quality/golden-sample-refs.md skillpack/quality/negative-sample-notes.md skillpack/quality/concept-index-injection.md skillpack/quality/known-pitfalls.md; do grep -q "^version:" "$f" || { echo "NO VERSION in $f"; exit 1; }; done && echo PASS`
  - expect: `PASS`
- [ ] SC-3.7(工程门):本 step 测试与 lint 全绿
  - verify (method: command): `python3 -m pytest tests/quality/test_admission_negative.py tests/quality/test_admission_positive.py tests/quality/test_llm_router.py tests/quality/test_distill_extract_retry.py tests/quality/test_distill_frontmatter.py -q && ruff check src/akb2/quality tests/quality && echo PASS`
  - expect: `PASS`

## 5. 非目标

- 真 API 调用测试(校准跑属 step-6 / SC-3)
- `skillpack/quality/repair-adapter-card.yaml`(适配卡属 step-6 owned)
- 质量评估/修复/升格逻辑(step-4/5 owned_paths)
- 抓取与 raw 锚点化(step-2);翻译(休眠)

## 6. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/llm_router.py(新建)
  WHAT:  实现 OpenAI 兼容端点路由:deepseek-v4-pro + reasoning_effort="max"(提炼,Think Max 档)/gpt-5.4(复核)/gemini-flash(兜底),key 走环境变量缺失即抛 RouterConfigError,预算具名常量(MAX_CALLS_PER_ARTICLE/TIMEOUT_S),模型名含 "-preview" 直接拒绝。**passthrough 验证**:litellm 已知 bug(BerriAI/litellm#27439)会丢弃 reasoning_effort 统一映射 thinking enabled——router 必须在请求体显式带 reasoning_effort 并在测试中断言出站 payload 含 "reasoning_effort":"max"(fixture 断言,不打真 API)
  INTERFACE_CONTRACT(供 step-4 required_context 引用):公开接口签名 `route_llm(model_family: str, messages: list[dict], response_schema: dict | None = None) -> LLMResponse`；LLMResponse dataclass 含 `content: str | dict, model_used: str, calls_used: int`；RouterConfigError 在 key 缺失时抛出。接口规格同步锁定在 `docs/interface-contracts/llm_router.md`（本 step 交付时同步写出）。
  WHY:   宪法 P7 禁 -preview + P4 预算显式;后续 admission/distill/step-4 grader 共用同一路由,避免三处各写一套
  VERIFY: python3 -m pytest tests/quality/test_llm_router.py -q
Step.2
  WHERE: src/akb2/quality/admission.py(新建)
  WHAT:  实现三硬判定(主题相关性=领域 taxonomy 锚点命中/How 倾向=可执行知识占比/新颖度=对既有 wiki 增量),每项轻量 LLM 函数输出 jsonschema 校验的 {pass: bool, reason: str},任一 fail → 整体拒收并按 step-1 账本 schema 落拒收原因
  WHY:   FR-004 二值无连续分数 + 宪法 P3 fail-closed;负样本必须死在此段,提炼段零调用(master SC-2)
  VERIFY: python3 -m pytest tests/quality/test_admission_negative.py tests/quality/test_admission_positive.py -q
Step.3
  WHERE: src/akb2/quality/distill.py(新建,段 1)
  WHAT:  实现结构化抽取:raw → {claims[带 raw §X 锚点], how_steps, params, referenced_concepts} JSON,jsonschema 校验失败时确定性代码有界重试(具名常量 MAX_EXTRACT_RETRIES),耗尽 → 显式失败不出候选
  WHY:   样张步 3 标准①(无锚点论断不许出现)+ 自动化映射 fail-closed 落点(锚点/校验失败不出候选)
  VERIFY: python3 -m pytest tests/quality/test_distill_extract_retry.py -q
Step.4
  WHERE: src/akb2/quality/distill.py(段 2)
  WHAT:  按 PAGE-TEMPLATES source 模板成稿 10 节,frontmatter 由确定性代码写死:gate_decision: null + status: candidate + source_refs 含 raw 路径 + gate_reason≤150 字符截断校验 + future_questions 2-3 条 + related_concepts≥2,落 staging/candidate/<batch>/
  WHY:   黄金样张已知坑 1-3(预填 active 被 HC-09 拦/source_refs 是 HC-05 硬指标/gate_reason 超 150 是 lint P1)必须在生成端规避,而非依赖 gate 拦截
  VERIFY: python3 -m pytest tests/quality/test_distill_frontmatter.py -q
Step.5
  WHERE: skillpack/quality/(新建,适配卡除外)
  WHAT:  写 4 件版本化文件(头部 version 字段):黄金样张引用清单/负样本对照说明/概念索引注入文件/已知坑 5 条（rubric 量规文件改由 step-4 以 skillpack/quality/rubric/ 目录形式交付，本 step 不再交付 rubric）
  WHY:   提炼 prompt 与 step-4 评分器需要稳定引用的量规真源;无版本字段则量规漂移不可审计(宪法 P6 样张锚定)
  VERIFY: SC-3.6 command
Step.6
  WHERE: tests/quality/test_admission*.py、tests/quality/test_distill*.py、tests/quality/fixtures/(新建)
  WHAT:  写全 fixture LLM 响应测试(不打真 API):负样本拒收×2 + 正样本误放回归 + router 缺 key/-preview/预算 + 抽取重试路径 + frontmatter 硬字段全检；同时写 `tests/quality/fixtures/candidate_page_fixture.md`（以黄金样张 distill 输出构造的候选页契约 fixture，定义 frontmatter 必填字段 + staging/candidate/ 路径格式，供 step-4 A2 检查作为输入契约引用）
  WHY:   master 风险表"API 波动致测试不稳定"防护;变更纪律要求断言绑定可观测行为(拒收落账/重试次数/字段值)
  VERIFY: python3 -m pytest tests/quality -q -k "admission or distill or llm_router"
Step.7
  WHERE: 项目根
  WHAT:  跑 SC-3.7 工程门命令,修复 lint findings 至 0
  WHY:   执行完成合约:lint 非 0 不得声明完成(样张步 14)
  VERIFY: SC-3.7 command 输出 PASS
```

## 7. Task Packet 拆分

| task_id | agent_role | owned_paths | blocked_by | can_parallel_with |
|---------|-----------|------------|-----------|-------------------|
| step3-admission-distill | child_executor | src/akb2/quality/{admission,distill,llm_router}.py; skillpack/quality/(适配卡 repair-adapter-card.yaml 除外，rubric/ 子目录归 step-4); tests/quality/test_admission*.py; tests/quality/test_distill*.py | step-1 | step-2 |

Task packet(关键字段):

```yaml
task_id: step3-admission-distill
agent_role: child_executor
task_goal: 准入三硬判定 + 路由 + 两段式提炼 + skillpack 量规包,负样本 100% 拒于准入,候选页规避样张已知坑 1-3
owned_paths: [src/akb2/quality/admission.py, src/akb2/quality/distill.py, src/akb2/quality/llm_router.py, docs/interface-contracts/llm_router.md, skillpack/quality/golden-sample-refs.md, skillpack/quality/negative-sample-notes.md, skillpack/quality/concept-index-injection.md, skillpack/quality/known-pitfalls.md, tests/quality/test_admission*.py, tests/quality/test_distill*.py, tests/quality/test_llm_router.py, tests/quality/fixtures/candidate_page_fixture.md]
required_context: [plans/gd/2026-06-12-content-quality-loop/master-plan.md, "docs/main-pipeline-golden-sample.md 步 3-4 标准列（准入判定依据 + 提炼格式要求）", docs/constitution.md, schema/loop-ledger.schema.json, schema/failure-signature.schema.json]
sc_refs: [SC-3.1, SC-3.2, SC-3.3, SC-3.4, SC-3.5, SC-3.6, SC-3.7]
blocked_by: [step-1]
forbidden: [真 API 调用进单测, skillpack/quality/repair-adapter-card.yaml, 连续分数排序, vault 写入]
```

## 8. 边界(修改 / 不修改)

修改:
- `src/akb2/quality/admission.py`、`src/akb2/quality/distill.py`、`src/akb2/quality/llm_router.py`(新建)
- `skillpack/quality/`(新建,**适配卡 repair-adapter-card.yaml 除外；`skillpack/quality/rubric/` 子目录归 step-4 owned，本 step 仅新建 rubric/ 以外的 4 件文件**)
- `docs/interface-contracts/llm_router.md`(新建,接口规格;step-4/step-6 required_context 引用)
- `tests/quality/test_admission*.py`、`tests/quality/test_distill*.py`、`tests/quality/fixtures/`(**`fixtures/intake/` 子目录归 step-2 owned，本 step 只写 admission/distill fixture，不碰 intake/ 下内容**)

不修改:
- 旧 `/rev` 任何 artifact;`/Users/praise/.claude/**`;vault 任何文件
- 其他 step 的 owned_paths(`src/akb2/quality/intake/`、`checks_structural.py`、`grader.py`、`repair.py`、`promote.py`、`runner.py`、schema 文件)
- `src/akb2/gate/`、`src/akb2/lint/`(复用,不改判定逻辑)

## 9. 风险与防护

| 风险 | 防护 |
|------|------|
| 轻量 LLM 判定波动导致准入二值结果不可复现 | 单测全 fixture 响应钉死路径;判定 prompt 与 schema 进 skillpack 版本化;输出 jsonschema 强校验,非法输出按 fail 处理(fail-closed) |
| 三硬判定过严把正样本误拒(校准死锁前兆) | SC-3.2 正样本误放回归用样张 raw fixture 钉死;阈值类参数具名常量集中在 admission.py 顶部,调参不动逻辑 |
| frontmatter 规避逻辑与 gate/lint 真实规则漂移 | 硬字段约束(null/150 字符/2-3 条/≥2)直接取自样张已知坑文档并写进测试断言;step-6 golden replay 兜底二次验证 |

## 10. 测试计划

```bash
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2"
python3 -m pytest tests/quality -q -k "admission or distill or llm_router"   # 本 step 全量(fixture LLM)
python3 -m pytest tests/quality/test_admission_negative.py -q                # SC-3.1 / master SC-2
ruff check src/akb2/quality tests/quality                                    # lint
```

## 11. Assumptions

- 模型阵容已锁(deepseek-v4-pro@reasoning_effort=max/gpt-5.4/gemini-flash),litellm 风格 OpenAI 兼容端点,key 由用户环境提供(master §9);max 档为参数档位非独立 model id
- step-1 交付的账本/故障签名 schema 在本 step 开工时已存在且 json.tool 可解析
- raw 文件格式由黄金样张 14 步预先锁定,step-2 未完成不阻塞本 step build(fixture 解耦)
- PAGE-TEMPLATES source 模板 10 节结构以黄金样张 active 页为准(只读引用)
- 领域 taxonomy 锚点以 skillpack/quality/ 概念索引注入文件为运行时输入,初版从既有 wiki/concepts 反提取
