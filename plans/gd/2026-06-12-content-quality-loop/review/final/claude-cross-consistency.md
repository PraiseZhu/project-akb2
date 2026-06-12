# Cross-Consistency Review — AKB2 Content Quality Loop

```
REVIEWER: claude_subagent_cross_consistency
REVIEW_TARGET: plans/gd/2026-06-12-content-quality-loop/ (7 files, cross-consistency)
REVIEW_KIND: plan
REVIEW_RUN_STATUS: completed
GD_REVIEW_DECISION: REQUIRES_CHANGES
```

日期: 2026-06-12
审查文件: master-plan.md + step-1 ~ step-6 (共 7 文件)

---

## 审查方法

逐一检查以下 5 个维度：
1. 字段命名跨文件一致性（config/sources-ledger 字段 slug/feed/feed_url）
2. owned_paths 无重叠（step-3 skillpack/quality/ vs step-4 skillpack/quality/rubric/）
3. verify 命令跨文件同步（master SC-3 vs step-6 SC-6.4；master SC-8 vs step-2 SC-2.5）
4. required_context 自包含（step-4/step-5/step-6 的引用路径是否真实）
5. blocked_by/can_parallel_with 双向声明完整性

---

## 发现汇总

### P1 发现（必须修复后才可声明计划完成）

#### P1-01：step-3 §8 修改边界未作 fixtures 子目录隔离（owned_paths 潜在越界）

- **位置**: `step-3-admission-distill.md` §8 "修改" 列表
- **问题**: §8 写 `tests/quality/fixtures/`（无子目录约束），master §5 步骤表明 `tests/quality/fixtures/intake/` 属 step-2 所有。step-3 的执行者若按 §8 字面理解，可能覆盖或误删 step-2 已建的 `fixtures/intake/` 目录。
- **具体文本**: step-3 §8："- `tests/quality/test_admission*.py`、`tests/quality/test_distill*.py`、`tests/quality/fixtures/`"
- **对比**: master §5 step-2 owned: `tests/quality/fixtures/intake/`；step-2 §8 边界: `tests/quality/fixtures/intake/`
- **修复**: step-3 §8 改为 `tests/quality/fixtures/`（仅含 admission/distill/llm_router 子目录，不含 `intake/`），并在 step-3 §8 "不修改" 列表中显式加入 `tests/quality/fixtures/intake/（step-2 owned）`。

#### P1-02：step-2 §7 task packet 表格 owned_paths 遗漏 `tests/quality/fixtures/intake/`

- **位置**: `step-2-intake-raw.md` §7 task packet 表格行
- **问题**: master §5 step-2 owned_paths 列包含 `tests/quality/fixtures/intake/`；step-2 §7 task packet 表格 owned_paths 列仅写 `config/main_sources.yaml, src/akb2/quality/intake/, tests/quality/test_intake*.py, reports/source-validation/`，**缺失** `tests/quality/fixtures/intake/`。dispatch_map 解析器读 task packet 表格而非 master 表，执行者将不知道自己 owns 这个 fixture 目录，可能触发越界保护或遗漏创建。
- **修复**: step-2 §7 表格 owned_paths 列补入 `tests/quality/fixtures/intake/`，与 master §5 保持一致。

#### P1-03：step-5 §7 task packet 表格 owned_paths 遗漏 `snapshots/`

- **位置**: `step-5-promote.md` §7 task packet 表格行
- **问题**: master §5 step-5 owned_paths 列含 `snapshots/`；step-5 §7 task packet 表格 owned_paths 仅列 `src/akb2/quality/promote.py, tests/quality/test_promote*.py, docs/vault-amendment-draft.md`，**缺失** `snapshots/`。step-5 的 SC-5.3 要求写入 `snapshots/<snapshot_id>/`，执行者若不知道自己 owns 该路径将导致越界阻断或静默写失败。
- **修复**: step-5 §7 表格 owned_paths 补入 `snapshots/`，与 master §5 保持一致。

---

### P2 发现（建议修复，不阻断但影响执行清晰度）

#### P2-01：master §5 step-3 owned_paths 列遗漏 `docs/interface-contracts/`

- **位置**: `master-plan.md` §5 步骤表格 step-3 行
- **问题**: step-3 §8 边界"修改"列表包含 `docs/interface-contracts/llm_router.md`（新建），master §6 边界也正确列出了 `docs/interface-contracts/`，但 master §5 step-3 owned_paths 列未包含此路径。dispatch_map 若从 master §5 扫描 owned_paths 会遗漏该目录。
- **修复**: master §5 step-3 owned_paths 列补入 `docs/interface-contracts/`。

#### P2-02：step-3 §7 YAML required_context 未引用 `docs/main-pipeline-golden-sample.md` 步骤范围

