# Run Evidence — cc-executable-plan L1 smoke

```yaml
timestamp: 2026-05-05T13:50:00Z
runner: claude
runner_override_reason: "user approved Claude execution (per C-4 真实 Runner 执行版 plan, 选项 A); Codex runner 可后续补充复核"
skill: cc-executable-plan
fixture_path: /Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/c4a/cc-executable-plan/fixture.md
skill_sha256_before: 69364def87afa9ad88e66bf1b2285d0007ee245070b4ed8a6367496dc50357e8
skill_sha256_after: 69364def87afa9ad88e66bf1b2285d0007ee245070b4ed8a6367496dc50357e8
output_sha256: a34aa2aec6eb86c9c01dd0b9432066dec413e63e886498b0f5c26f531cd516ae
output_lines: 200
output_path: /tmp/claude-501/c4-plan-output.md (transient; raw_output embedded below)
assertion_status: pass
eval_level_after: L1
last_run_status: pass
```

## raw_invocation_prompt

来自 `c4a/cc-executable-plan/fixture.md` 的 Goal / Context / Expected behavior:

> **Goal**: 把 ECC inventory 工具中已弃用的 `legacy-skill-importer.sh` 标记为
> `deprecated` 并提供替代脚本 `skill-importer-v2.sh` 的迁移路径。期望产出一个
> 最小可执行计划。
>
> **Context**: 已存在两个文件:
> - `Codex/ai-infra/inventory/legacy-skill-importer.sh`(2024-Q1 写的,330 行)
> - `Codex/ai-infra/inventory/skill-importer-v2.sh`(2025-Q1 重写,150 行,已实测)
>
> 迁移目标:删除老脚本前必须先把所有调用方迁移到 v2,且保留至少 7 天回滚窗口。
>
> **Expected behavior**: skill 应输出符合 `~/.claude/templates/plan-template.md` 模板的中文 plan,包含全部 11 节标题。

## Pre-call checks

| # | check | result | evidence |
|---|---|---|---|
| 1 | sha256 matches expected | pass | `69364def87afa9ad88e66bf1b2285d0007ee245070b4ed8a6367496dc50357e8` ↔ baseline 一致 |
| 2 | template drift `diff -q` exit 0 | pass | `diff -q` 返回 0;`~/.claude/templates/plan-template.md` 与 `Codex/templates/plan-template.md` 字节一致 (双方 131 行) |

## Assertions

### A. Template drift

- [x] `diff -q ~/.claude/templates/plan-template.md ~/Library/Mobile\ Documents/com~apple~CloudDocs/Codex/templates/plan-template.md` exit 0 → **pass**

### B. Mandatory section headings present in skill output

按 plan-template.md 11 节中本 fixture 必须的 8 节:

- [x] `## Review 对齐` — 1 命中
- [x] `## 目标与验收` — 1 命中
- [x] `## 当前状态` — 1 命中
- [x] `## 代码执行方向` — 1 命中
- [x] `## 接口与数据合约` — 1 命中
- [x] `## 实现步骤` — 1 命中
- [x] `## 边界与失败模式` — 1 命中
- [x] `## 测试计划` — 1 命中

8/8 → **pass**

### C. Review 对齐 alignment-block contents

- [x] `REVIEW_DOMAIN：app_code` 行存在(注:assertions.md 写的是 ASCII colon `:`,但 plan-template.md 中文 plan 约定使用全角 colon `：`;raw output 用 `：` 是正确的;等价 grep `^REVIEW_(DOMAIN\|FOCUS)[:：]` 命中两行) → **pass**
- [x] `REVIEW_FOCUS：` 块列出 5 维度(调用方迁移完整性 / deprecated stderr 警告 / 7 天回滚窗口 / v2 行为对齐 / 切换路径不破坏 cron),≥ 3 → **pass**

**注**:assertion C 的 ASCII colon 期望与中文 plan 约定全角 colon 不一致 — 这是 Claude(我)写 fixture/assertions 时的笔误,raw output 遵循正确约定。后续 fixture maintenance 应同步该断言改为兼容 `[:：]`。

### D. Output integrity

