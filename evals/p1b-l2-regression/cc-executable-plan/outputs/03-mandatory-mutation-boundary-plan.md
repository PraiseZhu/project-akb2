<!-- PLAN_TEMPLATE_V3 -->

# Plan: 旧 AKB Wiki/mcp.md 永久排除候选池(不删文件)

日期:2026-05-06
状态:ready-for-implementation
负责人:Claude Code(per cc-executable-plan skill)

## Review 对齐

REVIEW_DOMAIN:ai_infra

REVIEW_FOCUS:
- §不修改 段落严格列出旧 AKB 链路全部禁忌(fetch / synth / translate / NLM / 简报 / cron / LaunchAgent / Project AKB/**)
- staging/_review-queue.md 新建 + 1 行登记的 schema 简单 + 后续 lint 可解析
- 不引入对 Project AKB 任何写入(只新建 AKB2.0 内 1 文件)
- 边界声明清晰:"如发现旧 fetch 仍跑 → 单独 plan,不在本 plan 范围"
- 不接 cron / LaunchAgent / hook(automation_allowed 不动)

Domain-specific notes:
- ai_infra:runtime active = 不接;mirror backup = 不接;trigger surface = 不新增;generation surface = 不新增;state baseline = staging/_review-queue.md 1 行登记;validation runtime health = 旧 LaunchAgent 是否仍在跑由本 plan 排查 + 不修复;stale cleanup = 不在本 plan(物理移文件留 H6)

## 目标与验收

用户目标:
- 把已 superseded 的 `Project AKB/AKB/Wiki/mcp.md` 从 AKB2.0 候选 / 提升 / lint 任何路径上**永久排除**
- 不删除物理文件(用户保留对旧库的可读访问)
- 不动 Project AKB 任何文件
- 不重启 / 不接通 任何旧 AKB fetch / synth / translate 链路

成功标准:
- [ ] AKB2.0 内新建 `staging/_review-queue.md`(若不存在)+ 包含 1 行旧 mcp.md 排除登记
- [ ] 登记行含:source path / superseded_at / supersede_reason / replaces_with / 决策日期 / 决策人
- [ ] `Project AKB/**` 任何文件未被修改(`find Project\ AKB -newer <marker> -type f` 应空)
- [ ] 0 cron / LaunchAgent / hook 启用
- [ ] 0 旧 fetch / synth / translate / NLM 链路被触发(`launchctl list | grep -i akb` 不引入新 active job)
- [ ] phase-b-validation 候选 `legacy-akb-mcp-wiki--phase-b--2026-05-03.md` 内容不动(已存在,不重写)
- [ ] frontmatter 中无任何 `automation_allowed: true`
- [ ] AKB2.0 5 工具 5 件套 sha256 一字未改

非目标:
- 不删除 `Project AKB/AKB/Wiki/mcp.md` 物理文件
- 不修改 `Project AKB/**` 任何文件
- 不接通 / 不修复 旧 AKB fetch / synth / translate / NLM 链路
- 不接 cron / LaunchAgent / hook
- 不新建 lint 规则(P2.1 plan 范围)
- 不创建新的 active 工具
- 不修改既有 `legacy-akb-mcp-wiki--phase-b--2026-05-03.md`

## 当前状态

相关事实:
- 旧文件 `Project AKB/AKB/Wiki/mcp.md` 存在,~2026-04-30 之后未更新,user-stated superseded
- AKB2.0 phase-b-validation 已有候选 `legacy-akb-mcp-wiki--phase-b--2026-05-03.md`,frontmatter `gate_reason: 拟 needs_repair → 弃用 + 新建`
- AKB2.0 当前不存在 `staging/_review-queue.md` 文件
- 旧 AKB 的 fetch / synth / translate / NLM 链路状态(per AKB2.0 当前对账):已停;不重启
- LaunchAgent 当前清单:`launchctl list | grep -i akb` 无 active job(假设;实施前实证)

现有入口:
- 文件浏览:Read 工具
- 文件创建:Write / Edit 工具(本轮唯一 mutation)
- LaunchAgent 状态确认:`launchctl list | grep -i akb`(只读)

约束:
- 不修改 `Project AKB/**` 任何文件(包括 mcp.md 本身)
- 不修改 `~/.codex/skills/**`
- 不修改 AKB2.0 内 wiki/tools / toolbox/ / 5 件套 任何文件
- 不接 cron / LaunchAgent / hook
- 不写入 `automation_allowed: true`
- 不触发任何旧 AKB 链路

## 代码执行方向

新增:
- `AKB2.0/staging/_review-queue.md`(若不存在)— 1 节 + 1 行登记

修改:
- 无(若 _review-queue.md 已存在则改为 Edit 追加 1 行而不是 Write 整文件)

不修改:
- `Project AKB/AKB/Wiki/mcp.md` 物理文件
- `Project AKB/**` 任何文件
- 任何 AKB2.0 5 件套(`wiki/tools/` / `toolbox/{tools,interfaces,evals,registry}/`)
- 任何 SKILL.md / settings.json / hook / launchctl
- `legacy-akb-mcp-wiki--phase-b--2026-05-03.md` 既有 frontmatter / 内容
- 任何 cron / LaunchAgent
- 任何旧 AKB fetch / synth / translate / NLM 链路触发面

## 接口与数据合约

CLI/API 合约:
- 无 CLI / 无 API(纯文档 + 1 行登记)

函数/模块合约:
- 无函数变更

数据/Schema 变更:
- AKB2.0 新增 `staging/_review-queue.md` 文件 schema 极简:Markdown table,列 `path | superseded_at | supersede_reason | replaces_with | decided_at | decided_by`
- 旧数据兼容:首次创建时表头 + 1 行;未来追加只 append 行不改表头

文件 schema:

```markdown
---
slug: review-queue
asset_type: staging
status: ready
---

# AKB2.0 Review Queue — 永久排除登记

| path | superseded_at | supersede_reason | replaces_with | decided_at | decided_by |
|---|---|---|---|---|---|
| Project AKB/AKB/Wiki/mcp.md | 2026-04-30 | 旧 NLM 抓取产物,术语已过时 + 不再 sync | (待新建 wiki/tools/mcp-overview.md) | 2026-05-06 | praise |
```

## 实现步骤

1. 准备/审计步骤
   - `test -f "Project AKB/AKB/Wiki/mcp.md" && echo "EXISTS"` 确认旧文件确实存在(不删 / 不动)
   - `test -f "AKB2.0/staging/_review-queue.md" && echo "EXISTS" || echo "MISSING"` 决定 Write vs Edit
   - `launchctl list | grep -i akb` 实证旧 LaunchAgent 当前状态(只读)
   - `crontab -l 2>/dev/null | grep -i akb` 实证 cron 状态(只读)
   - 实证结果写入本 plan §当前状态(若实测有差异)

2. 核心实现步骤
   - 若 `staging/_review-queue.md` 不存在:Write 完整文件(frontmatter + 表头 + 1 数据行,见 §文件 schema)
   - 若已存在:Edit 在表底追加 1 行(锚定表头或最后一行)
   - 不动其它任何文件

3. 集成步骤
   - 无集成(staging/_review-queue.md 是被动登记,无消费方触发)
   - 不接 lint(P2.1 plan 范围)
   - 不接 cron / LaunchAgent / hook

4. 收尾步骤
   - `grep -c "Project AKB/AKB/Wiki/mcp.md" AKB2.0/staging/_review-queue.md` 应为 1
   - `find "Project AKB" -newer <S0_marker> -type f` 应空(0 改动)
   - `launchctl list | grep -i akb`:与实施前 byte-identical
   - `crontab -l 2>/dev/null | grep -i akb`:与实施前 byte-identical

## 边界与失败模式

必须处理:
- 旧 fetch / synth / translate / NLM 链路若实证仍在 active(`launchctl list` 命中):**不在本 plan 范围**,在本 plan §收尾步骤 输出告警 + 单独开 plan 处理
- staging/_review-queue.md 已存在但 schema 不同(列名 / 顺序差异):本 plan 不重构 schema,Edit 时追加最相近列(若完全不兼容则 abort + 告警 + 升级 plan)
- legacy candidate 文件 `legacy-akb-mcp-wiki--phase-b--2026-05-03.md` 缺失:本 plan 不重新创建,只登记 _review-queue.md(legacy candidate 是上游 phase 范围)
- frontmatter 写入时不打开 `automation_allowed: true`(强制 false 或不写该字段)

明确不处理:
- 物理删除 `Project AKB/AKB/Wiki/mcp.md`(留 H6 phase 处理)
- legacy candidate 重建
- 旧 NLM 链路修复 / 重启
- 新 wiki/tools/mcp-overview.md 创建(留独立 plan)

风险与防护:
- 风险:Edit 误改既有 staging/_review-queue.md 表头
  防护:Edit `old_string` 锚定表底最后一行的 newline 后位置,只 append
- 风险:本 plan 实施过程意外触发旧 LaunchAgent / cron
  防护:本 plan 不调用任何 launchctl load / launchctl start / crontab 变更命令(只读 list)
- 风险:本 plan 把 Project AKB 目录的 file 当成 AKB2.0 一部分误改
  防护:本 plan 所有 Write / Edit 路径锚定 AKB2.0/ 子树;Project AKB/** 严格只读
- 风险:_review-queue.md 已被 hook 或 lint 监听误判
  防护:本 plan 不接 hook / 不接 lint;_review-queue.md 当前 0 消费方

## 测试计划

单元测试:
- 无(纯文档 + 1 行登记)

集成/CLI 验证:
- `grep -c "Project AKB/AKB/Wiki/mcp.md" AKB2.0/staging/_review-queue.md`
  期望:1
- `grep -E "^\\\| Project AKB" AKB2.0/staging/_review-queue.md | wc -l`
  期望:1
- `find "Project AKB" -newer /tmp/s0_marker -type f 2>/dev/null | head -5`
  期望:空输出
- `launchctl list | grep -ic akb`
  期望:与实施前一致(本 plan 不引入新 LaunchAgent)
- `grep -rE "^automation_allowed: true" AKB2.0/staging`
  期望:空

测试隔离:
- 仅 1 文件 Write 或 Edit;0 数据库 / 0 临时目录
- 不动 Project AKB / 不动 wiki / 不动 toolbox

暂不执行的验证:
- 旧 LaunchAgent 解除注册(留 deprecation plan)
- 物理 mv mcp.md(留 H6)
- legacy candidate 重建(独立 plan)
- mcp-overview.md 新建(独立 plan)

## 待确认

- _review-queue.md 表中 `replaces_with` 列写"(待新建 wiki/tools/mcp-overview.md)"还是空?默认:写括号内待办,便于未来 grep
- _review-queue.md 是否需要双向反链到 legacy candidate 文件?默认:不双向(本 plan 范围只单向登记;反链留 P2.1 lint plan)
- 若 `launchctl list | grep -i akb` 命中 active job,本 plan 是 abort 还是先登记后告警?默认:**先登记 + 强告警 + 提议单独 plan**(不 abort,因为登记是只读级 mutation,不会让旧链路更糟)

## 实施前 Review Checklist

- [x] 目标、成功标准、非目标清楚(永久排除 + 不删 + 不动 Project AKB)
- [x] REVIEW_DOMAIN 和 REVIEW_FOCUS 清楚(ai_infra + 5 focus 含旧链路保护)
- [x] 新增/修改/不修改的文件范围清楚(只新建或追加 1 文件)
- [x] CLI/API/函数输出合约清楚(无 CLI;_review-queue.md schema 6 列)
- [x] 数据迁移和旧数据兼容清楚(首次 Write 或追加;不重构 schema)
- [x] 边界情况和失败模式清楚(旧 LaunchAgent 实证 + abort 策略 + Project AKB 严格只读)
- [x] 测试夹具不会污染真实数据(0 临时数据;只读 launchctl / crontab)
- [x] live runtime、密钥、外部服务、持久配置没有被默认修改(0 cron / 0 LaunchAgent / 0 hook / 0 settings.json / 0 SKILL.md)

---

> **审查提醒**: 计划完成后可手动 `/review plan` 触发 Codex 审查。不自动触发。
