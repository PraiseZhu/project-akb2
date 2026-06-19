# GD Plan Review — AKB2 内容质量循环 Plan 1

```
REVIEWER: claude_subagent_plan_reviewer
REVIEW_TARGET: plans/gd/2026-06-12-content-quality-loop/master-plan.md (+ 6 step plans)
REVIEW_KIND: plan
REVIEW_RUN_STATUS: completed
GD_REVIEW_DECISION: REQUIRES_CHANGES
```

---

## Scope Checked 表

| 文件 | 检查项 | 状态 |
|------|--------|------|
| master-plan §3 SC-1~SC-8 | 每条 verify 有命令/路径/断言 | ✓ 全部有 |
| master-plan §5 Step 表 | owned_paths 无重叠 | ✓（见注 1） |
| master-plan §5a Wave Matrix | 并行前提双向声明 | ⚠ 部分缺（见 F-2） |
| step-1 SC-s1~s6 | verify 可执行性 | ✓ 全部有命令 |
| step-1 SC-s5 | verify 命令路径 | ⚠ PYTHONPATH 依赖未显式处理（见 F-3） |
| step-2 SC-2.1~2.6 | verify 可执行性 | ✓ |
| step-2 SC-2.5 | fields 断言 | ✓（trial_fetch 字段检查到位） |
| step-3 SC-3.1~3.7 | verify 可执行性 | ✓ |
| step-3 SC-3.6 | shell 命令正确性 | ⚠ awk/grep 管道逻辑有缺陷（见 F-1，P1） |
| step-4 SC-4.1~4.6 | verify 可执行性 | ✓ |
| step-4 SC-4.5 blocked_by | task packet 声明依赖 llm_router（step-3 产物） | ⚠ task packet required_context 缺 llm_router 接口（见 F-4） |
| step-5 SC-5.1~5.5 | verify 可执行性 | ✓ |
| step-5 §3 blocked_by | 未声明 step-3 依赖 | ⚠（见 F-5） |
| step-6 SC-6.1~6.8 | verify 可执行性 | ✓ |
| step-6 SC-6.2 | -m integration 标记与 master SC-1 verify 命令一致性 | ⚠（见 F-6） |
| 全局 | can_parallel_with 双向声明一致性 | ⚠（见 F-2） |
| 全局 | required_context 无循环依赖 | ✓ |
| 全局 | owned_paths 无跨 step 重叠 | ✓（skillpack/quality/rubric/ 与 step-4 声明在 step-3 ownership 外） |

---

## Findings

### P1 Findings（Blocker）

---

**F-1 [P1]**
- title: step-3 SC-3.6 verify 命令逻辑缺陷——awk 条件与 grep 管道会导致错误判定
- sc_refs: SC-3.6
- evidence:
  ```bash
  ls skillpack/quality/ | grep -vc adapter | awk '{exit ($1<5)}' && grep -L "^version:" skillpack/quality/*.md skillpack/quality/*.yaml 2>/dev/null | grep -v adapter | wc -l | awk '{exit ($1!=0)}' && echo PASS
  ```
  问题 1：`ls skillpack/quality/` 列出的是所有条目（含子目录 `rubric/`），`grep -vc adapter` 只过滤名字含 adapter 的行，数量统计包括子目录名；若 rubric/ 存在则计数会虚高，也可能低于 5 但 step 实际交付了 5 件文件。
  
  问题 2：`grep -L "^version:"` 对 `skillpack/quality/*.yaml` 做 glob，但 step-4 也向 `skillpack/quality/rubric/` 写量规版本文本；若 rubric/ 下有 .yaml 文件，glob 不会匹配到（`skillpack/quality/*.yaml` 只匹配一层）。此时 step-3 SC-3.6 可能误报 PASS 但实际版本字段遗漏。
  
  问题 3：`2>/dev/null | grep -v adapter | wc -l` 管道中 stderr 已重定向，grep 无 `-c` 直接计数，若 glob 展开失败（文件不存在时 bash 保留字面量）grep 输出非预期。
