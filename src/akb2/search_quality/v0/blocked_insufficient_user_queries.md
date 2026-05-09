# Search Quality v0 — BLOCKED: Insufficient User Queries

**Generated**: 2026-05-08
**Exit Code**: 2
**Verdict**: blocked_insufficient_user_queries

---

## 阻塞原因

```
n_user_observed = 0
threshold = 7
0 < 7 → HARD FAIL
```

所有 13 次 `mcp__akb-search__akb_search` 工具调用均为 `eval_observed`（A4 L1 评估 fixture 或 V2 connection 验证），无真实用户信息需求触发的查询可统计为 `user_observed`。

---

## 数据摘要

| 指标 | 值 |
|------|----|
| 扫描覆盖天数 | 23 天（2026-04-16 至 2026-05-08） |
| 扫描 JSONL 文件总数 | 674 个（跨 5 个项目目录） |
| 实际 akb_search tool_use 调用 | 13 次 |
| user_observed | 0 |
| internal_observed | 0 |
| eval_observed | 13 |
| 门槛 | ≥7 user_observed |

---

## 根因分析

cc-akb-search（`mcp__akb-search`）于 **2026-05-08** 完成 P1e A4 5/5 评估并升至 L1 active 状态。在此之前（2026-04-16 至 2026-05-07），MCP server 处于 `not_mounted` 状态（见 closure-manifest.json `block_reason: mcp__akb-search not mounted`），用户无法调用。

因此：
- 所有已记录的 akb_search 工具调用发生在 **2026-05-08 当天**（MCP 挂载后首日）
- 当天的 13 次调用全部为评估/验证目的（A4 fixtures、V2 验证）
- 真实用户日常使用尚未积累（MCP 挂载时间太短）

这是**预期内的硬约束**，不是评估逻辑 bug。

---

## 后续操作

### 选项 A：等待观察期（推荐）

等到 `2026-05-15`（距 MCP 挂载后 7 天）再次执行 SQ-1 扫描：

```bash
bash run-search-quality.sh --collect-queries
```

预估届时可获得 5-15 条 user_observed query（依据日常使用频率）。

若 n_user_observed ≥ 7 → 继续 SQ-2/3/4。
若仍 < 7 → 再延期 7-14 天，或扩大 internal_observed 抽取规则。

### 选项 B：扩大 internal_observed 规则（可选）

当前 Project AKB history/daily 和 work-logs 中无 CLI `main.py search` 调用日志。
可考虑：
- 在 `main.py search` 中添加 query 记录到 `history/search-log.jsonl`
- 积累 7+ 条 internal_observed 后重跑（需确认每条有时间戳和 source_path）

### 选项 C：提前执行（不推荐）

若用户可手动补充 ≥7 条 expected_terms 已标注的测试 query（source_type=user_observed，基于历史知识而非 transcript），可绕过门槛。但这违背计划的"不伪造"原则。

---

## 当前不变量确认

| 检查 | 结果 |
|------|------|
| automation_allowed=true 全局命中 | 0 (PASS) |
| catalog.db sha256 | 未改（preflight 已记录，本步骤未读 db） |
| cc-akb-search status/eval_level | active/L1（不变） |
| 任何工具 automation_allowed 字段 | 全为 false（不变） |

---

## 执行门状态

依据计划执行门规则：

- **默认门**：等到 `2026-05-15` → **当前状态：未到期**
- **提前门**：用户说"覆盖观察期，立即执行" → **当前状态：已触发提前执行，但数据不足**
- **延期门**：到 5/15 user_observed 仍 < 7 → 建议再延期 7-14 天

当前执行结果：提前执行 → 触发 hard fail → 建议等到 2026-05-15 重跑。
