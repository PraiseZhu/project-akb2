# Gate `needs_repair` 根因诊断（P0-2 / SC-14）

GOAL_SOURCE: Project AKB2 — 社交日报系统
日期：2026-06-02
范围：**只查根因 + 处置建议，不改 gate 算法本体**（master-plan §NON_GOALS）
调查方式：通读 gate 算法 + pipeline 调用链 + 实跑 gate 对当前 builder 产物逐项打印 10 条硬检查

---

## 一句话根因

gate 的 **HC-08 body-structure** 检查要求正文有 H2 标题匹配正则 `^##\s+(TLDR|TL;DR|核心摘要|总览)`，但 pipeline 的 markdown builder 实际产出的首章标题是 **`## 导读`**——`导读` 不在白名单里，导致 **10 条硬检查中唯一一条 critical 检查（HC-08）恒失败**，每篇简报恒判 `needs_repair`。这是模板演进未同步白名单的**确定性单点 bug**，非阈值、非概率、非多因素。

---

## 1. gate 在哪、怎么判

- **gate 算法本体**：`src/akb2/gate/akb2_gate_check.py`（`check` 子命令）
- **pipeline 入口**：`src/akb2/briefing/pipeline.py` `run_gate()`（L194-234），subprocess 调 gate CLI，正则提 `proposed_gate_decision`
- **输入**：`staging/candidate/<date> Daily Briefing.md`，由 `build_temp_briefing_markdown(synth_en, ...)` 生成（**翻译前的英文 temp 简报**，pipeline.py L567-568）

判定树（`derive_proposal()`，akb2_gate_check.py L598-678）：
1. 9 条 critical 硬检查（HC-01~HC-09）**全过 → `active`**
2. 任一 critical 未过 → **`needs_repair`**（repair_actions = `fix:<failed_rule>`）
3. briefing 路径下 `derive_proposal` 不会产出 reject/brief_only/archive（决策树无对应分支）

---

## 2. 为什么"每次"都 needs_repair（实测证据）

用当前代码实跑 gate，10 条检查逐项结果：

```
[PASS] HC-01-CANDIDATE-ONLY    [PASS] HC-02-PAGE-STATUS
[PASS] HC-03-SOURCE-TYPE       [PASS] HC-04-TEMPLATE-TYPE
[PASS] HC-05-EVIDENCE          [PASS] HC-06-FUTURE-QUESTIONS
[PASS] HC-07-GRAPH-LINK        [FAIL] HC-08-BODY-STRUCTURE  ← 唯一失败
[PASS] HC-09-NO-AUTO-ACTIVE    [PASS] HC-10-TOOLBOX-HANDOFF
>>> proposed_gate_decision: needs_repair
>>> repair_actions: ['fix:HC-08-BODY-STRUCTURE']
```

- HC-08（akb2_gate_check.py **L523**）：正则 `^##\s+(TLDR|TL;DR|核心摘要|总览)`
- builder 实际产出首章 H2 = **`## 导读`**（pipeline.py **L448** temp builder / **L366** 终稿 builder）
- 正则比对实测：`['导读','执行摘要',...]` → `has_tldr = False`；旧 fixture `['TLDR',...]` → `has_tldr = True`

**演进脱节**：旧模板用 `## TLDR`（gate 对它判 active，见 `tests/fixtures/vault-stub/staging/candidate/2026-05-29 Daily Briefing.md`）；后来 builder 改中文骨架把 `TLDR` 换成 `导读`，但 HC-08 白名单没同步加 `导读`。

**已排除的其他可能**：gate 不检查主题数/推文数（产量多少都不影响）；除 HC-08 外其余字段在 frontmatter 硬编码齐全（L421-437）。时序方面 gate 确在翻译前用英文 temp 跑，但失败的是**章节标题名**（temp 与终稿都用 `## 导读`），与中英文字段无关——挪到翻译后也照样 fail。

---

## 3. needs_repair 后 pipeline 怎么处理

**完全不阻断。** pipeline.py 只对 `reject` early-exit（L572-575，而 briefing 路径永不产 reject）。`needs_repair` 继续走完：翻译（L578）→ 审计（L581）→ 写终稿（L585）→ copy 到 Social（L611）。

唯一影响：`needs_repair` 被写进终稿 frontmatter `proposed_gate_decision:`（L343）和正文"质量门控：**needs_repair**"展示行（L367）。简报照常入库。

真正能拦入库的是另一独立闸——译后 `audit_verdict == "fail"`（L599-606，exit 2），与 gate 无关。

**needs_repair vs reject**：reject 会跳过翻译/入库（但 briefing 路径不产 reject）；needs_repair 仅标记不阻断。

---

## 4. 根因结论 + 处置建议

**根因（确定、唯一）**：HC-08 关键词白名单与简报模板标题不同步。
- 失败点：`akb2_gate_check.py:523`（正则缺 `导读`）vs `pipeline.py:448/366`（`## 导读`）
- 决策点：`akb2_gate_check.py:649` `if not failed_critical` 不成立 → L666-678 返回 needs_repair

**这是真 bug**：gate 设计意图是"有正文摘要章节就放行"，`导读` 语义上就是 TLDR/总览，只是命名没进白名单。不是阈值问题，也不是"gate 本该常驻 needs_repair"。

**是否伤害产出**：
- 不伤害内容本身——needs_repair 不阻断，翻译/审计/入库照常。
- 但是**误导性噪音 + 真实副作用**：每篇正式简报都被打 `needs_repair` 标，下游/读者会误以为"质量待修"，而实际只是标题没对上白名单。这个 gate 维度对 briefing 已**永久失效**（永远不可能产 active）——典型 silent 退化。

**处置建议**（按合规性排序）：

| 方案 | 改哪 | 现在哪里在痛 | 合规性 | 价值 |
|------|------|-------------|--------|------|
| **B（推荐）** | pipeline.py 两处 `## 导读` → `## 总览`（`总览` 已在白名单） | 每篇简报恒被误标 needs_repair，gate 质量信号失真 | ✅ 不动 gate 算法，在 pipeline owned_paths 内 | 高 |
| A | gate HC-08 正则加 `导读` | 同上 | ❌ 改 gate 本体，P0-2 NON_GOAL 禁止；需单独授权 | 高 |
| C | 不修，接受 needs_repair 常态 | 留误导标签在每篇简报 | — | 低 |

**本 P0-2 的处置**：本 step 范围是"调查 + 给建议"，gate 修复**不在 P0-2 的 SC 内**，故本轮**不应用**任何 gate 修复，仅产出本诊断。推荐方案 B（pipeline 侧 `导读→总览`，1 行级、合规、彻底解决），留待用户在后续 step / P3-1 上线前决定是否纳入。若选 A（gate 侧加 `导读`）需显式解锁"改 gate 算法"。
