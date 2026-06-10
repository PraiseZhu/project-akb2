<!--
Sync Impact Report
==================
Version change: (none) → 1.0.0
Bump rationale: MAJOR — 首次采纳,确立 AKB2 工程根的治理原则。

Principles defined (5):
  1. 有界自动化 (Bounded Automation)
  2. 收敛优先于重跑 (Convergence Over Restart)
  3. Fail-Closed 质量门控 (Fail-Closed Quality Gating)
  4. 预算显式 / 失败可见 (Explicit Budget, Visible Failure)
  5. 工程与 vault 解耦 (Engineering–Vault Separation)

Sections added:
  - 自动化纪律 (Automation Discipline)
  - 变更纪律 (Change Discipline)
  - Governance

Files requiring follow-up sync:
  - ⚠ docs/architecture.md — 安全边界 TODO 应引用本 constitution 原则 5（待处理）
  - ⚠ ~/.claude/commands/gd.md — `Review 对齐` 约束应能引用本 constitution（跨 repo，需用户确认是否纳入）
  - ✅ VERSIONING.md — 与本 constitution 不冲突（版本/提交策略各管各的层）
  - ✅ CLAUDE.md — 项目自述与本 constitution 一致

Deferred TODOs: none
-->

# AKB2 Constitution

> AKB2.0 工程根（lint / gate / intake / closure / automation_gate / matrix / promotion_gate / search_quality / mcp_servers / video / briefing）。
> 知识库本体不在本工程范围内（留 Obsidian vault `Praise Space/AKB2.0/`）。

## Core Principles

### 有界自动化 (Bounded Automation)
任何自动化循环（重试、修复、清空队列、轮询、补跑）**MUST** 声明显式上限——最大轮次（如 `max_rounds`）或时间/资源预算（如 `budget_seconds`）。

- 禁止无终止条件的 `while True`，除非是「等外部触发」的最外层循环（如 launchd / `__main__` 主入口），且该层 **MUST** 能被中断退出。
- 内层循环 **MUST** 有可判定的收敛条件（如 `len(issues) == 0`、`queue.empty()`、`elapsed >= deadline`）。

Rationale: AKB2 是无人值守的自动化系统，跑飞的循环直接烧 LLM 额度（Fable / NLM / DeepSeek）。现状已有 `VIDEO_BUDGET_SECONDS` / `VIDEO_NLM_MAX_WAIT` / `max_attempts` 等上限，本原则将其固化为强制约定。

### 收敛优先于重跑 (Convergence Over Restart)
当某阶段产出含**可定位**的缺陷（如 audit 的 `standard_issues[]` / `accuracy_issues[]`），系统 **SHOULD** 针对被点名的部分定点修复并重新校验，直到收敛或触上限；**SHOULD NOT** 丢弃全部产物、从最前置阶段（抓取 / 打分）整体重跑。

- 修复回路 **MUST** 有界（见原则 1）；触上限仍未收敛 **MUST** 落到 fail-closed 门控（见原则 3）。
- 已有的细粒度修复零件（如 `translate._translate_snippet`）**SHOULD** 优先复用，而非新造。

Rationale: 整体重跑会重复昂贵的上游成本（重抓 Twitter / 重新打分 / 重新合成），且靠概率碰运气修复局部问题；定点修复成本集中在缺陷本身。

### Fail-Closed 质量门控 (Fail-Closed Quality Gating)
质量未达标的产物 **MUST NOT** 进入知识库（保留在 staging / candidate）；门控裁决 **MUST** 可追溯地写入产物 frontmatter。

- 门控枚举遵循既有 enforcement matrix：`fail` → 不入库；`warn` → 入库但 frontmatter 标记；`pass` → 正常入库。
- 任何放宽门控（如允许残差）**MUST** 在 `docs/allowed-residuals.md` 显式登记，不得隐式跳过。

Rationale: 知识库是下游一切检索/合成的真相源，污染代价高于「今天没产出」。

### 预算显式 / 失败可见 (Explicit Budget, Visible Failure)
调用 LLM 或外部额度的循环 **MUST** 把预算声明在可见处（具名常量，非内联魔数）；因预算/超时导致的跳过、截断、重试耗尽 **MUST** 显式记录到日志，**MUST NOT** 静默吞掉。

