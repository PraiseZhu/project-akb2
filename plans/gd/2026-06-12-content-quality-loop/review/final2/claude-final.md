---
REVIEWER: claude_subagent_final
REVIEW_TARGET: plans/gd/2026-06-12-content-quality-loop/ (7 files)
REVIEW_KIND: plan
REVIEW_RUN_STATUS: completed
GD_REVIEW_DECISION: REQUIRES_CHANGES
---

# 全量审查报告 — Plan 1 (AKB2 内容质量循环)

审查时间: 2026-06-12
审查文件: master-plan.md + step-1 ~ step-6 (共 7 文件)

---

## §9 穷举义务 — SC 全覆盖状态

| SC-ID | 在哪个文件定义 | 有可执行 verify 命令 | 有路径/输出断言 | 状态 |
|-------|--------------|---------------------|----------------|------|
| SC-1 | master | 是（两档：collect-only + integration） | 是 | PASS |
| SC-2 | master | 是 | 是 | PASS |
| SC-3 | master | 是（calibration CLI + python -c 链式断言） | 是 | PASS |
| SC-4 | master | 是（test_ledger_lifeline.py） | 是 | PASS |
| SC-5 | master | 是（5 个 test + schema validate + watchdog --self-test） | 是（注: e 分两段，骨架归 step-1，适配卡实例归 step-6，均有 note 说明） | PASS |
| SC-6 | master | 是 | 是 | PASS |
| SC-7 | master | 是 | 是 | PASS |
| SC-8 | master | 是（76 源双重断言：config 数 + ledger 行数 + url 对等 + req_fields + reachable≥70） | 是 | PASS |
| SC-s1~s6 (step-1) | step-1 §4 | 是（逐条） | 是 | PASS |
| SC-2.1~2.6 (step-2) | step-2 §4 | 是（逐条） | 是 | PASS |
| SC-3.1~3.7 (step-3) | step-3 §4 | 是（逐条） | 是 | PASS |
| SC-4.1~4.6 (step-4) | step-4 §4 | 是（逐条） | 是 | PASS |
| SC-5.1~5.5 (step-5) | step-5 §4 | 是（逐条） | 是 | PASS |
| SC-6.1~6.8 (step-6) | step-6 §4 | 是（逐条） | 是 | PASS |

**穷举总结**: SC-1 ~ SC-8（master）及所有 step 本地 SC 均有可执行 verify。无遗漏空白 SC。

---

## P1 — 规则 A/C：SC verify 可执行性与输出断言

**总体**: 强。大多数 SC 的 verify 命令可直接复制运行，包含路径、输出断言、退出码期望。以下为具体发现：

### P1-F1 【P2】step-6 SC-6.6 verify 命令存在逻辑反转 bug

```bash
# SC-6.6 当前写法：
plutil -lint launchd/com.akb2.main-pipeline.plist \
  && grep -qE "launchctl (load|bootstrap)" launchd/com.akb2.main-pipeline.plist \
  && ! launchctl list | grep -q com.akb2.main-pipeline && echo PASS
```

问题：`grep -qE "launchctl (load|bootstrap)" plist文件` 检查 plist **文件内容**是否包含 `launchctl load|bootstrap` 字符串。但 plist 是 XML 格式，安装命令写在 **XML 注释** 中（`<!-- launchctl load ... -->`），grep 能匹配到。

但此处无 `-i` 忽略大小写，也无注释验证语义。若 plist 内容里只有注释而不含实际 Label，会误判。更关键的是：**step-6 §6 Step.3 实现说明写"头部注释含安装命令"，但 SC-6.6 的 verify 用 `grep -qE "launchctl (load|bootstrap)" plist` 验证——这验证了注释内容，而非 plist 语义**。若注释行被省略，此检查会静默通过失败含义相反的产物。

建议 verify 分离为：`plutil -lint plist && grep -q "launchctl" plist && ! launchctl list | grep -q com.akb2.main-pipeline && echo PASS`，并在注释中明确"grep 对象是注释行非 XML key"。

### P1-F2 【P2】step-1 SC-s4 的 grep 反断言模式可能漏检