- impact: 命令在执行期可能对已交付 5 件文件仍返回错误退出码（false negative），导致执行者误以为 SC 未过而反复修复；或对缺少 version 字段的文件返回 PASS（false positive）。两种方向均会导致 active path 异常。
- required_fix: 将 SC-3.6 verify 命令改为明确逐件检查：
  ```bash
  cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && \
  EXPECTED="rubric.md golden-sample-refs.md negative-sample-notes.md concept-index-injection.md known-pitfalls.md" && \
  for f in $EXPECTED; do test -f "skillpack/quality/$f" || { echo "MISSING $f"; exit 1; }; done && \
  for f in skillpack/quality/rubric.md skillpack/quality/golden-sample-refs.md skillpack/quality/negative-sample-notes.md skillpack/quality/concept-index-injection.md skillpack/quality/known-pitfalls.md; do grep -q "^version:" "$f" || { echo "NO VERSION in $f"; exit 1; }; done && echo PASS
  ```
  注：具体文件名以计划实际约定为准，但必须逐件明确检查而非 glob 计数。
- verify: 替换后命令在文件齐、version 字段齐时输出 PASS；任一缺失时明确报错并非 0 退出。

---

### P2 Findings（非阻塞但应修复）

---

**F-2 [P2]**
- title: can_parallel_with 声明单向——master §5 step 表与 wave matrix 的并行关系缺乏双向验证
- sc_refs: master-plan §5, §5a
- evidence:
  - master §5 表：step-2 `can_parallel_with: step-3`；step-3 `can_parallel_with: step-2` ✓（双向到位）
  - master §5 表：step-4 `can_parallel_with: step-5`；step-5 `can_parallel_with: step-4` ✓（双向到位）
  - step-2 task packet §7：`can_parallel_with: step-3(owned_paths 无交叉)` ✓
  - step-3 task packet §7：`can_parallel_with: step-2` ✓
  - step-4 task packet §7：`can_parallel_with: step-5` ✓
  - step-5 task packet §7：`can_parallel_with: step-4(wave e3)` ✓
  
  但 step-3 §3 前置条件写道：`blocked_by: step-1（账本/故障签名 schema）; step-2 仅为运行时数据依赖——build 期以黄金样张锁定的 raw 格式 fixture 解耦`。master §5 的 `blocked_by` 列只写了 `step-1`，与 step-3 §3 正文一致。但 master §5a wave matrix e2 注解写 `raw 文件格式由黄金样张 14 步预先锁定，接口无交叉写`，没有明确说明 step-3 build 期以 fixture 解耦 step-2，新执行者看 master §5 blocked_by 列容易疏漏。
- impact: 执行者按 master 表格执行时，看到 step-3 只 blocked_by step-1，可能以为可以在 step-2 任意阶段启动 step-3，但若 step-3 的 fixture 生成需要提前锁定格式，e2 并行的实际前提不够清晰。
- required_fix: 在 master §5a wave matrix e2 行的"并行前提验证"列中，补充说明：`step-3 build 期通过黄金样张格式 fixture 完全解耦 step-2 运行时数据，fixture 来源已在 step-3 §11 Assumptions 锁定`。
- verify: master §5a e2 的"并行前提验证"列文本已包含 fixture 解耦说明。

---

**F-3 [P2]**
- title: step-1 SC-s5 verify 命令依赖 PYTHONPATH 但未显式设置，在非 pytest 环境下会 ModuleNotFoundError
- sc_refs: SC-s5（step-1 §4）
- evidence:
  ```bash
  python3 -c "from akb2.loops import paths; assert 'quarantine' in paths.QUARANTINE_SUBDIR; print('PASS')"（经 pytest pythonpath 或 PYTHONPATH=src）
  ```
  括号内注释说明"经 pytest pythonpath 或 PYTHONPATH=src"，但 verify 命令本体没有显式设置 PYTHONPATH。若直接复制命令在 shell 跑（不经 pytest），会 `ModuleNotFoundError: No module named 'akb2'`。