- 同类预算 **SHOULD** 统一单位约定（按次数 or 按时间），同一模块内不得混用两种。
- 部分失败（如「队列只清了 60%」「14% 记录被跳过」）**MUST** 在结果中明示，不得报「成功」。

Rationale: 现状预算写法分散（秒 / 次数 / 分钟三套），且静默失败会让「跑飞」与「正常空跑」无法区分。

### 工程与 vault 解耦 (Engineering–Vault Separation)
本工程 **MUST NOT** 写入 vault 内任何可执行文件（`.py` / `.sh`）；对 vault 的写操作 **MUST** 限于知识库内容与 toolbox 元数据的回写。

- 数据流方向：vault → 工程（lint / gate） → 回写 vault 的内容/元数据，单向清晰。
- vault 路径与边界以 `config/vault.toml` + `docs/path-conventions.md` 为准。

Rationale: 工程逻辑与知识库本体混写会让两者互相污染、难以独立演进与回滚。

## 自动化纪律 (Automation Discipline)

自动化是**挣来的权利**，不是默认状态。把一个能力提升为无人值守自动化前 **MUST** 满足：

1. **触发器分类明确**——属于以下之一并在代码/配置中标注：
   - `manual`：人工调用（如 video 链路当前的 `__main__` 触发）
   - `event`：外部事件驱动
   - `schedule`：定时（如 briefing 的 launchd plist + backstop）
2. **可观测**——有日志落点，失败有信号（见原则 4）；不存在「装了就不管」的自动化。
3. **权限靠 key 不靠 prompt**——若某 agent/脚本不应执行某外部副作用（发消息 / 删除 / 写 vault），**MUST** 通过缺失凭证/作用域从物理上阻断，而非靠 prompt 约束。提示词不是权限层。

新增自动化触发器（plist / event / loop）**MUST** 经人工确认，**MUST NOT** 由 agent 自行注册（与 VERSIONING.md「不存在后台自动提交」一致）。

## 变更纪律 (Change Discipline)

1. **外科式改动**——只改任务所需，匹配既有模式与命名；改动面与风险成正比，doc/config 改动可轻量，循环/门控/入库逻辑改动 **MUST** 带测试。
2. **约定胜过新颖**——已有模式（如既有 budget 常量、collector 基类）**MUST** 沿用；不为「更漂亮」引入第二套并行模式。
3. **不过早抽象**——形状相似但停止条件本质不同的循环（如 briefing 的质量收敛 vs video 的队列清空）**SHOULD NOT** 被强行合并为单一共享引擎；先各自落地，出现第三处同形需求再考虑提取。
4. **测试门槛绑行为**——测试断言 **MUST** 绑定可观测行为（收敛/门控/预算的实际效果），而非仅检查结构或「能跑通」。
5. **步骤间 checkpoint**——多步改动 **SHOULD** 分独立 commit，使单步回滚不连累其余。

## Governance

- **权威层级**：本 constitution 是 AKB2 工程决策的最前置约束层，在 `/speckit-specify` → `/gd plan` 链路中作为 `Review 对齐` 的质量基线被引用。与全局 `~/.claude/CLAUDE.md` 冲突时，全局用户规则优先；与项目 `CLAUDE.md` / `VERSIONING.md` 冲突时，在本文件显式标注并由用户裁决。
- **修订程序**：任何原则的增/删/重定义 **MUST** 通过修改本文件完成，并在顶部 Sync Impact Report 记录版本变更、影响文件、待同步项。
- **版本策略**（语义化）：
  - **MAJOR**：删除或向后不兼容地重定义原则 / 治理规则。
  - **MINOR**：新增原则或章节，或实质性扩展既有原则。
  - **PATCH**：措辞澄清、typo、非语义调整。
- **合规审查**：涉及循环、门控、入库、预算、vault 写入的变更，其 review 与 `/gd plan` 的 `Review 对齐` **MUST** 逐条核对本 constitution 的相关原则；违反项 **MUST** 在交付前修复或在 `docs/allowed-residuals.md` 登记豁免。

**Version**: 1.0.0 | **Ratified**: 2026-06-10 | **Last Amended**: 2026-06-10
