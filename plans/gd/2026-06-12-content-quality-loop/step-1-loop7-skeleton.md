# Step 1: 循环 7 契约骨架(账本/签名/适配卡 schema + watchdog + 隔离区) v1

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
TASK_GOAL:    交付循环 7 契约骨架五件套:统一 jsonl 账本 schema + 故障签名 schema + 修复适配卡 schema(9 字段)、src/akb2/loops/(幂等账本 + 签名计数升级)、scripts/akb2-watchdog.sh(纯 shell ≤80 行,--self-test)、隔离区目录约定、tests/loops/ 契约测试全绿——为 step 2-6 提供唯一落账格式。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN:`ai_infra`
- REVIEW_FOCUS:`账本统一格式作为循环 7 唯一读取面(自动化纪律 4); 有界循环无 while True 内层(P1); 故障签名落账保证失败可见(P4); watchdog 纯 shell 无 LLM 依赖; schema 自洽与幂等追加的测试绑定可观测行为`
- Domain-specific notes:本 step 是 e1 串行地基,schema 字段一旦被 step 2-6 消费即冻结;改动必须带测试且断言绑定行为(幂等/升级/退出码),不是"能跑通"。

---

## 3. 前置条件

- blocked_by:`—`
- 必须的 baseline / artifact:`docs/constitution.md` v1.1.0、`docs/loops-design-decisions.md` §7、`docs/main-pipeline-golden-sample.md`、`config/vault.toml`(隔离区路径解析,禁止硬编码 vault 路径)
- Hard-stop 条件:constitution P1/P4 或 loops-design-decisions §7 与本 step schema 字段冲突时停止,回报 master plan 负责人,不得自行改设计权威文档

---

## 4. 成功标准(SC,本 step 内的)

- [ ] SC-s1(承载 master SC-5 骨架部分):三个 schema 文件存在且 JSON 自洽
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && test -f schema/loop-ledger.schema.json && test -f schema/failure-signature.schema.json && test -f schema/repair-adapter-card.schema.json && python3 -m json.tool schema/loop-ledger.schema.json >/dev/null && python3 -m json.tool schema/failure-signature.schema.json >/dev/null && python3 -m json.tool schema/repair-adapter-card.schema.json >/dev/null && echo PASS`
  - expect: `PASS`
- [ ] SC-s2(承载 master SC-4):账本幂等追加——同内容重复写入不产生重复行,七段生命线字段齐全
  - verify (method: test): `python3 -m pytest tests/loops/test_ledger_lifeline.py -q`
  - expect: exit 0
- [ ] SC-s3:故障签名计数正确,同签名第 3 次出现写入 escalation 标记
  - verify (method: test): `python3 -m pytest tests/loops/test_signatures.py -q`
  - expect: exit 0
- [ ] SC-s4:watchdog 纯 shell ≤80 行、不含 LLM 调用,--self-test 退出码 0
  - verify (method: command): `test -x scripts/akb2-watchdog.sh && [ "$(wc -l < scripts/akb2-watchdog.sh)" -le 80 ] && ! grep -qiE 'curl.*chat|openai|deepseek|llm' scripts/akb2-watchdog.sh && bash scripts/akb2-watchdog.sh --self-test && echo PASS`
  - expect: `PASS`
- [ ] SC-s5:隔离区目录约定以具名常量导出,可被后续 step import
  - verify (method: assertion): `python3 -c "from akb2.loops import paths; assert 'quarantine' in paths.QUARANTINE_SUBDIR; print('PASS')"`(经 pytest pythonpath 或 PYTHONPATH=src)
  - expect: `PASS`
- [ ] SC-s6(工程门局部):本 step 新增代码 lint 干净
  - verify (method: command): `ruff check src/akb2/loops tests/loops && echo PASS`
  - expect: `PASS`

---

## 5. 非目标

- 修复 agent 行为引擎(循环 7 只交契约+骨架,不交修复执行逻辑)
- 循环 1 适配卡 v1 实例 `skillpack/quality/repair-adapter-card.yaml`(step-6 owned;本 step 只交 schema)
- watchdog 的 launchd 注册/常驻化(agent 不得自行注册触发器)
- step 2-6 任何业务代码(intake/admission/grader/promote/runner)

---

## 6. 实现步骤

```text
Step.1
  WHERE: schema/loop-ledger.schema.json
  WHAT:  定义七循环共用 jsonl 账本行 schema:loop_id/node/event/content_hash/ts/budget 字段 + 七段生命线事件枚举
  WHY:   账本统一格式是循环 7 读取一切的前提(自动化纪律 4),必须先于所有写账方冻结
  VERIFY: python3 -m json.tool schema/loop-ledger.schema.json >/dev/null

Step.2
  WHERE: schema/failure-signature.schema.json
  WHAT:  定义故障签名结构:loop/node/error_class/count + escalation 标记字段
  WHY:   签名落账使失败可见(P4),计数字段支撑第 3 次升级规则
  VERIFY: python3 -m json.tool schema/failure-signature.schema.json >/dev/null

Step.3
  WHERE: schema/repair-adapter-card.schema.json
  WHAT:  定义修复适配卡 9 字段:故障签名表/诊断线索映射/S 档动作白名单/B 档可碰路径/R 档红线/验证方法/回滚方法/watchdog 配置行/共享依赖声明,9 字段全 required
  WHY:   适配卡是后续修复 agent 的唯一行动契约,字段缺失即放开红线
  VERIFY: python3 -c "import json;s=json.load(open('schema/repair-adapter-card.schema.json'));assert len(s['required'])==9"