```bash
! grep -qiE 'curl.*chat|openai|deepseek|llm' scripts/akb2-watchdog.sh
```

正则 `curl.*chat` 要求 `curl` 与 `chat` 在同一行，`deepseek` 是单词匹配但不含 `litellm` 等别名。若后续 watchdog 调用封装函数（如 `call_llm_api()`），此 grep 不会触发。建议增加：`! grep -qiE 'import.*llm|llm_router|litellm' scripts/akb2-watchdog.sh` 或在实现步骤中强制规定 watchdog 为纯 shell 禁 import Python。

### P1-F3 【P3】SC-3 verify 命令中 `all()` 过长导致可读性差，但逻辑正确

master SC-3 的 python -c 行含 5 个 assert 链，每个 assert 均有 f-string 错误信息，逻辑完整。无 blocking issue，仅记录为可读性警示。

---

## P2 — 规则 B：步骤 WHAT 动词具体性

**总体**: 强。各 step §6 实现步骤均采用"实现 X 逻辑 + WHY + VERIFY"三段式，动词具体（"实现 RSS/Atom 轮询优先"、"生成 slug--raw--date.md"、"断言 gate_decision: null"）。

### P2-F1 【P2】step-6 Step.7 WHAT 动词语焉不详

```
WHAT: 跑全量工程门:python3 -m pytest -q && ruff check src tests;红则按 finding 定点修复(不重写),修复轮上限 3
```

"按 finding 定点修复"是执行意图，不是可验证的 WHAT 动词。Step.7 的 WHAT 只应描述动作（运行 SC-6.8 的两条命令）。修复意图应移到 VERIFY 失败处理策略，不作为 WHAT 的组成部分。此问题不 blocking 但会导致 agent 执行时语义歧义。

---

## P3 — 规则 D：task packet 自包含性

**总体**: 良好，有若干缺口。

### P3-F1 【P1】step-4 task packet required_context 缺少 step-3 接口规格引用路径

step-4 §7 task packet required_context 写了：
```
docs/interface-contracts/llm_router.md（step-3 产物；接口签名：route_llm(...)）
```

这是**内联**的接口签名摘要，但缺少 `src/akb2/quality/admission.py` 与 `distill.py` 的接口说明。step-4 的 grader/repair 需要消费 distill 的候选页格式（特别是 `staging/candidate/<batch>/` 路径约定），但 required_context 中未列出 distill 的产出格式文档或 fixture 路径。

执行 agent 拿到 step-4 task packet 后，需要知道候选页的 frontmatter 结构才能实现 A 层检查（A2 checks：`gate_decision 必须 null`）。此信息只能从 step-3 §6 Step.4 推断，但 task packet 应 self-contained。

**建议**：在 step-4 required_context 中增加：`tests/quality/fixtures/candidate_page_fixture.md（step-3 产物，由 step-3 以黄金样张 distill 输出构造，定义候选页格式契约）`。

### P3-F2 【P2】step-5 task packet 中 e3 并行契约格式已内联（可接受）

step-5 task packet required_context 写了：
```yaml
格式来源：tests/quality/fixtures/grader_result_pass.json；
闸⑤消费字段：{"status": "PASS", "dimensions_checked": int, "findings": []}
```

这是一个内联的接口契约，足够执行 agent 使用。格式已自包含。无问题，仅确认符合规则 D。

### P3-F3 【P2】step-6 task packet required_context 列了"step-2/3/4/5 各模块公开接口"但未具体化

此处是模糊引用。执行 agent 无法从"step-2/3/4/5 各模块公开接口"直接知道需要 import 哪些函数。建议改为列出具体接口签名（至少 runner.py 需要调用的 4 个模块入口函数名）。

---

## P4 — 规则 E：owned_paths 跨 step 重叠与越界检查

**方法**: 逐 step 对照 master-plan §5 表格与各 step §8 边界。

