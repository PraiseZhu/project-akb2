---
title: AKB2.0 工具库全局矩阵
schema_version: v0.1
collected_at: 2026-05-08T11:10:05Z
scope: current_5_tools
p2_review2_artifact: present
---
# AKB2.0 工具库全局矩阵 v0.1

收集时间：2026-05-08T11:10:05Z

## 矩阵总览

| 工具 | wiki_status | toolbox_status | eval_level | automation_allowed | backlink_status | workspace_version | blocking_reason | next_action |
|------|------------|----------------|------------|-------------------|-----------------|-------------------|-----------------|-------------|
| cc-executable-plan | active | active | L2 | false | linked | 5af73fdadd91 | n/a | stable — L2 regression on schema change |
| cc-review | active | active | L2 | false | linked | 592cf3ed4b2c | n/a | stable — L2 regression on schema change |
| cc-local-llm | active | active | L1 | false | linked | 3a31ebe4f593 | n/a | stable — promote to L2 when ready |
| cc-skill-stocktake | active | active | L1 | false | linked | df16be83df67 | n/a | stable — promote to L2 when ready |
| cc-akb-search | active | active | L1 | false | active | b669380de8a0 | n/a | wait for mcp__akb-search mount then run L1 eval + upgrade to active |

## 关键区分：wiki_status vs toolbox_status

- **wiki_status**：知识页面发布状态（来源：`wiki/tools/<slug>.md` frontmatter `status`）
- **toolbox_status**：工具合约激活状态（来源：`toolbox/tools/<slug>.md` frontmatter `status`）
- **两者可以不一致**：`cc-akb-search` wiki=active（知识页面已发布）但 toolbox=candidate（合约未激活，blocked per P1a — mcp__akb-search 未 mount）

## 汇总

| 维度 | 数量 | 工具 |
|------|------|------|
| toolbox active | 4 | cc-executable-plan, cc-review, cc-local-llm, cc-skill-stocktake |
| toolbox blocked candidate | 1 | cc-akb-search |
| eval_level L2 | 2 | cc-executable-plan, cc-review |
| eval_level L1 | 2 | cc-local-llm, cc-skill-stocktake |
| eval_level L0 | 1 | cc-akb-search |
| automation_allowed true | 0 | P0 gate 清洁 |
| backlink linked | 4 | cc-executable-plan, cc-review, cc-local-llm, cc-skill-stocktake |
| backlink pending | 1 | cc-akb-search（预期；mcp__akb-search 未 mount） |

## P2 Review 2 证据

`p2_structure_lint_review2_artifact`: **present**

路径：`~/.claude/review-baselines/a3f782c15d0e/result-20260507T125000Z.md`

VERDICT: APPROVED（P1=0, P2=1 non-blocking, P3=2; 5 scope areas PASS）

## 矩阵说明

本矩阵为只读快照。收集脚本 `collect-global-matrix.sh` 不修改任何工具 5 件套 / SKILL.md / bundled scripts。
SHA256 基线来自 P2 lint v0 §1 baseline（2026-05-07）。
