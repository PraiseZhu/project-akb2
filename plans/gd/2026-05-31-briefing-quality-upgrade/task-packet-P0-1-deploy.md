# Task Packet: deploy-fixes-to-scheduler-branch

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统
TEMPLATE_KIND: gd-task-packet

> 自包含合约：本 packet 可被子 agent 单独执行；不依赖会话上下文。

---

## 1. 标识

```yaml
task_id: deploy-fixes-to-scheduler-branch
agent_role: implementer
parent_step: P0-1
parent_plan: plans/gd/2026-05-31-briefing-quality-upgrade/step-P0-1-deploy.md
created_at: 2026-05-31T10:30:00Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且第一时间被捕获
CHAIN_GOAL:   把已修复代码上线到调度链路
PHASE_GOAL:   ref master-plan §1
TASK_GOAL:    把 worktree 分支 worktree-akb2-briefing-zh-pipeline 的修复合入调度实际执行分支，全绿且关键修复点在
```

---

## 3. 依赖与并发

```yaml
blocked_by: []
can_parallel_with: []
required_context:
  - plans/gd/2026-05-31-briefing-quality-upgrade/master-plan.md
  - plans/gd/2026-05-31-briefing-quality-upgrade/step-P0-1-deploy.md
```

---

## 4. 路径权限

```yaml
owned_paths:
  - "(git 分支操作：merge worktree 分支 → 调度分支；无文件级新增)"
forbidden_paths:
  - "/Users/praise/.claude/**"
  - "调度 plist / cron / LaunchAgent"
  - "src/**（本 task 不改源码，只合并）"
```

---

## 5. 执行步骤

1. 确认调度分支：读 `~/Library/LaunchAgents/com.akb2.briefing.plist` 的 ProgramArguments → 确认跑的是主仓库 `Project AKB2/scripts/daily_briefing.sh`；`cd 主仓库; git rev-parse --abbrev-ref HEAD` 确认当前分支（预期 `feature/automation-pipeline-v2`）。
2. ff/merge：把 `worktree-akb2-briefing-zh-pipeline` 合入该分支（ff-only 优先；非 ff 用 merge commit，不 rebase 不 force）。
3. 验证全绿：合并后分支 `PYTHONPATH=src python3 -c "import pytest;pytest.main(['tests/'])"` 全 passed。
4. 验证关键修复点在：synthesis prompt 4 层路径、translate 主力 gemini、scoring `_load_fetch_tier` reserve 兼容、窗口默认 3。
5. 触发一次真跑（带 key）验证产出，或等次日 07:30 自动跑后 grep Social 文件。

---

## 6. 成功标准（SC，1:1 可验收）

- [ ] SC-1：合并后分支 `pytest tests/` 全绿
  - verify (command): pytest 全量
  - expect: all passed
- [ ] SC-2：关键修复点 grep 命中
  - verify (command): grep synthesis/translate/scoring 修复标志
  - expect: 命中
- [ ] SC-3：真跑产出 evidence=account_collector、全中文、多主题不同推文
  - verify (command): grep Social 文件
  - expect: 符合

---

## 7. Hard-stop

- 合并产生冲突且无法安全解决 → 停止，报告冲突文件，等人工决断
- 不 `git push --force` 任何默认/共享分支
- 若 plist 指向的分支与预期不符 → 停止确认，不擅自改调度