Step.4
  WHERE: src/akb2/loops/(__init__.py + paths.py + ledger.py)
  WHAT:  paths.py 导出隔离区约定常量(staging 下 quarantine 子目录,路径经 vault.toml 解析);ledger.py 实现幂等追加(行内容 hash 去重,有限重试具名常量,无 while True)
  WHY:   幂等保证修复循环重入不污染账本(P2 收敛优先);隔离区常量化避免各 step 各写一套路径
  VERIFY: python3 -m pytest tests/loops/test_ledger_lifeline.py -q

Step.5
  WHERE: src/akb2/loops/signatures.py
  WHAT:  实现签名记录到 failures.jsonl(按 signature schema)+ 同签名计数;count 达 3 写 escalation 标记行,不抛弃前两次记录
  WHY:   升级规则是循环 7 从"记录"到"行动"的触发条件,必须确定性可测
  VERIFY: python3 -m pytest tests/loops/test_signatures.py -q

Step.6
  WHERE: scripts/akb2-watchdog.sh
  WHAT:  纯 shell ≤80 行:检查心跳文件活性、账本最近写入活性、隔离区深度阈值,超限输出非 0;实现 --self-test 用临时目录自检三项检查
  WHY:   watchdog 是无 LLM 的最外层兜底,自身不可依赖被监控对象
  VERIFY: bash scripts/akb2-watchdog.sh --self-test; echo $?  → 0

Step.7
  WHERE: tests/loops/(test_schema_selfcheck.py + test_ledger_lifeline.py + test_signatures.py + test_watchdog.py)
  WHAT:  四组契约测试:三 schema json.tool 自洽与 9 字段断言/账本幂等追加与七段字段/签名计数与第 3 次升级/watchdog --self-test 退出码(经 conftest run_script,AKB2_TEST_HOME 隔离)
  WHY:   断言绑定可观测行为(幂等/升级/退出码),契约被 step 2-6 消费前必须钉死
  VERIFY: python3 -m pytest tests/loops -q && ruff check src/akb2/loops tests/loops
```

---

## 7. Task Packet 拆分

| task_id | agent_role | owned_paths | blocked_by | can_parallel_with |
|---------|-----------|------------|-----------|-------------------|
| loop7-contract-skeleton | executor | `schema/loop-ledger.schema.json`, `schema/failure-signature.schema.json`, `schema/repair-adapter-card.schema.json`, `src/akb2/loops/`, `scripts/akb2-watchdog.sh`, `tests/loops/` | — | —(e1 串行,child_agent_count=1) |

Task packet 关键字段(gd-task-packet-template 摘要):
- task_id:`loop7-contract-skeleton`;agent_role:executor;sc_refs:SC-s1~s6(上承 master SC-4/SC-5)
- required_context:`docs/constitution.md`、`docs/loops-design-decisions.md` §7、`docs/main-pipeline-golden-sample.md`、`config/vault.toml`、本 step plan
- 完成判据:§4 全部 SC verify 命令通过;边界:§8

---

## 8. 边界(修改 / 不修改)

修改:
- `schema/loop-ledger.schema.json`、`schema/failure-signature.schema.json`、`schema/repair-adapter-card.schema.json`(新建)
- `src/akb2/loops/`(新建)、`scripts/akb2-watchdog.sh`(新建)、`tests/loops/`(新建)

不修改:
- 旧 `/rev` 任何 artifact;`/Users/praise/.claude/**`
- 其他 step 的 owned_paths(含 `skillpack/quality/repair-adapter-card.yaml`、`src/akb2/quality/**`)
- vault 任何文件;`docs/constitution.md`、`docs/loops-design-decisions.md`(设计权威只读)
- `src/akb2/gate/`、`src/akb2/lint/` 及既有 briefing/video/feishu_sync 链路

---

## 9. 风险与防护

| 风险 | 防护 |
|------|------|
| schema 字段定浅,step 2-6 消费时被迫破坏性改版 | 字段以 loops-design-decisions §7 + 黄金样张 14 步为准对账;schema 留 `x-extension` 扩展位;改版必须经 master plan 修订,不许 step 内私改 |
| watchdog 行数膨胀或暗引 LLM,失去最外层兜底独立性 | SC-s4 钉死 ≤80 行 + grep 反断言 + --self-test,作为测试常驻回归 |
| 账本 hash 去重把合法重复事件(同内容不同时刻)误吞 | content_hash 计算纳入 ts/事件序列字段,语义在 schema description 写死;test_ledger_lifeline 同时断言"重复写不增行"与"新事件必增行" |

---

## 10. 测试计划

```bash
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2"
python3 -m pytest tests/loops -q                 # 契约测试全量(SC-s2/s3 + schema/watchdog 用例)
ruff check src/akb2/loops tests/loops            # SC-s6
bash scripts/akb2-watchdog.sh --self-test        # SC-s4(等价于 master SC-5 watchdog 项)
python3 -m json.tool schema/loop-ledger.schema.json >/dev/null && echo PASS   # SC-s1 抽样
```

---

## 11. Assumptions

- 账本/签名/适配卡的字段语义以 `docs/loops-design-decisions.md` §7 为设计权威,本 step 只做 schema 化不再调研
- 隔离区物理位置在 vault staging 下,路径经 `config/vault.toml` 解析(P5),tests 用 AKB2_TEST_HOME 隔离不触真 vault
- master SC-5 中的适配卡 v1 实例与 watchdog 实际接入由 step-6 收口;本 step 交付 schema + 可执行 watchdog 本体即满足 e1 出口
- Python ≥3.11,pytest pythonpath 已含 src,无需安装即可跑 tests/loops
