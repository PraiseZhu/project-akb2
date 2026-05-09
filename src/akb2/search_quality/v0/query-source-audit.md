# Search Quality v0 — Query Source Audit

**Generated**: 2026-05-08
**Phase**: SQ-1 Query Source Audit
**Verdict**: HARD FAIL — blocked_insufficient_user_queries

---

## 扫描范围

| 目录 | JSONL 文件数 | 含 akb_search 字样的文件数 |
|------|-------------|--------------------------|
| `~/.claude/projects/-Users-praise-...Claude-Code/` | 68 | 16（实际 tool_use: 13） |
| `~/.claude/projects/-Users-praise/` | 218 | 52（实际 tool_use: 0） |
| `~/.claude/projects/-...-iCloud-md-obsidian-Documents/` | 372 | 10（实际 tool_use: 0） |
| `~/.claude/projects/-...-Codex/` | 14 | 0 |
| `~/.claude/projects/-...-Project-AKB/` | 2 | 0 |

> 注：其他目录（Cursor projects、Praise-Space）含 akb_search 字样均为文本引用，非 tool_use 调用。

### Project AKB CLI 日志扫描

- `Project AKB/history/daily/`：未发现 `main.py search` / `curator.search()` 命令日志
- `Project AKB/index/work-logs/`：curator/nlm-sync 等日志不含 search 命令调用
- **internal_observed**: 0 条

---

## 全部 akb_search 工具调用（13 条）

| QID | Query | 时间戳 | 前置用户消息（截取 200 字） | 分类 |
|-----|-------|--------|--------------------------|------|
| raw-01 | `test` | 2026-05-08T10:03:18Z | "V1: 确认当前 tool list 出现 mcp__akb-search。V2: 调用 mcp__akb-search(query="test", limit=1)，返回 JSON array 即通过，空数组也算通过，响应 <5s。" | eval_observed（V2 验证测试） |
| raw-02 | `LangGraph` | 2026-05-08T10:08:21Z | "V1/V2 已 pass。继续 P1e A4：cc-akb-search L1 eval。范围：1. 只验证 cc-akb-search，不扩展到其他工具..." | eval_observed（A4 F1 fixture） |
| raw-03 | `agent eval` | 2026-05-08T10:08:22Z | （同 raw-02，A4 同批） | eval_observed（A4 F2 fixture） |
| raw-04 | `多智能体框架` | 2026-05-08T10:08:22Z | （同 raw-02，A4 同批） | eval_observed（A4 F3 fixture） |
| raw-05 | `xyzzy_q9z8w7v6u5_notexist` | 2026-05-08T10:08:23Z | （同 raw-02，A4 同批） | eval_observed（A4 F4 nonsense fixture） |
| raw-06 | `MCP` | 2026-05-08T10:08:23Z | （同 raw-02，A4 同批） | eval_observed（A4 F5 fixture） |
| raw-07 | `agent evaluation framework` | 2026-05-08T10:31:13Z | 无（idx=-1，Codex Watch 运行） | eval_observed（Codex Watch cross-eval） |
| raw-08 | `LangGraph` | 2026-05-08T10:55:55Z | "采纳你的建议，更新下计划" | eval_observed（A4 验证再跑，计划更新上下文） |
| raw-09 | `agent harness evaluation` | 2026-05-08T10:55:55Z | （同 raw-08） | eval_observed（A4 F2 variant） |
| raw-10 | `智能体评测` | 2026-05-08T10:55:56Z | （同 raw-08） | eval_observed（A4 F3 fixture） |
| raw-11 | `xyz_nonexistent_term_2026_qwerty` | 2026-05-08T10:55:56Z | （同 raw-08） | eval_observed（A4 F4 nonsense fixture） |
| raw-12 | `MCP protocol` | 2026-05-08T10:55:57Z | （同 raw-08） | eval_observed（A4 F5 variant） |
| raw-13 | `LangGraph` | 2026-05-08T13:39:25Z | 无（idx=-1，另一 session） | eval_observed（eval 上下文） |

---

## source_type 统计

| source_type | 数量 | 说明 |
|-------------|------|------|
| user_observed | **0** | 无真实用户信息需求触发的查询 |
| internal_observed | **0** | 无 CLI 命令日志可追溯的查询 |
| eval_observed | 13 | A4 fixture + V2 验证 + Codex Watch eval |

---

## 分类依据

**eval_observed 判定依据**（依据计划 source_type 枚举规则）：

1. `raw-01`（test）：前置用户消息明确要求 `mcp__akb-search(query="test")` 作为 V2 connection 验证 → eval_observed
2. `raw-02~06`：前置用户消息 "继续 P1e A4：cc-akb-search L1 eval" → 明确 A4 eval skill 上下文 → eval_observed
3. `raw-07`：idx=-1，无可追溯前置 user 消息 → unverified → 丢弃（记为 eval_observed）
4. `raw-08~12`：前置用户消息 "采纳你的建议，更新下计划" → Claude 自发再跑 A4 验证 → eval_observed（上文间接为 A4 eval 上下文）
5. `raw-13`：不同 session，idx=-1，无可追溯前置 user 消息 → unverified → 丢弃（记为 eval_observed）

**eval_observed 不计入主指标**（依据计划统计口径规则）。

---

## 门槛检查

| 检查 | 期望 | 实际 | 结果 |
|------|------|------|------|
| n_user_observed ≥ 7 | ≥7 | 0 | **FAIL** |
| n_internal_observed | ≥0（补位） | 0 | — |
| n_qualifying (user + internal) | ≥7 | 0 | **FAIL** |

---

## 结论

**HARD FAIL** — `blocked_insufficient_user_queries`

所有 13 次 akb_search 调用均来自 A4 L1 Evaluation fixture 运行或 V2 connection 验证，无真实用户信息需求触发的查询，无 CLI 命令日志可补位。

依据计划规则：`n_user_observed < 7 → exit 2（blocked_insufficient_user_queries），不强行评分`

**下一步**: 详见 `blocked_insufficient_user_queries.md`
