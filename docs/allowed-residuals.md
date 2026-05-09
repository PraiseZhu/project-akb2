# Allowed Residuals — Step 5c

> 生成日期：2026-05-09  
> 来源：`reports/backups/stale-refs-classification.tsv`（Step 5a 扫描结果）  
> 状态：已审批（Step 5b 中 5 个 must_change 全部修复）

---

## 背景

Step 5a 对 3 类 stale-ref 模式（ai-infra/akb2 路径 / Codex 绝对路径 / .codex/skills/cc- install-target）扫描，发现 55 条命中，分三类：

| 分类 | 数量 | 处理 |
|------|------|------|
| `must_change` | 5 | Step 5b 已全部修复 + commit `06d418a` |
| `must_keep_fixture` | 1 | 本文档记录 |
| `keep_residual` | 48 | 本文档记录 |

---

## must_keep_fixture（1 条）

这类文件中的旧路径**是测试数据本身**，不是损坏的引用。修改会破坏对应的 lint 规则测试。

| 文件 | 匹配模式 | 原因 |
|------|----------|------|
| `lint_evals/p2-lint-v0/fixtures/r04-sha-drift/toolbox/interfaces/fixture-tool.md` | pattern 2（Codex 绝对路径） | `workspace_path` 字段是 r04-SHA-drift 规则的测试输入，旧路径 IS 测试数据；改动会使 lint 测试失去正向覆盖 |

**修复条件**：仅当 r04 规则从"基于 workspace_path" 改为其他机制时才需更新。

---

## keep_residual（48 条）

按子类分组：

### A. SHA 锁定封闭文件（绝不改动）

| 文件 | 原因 |
|------|------|
| `src/akb2/closure/2026-05-07/closure-manifest.json` | SHA `0fbd53703ff58802299ba8cb04f5290e4b973c124729376dc341f862f06f90cc` 锁定；automation-gate 以此为基准校验，改动即触发 `blocked_no_rollback_target` |
| `src/akb2/closure/2026-05-07/file-sha256.txt` | 封闭快照的文件 SHA 清单，同上 |
| `src/akb2/closure/2026-05-07/handoff.md` | 封闭交接文档，历史上下文，无执行路径 |
| `src/akb2/closure/2026-05-07/README.md` | 封闭目录文档（已在 TSV 中，属文档类） |
| `src/akb2/closure/2026-05-08-p1e-delta/delta-manifest.json` | p1e-delta 清单，历史记录 |
| `src/akb2/closure/2026-05-08-p1e-delta/effective-manifest.json` | 生效清单快照 |
| `src/akb2/closure/2026-05-08-p1e-delta/regression/bad-delta-cc-review.json` | 回归 fixture 文件 |
| `src/akb2/closure/2026-05-08-p1e-delta/regression/discovery-update-fixture-good.json` | 回归 fixture 文件 |
| `src/akb2/closure/2026-05-08-p1e-delta/regression/run-evidence.md` | 回归历史运行记录 |

### B. 基准快照（baseline-snapshot）

| 文件 | 原因 |
|------|------|
| `src/akb2/automation_gate/baseline-snapshot.md` | automation-gate 基准快照；改动会破坏 gate 基准对比 |
| `evals/l2-regression-v1/cc-local-llm/baseline-snapshot.md` | cc-local-llm L2 回归基准；应保持历史状态 |
| `evals/l2-regression-v1/cc-skill-stocktake/baseline-snapshot.md` | cc-skill-stocktake L2 回归基准 |
| `evals/p1d-cc-skill-stocktake/baseline-snapshot.md` | p1d 评估基准快照 |
| `lint_evals/p2-lint-v0/baseline-snapshot.md` | lint-v0 基准快照 |

### C. 历史运行记录（run-evidence.md / outputs/）

这类文件反映**评估执行时的工程状态**，改动会扭曲历史轨迹。均无执行路径。

