# Vault 修宪草案: 不变量 3「没有自动 active」-> 五闸自动升格

> **状态**: 草案（审批权在用户）
> **目标文档**: `AKB-2.0-CONTENT-STANDARD.md`（vault 侧宪法）
> **版本**: 修宪草案 v1.0
> **日期**: 2026-06-13
> **关联工程**: `src/akb2/quality/promote.py`（五闸判定核心实现）
> **关联模块**: `AKB2_AUTO_PROMOTE` 环境变量开关（默认 0，fail-closed）

---

## 1. Sync Impact Report

### 版本变更

```
AKB-2.0-CONTENT-STANDARD.md: <当前版本> -> <下一版本>
Bump rationale: MINOR — 实质性修改不变量 3，引入五闸自动升格机制，
替换原「永远人批」条款为「五闸全过即自动 active」。
```

### 影响文件

| 文件 | 影响 | 动作 |
|------|------|------|
| `AKB-2.0-CONTENT-STANDARD.md` | 不变量 3 文本替换 | 按本标准程序修订 |
| `AKB-2.0-INTAKE-GATE.md` | 步骤 9「人批」自动化映射行需更新 | 同步修订 |
| `AKB-2.0-AUTOMATION-BOUNDARY.md` | L4 提升层描述需更新 | 同步修订 |
| `docs/main-pipeline-golden-sample.md` | 步骤 9 从「永不自动化」改为「可自动化（五闸）」 | 同步修订 |
| `src/akb2/quality/promote.py` | 五闸实现（已交付，无需改动） | 无需改动 |
| `config/vault.toml` | 无变更 | 无需改动 |

### 待同步项

- [ ] vault `AKB-2.0-CONTENT-STANDARD.md` 不变量 3 修订
- [ ] vault `AKB-2.0-INTAKE-GATE.md` 步骤 9 自动化映射更新
- [ ] `docs/main-pipeline-golden-sample.md` 步骤 9 状态更新
- [ ] 工程侧 `AKB2_AUTO_PROMOTE` 默认值从 0 翻转为 1（出厂序列最后一步，不可提前）
- [ ] 工程 constitution `docs/constitution.md` Sync Impact Report 登记 vault 修宪完成

---

## 2. 五闸定义全文

以下五闸定义与 `src/akb2/quality/promote.py` 的 `evaluate_five_gates()` 实现一一对应。
五闸**串行**执行，任一闸 fail 则候选留 staging，不升格、不写 active。

### Gate 1: 10/10 硬检查通过

- **判定方式**: subprocess 调用 `src/akb2/gate/akb2_gate_check.py check --akb-root <vault> --input <candidate>`
- **通过条件**: 10 条硬检查(HC-01 到 HC-10)全部 pass，即 `proposed_gate_decision == "active"`
- **对应标准**: INTAKE-GATE 第 4 节 7 项硬指标 + HC-08/09/10 补充规则
- **失败动作**: 候选留 staging，不升格，返回闸名 "Gate-1 (10/10 hard checks)" + 详细原因

### Gate 2: 决策置信度为 high

- **判定方式**: 解析 gate CLI 输出中的 `decision_confidence` 字段
- **通过条件**: `decision_confidence == "high"`
- **对应标准**: gate CLI 的 `derive_proposal()` 函数：9 项关键检查全过 且 graph_readiness 为 real_link 时置信度为 high
- **失败动作**: 候选留 staging，不升格，返回闸名 "Gate-2 (decision_confidence)" + 实际值

### Gate 3: 图谱就绪度为 real_link

- **判定方式**: 解析 gate CLI 输出中的 `graph_readiness` 字段
- **通过条件**: `graph_readiness == "real_link"`（至少 1 个 related_* 字段非空）
- **对应标准**: GRAPH-LINKING 第 6 节：real_link 表示候选已声明与既有知识节点的具体关系
- **失败动作**: 候选留 staging，不升格，返回闸名 "Gate-3 (graph_readiness)" + 实际值

### Gate 4: Lint 零 P0/P1 违规

- **判定方式**: subprocess 调用 `src/akb2/lint/akb2_lint.py --vault-root <vault> <file>`
- **通过条件**: exit code 0（无 P0 或 P1 级别 findings）
- **对应标准**: LINT-RULES 第 1.1 节：P0/P1 阻断入库
- **失败动作**: 候选留 staging，不升格，返回闸名 "Gate-4 (lint 0 findings)" + P0/P1 计数

### Gate 5: 评分器 PASS

