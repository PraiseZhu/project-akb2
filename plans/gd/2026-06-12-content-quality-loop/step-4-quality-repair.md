# Step 4: 质量评估与修复(A 结构校验 / B 评分器 / 定点修复循环) v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-step-plan

日期:2026-06-12
状态:draft
负责人:Claude 执行;Codex cross-review

---

## 1. 目标链(继承 + 当前 task goal)

```text
PROJECT_GOAL: 在 Claude Code 中建设 /gd Goal-Driven 多 Agent 主链路,提升复杂任务的计划、审查、执行、验收效率,并通过 Codex 作为 cross-review sidecar 降低填表式计划与执行遗漏风险。
CHAIN_GOAL:   用 shared core 固定目标链、SC、任务包、review contract 和 anti-fill 标准,保证后续 /gd command、multi-agent dispatch、execution review、Codex cross-review 都引用同一套契约。
PHASE_GOAL:   ref master plan(plans/gd/2026-06-12-content-quality-loop/master-plan.md §1)
TASK_GOAL:    交付三层质量评估与修复闭环:A 结构校验器(纯代码 6 检查)+ B 评分器(GPT-5.4 经 llm_router、10 维量规、定位化缺陷清单)+ 定点修复循环(模板化指令→DeepSeek 定点重写→diff 校验→A+B 复检,MAX_REPAIR_ROUNDS=3,不收敛 fail-closed),全部测试用 fixture LLM 响应且含反填表用例。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN:`ai_infra`
- REVIEW_FOCUS:`A→B 成本递增顺序(FR-005)与 fail-closed 不收敛路径(P3); 缺陷清单可定位性、禁止无定位分数(P6/FR-006); maker/checker 异族分离与 gemini-flash 兜底、双裁决方不可用挂起(P7/FR-008); 修复循环有界 MAX_REPAIR_ROUNDS=3 具名常量(P1/P4); 未点名小节逐字节不变的 diff 校验`
- Domain-specific notes:反填表用例(结构 10 节全齐但内容空洞)是 P6 的核心验收,B 层必须打回;repair 测试必须证明"未点名小节被改动→fail"。

---

## 3. 前置条件

- blocked_by:`step-1`(循环 7 契约骨架);can_parallel_with:`step-5`
- 必须的 baseline / artifact:`schema/failure-signature.schema.json`、`schema/loop-ledger.schema.json`(step-1 产物);`src/akb2/quality/llm_router.py`(step-3 产物,声明已完成依赖);黄金样张 6 文件 fixture(只读);`docs/main-pipeline-golden-sample.md` 14 步清单
- Hard-stop 条件:step-1 schema 不存在或 `python3 -m json.tool` 解析失败 → 停止;llm_router 接口签名与本 step 假设不符 → 停止并上报

---

## 4. 成功标准(SC,本 step 内的)

- [ ] SC-4.1:A 层 6 检查(A1 模板 10 节齐/A2 frontmatter 硬字段/A3 raw 锚点真实存在/A4 图谱边四列齐/A5 wikilink 无死链/A6 维护记录≤5 条)对正负 fixture 判定正确,输出与 B 层同构的缺陷清单
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python3 -m pytest tests/quality/test_checks_structural.py -q`
  - expect: exit 0
- [ ] SC-4.2:B 层 10 维量规输出 JSON 缺陷清单,每条含 {dimension, location, defect, suggestion};无定位的评分输出被拒(FR-006);量规文本从 skillpack/quality/rubric 读取
  - verify (method: test): `python3 -m pytest tests/quality/test_grader.py -q`
  - expect: exit 0
- [ ] SC-4.3:反填表用例——结构 10 节全齐但内容空洞的稿件,A 层全过、B 层必须打回
  - verify (method: test): `python3 -m pytest tests/quality/test_grader_antifill.py -q`
  - expect: exit 0
- [ ] SC-4.4:修复循环——缺陷清单→模板化修复指令→定点重写→未点名小节逐字节不变(diff 校验,被改动则测试 fail)→重过 A+B;轮次上限 MAX_REPAIR_ROUNDS=3;3 轮不收敛 → 留候选区 + 落 distill_no_converge 故障签名
  - verify (method: test): `python3 -m pytest tests/quality/test_repair_loop.py -q`
  - expect: exit 0