| 文件 | 原因 |
|------|------|
| `evals/c4a/cc-review/run-evidence.md` | C4a 阶段历史运行记录 |
| `evals/c4a/cc-executable-plan/run-evidence.md` | C4a 历史运行记录 |
| `evals/c4b/cc-akb-search/run-evidence.md` | C4b 历史运行记录 |
| `evals/c5b/cc-review/run-evidence.md` | C5b 历史运行记录 |
| `evals/c5b/cc-executable-plan/run-evidence.md` | C5b 历史运行记录 |
| `evals/p1b-l2-regression/cc-review/run-evidence.md` | p1b 历史运行记录 |
| `evals/p1b-l2-regression/cc-executable-plan/run-evidence.md` | p1b 历史运行记录 |
| `evals/p1c-cc-local-llm/run-evidence.md` | p1c 历史运行记录 |
| `evals/p1d-cc-skill-stocktake/run-evidence.md` | p1d 历史运行记录 |
| `evals/p1e-cc-akb-search-a3/run-evidence.md` | p1e A3 历史运行记录 |
| `evals/p1d-cc-skill-stocktake/outputs/preflight.json` | p1d 历史 preflight 输出 |
| `evals/p1d-cc-skill-stocktake/outputs/01-scan/raw-output.json` | p1d 扫描历史输出 |
| `evals/p1d-cc-skill-stocktake/outputs/01-scan/stderr.txt` | p1d 扫描历史 stderr |
| `evals/p1d-cc-skill-stocktake/outputs/02-quickdiff-nochange/stderr.txt` | p1d quickdiff 历史输出 |
| `evals/p1d-cc-skill-stocktake/outputs/03-quickdiff-changed/raw-output.json` | p1d quickdiff 历史输出 |
| `evals/p1d-cc-skill-stocktake/outputs/03-quickdiff-changed/stderr.txt` | p1d quickdiff 历史 stderr |
| `evals/p1d-cc-skill-stocktake/outputs/04-save-results/results.json` | p1d 保存结果历史输出 |
| `evals/p1d-cc-skill-stocktake/outputs/06-isolation-failure/6b1-err.txt` | p1d 隔离失败历史错误 |
| `src/akb2/automation_gate/outputs/current/gate-result.json` | automation-gate 最近一次运行输出（运行时产物） |
| `lint_evals/p2-lint-v0/outputs/lint-current-5-tools.json` | lint-v0 最近一次运行输出 |

### D. install-target 引用（Step 6 sync 后仍有效）

这些文件引用 `~/.codex/skills/cc-*/` 路径。该路径是 **install target**，不是旧工程位置——Step 6 sync 脚本将持续维护此镜像。引用有效，不需修改。

| 文件 | 原因 |
|------|------|
| `evals/c4a/cc-review/fixture-positive.md` | fixture 中引用 `~/.codex/skills/cc-review/SKILL.md`；Step 6 后该路径仍存在 |
| `evals/c4a/cc-review/fixture-negative.md` | 同上 |
| `evals/c4a/cc-executable-plan/fixture.md` | 引用 install-target；有效 |
| `evals/c4b/cc-akb-search/preflight.md` | 引用 install-target；有效 |
| `evals/p1e-cc-akb-search-a2/a2-preflight.md` | 历史 preflight 文档，引用 install-target |
| `evals/l2-regression-v1/cc-local-llm/run-l2.sh` | L50 行检查 install-target SHA；Step 6 后仍有效 |
| `evals/p1d-cc-skill-stocktake/run-smoke.sh` | L182/408/428 是测试断言，检查 scan 输出是否包含 install-target 路径（行为正确） |

### E. 文档 / 断言 / 案例清单（叙述性，无执行路径）

| 文件 | 原因 |
|------|------|
| `src/akb2/gate/README.md` | 文档，历史路径作为上下文说明 |
| `src/akb2/matrix/README.md` | 文档 |
| `src/akb2/promotion_gate/v0/README.md` | 文档 |
| `src/akb2/closure/README.md` | 文档（TSV 中作为 gate/README 记录，此处归一） |
| `evals/p1d-cc-skill-stocktake/assertions.md` | 测试断言描述文档 |
| `evals/p1b-l2-regression/cc-review/case-register.md` | 历史测试用例清单 |
| `evals/p1b-l2-regression/cc-executable-plan/case-register.md` | 历史测试用例清单 |
| `evals/p1b-l2-regression/cc-executable-plan/outputs/05-edge-review-handoff-plan.md` | 历史输出文档 |

---

## 汇总

| 类别 | 数量 | 修复触发条件 |
|------|------|------------|
| SHA 锁定封闭文件 | 9 | 不触发（SHA 不变原则） |
| 基准快照 | 5 | 基准重设时同步更新 |
| 历史运行记录 / outputs | 20 | 永不修改（历史证据） |
| install-target 引用 | 7 | Step 6 失效或 install-target 路径变更时 |
| 文档 / 断言 / 案例清单 | 7 | 对应模块重构时 |
| **must_keep_fixture** | **1** | r04 规则机制变更时 |
| **合计** | **49** | |

---

## Step 6 后的 stale-ref 状态预测

Step 6 实现 sync 脚本后，`~/.codex/skills/cc-*/` install-target 将持续由 Project AKB2 的 `toolbox/cc-*/` 维护。D 类 7 条引用永久有效。

Step 8 清理 Codex 原位文件后，B/C/E 类历史文件中的旧路径仍是"历史记录"，不需追溯修改。

A 类（SHA 锁定）在工程生命周期内永不触碰。
