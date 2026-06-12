# Claude Self-Review (Fresh Round)

```
REVIEWER: claude_subagent_plan_reviewer_fresh
REVIEW_TARGET: plans/gd/2026-06-12-content-quality-loop/ (7 files, fresh round)
REVIEW_KIND: plan
REVIEW_RUN_STATUS: completed
GD_REVIEW_DECISION: REQUIRES_CHANGES
```

---

## 修复验证（前一轮已修复项）

### F-1：step-3 SC-3.6 verify 命令逐件检查
**状态：✅ VERIFIED**

SC-3.6 verify 命令已改为两轮 `for` 循环：第一轮逐件 `test -f`，第二轮逐件 `grep -q "^version:"`，任一失败即 `exit 1`，最终 `echo PASS`。满足规则 A（逐件检查，有具体路径与输出断言）。

### F-R3-C1：reports/calibration/ 三处补全
**状态：✅ VERIFIED**

- master §5 step-6 owned_paths 含 `reports/calibration/` ✅
- master §6 修改清单含 `reports/calibration/**（SC-3 出厂验收产物，calibration.py 写出）` ✅
- step-6 §7 task packet owned_paths 含 `reports/calibration/` ✅

---

## 全量扫描 Findings

### F-NEW-1（P1）：step-3 与 step-4 对 skillpack/quality/rubric 路径存在边界重叠

**文件：** step-3-admission-distill.md §8 / step-4-quality-repair.md §7 §8

**问题描述：**

step-3 §8 修改节声明 owned `skillpack/quality/`（整目录），Step.5 WHAT 写"写 5 件版本化文件：rubric 量规 v1 / ..."，即 `skillpack/quality/rubric.md`（文件）。

step-4 §7 task packet owned_paths 列 `skillpack/quality/rubric/`（子目录），§8 修改节写 `skillpack/quality/rubric/`（量规版本化文本，新建），Step.2 WHERE 写 `skillpack/quality/rubric/`（目录）。

两步的 owned_paths 产生子路径重叠：step-3 声明 own 了 `skillpack/quality/`，step-4 的 `skillpack/quality/rubric/` 是其子路径。step-4 §8 不修改节没有排除 step-3 already-owned 的文件，也没有说明本 step 接管 `rubric/` 子目录的前提（step-3 是否只建文件而非目录）。

执行者（并行 e3 wave 中 step-4 会独立运行）无法从计划文本中确定：rubric 到底是 `skillpack/quality/rubric.md`（step-3 文件）还是 `skillpack/quality/rubric/`（step-4 目录）。若两者都被执行，将产生路径冲突或 step-3 产物被 step-4 覆盖。

**修复建议：**
1. 若设计意图是 step-3 建文件（`rubric.md`），step-4 建目录（`rubric/` 内放量规分维度文件）：则 step-3 §8 应精确列出所 owned 文件（不泛写 `skillpack/quality/`），step-4 §7 §8 需在不修改节中明确排除 step-3 建立的 5 件文件；
2. 若意图是 step-4 接管 rubric：则 step-3 应改 "rubric 量规 v1 内容写入 `skillpack/quality/rubric.md`" 为 "量规文本结构由 step-4 决定，本 step 只交付其余 4 件文件"，并从 step-3 owned_paths 中移除 rubric 相关条目；
3. 两文件同步修改后，rubric 路径一致性必须在 step-4 SC-4.2 的 verify 命令（`skillpack/quality/rubric` 读取路径）与 step-3 SC-3.6 的 verify 命令（文件列表）保持同步。

---

### F-NEW-2（P2）：step-6 §7 task packet required_context 泛指模块接口，缺 llm_router 接口契约具体路径

**文件：** step-6-runner-calibration.md §7

**问题描述：**

step-3 Step.1 WHAT 中明确写："接口规格同步锁定在 `docs/interface-contracts/llm_router.md`（本 step 交付时同步写出）"。step-4 §7 task packet required_context 已正确引用 `docs/interface-contracts/llm_router.md`。