| Step | master §5 owned_paths | step 本地 §7 owned_paths | 差异 |
|------|----------------------|--------------------------|------|
| step-1 | schema/loop-*.json, failure-sig, repair-adap, src/akb2/loops/, scripts/akb2-watchdog.sh, tests/loops/ | 同 master | 一致 |
| step-2 | config/main_sources.yaml, src/akb2/quality/intake/, tests/quality/test_intake*.py, tests/quality/fixtures/intake/, reports/source-validation/ | 同 master | 一致 |
| step-3 | src/akb2/quality/admission.py, distill.py, llm_router.py, skillpack/quality/(适配卡除外), docs/interface-contracts/llm_router.md, test_admission*.py, test_distill*.py | §7 表格: `src/akb2/quality/{admission,distill,llm_router}.py; skillpack/quality/(rubric/除外); ...` | **见 P4-F1** |
| step-4 | checks_structural.py, grader.py, repair.py, skillpack/quality/rubric/, test_checks*.py, test_grader*.py, test_repair*.py | 同 | 一致 |
| step-5 | promote.py, test_promote*.py, docs/vault-amendment-draft.md, snapshots/ | 同 | 一致 |
| step-6 | runner.py, calibration.py, akb2-main-pipeline.sh, plist, evals/, repair-adapter-card.yaml, test_runner*.py, test_golden_replay.py, test_negative_samples.py, reports/calibration/ | 同 | 一致 |

### P4-F1 【P1】skillpack/quality/ 的 rubric/ 归属在 master §5 与 step-3/step-4 之间存在歧义

**master §5 step-3 行**：
```
src/akb2/quality/admission.py, distill.py, llm_router.py,
skillpack/quality/(适配卡及 rubric/ 子目录除外),
docs/interface-contracts/llm_router.md, test_admission*.py, test_distill*.py
```

**master §5 step-4 行**：
```
..., skillpack/quality/rubric/, ...
```

这是明确的分割：rubric/ 归 step-4。

然而在 **step-3 §8 边界（修改）** 中写：
```
skillpack/quality/(新建，适配卡 repair-adapter-card.yaml 除外；skillpack/quality/rubric/ 子目录归 step-4 owned，本 step 仅新建 rubric/ 以外的 4 件文件)
```

这里 `skillpack/quality/` 的新建声明包含了 **rubric/ 目录的父目录**。执行 agent 在 step-3 新建 `skillpack/quality/` 时，自然会创建该目录。而 step-4 要 **新建** `skillpack/quality/rubric/`——父目录由 step-3 建，子目录 rubric/ 由 step-4 建，不是直接冲突。

**但**：step-3 §7 task packet（yaml 块）中的 owned_paths 写：
```yaml
owned_paths: [..., "skillpack/quality/(rubric/ 子目录及 repair-adapter-card.yaml 除外)", ...]
```

这个 glob 表达式在 dispatch_map 解析器中能否正确排除 rubric/ 子目录，取决于解析器实现。master-plan §5 字段别名规则仅说"forbidden/forbidden_paths/禁改语义等价"，但 owned_paths 内嵌排除注释的格式未经标准化。

**风险**：若两个并发 agent（e3 波 step-4 与 e2 波 step-3）同时持有 `skillpack/quality/` 下文件的操作权，会导致 e3 执行时 rubric/ 父目录不存在（step-3 尚未完成），或 step-3 意外建了 rubric/ 占位目录。

**建议**：master §5 step-3 的 owned_paths 改为精确文件列表（4 个具名文件），不用 glob 排除语法；step-4 owned_paths 保持 `skillpack/quality/rubric/`。

### P4-F2 【P2】tests/quality/fixtures/ 共享目录的子目录分属三个 step，无统一声明

- step-2 owned: `tests/quality/fixtures/intake/`
- step-3 owned: `tests/quality/fixtures/`（非 intake/ 下内容）
- step-5 §6 Step.6 提到"用黄金样张 6 文件结构造 fixture vault"——但未在 owned_paths 中列出 fixtures/ 相关路径

step-3 §8 边界写：
```
tests/quality/fixtures/（fixtures/intake/ 子目录归 step-2 owned，本 step 只写 admission/distill fixture，不碰 intake/ 下内容）
```

这表明 step-3 的 owned_paths 是 `tests/quality/fixtures/`（排除 `fixtures/intake/`）。逻辑清晰，不直接冲突。