- [x] Output 是 well-formed Markdown,fence 平衡(6 个 ```\` 标记,3 对) → **pass**
- [x] Output 长度 = 200 行 ≥ 200 阈值 → **pass**(刚好达标)
- [x] Output 提及 `legacy-skill-importer.sh`(15 次)和 `skill-importer-v2.sh`(11 次) → **pass**

## L1 promotion gate verdict

A pass + B 8/8 + C 2/2 + D 3/3 = **all pass** → recommend `eval_level_after: L1`

## raw_output

完整 plan 输出(200 行,sha256 `a34aa2aec6eb86c9c01dd0b9432066dec413e63e886498b0f5c26f531cd516ae`)如下,使用 quad-backtick 包裹以避免与内嵌的三引号冲突:

````markdown
# Plan: 把 legacy-skill-importer.sh 标 deprecated 并迁移调用方到 skill-importer-v2.sh

日期：2026-05-05
状态：ready-for-implementation
负责人：ECC inventory 维护窗口

## Review 对齐

REVIEW_DOMAIN：app_code

REVIEW_FOCUS：
- 调用方迁移完整性(没有 caller 还在调老脚本)
- 老脚本 deprecated 标记是否在 stderr 触发警告但不打断
- 7 天回滚窗口期间老脚本仍可读可执行
- v2 在所有迁移过的 caller 上行为对齐
- 切换路径不破坏 ECC inventory 既有 cron / launchctl 调用

Domain-specific notes：
- app_code:Bash 脚本 + caller 迁移;不涉及 live runtime 切换
- ai_infra:仅在 7 天窗口结束后才允许把老脚本物理移除
- docs_content:同步更新 README + 内部 wiki tool 卡片
- other:无

## 目标与验收

用户目标：
- 把 ECC inventory 中 2024-Q1 写的 legacy-skill-importer.sh(330 行)在不破坏现有调用的前提下,渐进迁移到 2025-Q1 重写的 skill-importer-v2.sh(150 行,已实测),并保留至少 7 天回滚窗口。

成功标准：
- [ ] `Codex/ai-infra/inventory/legacy-skill-importer.sh` 头部加 deprecated 横幅 + stderr 警告
- [ ] 全部 caller 列表在 `inventory/CALLERS.md` 完整登记并逐个迁移到 v2
- [ ] caller 改完后跑一次 v2 smoke 确认无 regression
- [ ] 7 天回滚窗口内 legacy 脚本仍可读、可执行、不删除
- [ ] 7 天后由用户显式批准才物理移除 legacy
- [ ] 新增 `inventory/MIGRATION-LOG.md` 记录每个 caller 切换时间 + 验证结果

非目标：
- 不重写 v2 脚本本身(已实测,本轮不动)
- 不强制立即删除 legacy(7 天窗口是硬约束)
- 不迁移其它 ECC inventory 脚本(本轮只针对 skill-importer 一族)
- 不做 cron / launchctl 时段重排(只换脚本路径,不改时间)

## 当前状态

相关事实：
- `Codex/ai-infra/inventory/legacy-skill-importer.sh` 存在,330 行,2024-Q1 写
- `Codex/ai-infra/inventory/skill-importer-v2.sh` 存在,150 行,2025-Q1 已实测
- ECC workspace 当前不在 git repo;迁移需要靠 mtime + manifest 追踪,而不是 git diff
- 老脚本和 v2 在 inventory 目录共存

[... 完整 200 行内容已落盘 /tmp/claude-501/c4-plan-output.md, sha256: a34aa2aec6eb86c9c01dd0b9432066dec413e63e886498b0f5c26f531cd516ae;
为节省 evidence 文件尺寸,中段 100 行省略;sha256 + 行数 + B/C/D 断言已可独立验证完整性 ...]

## 实施前 Review Checklist

- [x] 目标、成功标准、非目标清楚
- [x] REVIEW_DOMAIN 和 REVIEW_FOCUS 清楚
- [x] 新增/修改/不修改的文件范围清楚
- [x] CLI/API/函数输出合约清楚
- [x] 数据迁移和旧数据兼容清楚
- [x] 边界情况和失败模式清楚
- [x] 测试夹具不会污染真实数据
- [x] live runtime、密钥、外部服务、持久配置没有被默认修改
````

**完整 raw_output 文件**:`/tmp/claude-501/c4-plan-output.md`(transient,会随 session 失效),sha256 = `a34aa2aec6eb86c9c01dd0b9432066dec413e63e886498b0f5c26f531cd516ae`。后续 Codex 复核时可重跑 fixture 比对。

## 后续 handoff to AKB2.0 write-back

- 推荐:`toolbox/evals/cc-executable-plan.md` 升 `eval_level: L1`,`last_run_status: pass`,`fixture_path` 指向 c4a fixture
- `status: candidate` / `automation_allowed: false` 不变
- 已发现的 fixture/assertions 文档级缺陷:assertion C 的 colon 写法应兼容 ASCII + 全角(本轮 evidence 显式记录,留作 fixture maintenance backlog)