- [ ] SC-4.5:执行顺序与兜底——A 不过不调 B(账本零 B 调用,FR-005);GPT-5.4 不可用切 gemini-flash;两者皆不可用 → 该篇挂起不放行(FR-008)
  - verify (method: test): `python3 -m pytest tests/quality/test_grader_fallback.py tests/quality/test_checks_order.py -q`
  - expect: exit 0
- [ ] SC-4.6:工程门——本 step owned 范围测试与 lint 全绿
  - verify (method: command): `python3 -m pytest tests/quality/test_checks_structural.py tests/quality/test_grader.py tests/quality/test_grader_antifill.py tests/quality/test_grader_fallback.py tests/quality/test_checks_order.py tests/quality/test_repair_loop.py -q && ruff check src/akb2/quality/checks_structural.py src/akb2/quality/grader.py src/akb2/quality/repair.py && echo PASS`
  - expect: `PASS`

---

## 5. 非目标

- 升格五闸与提议模式开关(step-5);校准语料与出厂跑(step-6);llm_router 本体实现(step-3 产物,只消费)
- 修复 agent 行为引擎(循环 7 Plan 4 范围);真 API 调用(单测全 fixture)
- 量规维度增删(10 维已锁,版本化文本只读取不重定义)

---

## 6. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/checks_structural.py
  WHAT:  实现 A 层 6 检查(A1 十节齐 / A2 frontmatter: source_refs≥1、future_questions 2-3、related_concepts≥2、gate_decision 必须 null、gate_reason≤150 字符 / A3 [raw §X] 锚点在 raw 文件中真实存在 / A4 图谱边 target/relation/strength/evidence 四列齐 / A5 wikilink 死链需在 pending 队列 / A6 维护记录≤5 条),零 LLM,输出与 B 同构的缺陷清单 dataclass
  WHY:   纯代码低成本前置过滤,A 全过才允许烧 B 层 LLM 调用(FR-005、constitution P4)
  VERIFY: python3 -m pytest tests/quality/test_checks_structural.py -q → exit 0

Step.2
  WHERE: skillpack/quality/rubric/(量规文本) + src/akb2/quality/grader.py
  WHAT:  落 10 维量规版本化文本(论断锚定率/How 密度/How 可执行性/证据诚实/解读分离/TLDR 密度/新颖增量/边强度匹配/Future Questions 质量/适用边界);grader 经 llm_router 调 GPT-5.4,解析输出为 JSON 缺陷清单 {dimension, location, defect, suggestion},缺 location 的条目整批拒收(FR-006)
  WHY:   B 层是质量裁决核心,量规外置版本化保证可审计,定位化清单是修复循环的输入契约
  VERIFY: python3 -m pytest tests/quality/test_grader.py -q → exit 0

Step.3
  WHERE: src/akb2/quality/grader.py(兜底分支)
  WHAT:  实现 GPT-5.4 不可用→gemini-flash 兜底切换;两者皆不可用→该篇标记挂起(suspended)不放行,不降级不跳过
  WHY:   maker/checker 异族分离的可用性兜底(P7),fail-closed 不允许无裁决放行(P3/FR-008)
  VERIFY: python3 -m pytest tests/quality/test_grader_fallback.py -q → exit 0

Step.4
  WHERE: src/akb2/quality/repair.py
  WHAT:  实现缺陷清单→模板化拼接修复指令(非自由生成)→DeepSeek 定点重写被点名小节→diff 校验未点名小节逐字节不变(违规即丢弃该轮重写)→重新过 A+B;模块级具名常量 MAX_REPAIR_ROUNDS = 3
  WHY:   定点修复防止全文重写引入新缺陷,diff 校验把"只改点名处"从约定变成机器强制,有界循环满足 P1
  VERIFY: python3 -m pytest tests/quality/test_repair_loop.py -q → exit 0

Step.5
  WHERE: src/akb2/quality/repair.py(不收敛出口)
  WHAT:  3 轮后仍有缺陷→fail-closed:稿件留候选区,按 schema/failure-signature.schema.json 落 distill_no_converge 故障签名,账本记录终态
  WHY:   不收敛必须可见且可被循环 7 watchdog 消费,不得静默丢弃或带病放行(P4 失败可见)
  VERIFY: pytest tests/quality/test_repair_loop.py 内 no_converge 用例断言签名文件符合 schema → exit 0