但 step-5 的 §7 owned_paths 未列任何 fixtures/ 路径，而 §6 明确要"构造 fixture vault"——这是遗漏。如果 step-5 在执行时写入 `tests/quality/fixtures/vault_fixture/`，按照现有声明它越界了（step-3 owned `fixtures/`）。

**建议**：step-5 owned_paths 补充 `tests/quality/fixtures/vault_fixture/`（或具体命名的 vault fixture 目录）。

---

## P5 — §6：blocked_by / can_parallel_with 双向一致性

**检查方法**: 构建完整 DAG，验证每条声明有对应反向声明。

构建的依赖图：

```
step-1 → step-2 (step-2 blocked_by step-1 ✓; step-1 无 can_parallel_with，step-2 不在 step-1 can_parallel_with ✓)
step-1 → step-3 (step-3 blocked_by step-1 ✓)
step-2 ↔ step-3 parallel (step-2 can_parallel_with step-3 ✓; step-3 can_parallel_with step-2 ✓ 双向一致)
step-1 → step-4 (step-4 blocked_by step-1 ✓)
step-4 ↔ step-5 parallel (step-4 can_parallel_with step-5 ✓; step-5 can_parallel_with step-4 ✓ 双向一致)
step-1,2,3,4,5 → step-6 (step-6 blocked_by step-2,3,4,5 ✓)
```

### P5-F1 【P1】step-4 blocked_by 声明不包含 step-3，但实际消费 step-3 产物

step-4 §3 前置条件写：
```
blocked_by: step-1（循环 7 契约骨架）；can_parallel_with: step-5
```

step-4 §3 必须的 baseline/artifact 写：
```
src/akb2/quality/llm_router.py（step-3 产物，声明已完成依赖）；黄金样张 6 文件 fixture（只读）；...
```

注释"声明已完成依赖"暗示 step-4 在 e3 波执行时 step-3 已完成（因为 e3 = step-4+step-5 并行，而 step-3 在 e2 已完成）。

**但**：波序约束（e2 完成后才启动 e3）是执行约束，而 blocked_by 是计划声明。按照 GD 标准，如果 step-N 运行时需要 step-M 的产物，即使执行顺序已保证，blocked_by 仍应声明（以便 dispatch_map 检查）。

step-4 §3 中"声明已完成依赖"这种括号注释不是 blocked_by 字段的合规写法。

**建议**：step-4 §3 blocked_by 改为：`step-1, step-3（llm_router 接口，e3 wave 前 e2 已完成，运行时不阻塞并行）`。

### P5-F2 【P2】master §5 表格中 step-1 的 can_parallel_with 为空（"—"）

按 wave matrix：e1 = step-1 串行。这是正确的，step-1 无并行。step-1 §7 task packet 也写了 `—(e1 串行,child_agent_count=1)`。一致。✓

### P5-F3 【P2】step-5 blocked_by 写 step-1，但未声明对 step-3 的依赖

step-5 §3：
```
blocked_by: step-1（loop-ledger schema）；声明已完成依赖：step-4 grader 结果格式（闸⑤读其 PASS 输出，测试用 fixture 解耦，可与 step-4 并行 e3）
```

与 P5-F1 同类问题：step-5 五闸中的闸⑤需要 step-4 grader 结果，且五闸①②调用 gate/lint CLI（这些 CLI 操作 distill 输出的候选页，而 distill 由 step-3 产出）。

在 e3 执行时（step-4 与 step-5 并行），step-3 已完成，所以运行时无冲突。但 blocked_by 字段未声明 step-3。

注：step-5 的"声明已完成依赖"括号写法与 step-4 相同，均是对波序隐式依赖的内联注释，不符合 dispatch_map 解析器的 blocked_by 字段规范。

**建议**（与 P5-F1 同）：标准化写法，将 step-3 加入 blocked_by 并附注"e3 执行前 e2 已完成，运行时不阻塞"。

---

## 总体评估

### 优点（值得记录）

