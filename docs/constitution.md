<!--
Sync Impact Report
==================
Version change: 1.2.0 → 1.2.1
Bump rationale: PATCH — 原则 7 当前阵容型号行与运行时对齐：复核模型由 gpt-5.4 改为 gpt-5.6-luna（gemini-flash 仍只诊断不 PASS）。原则内核（异族分离 / 禁自评自批 / 禁 -preview）未改。
来源：loop2 通电（_tmp/loop2-final-v1.1.md C4）。

Principles:
  1-6, 8 — 未改
  7. Maker/Checker 异族分离 — AMENDED（当前阵容型号行：gpt-5.4 → gpt-5.6-luna）

Sections amended:
  - Core Principles → 原则 7 当前阵容型号行
  - Governance → Version / Last Amended
  - 其余章节（自动化纪律 / 变更纪律）— 未改

Files requiring follow-up sync:
  - ✅ src/akb2/quality/grader.py — PRIMARY_GRADER_MODEL 默认 gpt-5.6-luna
  - ✅ CLAUDE.md「治理约束」节 — 原则 7 摘要行已同步
  - n/a docs/allowed-residuals.md — 本次为正式修宪 PATCH，非残差豁免

Deferred TODOs: 无
-->

# AKB2 Constitution

> AKB2.0 工程根（lint / gate / intake / closure / automation_gate / matrix / promotion_gate / search_quality / mcp_servers / video / briefing / loops）。
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
- 主链路语境下 fail-closed **MUST** 贯穿全链七段（抓取 / raw / 准入 / 提炼组装 / 门控 / 升格 / 收尾）：任一段检查不过，后段 **MUST NOT** 启动。

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

### 黄金样张质量锚定 (Golden-Sample Quality Anchoring)
主链路任何入库产出（含合成品、概念页）**MUST** 达到黄金样张质量级别；该级别 **MUST** 以机器可判形式定义（14 步清单「标准」列 + 评分量规），**MUST NOT** 以「与样张差不多」等主观表述替代。

- 当前正样本：`wiki/sources/addy-osmani-loop-engineering.md` 全套 6 文件；负样本 2 个（洗稿型 / 新闻型）**MUST** 被准入段拒收——质量级别由「放进什么」与「拒掉什么」共同定义。
- 评分量规 **MUST** 从样张反向提取并版本化；量规 **MUST** 输出可定位的缺陷清单（供原则 2 的定点修复）——加权质量分 **MAY** 与缺陷清单并存作为过阈判据，但 **MUST NOT** 以单一分数取代可定位缺陷清单。
- 新循环 / 新机制设计 **MUST** 通过「样张八要素对账」后才算设计完成（见自动化纪律第 4 条）。
- 样张换代 **MUST** 经提案程序（更优内容晋升），换代后校准语料与量规 **MUST** 同步重验。

Rationale: 设计期两次对账均抓出真实缺口（skill 包缺失、基座面三要素缺失）；没有机判锚点,「每一篇都和样张同级」只是愿望而非工程属性。

### Maker/Checker 异族分离 (Cross-Family Maker/Checker Separation)
内容的生产者与质量裁决者 **MUST** 分离，且裁决模型 **MUST** 与生产模型来自不同厂商家族（当前阵容：deepseek-v4-pro 提炼 / gpt-5.6-luna 复核 / gemini-flash 兜底）。

- 任何机制 **MUST NOT** 自评自批：提炼模型不判自己达标；修宪提案者与通过者不得为同一 agent 会话；执行 agent 不自报完成（完成由独立验收判定）。
- 7×24 管线 **MUST NOT** 使用 `-preview` 后缀模型（预览版的限流/改版/下线不受控，违反原则 4 的可见性要求）。
- 确定性裁决（gate CLI / lint / 结构校验）**SHOULD** 优先于 LLM 裁决；LLM 裁决仅用于结构检查无法覆盖的质量维度。

Rationale: 写作业的模型给自己打分必然偏松（黄金样张 raw §G）；同族模型共享训练偏好,分歧才有信息量；三族三家使任何一家的系统性偏见过不了另外两家。