- impact: 执行者在 shell 层手工验证 SC-s5 时命令失败，造成误判；且 verify 命令的可执行性不满足 anti-fill 规则 A（路径/命令/断言必须直接可执行）。
- required_fix: 将 verify 命令改为：
  ```bash
  cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && PYTHONPATH=src python3 -c "from akb2.loops import paths; assert 'quarantine' in paths.QUARANTINE_SUBDIR; print('PASS')"
  ```
  或改用 pytest 调用：`python3 -m pytest tests/loops/test_paths_constants.py -q`（并补充该测试）。
- verify: 替换后命令在 src/ 存在时直接可执行并输出 PASS。

---

**F-4 [P2]**
- title: step-4 task packet required_context 缺少 llm_router 接口文档，执行者无法独立获取接口签名
- sc_refs: SC-4.1, SC-4.5（step-4 §7）
- evidence: step-4 §7 task packet 写道：
  `required_context: master-plan §3/§7、docs/main-pipeline-golden-sample.md 14 步清单、step-1 schema 三件、llm_router 接口(step-3)、黄金样张 fixture(只读)`
  
  但 `llm_router 接口(step-3)` 只是描述，没有具体文件路径或接口规格锁定。step-4 §11 Assumptions 写：`step-3 的 llm_router.py 已交付且暴露按模型族路由的 OpenAI 兼容调用接口`，但接口签名（函数名、参数、返回类型）在计划里未列出也未指向具体文档。
  
  GD 规则 D 要求 task packet 必须自包含，不得依赖对话上下文。执行者在 e3 wave 启动 step-4 时，step-3 模块仅"已存在"而接口未冻结，diff 风险真实存在（step-4 §9 风险表已承认该点）。
- impact: 执行期若 step-3 llm_router 接口与 step-4 假设不符，step-4 执行者无依据判断正确签名，需回溯对话上下文，违反 task packet 自包含原则；且 step-4 §9 防护措施仅说"不一致在 step-6 暴露"，不构成 build 期硬保护。
- required_fix: 在 step-4 task packet required_context 中补充 llm_router 公开接口最小规格（或指向 step-3 §6 Step.1 中的接口约定段落），并将接口签名作为 step-4 执行前 hard-stop 检查项添加到 §3。建议在 step-3 §6 Step.1 WHAT 中追加一行：`公开接口签名锁定在 docs/interface-contracts/llm_router.md`，并要求 step-4 required_context 引用该文件。
- verify: step-4 task packet required_context 包含具体文件路径或接口规格行，执行者无需阅读 step-3 源码即可确认调用签名。

---

**F-5 [P2]**
- title: step-5 §3 blocked_by 未声明对 step-3 llm_router 的隐式依赖（五闸⑤读 grader 结果，grader 经 llm_router）
- sc_refs: SC-5.2（step-5 §4），step-5 §3
- evidence: step-5 §3 写：
  `blocked_by: step-1（loop-ledger schema 落账依赖）；声明已完成依赖: step-4 grader 结果格式（闸⑤读其 PASS 输出，测试用 fixture 解耦，可与 step-4 并行 e3）`
  
  未声明 step-3 依赖。然而 step-5 §3 "必须的 baseline / artifact" 写 `src/akb2/gate/akb2_gate_check.py`、`lint CLI`、`loop-ledger.schema.json`，均无 step-3 产物。
  
  但 step-5 §6 Step.1 WHAT 写：`subprocess 调 src/akb2/gate/akb2_gate_check.py` 和 `lint CLI`，五闸⑤消费 grader 结果文件（格式由 fixture 钉死）。promote.py 不直接 import llm_router，build 期依赖经 fixture 解耦——这部分是合理的。
  
  问题在于 step-5 §3 的 "声明已完成依赖" 写法隐含 step-4 在 e3 期间与 step-5 并行，但实际 master §5a wave matrix 中 step-4 和 step-5 都在 e3，step-4 grader 结果文件格式的"fixture 先钉死"这一契约需要明确写在 step-5 task packet，否则执行者启动 step-5 时缺乏 step-4 grader 结果文件 fixture 的具体路径/格式来源。