1. **SC verify 质量高**：所有 SC 均有可执行命令、具体路径、输出断言，无填表式"运行不报错"断言。
2. **fail-closed 链路设计严谨**：SC-7（提议模式开关）、SC-2（负样本拒收+零提炼调用）、diff 校验的不收敛出口、gate --write-manifest 提议路径均有独立测试钉死。
3. **owned_paths 主体无重叠**：6 个 step 的核心模块文件无直接冲突，边界条款精确。
4. **maker/checker 异族分离已在 SC 级别落实**：SC-4.5 专项测试执行顺序与兜底，FR-008 的"双不可用挂起"有对应 test_grader_fallback.py。
5. **有界循环具名常量全部落实**：MAX_REPAIR_ROUNDS, POLL_INTERVAL_SECONDS, MAX_EXTRACT_RETRIES, QUARANTINE_MULTIPLIER 均在各 step §6 中命名。
6. **launchd 不自动加载纪律**：SC-6.6 的 `! launchctl list` 断言将 constitution 自动化纪律机判化。

### 必须修复的问题汇总

| 编号 | 严重度 | 文件 | 问题摘要 |
|------|--------|------|---------|
| P1-F1 | P2 | step-6 SC-6.6 | verify grep 目标语义模糊，无法验证注释行存在的 semantic 意义 |
| P3-F1 | P1 | step-4 §7 task packet | required_context 缺候选页格式 fixture 路径，不自包含 |
| P4-F1 | P1 | step-3 §7 owned_paths | skillpack/quality/ glob 排除写法未标准化，与 step-4 rubric/ 归属存在解析歧义 |
| P4-F2 | P2 | step-5 §7 owned_paths | fixture vault 目录未列入 owned_paths，执行时越界到 step-3 owned 范围 |
| P5-F1 | P1 | step-4 §3 blocked_by | 未声明对 step-3 的运行时依赖（llm_router），仅内联注释不满足 dispatch_map 解析规范 |
| P5-F3 | P2 | step-5 §3 blocked_by | 未声明对 step-3 的运行时依赖（distill 候选页格式） |

### P1 级（必须修复，否则 dispatch / 执行会产生实际错误）

1. **P3-F1**：step-4 task packet 缺候选页格式文档引用 → 执行 agent 可能自定义候选页格式与 distill 不符
2. **P4-F1**：step-3 owned_paths 的 glob 排除 rubric/ 写法 → dispatch_map 解析器无法正确确认边界，e3 并行时可能冲突
3. **P5-F1**：step-4 blocked_by 未包含 step-3 → 若 dispatch_map 严格解析 blocked_by 字段，step-4 可能被调度到 step-3 完成前（虽然 wave 设计阻止了这点，但字段本身应完整）

---

## 修复建议（最小化改动）

### 修复 1（P3-F1 / step-4 §7）
在 required_context 列表末尾增加一行：
```
tests/quality/fixtures/candidate_page_fixture.md（step-3 以黄金样张 distill 输出构造，定义候选页 frontmatter + staging 路径格式，本 step A2 检查的输入契约）
```
若该 fixture 路径尚未在 step-3 中命名，需在 step-3 §6 Step.6（fixtures 章节）补充其命名。

### 修复 2（P4-F1 / step-3 §7 owned_paths）
将 task packet yaml 中的：
```yaml
owned_paths: [..., "skillpack/quality/(rubric/ 子目录及 repair-adapter-card.yaml 除外)", ...]
```
改为精确枚举：
```yaml
owned_paths: [
  src/akb2/quality/admission.py,
  src/akb2/quality/distill.py,
  src/akb2/quality/llm_router.py,
  docs/interface-contracts/llm_router.md,
  skillpack/quality/golden-sample-refs.md,
  skillpack/quality/negative-sample-notes.md,
  skillpack/quality/concept-index-injection.md,
  skillpack/quality/known-pitfalls.md,
  tests/quality/test_admission*.py,
  tests/quality/test_distill*.py,
  tests/quality/test_llm_router.py
]
```

### 修复 3（P5-F1/F3 / step-4 §3 + step-5 §3）
step-4 §3 blocked_by 改为：
```
blocked_by: step-1, step-3（llm_router + distill 模块接口；e3 wave 执行时 e2 已完成，运行时不产生等待）
```
step-5 §3 blocked_by 改为：
```
blocked_by: step-1, step-3（distill 候选页格式；e3 wave 执行时 e2 已完成，运行时不产生等待）
```