Step.6
  WHERE: tests/quality/(test_checks_structural.py, test_checks_order.py, test_grader.py, test_grader_antifill.py, test_grader_fallback.py, test_repair_loop.py)+ fixture LLM 响应
  WHAT:  编写全 fixture 测试:含反填表用例(10 节全齐但空洞→B 必打回)、A 不过零 B 调用断言、未点名小节被改动→必须 fail、兜底切换与双不可用挂起、不收敛签名落盘
  WHY:   断言绑定可观测行为(门控/收敛/预算效果)而非"能跑通",反填表用例是 P6 黄金样张锚定的机判化
  VERIFY: python3 -m pytest tests/quality -q -k "checks or grader or repair" → exit 0

Step.7
  WHERE: 项目根
  WHAT:  跑 SC-4.6 工程门命令(全部本 step 测试 + ruff 对 3 个 owned 源文件)
  WHY:   step 收口验收,确认无 lint 债遗留给 step-6 集成段
  VERIFY: SC-4.6 verify 命令输出 PASS
```

---

## 7. Task Packet 拆分

| task_id | agent_role | owned_paths | blocked_by | can_parallel_with |
|---------|-----------|------------|-----------|-------------------|
| quality-eval-repair-loop | executor | src/akb2/quality/checks_structural.py; src/akb2/quality/grader.py; src/akb2/quality/repair.py; skillpack/quality/rubric/; tests/quality/test_checks*.py; tests/quality/test_grader*.py; tests/quality/test_repair*.py | step-1 | step-5 |

Task packet 关键字段:
- task_id: `quality-eval-repair-loop`
- required_context: master-plan §3/§7、`docs/main-pipeline-golden-sample.md` 14 步清单、step-1 schema 三件、llm_router 接口(step-3)、黄金样张 fixture(只读)
- definition_of_done: 本 step SC-4.1~SC-4.6 全 pass
- 禁改: 其他 step owned_paths、`src/akb2/gate/`、`src/akb2/lint/`、vault 任何文件

---

## 8. 边界(修改 / 不修改)

修改:
- `src/akb2/quality/checks_structural.py`、`src/akb2/quality/grader.py`、`src/akb2/quality/repair.py`(新建)
- `skillpack/quality/rubric/`(量规版本化文本,新建)
- `tests/quality/test_checks*.py`、`tests/quality/test_grader*.py`、`tests/quality/test_repair*.py`(新建)

不修改:
- 旧 `/rev` 任何 artifact;`/Users/praise/.claude/**`
- 其他 step 的 owned_paths(含 `llm_router.py`/`distill.py`/`promote.py`/schema 三件——只消费)
- `src/akb2/gate/`、`src/akb2/lint/`、vault 任何文件

---

## 9. 风险与防护

| 风险 | 防护 |
|------|------|
| B 层 LLM 输出格式漂移导致缺陷清单解析失败 | 解析失败=该篇挂起(fail-closed),不猜测补全;fixture 含畸形输出用例钉死行为 |
| diff 校验过严(空白/行尾差异误判)阻塞所有修复 | 校验按"未点名小节逐字节不变"实现于稿件重组层:重写仅替换点名小节文本块,其余小节原样拼回,从构造上保证不变 |
| 量规文本与 grader 解析逻辑漂移(改 rubric 不改代码) | rubric 文件带版本号 frontmatter,grader 启动断言版本匹配,不匹配即拒跑 |

---

## 10. 测试计划

```bash
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2"
python3 -m pytest tests/quality/test_checks_structural.py tests/quality/test_checks_order.py -q   # A 层 + 顺序门
python3 -m pytest tests/quality/test_grader.py tests/quality/test_grader_antifill.py tests/quality/test_grader_fallback.py -q  # B 层 + 反填表 + 兜底
python3 -m pytest tests/quality/test_repair_loop.py -q                                             # 修复循环 + 不收敛签名
ruff check src/akb2/quality/checks_structural.py src/akb2/quality/grader.py src/akb2/quality/repair.py
```

---

## 11. Assumptions

- step-3 的 `llm_router.py` 已交付且暴露按模型族路由的 OpenAI 兼容调用接口;本 step 只消费不修改
- step-1 的 `schema/failure-signature.schema.json` 已存在且含 distill_no_converge 可表达的签名结构
- 黄金样张 6 文件与 14 步清单(`docs/main-pipeline-golden-sample.md`)固化只读,可作 fixture 引用
- 模型阵容已锁:deepseek-v4-pro 重写 / gpt-5.4 评分 / gemini-flash 兜底;单测全 fixture,不依赖真 key