### 实测价值裁决 (Measured-Value Verdict)
知识的价值与机制的有效性 **MUST** 由真实使用数据裁决（调用频次、共现、命中率、转化率），**MUST NOT** 由生产方模型预测或自评。

- 入库判定 **MUST** 分两层：(a) **完整性地板**（论断锚定、证据诚实、解读可溯源、车道核心价值）**MUST** 为二值硬 pass/fail，任一不过即拒，**MUST NOT** 被其他维度加分补偿；(b) 地板之上的质量维度 **MAY** 由版本化、车道感知的量规加权合成为单一阈值的 pass/fail 判定。
- 加权质量分 **MUST** 仅用于「是否达阈值」的一次性二值裁决，**MUST NOT** 用于候选之间排序择优（防队列稀疏时按相对排名放宽标准）；具体权重、阈值与车道定义 **MUST** 落在版本化量规文件（`skillpack/quality/rubric/`），使校准调参不触动本宪法。
- 星级（0-3 封顶）**MUST** 由调用数据驱动：同层共现达阈才升格合成，混层只喂养不升星；合成品 30 天无人调用 **MUST** 降星归档。
- 机制参数（共现阈值 / 量规 / 准入规则）的调整 **MUST** 以其下游实测指标变化为验收，不以「看起来更合理」为验收。

Rationale: 入库时预测的价值分数是猜测；调用数据是用户真实需求的投票。该原则同样反身约束价值体系自己——升星机制若产出无人调用的「高级知识」,被修正的是机制而非继续生产。

（v1.2.0 修订）原 v1.1.0「入库一律硬 pass/fail、零分数」过严——单一 How 硬门系统性误杀有价值的概念内容（满仓校准概念源 0% 通过）。故拆两层：完整性（锚定 / 诚实 / 可溯源 / 车道核心价值）仍是不可交易的硬地板，质量充分性改由车道感知加权量规按阈值裁决；「无连续分数」的真正用意是禁排序滑坡，该禁令完整保留。地板 + 阈值仍是 fail-closed（原则 3）：低于阈值即拒，不降级放行。

## 自动化纪律 (Automation Discipline)

自动化是**挣来的权利**，不是默认状态。把一个能力提升为无人值守自动化前 **MUST** 满足：

1. **触发器分类明确**——属于以下之一并在代码/配置中标注：
   - `manual`：人工调用（如 video 链路当前的 `__main__` 触发）
   - `event`：外部事件驱动
   - `schedule`：定时（如 briefing 的 launchd plist + backstop）
2. **可观测**——有日志落点，失败有信号（见原则 4）；不存在「装了就不管」的自动化。
3. **权限靠 key 不靠 prompt**——若某 agent/脚本不应执行某外部副作用（发消息 / 删除 / 写 vault），**MUST** 通过缺失凭证/作用域从物理上阻断，而非靠 prompt 约束。提示词不是权限层。
4. **循环规格八要素卡**——任何小循环（内容质量 / 治理 / 星级 / 源汰换 / 图谱 / 检索 / 修复）投入实现前 **MUST** 有完整八要素卡：①标准(机判) ②感知 ③驱动 ④收敛 ⑤裁决（控制面）+ ⑥心跳 ⑦状态(jsonl 账本) ⑧技能包（基座面）。缺任一要素 = 设计未完成，**MUST NOT** 开工。账本格式 **MUST** 遵循全循环统一 jsonl 约定（循环 7 读一切的前提）。
5. **agent 边界判定规则**——节点内需要「自主决定下一步」才 **MAY** 上 agent；固定变换（固定输入 → 一次调用 → schema 校验输出）**MUST** 实现为脚本或 LLM 函数（API 直调），**MUST NOT** 实现为 agent 会话。LLM 函数的重试 / 校验 / 预算 **MUST** 是确定性代码（不把非语言工作交给模型）。

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
- **边界备注**：vault 侧宪法（`AKB-2.0-CONTENT-STANDARD.md`）的修订（如不变量 3「没有自动 active」→ 五闸自动升格）**MUST** 走 vault 宪法自身的修订程序，本文件不替代之；两边修订 **MUST** 互相在 Sync Impact Report 中登记。

**Version**: 1.2.1 | **Ratified**: 2026-06-10 | **Last Amended**: 2026-09-01
