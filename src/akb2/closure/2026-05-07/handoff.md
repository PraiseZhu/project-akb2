---
title: AKB2.0 工具库 P1c/P1d/P2 收口交接文档
closure_date: 2026-05-08
phases_closed: P1c, P1d, P2-lint-v0, P2-matrix-v0
commit_mode: non_git_snapshot
status: complete
---

# AKB2.0 工具库 P1c/P1d/P2 收口交接

## 收口摘要

| 维度 | 状态 | 备注 |
|------|------|------|
| P0 gate (automation_allowed: true count) | 0 ✓ | 5 工具全 false |
| 4 active SKILL.md byte-identical | ✓ | sha256 已锁入 closure-manifest.json |
| cc-akb-search SKILL.md byte-identical | ✓ | blocked 状态不影响 hash 要求 |
| 25 五件套 sha256 已归档 | ✓ | file-sha256.txt |
| P2 lint v0 verdict | APPROVED | Review 1 + Review 2 + C-5 11/11 |
| P2 matrix v0 verdict | APPROVED | P2 review APPROVED (0 P1, 3 P2 applied) |
| A1 MCP 发现 | not_mounted | mcp__akb-search 未挂载，cc-akb-search 继续 blocked |
| 沙箱 preflight (cc-local-llm) | not_reachable | 阶段 5 需沙箱外执行 |

## 工具状态快照（收口时）

| 工具 | toolbox_status | eval_level | automation_allowed | 最后 phase |
|------|---------------|------------|-------------------|-----------|
| cc-executable-plan | active | L2 | false | P1b L2 regression |
| cc-review | active | L2 | false | P1b L2 regression |
| cc-local-llm | active | L1 | false | P1c full sequence |
| cc-skill-stocktake | active | L1 | false | P1d v3 full sequence |
| cc-akb-search | candidate (blocked) | L0 | false | P1a preflight |

## 后续任务交接

### 阶段 3：cc-akb-search 解锁

- **A1（只读发现）**：已完成 — `mcp__akb-search` 在当前 Claude Code session 未挂载
- **A2（MCP 配置）**：需独立 plan + 用户授权；涉及 `settings.json` 写入，不在本收口范围
- **B（本地 fallback）**：cc-akb-search SKILL.md 中已有本地 markdown 搜索回退逻辑，可临时使用
- **决策权**：用户

### 阶段 4：P2 Structure Lint v1

- 主要修复：r04 eval-side `workspace_version` 字段缺失时静默跳过（当前 v0 存在此 false-negative）
- 扩展：candidate 工具支持（当前 v0 只检查 current_5_tools）
- 前置条件：本次收口完成

### 阶段 5：L2 回归测试

- cc-local-llm：`http://127.0.0.1:8080/health` 在沙箱中 `not_reachable`
  → 需在沙箱外手动启动 llama-swap 后执行，或用户确认跳过沙箱
- cc-skill-stocktake：依赖 cc-local-llm L2 完成
- **automation_allowed 守门**：L2 回归不改变任何 `automation_allowed` 字段

### 阶段 6：P3 Automation Gate 设计

- 仅设计文档，0 工具在本阶段获得 `automation_allowed: true`
- 前置条件：阶段 5 完成

## 收口制品清单

```
Codex/ai-infra/akb2-toolbox-closure/2026-05-07/
├── closure-manifest.json       ← 主 manifest（工具状态 + sha256 + 下一步）
├── file-sha256.txt             ← 30 文件 sha256 清单（25 五件套 + 5 SKILL.md）
└── handoff.md                  ← 本文件

AKB2.0/docs/phase-c-reports/
├── p2-toolbox-structure-lint-v0-2026-05-07.md   ← P2 lint v0 phase report
├── toolbox-global-matrix-v0-2026-05-07.md       ← P2 matrix v0 phase report
└── toolbox-p1c-p1d-p2-closure-2026-05-07.md    ← 本收口 phase report

Codex/ai-infra/akb2-toolbox-matrix/v0/outputs/
├── global-matrix.json          ← 5 工具全局矩阵（schema_version: v0.1）
└── global-matrix.md            ← 同上，Markdown 格式
```

## 非 git 快照说明

AKB2.0 和 Codex/ 均不是 git repo。本收口包以 sha256 manifest 替代 git commit：
- `file-sha256.txt` 替代 `git log` / `git show` 的文件内容锁定
- `closure-manifest.json` 替代 commit message 的元数据
- iCloud 同步提供历史快照（通过文件版本历史可恢复）

如未来任何工具 5 件套或 SKILL.md 发生变更，需与此收口 sha256 对比以判断是否为预期变更。
