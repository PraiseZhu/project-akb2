# Step 5: 升格与收尾(五闸/修宪草案/提议模式开关/索引回链/快照) v1

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
TASK_GOAL:    交付 promote 模块:五闸自动升格判定 + AKB2_AUTO_PROMOTE 提议模式开关(默认 0,fail-closed)+ 升格收尾完整动作(frontmatter 翻转/索引摘要行/概念回链/维护记录/lint 复验 0)+ 前 50 条快照与 rollback CLI + vault 修宪草案文档,全部 SC 带可执行 verify。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN:`ai_infra`
- REVIEW_FOCUS:`提议模式 fail-closed(开关 0 绝不写 active,master SC-7/FR-009); 五闸判定复用现有 gate/lint CLI 不改其逻辑; 快照-回滚往返完整性(FR-015); 收尾回链/索引/维护记录对黄金样张步 9-14 标准列逐项对齐; 修宪草案不直接写 vault 且批准权留用户`
- Domain-specific notes:黄金样张已知坑 4(维护记录 >5 条先合并同日)与坑 5(引用不存在概念不建 wikilink,落 pending 队列)必须在收尾动作与测试中显式覆盖;黄金样张步 9"人批不变量"在修宪生效前仍然有效——本 step 代码默认行为必须等价于"没有自动 active"。

---

## 3. 前置条件

- blocked_by:`step-1`(loop-ledger schema 落账依赖);声明已完成依赖:step-4 grader 结果格式(闸⑤读其 PASS 输出,测试用 fixture 解耦,可与 step-4 并行 e3)
- 必须的 baseline / artifact:`src/akb2/gate/akb2_gate_check.py`(现有 gate CLI,只 subprocess 调用不修改)、现有 lint CLI(`src/akb2/lint/akb2_lint.py`)、`docs/main-pipeline-golden-sample.md` 步 9-14 标准列、step-1 产出的 `schema/loop-ledger.schema.json`
- Hard-stop 条件:gate CLI 或 lint CLI 入口不存在/不可 subprocess 调用 → 停止并上报,不得复制其判定逻辑到 promote.py

---

## 4. 成功标准(SC,本 step 内的)

- [ ] SC-5.1(master SC-7 / FR-009):`AKB2_AUTO_PROMOTE=0`(含未设置)时 promote 只走 `gate --write-manifest` 提议路径,对 `wiki/` active 区零写入
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python3 -m pytest tests/quality/test_promote_gate_switch.py -q`
  - expect: exit 0(含断言:开关 0 下 active 目录 tree_sha256 前后不变)
- [ ] SC-5.2:五闸(gate 10/10 / decision_confidence: high / graph_readiness: real_link / lint 0 / 评分器 PASS)任一闸 fail → 候选留 staging,不升格、不写 active
  - verify (method: test): `python3 -m pytest tests/quality/test_promote_gates.py -q`
  - expect: exit 0(参数化 5 闸各自单独 fail 的 5 个用例 + 全过用例)
- [ ] SC-5.3(FR-015):升格前快照全部将改文件 + manifest 至 `snapshots/<snapshot_id>/`,`python3 -m akb2.quality.promote --rollback <snapshot_id>` 可逐字节还原
  - verify (method: test): `python3 -m pytest tests/quality/test_promote_snapshot_rollback.py -q`
  - expect: exit 0(断言:升格→回滚后受影响文件 sha256 与快照前全等)
- [ ] SC-5.4:开关 1 升格收尾对齐黄金样张步 10-14:frontmatter 翻转、00-Index 人读摘要行(非裸文件名)+ 修订历史行、概念回链四列齐、缺失概念落 pending 队列不建 wikilink、维护记录超 5 条合并同日、staging 保留、触及文件 lint 复验 0
  - verify (method: test): `python3 -m pytest tests/quality/test_promote_finalize.py -q`
  - expect: exit 0(含索引行格式断言:正则要求摘要行包含来源+日期+内容描述,拒绝裸 `.md` 文件名)
- [ ] SC-5.5:修宪草案 `docs/vault-amendment-draft.md` 存在且含五个必备节:Sync Impact Report、五闸定义全文、双轨试运行条款(前 50 条快照抽查)、回滚条款、批准程序(批准权在用户)
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && for s in "Sync Impact" "五闸定义" "双轨试运行" "回滚条款" "批准"; do grep -q "$s" docs/vault-amendment-draft.md || exit 1; done && echo PASS`
  - expect: `PASS`

---

## 5. 非目标

- 不修改 `src/akb2/gate/`、`src/akb2/lint/` 任何判定逻辑(五闸只 subprocess 调用现有 CLI)
- 不实现 step-4 的评分器本体(闸⑤只消费其结果文件,格式由 fixture 钉死)
- 不直接写 vault `AKB-2.0-CONTENT-STANDARD.md`(只产 draft,批准与应用在用户侧)
- 不实现调度/daemon 接线(step-6 范围);不实现抽检阈值的运营报表
- 不实现黄金样张步 9 人批 UI/交互——修宪生效前人批仍是唯一升格路径

