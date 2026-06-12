# Step 6: 常驻调度与校准出厂 v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-step-plan

日期:2026-06-12 | 状态:draft | 负责人:Claude 执行;Codex cross-review

---

## 1. 目标链(继承 + 当前 task goal)

```text
PROJECT_GOAL: 在 Claude Code 中建设 /gd Goal-Driven 多 Agent 主链路,提升复杂任务的计划、审查、执行、验收效率,并通过 Codex 作为 cross-review sidecar 降低填表式计划与执行遗漏风险。
CHAIN_GOAL:   用 shared core 固定目标链、SC、任务包、review contract 和 anti-fill 标准,保证后续 /gd command、multi-agent dispatch、execution review、Codex cross-review 都引用同一套契约。
PHASE_GOAL:   ref master plan(plans/gd/2026-06-12-content-quality-loop/master-plan.md §1)
TASK_GOAL:    交付 24h 常驻串行单工 runner + launchd 产物(产出不加载) + 20 条校准语料与出厂验收(positive_pass_rate≥0.9 且 negative_reject_rate==1.0) + 周抽检触发位 + 循环 1 修复适配卡 v1 + 黄金样张/负样本回归测试,SC 全带可执行 verify。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN:`ai_infra`
- REVIEW_FOCUS:`有界循环与具名常量(P1/P4,轮询 sleep/修复轮上限); fail-closed 七段串联完整性(P3); launchd 产物不加载纪律(自动化纪律); 校准报告区分管线缺陷与语料不当(P6); 适配卡 S/B/R 三档与 schema 自洽(循环 7 契约)`
- Domain-specific notes:SC 验收对照 `docs/main-pipeline-golden-sample.md` 14 步"标准"列;真 API 测试一律打 `integration` 标记,默认 pytest 不触发;agent 不得执行任何 `launchctl load/bootstrap`。

---

## 3. 前置条件

- blocked_by:`step-2, step-3, step-4, step-5`
- 必须的 baseline / artifact:step-1 的 `schema/repair-adapter-card.schema.json` 与 ledger API(`src/akb2/loops/`);step-2 intake、step-3 admission/distill、step-4 checks/grader/repair、step-5 promote 模块均已合入且各自测试绿
- Hard-stop 条件:上述任一 owned_paths 模块缺失或其测试红 → 本 step 不启动

---

## 4. 成功标准(SC)

- [ ] SC-6.1:runner 行为契约成立——文件队列驱动(raw 有而账本未标记=待处理)、串行单工(同一时刻仅 1 篇)、SIGTERM 优雅退出、每篇七段生命线完整落账、周抽检检查位在达标率<80% 时创建校准任务文件并落账(全部 fixture mock,不调真 API)
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python3 -m pytest tests/quality/test_runner_queue.py tests/quality/test_runner_lifecycle.py -q`
  - expect: exit 0
- [ ] SC-6.2(master SC-1):黄金样张 URL 端到端重跑,14 步逐项断言结构等价(真 API,执行期)
  - verify (method: test): `python3 -m pytest tests/quality/test_golden_replay.py -q -m integration`
  - expect: exit 0
- [ ] SC-6.3(master SC-2):2 个负样本 100% 拒于准入段,账本零提炼调用
  - verify (method: test): `python3 -m pytest tests/quality/test_negative_samples.py -q`
  - expect: exit 0
- [ ] SC-6.4(master SC-3):校准语料全量端到端,出厂判定达标
  - verify (method: command): `python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json && python3 -c "import json;r=json.load(open('reports/calibration/factory.json'));assert r['positive_pass_rate']>=0.9 and r['negative_reject_rate']==1.0;print('PASS')"`
  - expect: `PASS`
- [ ] SC-6.5:语料结构正确——corpus.yaml 恰 20 条,18 条 `expected: ingest` + 2 条 `expected: reject`,每条带 url 与领域标签
  - verify (method: command): `python3 -c "import yaml;c=yaml.safe_load(open('evals/main-pipeline-quality/corpus.yaml'));e=[x['expected'] for x in c['entries']];assert len(e)==20 and e.count('ingest')==18 and e.count('reject')==2;print('PASS')"`
  - expect: `PASS`
- [ ] SC-6.6:launchd 产物合法且**未加载**——plist 语法合法、头部注释含安装命令、launchctl 中无该 label
  - verify (method: command): `plutil -lint launchd/com.akb2.main-pipeline.plist && head -20 launchd/com.akb2.main-pipeline.plist | grep -q "launchctl" && ! launchctl list | grep -q com.akb2.main-pipeline && echo PASS`
  - expect: `PASS`
- [ ] SC-6.7:循环 1 修复适配卡 v1 通过 step-1 schema 校验(9 字段齐:故障签名表 7 项/诊断线索/S 白名单/B 可碰路径/R 红线/验证/回滚/watchdog 配置/共享依赖)
  - verify (method: test): `python3 -m pytest tests/quality/test_adapter_card_schema.py -q`
  - expect: exit 0