- **判定方式**: 读取 step-4 grader 结果 JSON 文件
- **通过条件**: `{"status": "PASS", ...}`
- **结果格式契约**: `{"status": "PASS"|"FAIL", "dimensions_checked": int, "findings": [...]}`
- **对应标准**: 黄金样张 14 步清单步骤 5-8（gate 首跑、定点修复、复跑至 pass）
- **失败动作**: 候选留 staging，不升格，返回闸名 "Gate-5 (grader PASS)" + findings 数量

### 五闸判定流程图

```
候选页 -> [Gate 1: 10/10] -> [Gate 2: confidence=high] -> [Gate 3: graph=real_link]
       -> [Gate 4: lint 0] -> [Gate 5: grader PASS] -> 升格收尾或提议模式
```

任一闸 fail -> 候选留 staging + 落账 abort 事件。

---

## 3. 双轨试运行条款

### 3.1 快照机制

- 每条自动升格前对全部将改文件做完整快照，存入 `snapshots/<snapshot_id>/`
- 快照含 rollback-manifest.json（原路径 + SHA-256 摘要）
- 前 50 条快照保留（`SNAPSHOT_MAX_COUNT = 50`），超出自动清理
- 快照目录位于工程根（`Project AKB2/snapshots/`），非 vault 内

### 3.2 抽查条款

- 试运行期内，每批 10 条自动入库中抽查至少 2 条
- 抽查内容：active 页质量、概念回链准确性、索引摘要行完整性
- 抽查不合格率 > 20% -> 暂停自动升格，开关切回 0，启动修复

### 3.3 开关控制

- `AKB2_AUTO_PROMOTE` 环境变量：`"0"` = 提议模式（fail-closed），`"1"` = 自动升格
- 默认值 `"0"`（未设置/非 "1" 值一律按 0）
- 开关翻转前置条件：出厂校准通过 + 修宪文本应用到 vault
- 开关可在运行时翻转，无需重启管线（每次 `run_promote()` 调用时读取）

---

## 4. 回滚条款

### 4.1 技术回滚

- CLI 命令: `python3 -m akb2.quality.promote --rollback <snapshot_id>`
- 按 rollback-manifest.json 逐文件还原，验证 SHA-256 全等
- 回滚后需手动清理 active 页残留（00-Index 摘要行、概念回链等由快照覆盖）

### 4.2 修宪回滚

- 若五闸自动升格在试运行期内发现系统性质量问题：
  1. 将 `AKB2_AUTO_PROMOTE` 切回 `"0"`
  2. 对已入库内容逐条复查
  3. 必要时将 vault 修宪回滚到「没有自动 active」原文
- 回滚不删除已入库内容，仅停止新增自动升格

### 4.3 触发条件

- 自动回滚（代码层）：lint 复验非 0 -> `finalize_promotion()` 自动调用 `rollback_snapshot()`
- 手动回滚（运维层）：抽查不合格率 > 20% 或发现门控绕过 -> 运维执行 `--rollback` CLI

---

## 5. 批准程序

### 5.1 批准权

- **批准权在用户**，不在任何 agent 会话或自动化流程
- 修宪文本应用到 vault `AKB-2.0-CONTENT-STANDARD.md` 的动作须由用户显式执行
- 工程侧 `AKB2_AUTO_PROMOTE` 开关从 0 翻转为 1 须在修宪文本应用后由用户确认

### 5.2 批准前置条件

1. 出厂校准通过（step-6 SC-3: >= 90% 正样本 <= 3 修复轮走完全链）
2. 负样本 100% 拒收（step-6 SC-2）
3. 黄金样张端到端重跑通过（step-6 SC-1）
4. 本草案经用户审阅并批准

### 5.3 修订程序

- vault 宪法修订走其自身程序（`AKB-2.0-CONTENT-STANDARD.md` Governance 节）
- 工程侧本草案修订通过 `docs/vault-amendment-draft.md` 更新
- 两边修订互相在 Sync Impact Report 中登记（对齐工程 constitution 第 1.1.0 版 Governance 节）

### 5.4 出厂序列

修宪批准后的出厂序列（step-6 执行）：

1. 校准通过 -> 校准报告确认 promotion_mode=active + amendment_status=applied
2. 将本草案修宪文本应用到 vault `AKB-2.0-CONTENT-STANDARD.md`
3. 设置 `AKB2_AUTO_PROMOTE=1`
4. 跑 SC-1 execution-gate-active 验证门
5. 跑 SC-3 出厂终态校准（promotion_mode=active + amendment_status=applied）

---

> **草案签署**: 本草案由 step-5 promote 模块实现时起草，作为 vault 修宪的技术依据。
> 最终批准权在用户，修宪文本不直接写 vault。