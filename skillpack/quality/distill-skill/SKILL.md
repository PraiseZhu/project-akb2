version: v1
date: 2026-06-22
status: active

# AKB2 源文档提炼 Skill（agent 工具态）

## 定位

给**能真实 Read/Grep 的 agent**（Claude / GPT-5.4 等）用的提炼协议。适用：人工兜底提炼、黄金样张制作、debug 对照、难篇精修。

**不是生产主链路**。生产批量走 distill.py。本 skill 是 agent 手动执行时的流程契约，确保产出符合黄金样张标准。

## 核心原则（先审计，再写知识）

- 先抽证据，再写知识
- 先覆盖，再精炼
- 先分清原文事实和 AKB 解读，再组织成 wiki
- 任何重要知识点没有 anchor，不能进候选页
- 任何 must_cover 证据被漏，不能自称完成

## 为什么需要 skill（不是 prompt）

纯把指令当 prompt 喂 LLM API，模型会**假装查过证据**——全靠脑内，锚点/占比/结构守不住（实测 deepseek 与 gpt-5.4 纯 API 执行，self-gate 缺陷都 ~15）。

本 skill 区别：**agent 真用 Read/Grep 建证据账本，再基于账本组织候选，输出机器可验 coverage**。确定性来自工具调用，不来自模型脑补。

## 执行协议（5 步）

### Step 1：Anchor Pass（读 raw + 锚点账本）
- Read raw 全文
- Grep `§[A-Z0-9]+`，产出 `anchor_inventory`：每个锚点 + 对应段落 + 一句话说明原文在说什么
- **raw 无 §X 锚点 → 直接失败**（raw 未锚点化，不该提炼）

### Step 2：Evidence Pass（七类证据账本，确定性 Grep）
逐类 Grep raw，产出 `evidence_inventory`，每条带 `evidence_id` + `raw_anchor` + `evidence_text`：
- `anchor_inventory`：§X 段落清单（Step 1 产出）
- `core_claim_inventory`：每段核心论点（一句）
- `operation_inventory`：API / CLI / protocol / workflow / code / config 操作
- `parameter_inventory`：参数、配置、阈值、数字、版本、限制
- `boundary_inventory`：适用、不适用、前提、风险、失败条件
- `comparison_inventory`：对比、取舍、优劣、替代方案
- `concept_inventory`：概念、对象、组件、角色、系统关系

**这是组织候选的唯一事实来源，不许跳过它从记忆发挥。**

### Step 3：Must-Cover Pass（判定必须覆盖项，给理由）
从 evidence_inventory 标 must_cover（漏了不许过）。

**默认 must_cover**：
- 标题/摘要/结论反复强调的核心机制
- 原文明确"必须/关键/核心/required/important"的内容
- 支撑主要 claim 的 API、命令、流程、配置
- 影响使用边界的限制、前提、反例、失败条件
- 原文关键数字、阈值、版本、成本、性能结论
- 与 AKB 既有知识产生明显新增量的点

**非 must_cover**：枚举列表次要项、重复例子、背景铺垫、同义改写、没进主论证链的操作 token。

must_cover = "结论成立所必需的证据"，**不是全量罗列**（否则变流水账）。

### Step 4：Knowledge Assembly（基于账本组织候选）
基于 evidence_inventory 组织 10 节候选，**每条绑 evidence_id + raw_anchor**：
- claims：3-5 条核心知识压缩，每条绑 evidence_id，**必须覆盖所有 must_cover**
- how_steps：来自 operation_inventory（标 source_grounded）；原文没有可执行操作就写不够，**不许编 command**
- graph_edges：来自 concept_inventory 共现（默认 weak，原文直接支持才 medium/strong）
- 适用边界：来自 boundary_inventory，≥2 适用 + ≥1 不适用
- evidence_gap：原文没讲清/没证明/没覆盖的缺口（来自 evidence 反查）
- TLDR：≤3 句对应核心 claim
- 新颖增量：Grep wiki/sources 同名条目是否存在，列本文相对既有库新增
- provenance：原文事实=source_grounded，agent 解读=akb_interpretation，**akb ≤40%**

