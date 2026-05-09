# Search Quality v0 — Run Evidence

**生成时间**：2026-05-08
**计划**：Search Quality Enhancement v0（cc-akb-search 搜索质量基线 + 建议）
**总体状态**：BLOCKED — blocked_insufficient_user_queries（SQ-1 hard fail）

---

## 自测（Self-Test）

```
[self-test] Running SQ-1 gate logic tests...
[PASS] catalog.db exists
[PASS] server.py exists
[PASS] transcript dir exists
[PASS] automation_allowed=true global 0 hits
[PASS] output dir writable

Self-test: 5 passed, 0 failed
exit=0
```

## SQ-0 Preflight

```
[preflight] Checking upstream evidence...
[preflight] PASS
[snapshot] catalog.db=2082e717cf10a95d2de543188bdc16912112d44c1c45a1195a9d7ee219778612
```

所有 upstream 文件均存在，sha256 前快照已记录（详见 baseline-snapshot.md）。

## SQ-1 Query Source Audit

```
[invariant] automation_allowed=true global count: 0 (PASS)
[preflight] Checking upstream evidence...
[preflight] PASS
[snapshot] catalog.db=2082e717cf10a95d2de543188bdc16912112d44c1c45a1195a9d7ee219778612
[SQ-1] Scanning transcripts for mcp__akb-search__akb_search tool calls...
[SQ-1] Total calls: 13
[SQ-1] user_observed: 0
[SQ-1] eval_observed: 13
[SQ-1] threshold: 7
[SQ-1] HARD FAIL: n_user_observed (0) < 7
[SQ-1] catalog.db sha256 unchanged: 2082e717cf10a95d2de543188bdc16912112d44c1c45a1195a9d7ee219778612
[ERROR] Hard fail: insufficient user_observed queries (< 7). See blocked_insufficient_user_queries.md
```

```
exit=2 (blocked_insufficient_user_queries)
```

## 当前阶段产出文件

| 文件 | 状态 | 说明 |
|------|------|------|
| `baseline-snapshot.md` | ✓ 完成 | SQ-0 sha256 前快照 |
| `query-source-audit.md` | ✓ 完成 | 13 条调用全量审计，全部 eval_observed |
| `blocked_insufficient_user_queries.md` | ✓ 完成 | hard fail 原因 + 后续选项 |
| `run-search-quality.sh` | ✓ 完成 | 只读 runner，含 self-test 5/5 PASS |
| `queries.jsonl` | — 未生成 | SQ-1 hard fail 阻断 |
| `baseline-results.jsonl` | — 未生成 | SQ-2 blocked |
| `mcp-cli-parity.json` | — 未生成 | SQ-2 blocked |
| `metrics.json` | — 未生成 | SQ-3 blocked |
| `recommendation.md` | — 未生成 | SQ-4 blocked |

## 不变量确认

| 检查 | 结果 |
|------|------|
| automation_allowed=true 全局命中 | 0 (PASS) |
| catalog.db sha256（前） | `2082e717cf10a95d2de543188bdc16912112d44c1c45a1195a9d7ee219778612` |
| catalog.db sha256（后） | `2082e717cf10a95d2de543188bdc16912112d44c1c45a1195a9d7ee219778612`（PASS — post-sha guard 在 hard fail 路径上已验证） |
| cc-akb-search status | active |
| cc-akb-search eval_level | L1 |
| cc-akb-search automation_allowed | false |
| 5 工具 active/L1 状态 | 未改动 |

## 根因摘要

cc-akb-search 于 2026-05-08 P1e 完成后首次挂载 MCP server。在此之前 23 天（2026-04-16 至 2026-05-07）均处于 `not_mounted` 状态，无用户调用可积累。

挂载首日的 13 次调用：
- 7 次为 A4 L1 Evaluation fixture（F1~F5 × 2 轮）
- 2 次为 V2 connection 验证测试
- 2 次为无可追溯前置用户消息（unverified，丢弃）
- 1 次为 Codex Watch cross-eval

**结论**：无可统计的 user_observed query，达不到 n ≥ 7 门槛。这是预期内的硬约束，不是评估逻辑 bug。

## 后续操作

推荐等到 `2026-05-15`（MCP 挂载后 7 天）重跑 SQ-1：

```bash
bash run-search-quality.sh --collect-queries
```

届时预计 user_observed ≈ 5–15 条（依日常使用频率）。

详见 `blocked_insufficient_user_queries.md` §后续操作。

## Review 状态

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_COMMAND：canonical Codex Watch `/review code`
- REVIEW_STATUS：待执行