### 修复 4（P4-F2 / step-5 §7 owned_paths）
在 step-5 §7 task packet owned_paths 补充：
```
tests/quality/fixtures/vault_fixture/（或具体目录名，用于 test_promote_*.py 构造隔离 vault）
```
并在 step-5 §8 边界（修改）中同步添加该路径。

### 修复 5（P1-F1 / step-6 SC-6.6，可选 P2）
若保留现有 verify 结构，在 SC-6.6 说明中增加注释：
```
# note: grep 目标为 plist XML 注释行（<!-- launchctl load ... -->），plutil -lint 保证 plist 合法，
#       grep 保证安装命令注释存在，! launchctl list 保证运行时未加载
```
如需语义更严格，可改为检查注释行格式：`grep -q "<!-- .*launchctl load.*com.akb2.main-pipeline" plist`。

---

## SCOPE_CHECKED（穷举）

- SC-1: 黄金样张端到端重跑 ✓ verify 有两档命令 + 输出断言
- SC-2: 负样本拒收+零提炼调用 ✓
- SC-3: 出厂验收 20条/pass_rate/repair_rounds ✓
- SC-4: 七段生命线落账 ✓
- SC-5: 循环7骨架五件套（骨架SC-s1-s6 / 适配卡实例SC-6.7，分属 step-1/step-6，note 标注清晰） ✓
- SC-6: 全量测试+lint绿 ✓ (step-6 SC-6.8 收口)
- SC-7: 提议模式开关 fail-closed ✓
- SC-8: 76源台账 ✓ (config数+ledger行数+url对等+req_fields+reachable≥70 五重断言)
- step-1 SC-s1~s6: 全部有 verify + 可执行命令 ✓
- step-2 SC-2.1~2.6: 全部 ✓；SC-2.5 与 master SC-8 的 verify 命令对齐检查：step-2 SC-2.5 缺少 master SC-8 中的 unique_urls 去重断言和 config_urls 对等断言 → 见 P2 补充项
- step-3 SC-3.1~3.7: 全部 ✓
- step-4 SC-4.1~4.6: 全部 ✓
- step-5 SC-5.1~5.5: 全部 ✓
- step-6 SC-6.1~6.8: 全部 ✓

### SC-8 与 SC-2.5 断言对齐缺口（补充 P2 级）

master SC-8 verify 命令含 `unique_urls` 去重断言与 `config_urls` 对等断言（保证 ledger url 与 config url 集合一致）：
```python
config_urls={s.get('feed_url') or s.get('listing_url') for s in sources} - {None,''}
unique_urls={r['url'] for r in rs}
assert unique_urls==config_urls, ...
```

step-2 SC-2.5 verify 命令未含这两个断言（只检查 len==76 + req_fields + reachable≥70）。

这意味着 step-2 的本地 SC 通过，但 master SC-8 集成时仍可能因 ledger url 与 config url 不对齐而失败。step-2 SC-2.5 应是 master SC-8 的完整等价，而非子集。

**建议**：step-2 SC-2.5 verify 命令改为直接引用 master SC-8 的完整命令，或在 note 中明确"SC-2.5 为 master SC-8 的执行期完整验收版，包含 url 对等断言"。

---

## 结论

计划整体质量较高，架构决策清晰（fail-closed、maker/checker 分离、有界循环、提议模式开关），SC verify 命令密度足够，无空洞 SC。

发现 3 个 P1 级问题（task packet 不自包含、owned_paths glob 排除歧义、blocked_by 字段不完整）需修复后方可进入执行阶段。4 个 P2 级问题建议同期修复以降低执行时模糊性。

**GD_REVIEW_DECISION: REQUIRES_CHANGES**

修复范围：step-3 §7 owned_paths 精确化、step-4 §3 blocked_by + required_context、step-5 §3 blocked_by + §7 owned_paths、step-2 SC-2.5 断言对齐（可选）。无需重写计划，均为定点补充。

---
*CHILD_DONE: claude_final decision=REQUIRES_CHANGES*
