# Search Quality v0 — Baseline Snapshot

**Generated**: 2026-05-08T00:00:00Z
**Phase**: SQ-0 Preflight

---

## cc-akb-search 当前状态（disk-verified）

| 字段 | 值 | 来源 |
|------|----|------|
| status | active | toolbox/tools/cc-akb-search.md |
| eval_level | L1 | toolbox/evals/cc-akb-search.md |
| automation_allowed | false | toolbox/tools + wiki/tools |
| last_phase | P1e-2026-05-08 | toolbox/tools/cc-akb-search.md |
| last_run_status | pass | toolbox/evals/cc-akb-search.md |
| total_runs | 1 | toolbox/evals/cc-akb-search.md |
| recent_pass_rate | 1.0 | toolbox/evals/cc-akb-search.md |

> 注：closure-manifest.json（2026-05-07 快照）显示 L0/candidate，已过时；P1e (2026-05-08) 升级后实际 L1/active。

---

## SHA256 快照（前）

| 文件 | SHA256 | 说明 |
|------|--------|------|
| `Project AKB/index/catalog.db` | `2082e717cf10a95d2de543188bdc16912112d44c1c45a1195a9d7ee219778612` | 71,360,512 bytes，无 WAL |
| `mcp-servers/akb-search-readonly/server.py` | `7a26f5cac0c796797c5b9b15f6b4aa9b5bee27a7ca1e8cb396357547a067dd31` | MCP 入口 |
| `Project AKB/scripts/core/search.py` | `57d2797cd72f832ce720694d8dca51dadfcf5626dcaff884b8b9e70887e44021` | FTS5 + 语义搜索实现 |
| `Project AKB/scripts/utils/db.py` | `c321289a651d2254b0f62b6f9c7297c909ed77ef646ec57cdd1a0a21ac7a34ae` | SQLite 连接工具 |
| `AKB2.0/toolbox/tools/cc-akb-search.md` | `daad506f59b81c446e686593f1b15f71d2b153d1eb5d188944e19291cb354c8e` | 工具卡（toolbox） |
| `AKB2.0/wiki/tools/cc-akb-search.md` | `35f0340ae5fdbd793cf5bb5db25cd4c8b16e4b73bfef198da937e00472b59258` | 工具卡（wiki） |

---

## Invariant 检查（前）

| 检查 | 结果 |
|------|------|
| automation_allowed=true 全局命中数 | 0 (PASS) |

---

## SHA256 快照（后）

*SQ-1 hard fail 路径 post-run sha256 比对（2026-05-08，`--collect-queries` 输出）*

| 文件 | SHA256 | 漂移 |
|------|--------|------|
| `Project AKB/index/catalog.db` | `2082e717cf10a95d2de543188bdc16912112d44c1c45a1195a9d7ee219778612` | 无（PASS） |

> 注：SQ-2/3/4 未运行（SQ-1 hard fail 阻断）。其余 5 个文件（server.py / search.py / db.py / 两张工具卡）均未被 SQ-1 路径访问，sha256 保持不变（等效 PASS）。

---

## 结论

SQ-0 Preflight PASS：
- cc-akb-search active/L1/automation_allowed=false ✓
- 6 文件 sha256 记录完毕
- automation_allowed=true 全局 0 命中 ✓
- 进入 SQ-1 Query Source Audit