- **位置**: `step-3-admission-distill.md` §7 task packet YAML required_context
- **问题**: step-2 §7 required_context 明确写 `docs/main-pipeline-golden-sample.md 步 1-2 标准列`，而 step-3 §7 YAML required_context 列表仅写 `docs/main-pipeline-golden-sample.md`，未标注"步 3-4 标准列"。step-3 §2 Review 对齐已说明"SC 验收对照步 3-4 标准列"，但 required_context 未同步。执行者可能仅泛读样张而不聚焦步 3-4。
- **修复**: step-3 §7 YAML required_context 改为 `docs/main-pipeline-golden-sample.md（步 3-4 标准列）`。

---

## 核实通过项

以下各项已逐条对照原文验证，**一致，无问题**：

| 核实项 | 结论 |
|--------|------|
| master SC-8 verify 命令 vs step-2 SC-2.5 verify 命令（逐字符） | 完全一致 ✓ |
| master SC-3 verify 命令 vs step-6 SC-6.4 verify 命令（逐字符） | 完全一致 ✓ |
| sources-ledger 字段 `feed`（SC-8/SC-2.5）vs step-2 source_validator 实现（"feed 字段写入 feed_url 实测值"）| 命名有意设计：config 用 `feed_url`，ledger 输出字段用 `feed`，step-2 §6 Step.5 显式说明映射规则，一致 ✓ |
| config/main_sources.yaml schema 字段：step-2 用 `slug/domain/feed_url\|listing_url/rate_limit/status`，master §5 step-2 row 一致 | 一致 ✓ |
| skillpack/quality/ vs skillpack/quality/rubric/ 分离：step-3 owns `skillpack/quality/`（rubric/ 除外），step-4 owns `skillpack/quality/rubric/` | 干净分离，双方均有明确排除声明 ✓ |
| step-2 can_parallel_with=step-3，step-3 can_parallel_with=step-2（双向声明） | 双向一致 ✓ |
| step-4 can_parallel_with=step-5，step-5 can_parallel_with=step-4 | 双向一致 ✓ |
| step-3 blocked_by=step-1（§3 文本 + §7 表格 + §7 YAML 三处） | 三处一致 ✓ |
| step-4 blocked_by=step-1（§3 + §7 表格） | 一致 ✓ |
| step-5 blocked_by=step-1（§3 + §7 表格） | 一致 ✓ |
| step-4 required_context 引用 `docs/interface-contracts/llm_router.md`（step-3 产物）| 有效路径，step-3 §8 承诺新建，step-4 §7 引用 ✓ |
| step-6 required_context 引用 `docs/interface-contracts/llm_router.md` | 有效路径 ✓ |
| step-5 required_context 引用 `tests/quality/fixtures/grader_result_pass.json`（e3 并行契约） | step-5 §7 detailed，step-5 §9 风险防护说明该 fixture 由 step-5 先钉死，step-4 以其为产出契约，设计合理 ✓ |
| SC-6.6 verify 命令逻辑（grep plist 中的 launchctl 注释）| 合理：grep 检查 XML 注释中的安装命令存在性，plutil -lint 检查 XML 合法性，逻辑无误 ✓ |
| master SC-5 分解：step-1 承载 schema 骨架，step-6 收口适配卡实例+全量 verify | 有 note 明确说明，设计清晰 ✓ |
| step-3 §8 "不修改" 列明确排除其他 step owned_paths（intake/, checks_structural.py, grader.py 等） | 排除项完整 ✓ |
| master §5 step-5 owned_paths 表格已含 `snapshots/` | 主表格正确 ✓（但 step-5 §7 task packet 表格未同步，见 P1-03） |
| SC-1 被 master §5 step-4 标注"部分"、step-6 完整承接 | 无矛盾，step-4 不在自己 sc_refs 中列 SC-1，step-6 SC-6.2 完整承载 ✓ |

---

## 修复清单（穷举，对应 P1/P2）

| 编号 | 文件 | 位置 | 操作 | 优先级 |
|------|------|------|------|--------|
| F-01 | step-3-admission-distill.md | §8 修改列表第 4 条 | 改 `tests/quality/fixtures/` → `tests/quality/fixtures/`（仅 admission/distill/llm_router 子目录，**不含 intake/**），"不修改"列补 `tests/quality/fixtures/intake/（step-2 owned）` | P1 |
| F-02 | step-2-intake-raw.md | §7 task packet 表格 owned_paths 列 | 补入 `tests/quality/fixtures/intake/` | P1 |
| F-03 | step-5-promote.md | §7 task packet 表格 owned_paths 列 | 补入 `snapshots/` | P1 |
| F-04 | master-plan.md | §5 step-3 行 owned_paths 列 | 补入 `docs/interface-contracts/` | P2 |
| F-05 | step-3-admission-distill.md | §7 YAML required_context | `docs/main-pipeline-golden-sample.md` 后补注 `（步 3-4 标准列）` | P2 |

---

## 决策依据

3 项 P1 均为 task packet 字段漏项（owned_paths 遗漏），将导致执行期 dispatch_map 解析器产生歧义或 agent 写越界。两项 P2 为信息完整度问题，不影响执行安全但影响执行清晰度。全部问题有精确修复点，修改量极小。

`GD_REVIEW_DECISION: REQUIRES_CHANGES`
