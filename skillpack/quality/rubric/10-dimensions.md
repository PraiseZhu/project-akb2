# AKB2 内容质量评分量规 v1
#
# 【机器可读源为 rubric.yaml】本文件是人读版判据说明；grader.py 的维度集/权重/阈值/
# 完整性地板/版本从同目录 rubric.yaml 读取（spec FR-010：标准与判定代码分离）。改判分
# 标准请改 rubric.yaml，本 .md 仅同步维度判据要点文字（grader 不从本文件解析维度集）。
#
# 10 维量规版本化文本。grader.py 启动时断言版本匹配，不匹配即拒跑。
# 量规维度已锁，增删需修宪程序。
#
# 版本: 1.1.0
# 锁定日期: 2026-06-13（v1.1.0 外置 rubric.yaml: 2026-06-26）

## 维度 1: 论断锚定率 (Claim Anchor Rate)

**定义**: 正文中每个指向来源的论断是否都带有 `[raw §X]` 锚点引用。

**评分标准**:
- pass: 所有以"原文指出""作者认为""文章提到"等引导的论断 100% 携带 `[raw §X]` 锚点
- fail: 存在无锚点的来源论断（裸断言），或锚点引用不存在的 § 标记

**样张基准**: 5 核心论点全带 `[raw §A]`-`[raw §J]` 锚点

---

## 维度 2: How 密度 (How Density)

**定义**: 正文中可执行步骤/代码/参数的数量是否充分。

**评分标准**:
- pass: 至少 3 个可执行步骤（如"1. 定义 trigger 类型""2. 实现 work unit selector"），步骤含具体参数或代码片段
- fail: 全篇只有 What/Why 没有 How，或 How 仅是模糊描述无具体操作

**样张基准**: 5 步骤 + params 含 max_rounds/budget_seconds/maker_model/checker_model

---

## 维度 3: How 可执行性 (How Actionability)

**定义**: How 内容是否可直接复制执行，而非仅"概念性指导"。

**评分标准**:
- pass: 至少 2 个 How 步骤是可直接执行的（含具体命令/函数签名/配置键/参数值）
- fail: 所有 How 步骤都是"应该考虑 X""建议采用 Y"等不可执行建议

**样张基准**: "Write ledger entry BEFORE next action" 是可直接执行的操作指令

---

## 维度 4: 证据诚实 (Evidence Honesty)

**定义**: 是否诚实暴露证据盲区与不确定性。

**评分标准**:
- pass: 证据地图含"未覆盖"列，且至少 1 行标注了来源未覆盖的维度
- fail: 证据地图无"未覆盖"列，或全部行标注"全部覆盖"但实际有盲区

**样张基准**: 证据地图 6 行全含未覆盖列

---

## 维度 5: 解读分离 (Interpretation Separation)

**定义**: 是否区分了 source_grounded（来源事实）与 akb_interpretation（AKB 解读）。

**评分标准**:
- pass: 每个论点明确标注 provenance（source_grounded 或 akb_interpretation），且两者比例合理（akb_interpretation 占比 ≤40%）
- fail: provenance 标注缺失，或 akb_interpretation 占比 >60%（变成读后感而非知识提炼）

**样张基准**: 5 论点标注 provenance，akb_interpretation 占 2/5

---

## 维度 6: TLDR 密度 (TLDR Density)

**定义**: TLDR 节是否用 ≤3 句话覆盖全文核心信息。

**评分标准**:
- pass: TLDR 在 3 句话以内，且每句对应一个核心论点（含锚点）
- fail: TLDR 超过 3 句话，或内容与正文核心论点无关（泛泛而谈）

**样张基准**: 3 句话，分别对应 §A/§B/§C 核心论点

---

## 维度 7: 新颖增量 (Novelty Increment)

**定义**: 本文内容相对既有知识库是否有增量。

**评分标准**:
- pass: 至少 1 个核心论点在既有 wiki 中无覆盖（或本文提供了显著更深的 How 细节）
- fail: 全文内容与既有 wiki 高度重叠，零增量

**样张基准**: Loop Engineering 概念在入库前不存在于 AKB，全新增量

---

## 维度 8: 边强度匹配 (Edge Strength Matching)

**定义**: 图谱关系表中的 strength 是否与证据强度匹配。

**评分标准**:
- pass: 每条边的 strength（strong/medium/weak）与 evidence 列内容一致，原文明示=strong，场景应用=medium，推测关联=weak
- fail: 存在 strength 与 evidence 不匹配的边，或全部标为 strong（无差别）

**样张基准**: harness=strong(原文明示) / collaboration=strong / context=medium

---

## 维度 9: Future Questions 质量 (Future Questions Quality)

**定义**: Future Questions 是否具体、可研究、与正文内容直接相关。

**评分标准**:
- pass: 2-3 条具体问题，每条指向明确的研究方向（如"X 在 Y 场景下如何适配"），非泛泛"还有哪些应用"
- fail: 问题数量 <2 或 >3，或问题全是泛泛而谈（如"这个领域还有什么发展""未来会怎样"）

**样张基准**: 3 条具体问题，分别指向 loop 在不同场景的适配

---

## 维度 10: 适用边界 (Applicability Boundary)

**定义**: 是否明确了本文知识的适用范围与不适用场景。

**评分标准**:
- pass: 明确列出 ≥2 个适用场景 + ≥1 个不适用场景（或"此方案不适用于 X 情况"）
- fail: 适用边界节为空，或只写"适用于所有场景"（无边界意识）

**样张基准**: 明确列出适用（agent pipeline）+ 不适用（简单 CRUD 脚本）