- [ ] SC-6.8(master SC-6 本 step 份额):全量测试 + lint 绿
  - verify (method: command): `python3 -m pytest -q && ruff check src tests && echo PASS`
  - expect: `PASS`

---

## 5. 非目标

- launchd plist 的实际加载/注册(安装命令写注释,由用户在 Mac mini 手动执行)
- 修复 agent 行为引擎(适配卡只交付契约数据,消费方属循环 7 后续 Plan)
- 周抽检的独立定时器(由 runner 周期检查位驱动,不新增触发器)
- 校准语料扩充到 76 源全覆盖(20 条出厂配比即止)

---

## 6. 实现步骤

```text
Step.1
  WHERE: evals/main-pipeline-quality/corpus.yaml(新建)
  WHAT:  写 20 条语料:18 正样本按领域配比 + 2 负样本(Zynd AI X Article 洗稿型 https://x.com/zyndai/status/2064344562110263722;谷歌 I/O 视频简报新闻型,fixture 引用 vault AI TRENDS 文件),每条带 url/领域/expected: ingest|reject
  WHY:   SC-6.4 出厂验收与 SC-6.3 负样本回归的唯一数据源,先锁数据再写消费方
  VERIFY: SC-6.5 的 python -c 断言输出 PASS

Step.2
  WHERE: src/akb2/quality/runner.py(新建)+ tests/quality/test_runner_queue.py、test_runner_lifecycle.py(新建)
  WHAT:  实现 24h 常驻串行单工 daemon:文件队列扫描(raw 有而账本未标记=待处理)、外层等触发循环(轮询 sleep 用具名常量 POLL_INTERVAL_SECONDS)、SIGTERM 优雅停、每篇调 step-1 ledger 落七段生命线、串联 step-2/3/4/5 模块、内嵌周抽检检查位(抽近期入库 3 篇重过 grader,<80% 写校准任务文件+落账)
  WHY:   FR-017 常驻调度是七段链的集成壳,无它则前 5 个 step 只是零件
  VERIFY: python3 -m pytest tests/quality/test_runner_queue.py tests/quality/test_runner_lifecycle.py -q 退出码 0

Step.3
  WHERE: scripts/akb2-main-pipeline.sh + launchd/com.akb2.main-pipeline.plist(均新建)
  WHAT:  sh 做启动包装(env 检查/单实例锁/日志重定向);plist 为 KeepAlive 常驻型,头部 XML 注释写明 launchctl 安装命令,本 step 绝不执行加载
  WHY:   Mac mini 部署需要可审计的启动物料,且宪法禁止 agent 自行注册触发器
  VERIFY: SC-6.6 命令输出 PASS(含 ! launchctl list 断言未加载)

Step.4
  WHERE: src/akb2/quality/calibration.py(新建)
  WHAT:  实现全量端到端校准跑(真 API):逐条跑 corpus → 统计 JSON 报告{positive_pass_rate, negative_reject_rate, per_segment_failures, rounds_distribution} → 出厂判定;报告含 failure 归因字段区分"管线缺陷"与"语料不当",语料替换需在 corpus.yaml 条目内记录 replaced_reason
  WHY:   master SC-3 的 verify 命令已锁定此模块入口与报告路径,出厂门槛靠它机判
  VERIFY: python3 -m akb2.quality.calibration --help 退出码 0(真 API 全量跑留待执行期 SC-6.4)

Step.5
  WHERE: skillpack/quality/repair-adapter-card.yaml(新建)+ tests/quality/test_adapter_card_schema.py(新建)
  WHAT:  按 schema/repair-adapter-card.schema.json 9 字段写循环 1 适配卡 v1:故障签名表(fetch_fail/anchor_mismatch/admission_error/distill_no_converge/grader_all_down/promote_integrity_fail/quarantine_overflow)/诊断线索映射/S 档白名单(退避重试/重排队/死链标记/兜底切换)/B 档(intake 解析器/重试参数)/R 档红线(gate 规则/量规语义/升格逻辑/准入判定题)/验证(重跑该篇/语料子集回放)/回滚(配置版本回退)/watchdog 行(队列心跳≤1h/失败率≤5%)/共享依赖(llm 网关/vault fs/账本目录)
  WHY:   循环 7 契约要求循环 1 出厂时自带修复适配卡,否则 master SC-5 缺最后一块
  VERIFY: python3 -m pytest tests/quality/test_adapter_card_schema.py -q 退出码 0

Step.6
  WHERE: tests/quality/test_golden_replay.py + tests/quality/test_negative_samples.py(均新建)
  WHAT:  golden replay 标 @pytest.mark.integration:黄金样张 URL 端到端重跑后按 14 步清单逐项断言(锚点全命中/候选 gate_decision: null/manifest 硬检查留痕/索引摘要行/概念回边/lint 0 findings);negative samples 包装校准管线对 2 负样本断言拒于准入段+账本零提炼调用
  WHY:   master SC-1/SC-2 的可执行落点,黄金样张是质量锚定(宪法 P6)的唯一基准
  VERIFY: python3 -m pytest tests/quality/test_negative_samples.py -q 退出码 0;golden replay 收集不报错(pytest --collect-only)

Step.7
  WHERE: 项目根(/Users/praise/AI-Agent/Claude/projects/Project AKB2)
  WHAT:  跑全量工程门:python3 -m pytest -q && ruff check src tests;红则按 finding 定点修复(不重写),修复轮上限 3
  WHY:   SC-6.8 是 master SC-6 收口,出厂前最后一道确定性门
  VERIFY: 两命令均退出码 0 且输出 PASS
```