**4 类内容意识**（不改 provenance 字段，但组织时区分）：source_fact（原文事实）/ source_operation（原文操作）/ akb_interpretation（解读）/ evidence_gap（缺口）。让未来 AI 读 wiki 知道哪些可当事实引用、哪些是操作、哪些是解读、哪些不能过度推断。

### Step 5：Coverage Audit（反查，不是自夸）
逐条对照 evidence_id 反查候选是否覆盖 must_cover。没覆盖→补（targeted，不整篇重抽）；补不了→fail-closed 输出 failure report。

## 输出两类产物

**A. `candidate.md`**：符合 AKB2 10 节结构 + frontmatter（`gate_decision: null`，`evidence_provenance` 按 akb 占比算）

**B. `candidate.coverage.json`**（机器可验，非口头自检）：
```json
{
  "raw_path": "...",
  "must_cover_total": 8,
  "must_cover_used": 8,
  "omitted_must_cover": [],
  "claims_without_anchor": [],
  "how_steps_without_source": [],
  "akb_interpretation_ratio": 0.28,
  "weak_edges": 2,
  "failed_closed": false
}
```
`omitted_must_cover` 非空 → 停，不准写"已完成"。

## 禁止行为（写死）

1. 禁止没有 `[raw §X]` 的 source claim
2. 禁止把 AKB 自己发明的命令写成原文操作（泛化命令黑名单：`assert maker_family`/`value_score=measure_usage`/`akb2_` 等）
3. **禁止因原文没讲清就补常识**（缺口标 evidence_gap，不用常识填）
4. **禁止把多个 weak evidence 拼成 strong conclusion**
5. **禁止用"通常/一般/可能"包装成 source_grounded**（模糊词必标 akb_interpretation）
6. **禁止只写摘要不写 boundary 和 evidence_gap**
7. 禁止 self-check 只打勾不给 evidence_id 对照表
8. 禁止预填 `gate_decision: active`

## 成功标准

- `must_cover 覆盖率 = 100%`（omitted 空）
- `source_grounded claim anchor 覆盖率 = 100%`
- `how_step 来源可追溯 = 100%`（每条绑 evidence_id）
- `akb_interpretation ≤ 40%`
- `candidate 不出现 unsupported inference`
- **AI 读 candidate 后能回答 5 问**：是什么 / 怎么用 / 边界是什么 / 证据在哪 / 缺口在哪
- `run_candidate_self_gate(candidate, raw_content) == []`

## 测试样本（4 类，验抗漏抽）

1. **Protocol/API 样本**：原文含 API 方法、调用顺序、参数。验：核心操作进 operation_inventory，how_steps 不漏关键顺序。
2. **概念/框架样本**：原文无明确命令，只有理念和边界。验：不硬编 how command，重点产 claim/boundary/evidence_gap。
3. **长文多段样本**：≥8 anchor。验：每个 anchor 进 inventory；非重点可不进正文，但 must_cover 不漏。
4. **陷阱样本**：原文有次要枚举列表 + 一个真正核心限制条件。验：不被枚举带偏，必须抽出限制条件。

## 与生产主链路的关系

agent 工具态。生产要采纳 evidence inventory 机制，把 Step 2-3 代码化（Python）由 runner 调，**不能复制本 SKILL.md 当 prompt 喂 DeepSeek**——又退回"模型假装查证据"。

## 引用
- 10 维量规：`skillpack/quality/rubric/10-dimensions.md`
- 黄金样张：`docs/main-pipeline-golden-sample.md`
- 已知坑：`skillpack/quality/known-pitfalls.md`
- 样张基线：`skillpack/quality/golden-sample-refs.md`