- impact: step-5 执行者在 e3 启动时，若没有找到 grader 结果 fixture 的具体路径，会无法写出 test_promote_gates.py 中的五闸⑤用例，可能自行捏造格式导致与 step-4 实际产出不符。
- required_fix: 在 step-5 §3 "必须的 baseline / artifact" 中补充：`step-4 grader 结果文件格式 fixture（格式来源：step-5 §9 风险防护"闸⑤消费的结果文件格式由本 step fixture 先钉死"，具体写入 tests/quality/fixtures/grader_result_pass.json）`；并在 task packet required_context 中增加该 fixture 文件路径。
- verify: step-5 task packet required_context 中含 grader 结果 fixture 路径，执行者无需查阅其他 step 即可确定格式。

---

**F-6 [P2]**
- title: master SC-1 verify 命令与 step-6 SC-6.2 verify 命令不一致——marker 不同
- sc_refs: master SC-1, step-6 SC-6.2
- evidence:
  - master §3 SC-1 verify: `python3 -m pytest tests/quality/test_golden_replay.py -q`（无 `-m integration`）
  - step-6 §4 SC-6.2 verify: `python3 -m pytest tests/quality/test_golden_replay.py -q -m integration`（有 `-m integration`）
  - step-6 §10 测试计划也写：`python3 -m pytest tests/quality/test_golden_replay.py -q -m integration`
  - step-6 §7 task packet completion definition 写：`SC-6.2 为执行期真 API 项，build 期以测试可收集 + CLI 入口可用为提交门，出厂宣告以真 API 跑通为准`
  
  即 step-6 的策略是：build 期 `--collect-only` 验证可收集，执行期加 `-m integration` 跑真 API。但 master SC-1 的 verify 命令直接跑不含 `-m integration`，如果 test_golden_replay.py 的全部用例都标 `@pytest.mark.integration`，则 master SC-1 的命令在 build 期实际跑零测试用例，pytest 返回退出码 0（no tests collected 不是 error），结果与预期不符。
- impact: master SC-1 verify 命令在 build 期实际上是"零用例通过"，执行完成报告中可以声称 SC-1 pass 但完全没有验收到黄金样张回放——这是 active path 的静默失败风险。
- required_fix: 将 master SC-1 verify 命令与 step-6 保持一致，明确标注两阶段：
  ```
  verify (method: test, build-gate): python3 -m pytest tests/quality/test_golden_replay.py --collect-only -q
  expect: 至少 1 用例可收集（no errors）
  
  verify (method: test, execution-gate): python3 -m pytest tests/quality/test_golden_replay.py -q -m integration
  expect: exit 0
  ```
  或将 master SC-1 的 expect 改为明确说明"build 期以可收集为门、执行期以真 API 跑通为门"。
- verify: master SC-1 verify 命令的 build-gate 与 execution-gate 区分明确，不会用零用例 exit 0 虚报通过。

---

## Residual Risk（P3）

- **R-1**：step-2 SC-2.5 断言 `trial_fetch` 字段存在于 sources-ledger.jsonl，但 source_validator 是执行期真网动作，且 plan 中仅写 `--dry-run` 验证 CLI 可运行性。若真网跑遇大量失败（≥7 源不可达），sources-ledger 可能满足 ≥70 条但 trial_fetch 字段空值，影响后续 runner 质量。防护已在 master §7 风险表列出（阈值 ≥70），属已知可接受风险，但 SC-2.5 assert 语句应考虑 trial_fetch 非空约束（现有命令只检查 key 存在）。
- **R-2**：step-5 §6 Step.4 写 `finalize_promotion()` 在收尾失败时 `自动回滚到 Step.3 快照`，但 Step.3 快照逻辑在同一函数体内，若 snapshot_before_promote() 本身失败（磁盘满/权限错误），将无快照可回滚。此边界案例未在 §9 风险表覆盖，建议在 §9 补充并在 test_promote_snapshot_rollback.py 中添加快照失败的 fail-closed 用例。
- **R-3**：step-6 SC-6.6 verify 命令 `! launchctl list | grep -q com.akb2.main-pipeline` 在 macOS 上依赖 launchctl 可用，CI 环境（Linux Docker）无 launchctl，命令会失败。该 SC 的环境假设未在 §11 Assumptions 说明；若 CI 是 Linux 环境，需调整为平台条件检查或仅标注为"本地 Mac mini 验收项"。