---

## 6. 实现步骤

```text
Step.1  五闸判定核心
  WHERE: src/akb2/quality/promote.py(新建,GateCheckResult 数据类 + evaluate_five_gates())
  WHAT:  实现五闸串行判定:①subprocess 调 src/akb2/gate/akb2_gate_check.py 取 10/10 ②解析 gate 报告 decision_confidence==high ③graph_readiness==real_link ④subprocess 调 lint CLI 取 0 findings ⑤读 step-4 grader 结果文件断言 PASS;任一闸 fail 返回 (False, 闸名, 原因) 且候选留 staging
  WHY:   升格是 vault 写入的唯一闸口,判定必须可审计、复用现有确定性 CLI,不复制判定逻辑(constitution P3/P7)
  VERIFY: python3 -m pytest tests/quality/test_promote_gates.py -q → exit 0

Step.2  提议模式开关(fail-closed 兜底)
  WHERE: src/akb2/quality/promote.py(run_promote() 入口,读 os.environ.get("AKB2_AUTO_PROMOTE","0"))
  WHAT:  开关 "0"(默认/未设置/非 "1" 值一律按 0)时只执行 gate --write-manifest --allow-l3-manifest-write 提议路径并落账 proposal 事件,任何 wiki/ active 写入代码路径不可达;开关 "1" 才进入 Step.3-4 自动升格
  WHY:   master SC-7 + spec FR-009:vault 修宪(不变量 3"没有自动 active")生效前必须 fail-closed,默认行为等价人批模式
  VERIFY: python3 -m pytest tests/quality/test_promote_gate_switch.py -q → exit 0

Step.3  升格前快照与 rollback CLI
  WHERE: src/akb2/quality/promote.py(snapshot_before_promote() + __main__ 的 --rollback 子命令)
  WHAT:  升格写入前把 active 目标路径 + 全部将改文件(00-Index/概念页/维护记录目标)完整复制到 snapshots/<snapshot_id>/ 并写回滚清单 manifest(原路径+sha256);python3 -m akb2.quality.promote --rollback <snapshot_id> 按 manifest 逐文件还原
  WHY:   FR-015 前 50 条自动入库留快照,双轨试运行期出错可一键回退,是修宪草案回滚条款的技术兑现
  VERIFY: python3 -m pytest tests/quality/test_promote_snapshot_rollback.py -q → exit 0

Step.4  自动升格收尾动作(开关 1)
  WHERE: src/akb2/quality/promote.py(finalize_promotion(),对齐黄金样张步 10-14 标准列)
  WHAT:  顺序执行:frontmatter 翻转(status: active + gate_decision: active + 去候选自评前缀)→ 00-Index.md 插一句人读摘要行(来源+日期+核心内容+服务概念+入库依据,非裸文件名)+ 修订历史行 → 概念页回链(target/relation/strength/evidence 四列,strength 按证据强度;概念不存在→不建 wikilink 落 pending 队列[坑 5])→ staging 候选保留 → 维护记录追加(超 5 条先合并同日[坑 4])→ 全部触及文件 subprocess lint 复验 0,非 0 即整体失败并自动回滚到 Step.3 快照
  WHY:   黄金样张步 10-14 是收尾完整性的验收基准,缺任何一段都是"部分失败报成功"(constitution P4)
  VERIFY: python3 -m pytest tests/quality/test_promote_finalize.py -q → exit 0

Step.5  修宪草案文档
  WHERE: docs/vault-amendment-draft.md(新建)
  WHAT:  起草 vault AKB-2.0-CONTENT-STANDARD.md 不变量 3"没有自动 active"→五闸自动升格的修订草案,含 Sync Impact Report(版本变更/影响文件/待同步项)、五闸定义全文(与 promote.py 实现一一对应)、双轨试运行条款(前 50 条快照+抽查)、回滚条款(指向 --rollback CLI)、批准程序声明(批准权在用户,修订走 vault 自身程序)
  WHY:   master 非目标明确"修宪批准权在用户";没有草案,开关 1 永远没有合法生效前提,SC-7 的开关也就失去解除条件
  VERIFY: §4 SC-5.5 的 grep 命令 → PASS

Step.6  测试套件(全 fixture,AKB2_TEST_HOME 隔离)
  WHERE: tests/quality/test_promote_gate_switch.py / test_promote_gates.py / test_promote_snapshot_rollback.py / test_promote_finalize.py(新建,用黄金样张 6 文件结构造 fixture vault)
  WHAT:  钉死四组断言:开关 0 下 active 目录 tree_sha256 不变(SC-5.1);五闸逐闸 fail 留 staging(SC-5.2);快照→升格→回滚 sha256 往返全等(SC-5.3);索引行格式正则拒裸文件名 + 回链四列 + 维护记录合并 + lint 复验 0(SC-5.4);grader 结果与 gate 报告全用 fixture,不触真 CLI 之外的外部依赖
  WHY:   断言绑定可观测行为(写入边界/文件 hash/格式正则)而非"能跑通",符合 AKB2 变更纪律;vault 操作隔离防污染真实 vault
  VERIFY: python3 -m pytest tests/quality/test_promote*.py -q && ruff check src/akb2/quality/promote.py tests/quality → exit 0
```