但 step-6 §7 task packet required_context 只写 "step-2/3/4/5 各模块公开接口"，没有具体路径，执行者无法确定从哪里获取 llm_router 接口签名（runner.py 集成 calibration 时需调用 LLM 路由）。规则 D 要求 task packet 自包含。

**修复建议：**
在 step-6 §7 required_context 中补充 `docs/interface-contracts/llm_router.md`（step-3 产物），以及 step-5 promote 模块公开入口（`src/akb2/quality/promote.py --help` 或接口注释），不能仅以泛指代替具体引用。

---

### F-NEW-3（P3）：step-2 §7 task packet owned_paths 遗漏 tests/quality/fixtures/intake/

**文件：** step-2-intake-raw.md §7

**问题描述：**

step-2 §8 修改节明确列出 `tests/quality/fixtures/intake/`（全 fixture 单测所需本地 HTML/RSS fixture 目录），但 §7 task packet 表格 owned_paths 列只有 `tests/quality/test_intake*.py`，未包含 `tests/quality/fixtures/intake/`。

Task packet 是 dispatch 时执行者获取边界约束的唯一来源（规则 D）。若 §7 与 §8 不一致，执行者可能认为 fixture 目录不在本 step 职责范围内而不建立，导致测试无法运行。

**修复建议：**
在 step-2 §7 owned_paths 中补充 `tests/quality/fixtures/intake/`，与 §8 保持一致。

---

## 已确认无问题项

- master SC-1~8 全部有可执行 verify 命令 + 输出断言 ✅
- master §5 波次矩阵（wave e1-e4）owned_paths 不重叠检查（step-3/step-4 rubric 路径问题已单独列为 F-NEW-1）✅
- step-1 全部 SC verify 有命令 + 期望输出，SC-s4 有 LLM 反断言 ✅
- step-2 SC-2.1~2.6 verify 命令完整，SC-2.5 与 master SC-8 关系正确说明 ✅
- step-3 SC-3.1~3.7 verify 命令完整，SC-3.6 逐件检查已修复 ✅
- step-4 SC-4.1~4.6 verify 命令完整，反填表用例（SC-4.3）有独立 test 文件 ✅
- step-5 SC-5.1~5.5 verify 命令完整，SC-5.5 使用 grep 循环检查五节 ✅
- step-6 SC-6.1~6.8 verify 命令完整，SC-6.6 含 `! launchctl list` 未加载断言 ✅
- step-1 §7 task packet 自包含，required_context 明确 ✅
- step-3 §7 task packet 含 `docs/interface-contracts/llm_router.md` 输出声明 ✅
- step-4 §7 task packet 已引用 `docs/interface-contracts/llm_router.md` ✅
- step-5 §7 task packet grader 结果格式契约具体说明 ✅
- 所有 step blocked_by / can_parallel_with 声明与 master wave matrix 一致 ✅
- 修宪草案不直接写 vault，批准权留用户，多处明确声明 ✅
- launchd 不自行注册，SC-6.6 未加载断言钉死 ✅
- constitution P1 有界循环（具名常量）：step-1 ledger 有限重试、step-4 MAX_REPAIR_ROUNDS=3、step-6 POLL_INTERVAL_SECONDS ✅

---

## 总结

| Finding | 严重级别 | 文件 | 状态 |
|---------|---------|------|------|
| F-1（前轮）SC-3.6 逐件 verify | P1 | step-3 | ✅ 已修复 |
| F-R3-C1（前轮）reports/calibration/ 三处补全 | P1 | master/step-6 | ✅ 已修复 |
| F-NEW-1 step-3/step-4 rubric 路径边界重叠 | P1 | step-3 §8 / step-4 §7 §8 | ❌ 需修复 |
| F-NEW-2 step-6 required_context 缺 llm_router 接口路径 | P2 | step-6 §7 | ❌ 需修复 |
| F-NEW-3 step-2 §7 owned_paths 遗漏 fixtures 目录 | P3 | step-2 §7 | ❌ 建议修复 |

**GD_REVIEW_DECISION: REQUIRES_CHANGES**

F-NEW-1（P1）必须修复后重新 review。F-NEW-2 P2 建议同批修复。F-NEW-3 P3 可选但推荐同批处理避免遗漏。
