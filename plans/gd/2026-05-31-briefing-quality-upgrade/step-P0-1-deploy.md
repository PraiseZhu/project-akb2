# Step P0-1: 上线现有修复（合并到调度分支）v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-step-plan

日期：2026-05-31
状态：draft
负责人：Claude 执行

---

## 1. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且第一时间被捕获
CHAIN_GOAL:   把已修复代码上线到调度链路
PHASE_GOAL:   ref master-plan §1
TASK_GOAL:    把 worktree 分支已验证的 8 项修复合入调度实际执行的分支，使 07:30 自动跑用上新代码
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`app_code`
- REVIEW_FOCUS：合并无冲突丢失; 调度分支指向正确; 上线后产出符合预期
- Domain notes：调度 plist 跑 `Project AKB2/scripts/daily_briefing.sh`，用主仓库 checkout（当前在 `feature/automation-pipeline-v2 @ 64b1347`），落后 worktree 40+ commit。

---

## 3. 前置条件

- blocked_by：`—`
- 必须 artifact：worktree 分支 `worktree-akb2-briefing-zh-pipeline`（全仓库 232 passed）
- Hard-stop：合并若产生冲突且无法 ff → 停止报告，不强推；不 `push --force` 默认分支

---

## 4. 成功标准（SC）

- [ ] SC-P0-1a：worktree 分支合入 `feature/automation-pipeline-v2`，合并后该分支 `pytest tests/` 全绿
  - verify (command): `git merge` 后 `python3 -c "...pytest.main(['tests/'])"`
  - expect: all passed
- [ ] SC-P0-1b：合并后该分支 synthesis.py 用 4 层 prompt 路径、translate 主力 gemini、scoring reserve 配置修复均在
  - verify (command): `grep -c "parent.parent.parent.parent /" src/akb2/briefing/synthesis.py` 等关键修复点
  - expect: 命中
- [ ] SC-P0-1c：下次 07:30（或手动触发一次）自动跑产出 evidence_paths=account_collector、全中文、多主题不同推文
  - verify (command): grep 次日 Social 文件
  - expect: 符合

---

## 5. 非目标

- 不改调度时间 / plist EnvironmentVariables（除非发现 key 注入问题，另立）
- 不在本 step 做语义改造（P1）

---

## 6. 边界与失败模式

- 合并冲突 → 停止报告，人工决断
- 调度分支若不是 `feature/automation-pipeline-v2`（用户后续可能改）→ 先确认 plist 指向再合
- 注意：plist EnvironmentVariables 不含 `AI_GATEWAY_API_KEY`，依赖 `~/.akb2-secrets.sh`；旧代码查 `OPENROUTER_API_KEY`，新代码查 `AI_GATEWAY_API_KEY`，合并后即对齐（这正是 06-01 残报的根因之一）