---

## 7. Task Packet 拆分

| task_id | agent_role | owned_paths | blocked_by | can_parallel_with |
|---------|-----------|------------|-----------|-------------------|
| promote-and-closure | executor | `src/akb2/quality/promote.py`, `tests/quality/test_promote*.py`, `docs/vault-amendment-draft.md` | step-1 | step-4(wave e3) |

Task packet 关键字段:
- task_goal:实现五闸升格 + 提议模式开关 + 快照回滚 + 收尾完整性 + 修宪草案,SC-5.1~5.5 全过
- required_context:master plan §5 step-5 行、`docs/main-pipeline-golden-sample.md` 步 9-14 + 已知坑 4-5、`src/akb2/gate/akb2_gate_check.py` CLI 接口(只读)、step-1 `schema/loop-ledger.schema.json`、step-4 grader 结果格式 fixture
- sc_refs:SC-5.1~5.5(映射 master SC-7)
- 禁改:`src/akb2/gate/**`、`src/akb2/lint/**`、vault 任何文件、其他 step owned_paths

---

## 8. 边界(修改 / 不修改)

修改:
- `src/akb2/quality/promote.py`(新建)
- `tests/quality/test_promote_gate_switch.py`、`test_promote_gates.py`、`test_promote_snapshot_rollback.py`、`test_promote_finalize.py`(新建)
- `docs/vault-amendment-draft.md`(新建)

不修改:
- 旧 `/rev` 任何 artifact;`/Users/praise/.claude/**`
- `src/akb2/gate/`、`src/akb2/lint/`(五闸只 subprocess 调用)
- vault 任何文件(build 期测试全走 AKB2_TEST_HOME 隔离;运行期写入遵循 vault.toml,不在本 step build 改动内)
- 其他 step 的 owned_paths(含 step-4 grader、step-6 runner/calibration)

---

## 9. 风险与防护

| 风险 | 防护 |
|------|------|
| 开关判定有旁路,提议模式下意外写 active(违反 vault 不变量 3) | active 写入只存在于 finalize_promotion() 单一路径,该函数入口断言开关=="1";SC-5.1 用 tree_sha256 钉死开关 0 下 active 目录零变化 |
| 收尾半途失败(如回链成功但 lint 复验非 0)留下不一致 vault 状态 | Step.3 快照先于任何写入;收尾任一段失败→自动 --rollback 到本次 snapshot_id 并落账失败事件,不报成功(constitution P4) |
| step-4 grader 结果格式未定导致闸⑤接口漂移 | 闸⑤消费的结果文件格式由本 step fixture 先钉死并写入 task packet required_context,e3 wave 内 step-4 以该 fixture 为产出契约;不一致在 step-6 集成段暴露 |

---

## 10. 测试计划

```bash
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2"
python3 -m pytest tests/quality/test_promote_gate_switch.py -q      # SC-5.1(master SC-7)
python3 -m pytest tests/quality/test_promote_gates.py -q            # SC-5.2 五闸逐闸 fail
python3 -m pytest tests/quality/test_promote_snapshot_rollback.py -q # SC-5.3 快照回滚往返
python3 -m pytest tests/quality/test_promote_finalize.py -q         # SC-5.4 收尾完整性
for s in "Sync Impact" "五闸定义" "双轨试运行" "回滚条款" "批准"; do grep -q "$s" docs/vault-amendment-draft.md || exit 1; done && echo PASS  # SC-5.5
ruff check src/akb2/quality/promote.py tests/quality                 # 工程门
```

---

## 11. Assumptions

- 现有 gate CLI(`akb2_gate_check.py`)与 lint CLI 的输出格式稳定,报告含 decision_confidence / graph_readiness 字段,可被 promote.py 解析
- step-1 已交付 loop-ledger schema,promote 的 proposal/升格/回滚事件按该 schema 落账
- step-4 评分器 PASS 结果以文件形式产出,格式以本 step fixture 为契约(e3 并行前提)
- 黄金样张 6 文件结构可复制为测试 fixture(只读引用,不动 vault 原件)
- snapshots/ 目录归本 step 运行期产物,位于工程 owned 目录(非 vault),不入 git 大文件范围
