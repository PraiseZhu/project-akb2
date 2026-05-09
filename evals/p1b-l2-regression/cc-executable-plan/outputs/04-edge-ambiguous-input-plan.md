<!-- PLAN_TEMPLATE_V3 -->

# Plan: <待用户明示标题 — 输入不完整,不可执行>

日期:2026-05-06
状态:draft(blocked-by-incomplete-input)
负责人:Claude Code(per cc-executable-plan skill)

## Review 对齐

REVIEW_DOMAIN:other

REVIEW_FOCUS:
- 输入完整性:用户原 Goal "想给 AKB2.0 加一个东西,处理那个事" 不含可执行信号(无目标 / 无路径 / 无验收 / 无优先级)
- 不强行编造:本 plan 拒绝在缺少用户明示的情况下虚构 file path / function name / success criteria
- 待确认列表完整性:覆盖目标 / 范围 / 验收 / 边界 / 优先级 5 个最小决策面

Domain-specific notes:
- other:本 plan 不进入任何 specific domain;待用户回答 §待确认 后再判 ai_infra / app_code / docs_content

## 目标与验收

用户目标:
- 目标不明,无法定 success criteria
- 用户原话:"想给 AKB2.0 加一个东西,处理那个事"
  - "加一个东西" — 不知是文件 / 工具 / standard / phase / 流程
  - "处理那个事" — 不知是修复 / 重构 / 新建 / 删除 / 排查
  - 无 IN_SCOPE / OUT_OF_SCOPE / SUCCESS_CRITERIA

成功标准:
- [ ] (不可定;待用户答 §待确认 后填)

非目标:
- (不可定;待用户答 §待确认 后填)

## 当前状态

相关事实:
- 上下文未提供,无法盘点
- 用户提供的 Context 字段:**(空)**
- 当前 AKB2.0 整体状态切片在 `docs/phase-c-reports/p0-toolbox-status-slice-2026-05-06.md`,但本 plan 不预设用户指的是 P0 范围

现有入口:
- 不可定;取决于用户具体意图(可能是 Edit 1 文件 / 可能是新建子系统 / 可能是只读分析)

约束:
- 不预设用户意图
- 不强行套用某个 phase / 某个工具 / 某个 standard 的工作模式
- 不在 plan 内编造具体路径 / 函数名 / API 合约

## 代码执行方向

新增:
- 待用户明示

修改:
- 待用户明示

不修改:
- 在用户回答 §待确认 之前,**任何文件均不修改**
- 特别强调:不修改 `~/.codex/skills/**` / 不触发任何旧 AKB 链路 / 不接 cron / 不写 active

## 接口与数据合约

CLI/API 合约:
- 待用户明示是否涉及 CLI / API

函数/模块合约:
- 待用户明示

数据/Schema 变更:
- 待用户明示
- 旧数据兼容:待目标确定后再分析

## 实现步骤

1. 准备/审计步骤
   - **本 plan 当前不可执行**
   - reviewer 应确认本 plan 进入 §待确认 收集环节,而非提交 ready-for-implementation

2. 核心实现步骤
   - 待用户答 §待确认 后再填

3. 集成步骤
   - 待用户答 §待确认 后再填

4. 收尾步骤
   - 待用户答 §待确认 后再填

## 边界与失败模式

必须处理:
- 失败模式:**输入不完整,plan 不可执行**
  当前应对:返回 §待确认 给用户,等待补充;不强行写完整 plan
- 即使用户催促"快出 plan",在缺少 §待确认 答复前不出可执行 plan;只能再问

明确不处理:
- 不预设用户意图
- 不在缺少明示的情况下"借鉴" P0 / P1 / P2 等已有 plan 模式自动套用
- 不引入虚构 file path / 函数名 / API

风险与防护:
- 风险:用户失去耐心,催促直接出 plan
  防护:本 plan 明示 5 条 §待确认;若用户拒答,reviewer 可决定中止本 plan 而不是放行半成品
- 风险:reviewer 误判本 plan 可执行
  防护:本 plan `状态:draft(blocked-by-incomplete-input)` 显式标注 + REVIEW_DOMAIN: other 配合
- 风险:Claude 受到压力开始编造 file path / function name 填空 §代码执行方向
  防护:本 plan 把"待用户明示"显式写入 §代码执行方向 + §接口与数据合约 + §实现步骤,作为可读硬约束

## 测试计划

单元测试:
- 不适用(plan 不可执行)

集成/CLI 验证:
- 不适用

测试隔离:
- 不适用(0 mutation)

暂不执行的验证:
- 全部 — 等用户答 §待确认

## 待确认

> **本节是本 plan 的核心产出**。在用户回答以下 5+ 条之前,本 plan 不进入 ready-for-implementation。

1. **加什么东西?** — 是文件(哪类:source / concept / design / standard / phase-report / tool 5 件套 / staging candidate)? 是脚本?是 hook?是 lint 规则?是新 status 枚举?
2. **处理哪件事?** — 是修复某 known bug? 是重构某既有结构?是补充某文档?是排除某旧库?是新建某子系统?是清理某 legacy?
3. **优先级与时机?** — 这件事是阻塞当前 phase(如 P1b/P1c/P2)? 还是 backlog 项?是否有截止日期?是否依赖某前置 plan?
4. **验收标准是什么?** — 怎样算"做完了"?是某 grep 命中 / 某 CLI exit 0 / 某 review APPROVED / 某文件存在 / 某行为可重现?
5. **边界与不修改范围?** — 哪些文件 / 链路 / 工具 **不能** 被本 plan 触及?(默认 reservation:`~/.codex/skills/**` / 旧 AKB fetch / synth / translate / cron / LaunchAgent;请确认是否还有别的)
6. (可选)**REVIEW_DOMAIN?** — 本 plan 实施完后期望走 ai_infra / app_code / docs_content / other 哪条 review 路径?
7. (可选)**baseline confidence?** — 用户对当前状态了解多少?是否需要本 plan 第 1 步先做只读探查再回到本 §待确认 补全?

## 实施前 Review Checklist

- [x] 目标、成功标准、非目标清楚 → **不清楚,故标记为 draft 不放行**
- [x] REVIEW_DOMAIN 和 REVIEW_FOCUS 清楚 → REVIEW_DOMAIN 暂为 other,FOCUS 锁在"输入完整性 + 不强行编造"
- [x] 新增/修改/不修改的文件范围清楚 → **不清楚,但已显式标注"待用户明示"**
- [x] CLI/API/函数输出合约清楚 → 不适用 / 待明示
- [x] 数据迁移和旧数据兼容清楚 → 不适用 / 待明示
- [x] 边界情况和失败模式清楚 → 主失败模式 = 输入不完整,本 plan 拒绝放行
- [x] 测试夹具不会污染真实数据 → 0 mutation
- [x] live runtime、密钥、外部服务、持久配置没有被默认修改 → 0 mutation,本 plan 进入 ready 之前一字未改

---

> **审查提醒**: 本 plan 不可执行。请用户回答 §待确认 5 条后,Claude Code 重出 v2 plan。