---

<!-- gd-review-result-json:start -->
```json
{
  "reviewer": "claude_subagent_plan_reviewer",
  "review_target": "plans/gd/2026-06-12-content-quality-loop/master-plan.md (+6 step plans)",
  "review_kind": "plan",
  "review_run_status": "completed",
  "gd_review_decision": "REQUIRES_CHANGES",
  "findings": [
    {
      "id": "F-1",
      "severity": "P1",
      "title": "step-3 SC-3.6 verify 命令逻辑缺陷——awk/grep 管道导致 false positive/false negative",
      "sc_refs": ["SC-3.6"],
      "files": ["step-3-admission-distill.md"],
      "required_fix": "将 SC-3.6 verify 改为逐件明确检查文件存在性与 version 字段，消除 glob 计数歧义和子目录干扰"
    },
    {
      "id": "F-2",
      "severity": "P2",
      "title": "master §5a wave matrix e2 并行前提说明缺乏 step-3 fixture 解耦声明",
      "sc_refs": [],
      "files": ["master-plan.md"],
      "required_fix": "在 e2 并行前提验证列补充 step-3 build 期通过黄金样张格式 fixture 解耦 step-2 的说明"
    },
    {
      "id": "F-3",
      "severity": "P2",
      "title": "step-1 SC-s5 verify 命令缺少显式 PYTHONPATH 设置，非 pytest 环境下 ModuleNotFoundError",
      "sc_refs": ["SC-s5"],
      "files": ["step-1-loop7-skeleton.md"],
      "required_fix": "verify 命令前添加 PYTHONPATH=src 或改用 pytest 调用"
    },
    {
      "id": "F-4",
      "severity": "P2",
      "title": "step-4 task packet required_context 中 llm_router 接口无具体文件路径，不满足 task packet 自包含要求",
      "sc_refs": ["SC-4.1", "SC-4.5"],
      "files": ["step-4-quality-repair.md"],
      "required_fix": "在 step-3 锁定 llm_router 公开接口文档路径，step-4 required_context 引用该路径"
    },
    {
      "id": "F-5",
      "severity": "P2",
      "title": "step-5 task packet required_context 缺 grader 结果 fixture 具体路径，e3 并行时执行者无法独立确认格式",
      "sc_refs": ["SC-5.2"],
      "files": ["step-5-promote.md"],
      "required_fix": "在 step-5 §3 和 task packet 中补充 grader 结果 fixture 的具体路径"
    },
    {
      "id": "F-6",
      "severity": "P2",
      "title": "master SC-1 verify 命令缺 -m integration 标记，与 step-6 SC-6.2 不一致，build 期零用例通过虚报 PASS",
      "sc_refs": ["SC-1"],
      "files": ["master-plan.md"],
      "required_fix": "master SC-1 verify 命令区分 build-gate（--collect-only）和 execution-gate（-m integration）两阶段"
    }
  ],
  "residual_risks": ["R-1: SC-2.5 trial_fetch 字段非空未断言", "R-2: 快照本身失败时无回滚兜底", "R-3: SC-6.6 launchctl 在 Linux CI 不可用"],
  "blocker_count": 1,
  "p2_count": 5
}
```
<!-- gd-review-result-json:end -->

CHILD_DONE: claude_self_review