---

## 7. Task Packet 拆分

| task_id | agent_role | owned_paths | blocked_by | can_parallel_with |
|---------|-----------|------------|-----------|-------------------|
| t6-runner-calibration-factory | executor | `src/akb2/quality/runner.py`, `src/akb2/quality/calibration.py`, `scripts/akb2-main-pipeline.sh`, `launchd/com.akb2.main-pipeline.plist`, `evals/main-pipeline-quality/`, `skillpack/quality/repair-adapter-card.yaml`, `tests/quality/test_runner*.py`, `tests/quality/test_golden_replay.py`, `tests/quality/test_negative_samples.py`, `tests/quality/test_adapter_card_schema.py` | step-2,3,4,5 | —(execution wave e4 串行单工) |

Task packet 关键字段:
- required_context:master plan §5 step-6 行、`docs/main-pipeline-golden-sample.md` 14 步全表、`schema/repair-adapter-card.schema.json`(step-1 产物)、step-2/3/4/5 各模块公开接口、`docs/constitution.md` P1/P3/P4 条款
- sc_refs:SC-6.1 ~ SC-6.8(对应 master SC-1/SC-2/SC-3/SC-5(适配卡份额)/SC-6)
- 完成定义:§4 全部 SC verify 通过;其中 SC-6.2/SC-6.4 为执行期真 API 项,build 期以测试可收集 + CLI 入口可用为提交门,出厂宣告以真 API 跑通为准

---

## 8. 边界(修改 / 不修改)

修改:
- §7 owned_paths 全部(均为新建)+ `reports/calibration/`(运行产物目录)

不修改:
- 旧 `/rev` 任何 artifact;`/Users/praise/.claude/**`
- 其他 step 的 owned_paths(step-2/3/4/5 模块只 import 不改;step-1 schema 只读)
- `src/akb2/gate/`、`src/akb2/lint/`;vault 任何文件;不执行任何 `launchctl load/bootstrap`

---

## 9. 风险与防护

| 风险 | 防护 |
|------|------|
| 真 API 校准/样张回放波动导致 build 期门红 | SC-6.2/SC-6.4 打 integration 标记限执行期跑;单测全 fixture mock;裁决方不可用走挂起逻辑(FR-008) |
| 校准出厂死锁(语料全打回) | factory.json 区分"管线缺陷/语料不当"归因;语料替换强制 replaced_reason 字段留痕,禁止降量规达标 |
| launchd 产物被误加载或 runner 与 EOD 脚本抢写 | SC-6.6 钉死"未加载"断言;runner 只写自身 owned 目录 + vault staging,不碰 git;单实例锁防双跑 |

---

## 10. 测试计划

```bash
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2"
python3 -m pytest tests/quality/test_runner_queue.py tests/quality/test_runner_lifecycle.py -q   # SC-6.1
python3 -m pytest tests/quality/test_negative_samples.py tests/quality/test_adapter_card_schema.py -q  # SC-6.3/6.7
python3 -m pytest tests/quality/test_golden_replay.py --collect-only -q                          # SC-6.2 build 期收集
python3 -m pytest tests/quality/test_golden_replay.py -q -m integration                          # SC-6.2 执行期真 API
python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json  # SC-6.4 执行期真 API
plutil -lint launchd/com.akb2.main-pipeline.plist && ! launchctl list | grep -q com.akb2.main-pipeline && echo PASS  # SC-6.6
python3 -m pytest -q && ruff check src tests && echo PASS                                        # SC-6.8
```

---

## 11. Assumptions

- step-1 的 `schema/repair-adapter-card.schema.json` 与 ledger API 已定稿且测试绿;step-2/3/4/5 模块接口冻结
- 模型阵容经 litellm 风格 OpenAI 兼容端点路由,key 由用户环境提供;真 API 仅执行期消耗
- 黄金样张 6 文件在 vault 固化只读;谷歌 I/O 负样本 fixture 引用 vault AI TRENDS 文件可达
- 抽检阈值 80%/周 3 篇为参数级初值;部署目标 Mac mini,launchd 加载由用户手动执行
