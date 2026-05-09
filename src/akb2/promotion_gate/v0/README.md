# AKB2.0 Toolbox Promotion Gate v0

**状态**: 占位（Placeholder）
**创建日期**: 2026-05-08
**关联计划**: L2 Regression v1（`l2-regression-v1/README.md`）
**负责人**: 用户批准后由 Claude 执行

---

## 本阶段不执行 Promotion

本 README 为占位文件。**Promotion Gate v0 当前不执行任何操作，不修改任何工具的 `eval_level` 或 `status` 字段。**

本阶段唯一职责：声明 L2 → Promotion 的先决条件与启动方式，等待用户显式发起。

---

## 先决条件

在 Promotion Gate v0 启动之前，以下条件必须全部满足：

| # | 条件 | 验证方式 |
|---|------|---------|
| 1 | `cc-local-llm` L2 regression `verdict == "recommend_promote_L2"` | `jq '.verdict' l2-regression-v1/cc-local-llm/recommendation.json` |
| 2 | `cc-skill-stocktake` L2 regression `verdict == "recommend_promote_L2"` | `jq '.verdict' l2-regression-v1/cc-skill-stocktake/recommendation.json` |
| 3 | `automation_allowed: true` 全局 0 命中（不变量） | `grep -rn "^automation_allowed: true" AKB2.0/toolbox/tools AKB2.0/wiki/tools` |
| 4 | 两个工具 SKILL.md sha256 无漂移 | 对照 `baseline-snapshot.md` 中记录的 sha256 |
| 5 | L2 regression 已通过 canonical Codex Watch `/review code` APPROVED | `~/.claude/review-baselines/*/result-*.md` 含 `VERDICT: APPROVED` |
| 6 | 用户明确说 **"开始 promotion gate v0 plan"** | 用户消息（不接受 Claude 自主推进） |

---

## 范围

Promotion Gate v0 仅覆盖：
- `cc-local-llm`
- `cc-skill-stocktake`

不纳入本批 Promotion：
- `cc-akb-search`（观察 7 天后进入 L2 v2）
- `cc-executable-plan`、`cc-review`（已是 L2，不重复）

---

## 不做的事

- ❌ 不开启 `automation_allowed: true`（任何工具）
- ❌ 不修改 SKILL.md、.claude.json、MCP config、llama-swap config
- ❌ 不修改 L1 floor closure manifest（`2026-05-07/closure-manifest.json` 只读）
- ❌ 不执行 Automation Gate（独立计划，另开）
- ❌ 不做 Search Quality 评估（留 cc-akb-search L2 v2）

---

## 启动条件

Promotion Gate v0 的触发规范：

```
用户消息必须包含: "开始 promotion gate v0 plan"
（中英文均可，但必须是用户主动发起，不接受 Claude 自主推进）
```

触发后 Claude 将：
1. 重新验证上方全部先决条件（disk-verified，不依赖记忆）
2. 对两个工具执行 `eval_level` 升级（L1 → L2）
3. 更新五件套 `toolbox/evals` 记录，写 `eval_level: L2`
4. 写 promotion closure manifest
5. 通知用户并归档本 README

---

## 依赖文件路径

```
ai-infra/akb2-toolbox-evals/l2-regression-v1/cc-local-llm/recommendation.json
ai-infra/akb2-toolbox-evals/l2-regression-v1/cc-skill-stocktake/recommendation.json
ai-infra/akb2-toolbox-evals/l2-regression-v1/cc-local-llm/baseline-snapshot.md
ai-infra/akb2-toolbox-evals/l2-regression-v1/cc-skill-stocktake/baseline-snapshot.md
```

---

*生成于 L2 Regression v1 计划执行阶段（Step 5）。Promotion 本身由独立的 Gate v0 Plan 执行。*
