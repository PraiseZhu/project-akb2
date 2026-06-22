# AKB2 How 可靠性重构 — 主计划（Evidence Inventory + must_cover 代码渲染）

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md
TEMPLATE_KIND: gd-plan

> 作者：Claude（Opus 4.8）
> 日期：2026-06-22（r5：2026-06-23 过三轮 Codex cross-review 后修订）
> 状态：draft-r5（Claude self-review 5 + Codex 三轮 3+5+1 findings 全修；子计划内部一致性已收敛）
> 工作线：CQL（内容质量循环）第 4 份计划，承接 content-quality-loop / cql-intake / cql-backhalf

---

## Context（为什么做这次重构）

**目标不变**：wiki 层是 AI 第二大脑，**How 密度 / 准确性 / 可执行性 / 可靠性第一**，AI 读知识库更有效。

**问题（已闭环定位，亲读代码确认）**：提炼段 `distill.py` 现在是"LLM 抽 + 代码拼"。How 段操作步骤来自 LLM 抽的 `how_steps`，代码 `_build_body` 只筛选/补充/渲染。补充机制 `_raw_source_how_steps`（L2612）**只扫写死的 8 个 MCP 协议命令**（`_SOURCE_PROTOCOL_COMMANDS` L2412），CLI / 代码块 / 配置 / API 一概不扫。补不够 3 条时降级 `_claim_to_how_step`（L2551）**按关键词造 AKB 命令**——这正是"编"，且会被 self-gate 的 `SG-generic-command`（checks_structural L1020）打掉，导致 `compose_candidate` 返回 None。

**根因**：生产链路里"哪些 How 进正文"的决策交给了 LLM（概率系统），必然时漏（原文有的没选进）时编（造原文没有的）。与提示词强弱、模型强弱无关。

**模型角色现状（亲读确认）**：现状 `DISTILL_MODEL_FAMILY="deepseek-v4-pro"`（提炼，distill L52）/ `PRIMARY_GRADER_MODEL="gpt-5.4"`（评分，grader L42）——**当前已是异族分离**（deepseek vs gpt）。重构终态目标对调成 maker=codex/gpt-5.4 + checker=deepseek。**关键约束（Codex review 问题2）：只换 checker 一侧会让 maker=checker=deepseek 同族，违反宪法原则 7——所以本期不切 checker，checker→deepseek 与 maker→codex 绑成同一原子押后单元同期切（异族保护靠 SC-8 的 `DISTILL_MODEL_FAMILY != PRIMARY_GRADER_MODEL` 断言；注：`repair.py:494` 的 `assert maker!=checker` 是 `_claim_to_how_step` 生成的示例字符串，非执行断言，r2/r3 曾误引）。**

**解法（Codex 修正后架构）**：把"确定性覆盖"从 LLM 收回到代码——
```
A 代码扫 raw 建 evidence inventory（证据账本）
B 代码标 must_cover（必须覆盖项）
C LLM 填语义 segment（解释/论证，自由文本）
D 代码组装：must_cover 由代码直接渲染进正文对应节，LLM 无最终选择权
E coverage 校验 + 机器回查 + self-gate + fail-closed
```

**意图结果**：test-fresh 那篇真文章经链路产出的成稿，**原文操作 0 漏**、不编造原文没有的命令、机器可校验覆盖。

---

## 审核修订记录（Review Trail）

- **2026-06-23 r2**：过 `/gd review plan` 步骤 1（Claude self-review，标准 `gd-review-standard.md`），判 `REQUIRES_CHANGES`，5 findings 已修：SC-1 加 golden 基准 / SG-must-cover raw 缺失 fail-closed / SC-5 加 degraded 下游拦截 / SC-6 语料来源 / SC-7 hash 到函数体级。
- **2026-06-23 r3**：过 Codex cross-review（用户侧 codex/gpt-5.5 跑通；本机 bridge transport timeout 见 memory `codex-review-infra-unavailable`），3 findings 经亲读代码**全部验证为真并修复**：
  - **[P1] 问题1（degraded 漏到 repair loop）** → 护栏扩到 repair loop 二次评分：`repair.py:1261-1294` repair 后调 grader 只看 `suspended`、漏看 `fallback_used` → 加 fallback_used 检查不判收敛；`promote.py:194` `_read_grader_result` 只看 `status=="PASS"` → 拒收 fallback_used/suspended 的 PASS；`runner.py:600` `_persist_grader_pass` 不写 fallback PASS。SC-5 ③④ 覆盖。
  - **[P1] 问题2（只换 checker 致同族）** → 本期**不切 checker**（保持 gpt-5.4 异族）；checker→deepseek 与 maker→codex 绑成原子押后单元 + `maker_model != checker_model` 硬测试。分期表 + 关键决策 + 非目标已改。
  - **[P2] 问题3（self-gate 调用点写窄）** → P0-3 加 self-gate 调用点审计；**修正原计划误写的"promote 调 self-gate"**（实际不调，真实调用点 distill/repair/runner/smoke）。SC-3 ④ 覆盖。
  - **剩余风险** → SC-1 golden 覆盖 source_operation + boundary + conclusion 三类（防边界/结论 must_cover 纸面化）；分支提醒：当前 `experiment/deepseek-v4-pro-beta-strict-distill`，目标分支若为 `feature/main-pipeline` 需在目标分支复核。
- **2026-06-23 r4**：过 Codex cross-review 第二轮（总计划 + 8 子计划扩展范围），5 findings 全部经核实为真并修复（修在各子计划 + 本文案）：
  - **[P1] 问题1（step-4 无生产接入点）** → step-4 owned 加唯一生产接入点（`checks_structural.py` 新 SG-command-recheck 块，blocked_by step-3 串行）+ 生产路径测试，不再只有库函数+单测。
  - **[P2] 问题2（step-3 raw 审计不足）** → SC-3.3 从"非 None"升级为逐调用点 raw provenance 审计 + fixture 证明 `scan_all(raw)` 扫出预期 must_cover + 审计失败修复所有权。
  - **[P2] 问题3（step-6 degraded 漏 suspended + 缺 hash 验收）** → step-6 runner 步骤加 `suspended`；SC 加 grader 三函数体 + 10-dimensions sha256 比对。
  - **[P2] 问题4（step-7 must_cover/语义混用）** → step-7 fail-closed 对象改"semantic segment 连续不稳定"，声明不新增 must_cover kind、不改 scanner/SG/模型常量。
  - **[P2] 问题5（子计划分期未继承）** → step-1 删与 step-6 并行；step-6 blocked_by P0；step-8 blocked_by 全 P0+P1，SC-6 pass 须等 step-1~6。
  - **文案纠正** → `repair.py:494` 是示例字符串非执行断言（r2/r3 误引为铁证），已在 Context/关键文件改正；异族保护靠 SC-8。
- **2026-06-23 r5**：过 Codex cross-review 第三轮（窄范围复核）。前 4 项（step-4/6/7/8）确认通过；修 1 P2 + 3 剩余风险：
  - **[P2] step-3 实现步骤/边界与 SC-3.3 不一致** → 上轮只改 SC-3.3、漏改 Step.3 与边界（仍留"非 None / 不改调用点"旧口径）；现 Step.3 改逐调用点 raw provenance 审计、边界改"审计失败允许最小修复 raw 传递或 hard-stop，不得标 pass"。
  - **文案**：SC-3 expect "4 个调用点"→"5 处（4 类）"；step-8 边界补 `test_how_regression.py`；step-1 hard-stop 去掉"无 §X 即停"（scanner 用字符/行 span 定位，§X 锚点契约归链路层 SG-raw-anchor）。
- **2026-06-23 SC-9 补强（用户要求）**：补端到端达黄金验收 SC-9——原 8 条 SC 全验"机制零件"，无一条验"成品整车达黄金"；SC-9 用 test-fresh 真跑过 grader 全 10 维 + gate 10/10 + lint 0 作为计划最终判据。fail 分流：How 维回修机制 / 语义 5 维触发换 maker 评估（第二点，暂不执行）。**已知局限**：SC-9 通过依赖语义 5 维由当前 maker(deepseek) 写达标，本期不换 maker，故 SC-9 可能在语义维暴露缺口（这正是它的价值——把"机制对≠成品黄金"的真相显形）。

---

## Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：`evidence inventory scanner 精确回查原文(命令字面量可追溯回 raw); must_cover 由代码直接渲染进正文(LLM 无最终选择权); self-gate SG-must-cover fail-closed(漏/raw 缺失都挡)+全调用点审计; grader degraded fail-closed 护栏覆盖 repair loop 二次评分(fallback_used/suspended 不判收敛、不写 PASS); 本期维持 maker/checker 异族(不切 checker); FORBIDDEN 文件不动`

---

## 目标链（Goal Chain）

```
PROJECT_GOAL: wiki 层=AI 第二大脑，How 密度/准确/可靠/可执行第一，AI 读库更有效
CHAIN_GOAL:   现 How 靠 LLM 抽签式挑+意译，原文明示操作会漏、且会编造原文没有的命令，
              无任何机制保证"原文有的必进正文"，compose 频繁返回 None
PHASE_GOAL:   把"确定性 How"从"LLM 选择"变"代码强制渲染"——code 扫 raw 建账本、标 must_cover、
              代码直接摆进正文，机器校验覆盖，漏即 fail-closed
TASK_GOAL:    P0-1 建 scanner 独立模块 + test-fresh golden 操作清单，验"原文所有协议/CLI/代码操作 0 漏"
```

---

## 非目标（Non-Goals）

- **不改 `grader.py` 评分逻辑 + `skillpack/quality/rubric/10-dimensions.md` 量规**（FORBIDDEN，SC-7 守护）。
- **本期不切 checker、不换 maker**——保持现状 maker=deepseek / checker=gpt-5.4（**异族分离不变**）。本期 grader 侧只做 degraded fail-closed 护栏（重试 + repair/runner/promote 拒收 degraded PASS），**不动 `PRIMARY_GRADER_MODEL` 常量**。模型对调（checker→deepseek + maker→codex）作为原子押后单元，验证后单独做。
- **不动主链路 7 段骨架与 fail-closed 拓扑**。
- **不根治语义型 How**（隐含流程/决策/取舍）——交给 LLM segment + 多取兜底（P2）。
- **must_cover 不全量**——窄起步 3 类（决策 Q2），config/参数/数字阈值先进 inventory 不强制。

---

## 关键决策

**已实测定稿（见 memory `project_akb2_how_refactor_model_split`）**：
1. scanner = 独立模块 `src/akb2/quality/source_ops_scanners/`
2. 模型终态：maker=codex/gpt-5.4 + checker=deepseek-v4-pro；**本期都不切**（绑原子押后单元，避免中间态同族——Codex review 问题2）
3. 现状已确认：提炼 deepseek（distill L52）/ 评分 gpt-5.4（grader L42），**当前异族，本期维持**

**Q1 — grader degraded 护栏（本期，不换模型）**：
现状 `run_grader_detailed`（L341）调 route_llm 不传 strict（默认 True），**一次解析失败就直接切 gemini 兜底**（L363），gemini 是已知"假 0 缺陷"放水模型。本期判断（不动 L42 模型常量）：① 接线 `MAX_GRADER_RETRIES=2`（L46），解析失败先重试 2 次 ② 重试后仍坏 JSON 宁可 SUSPEND（fail-closed）也不无声掉 gemini；保留 gemini 兜底则 `fallback_used=True` ③ **repair loop 二次评分 + runner + promote 全部拒收 fallback_used/suspended 的 PASS**（问题1）。strict 保留 True。模型对调相关的 strict 行为（deepseek 是否遵循）押后到对调单元处理。

**Q2 — must_cover 窄起步 3 类**：
| 类 | 判定（代码确定性识别 + 精确回查 raw） | 渲染目标节 | golden 覆盖 |
|----|----------------------|-----------|-----------|
| `source_operation` | 原文出现的 MCP 方法 / API / CLI / 代码块可执行命令 | How 段（操作步骤） | SC-1 必含 |
| `source_boundary` | 原文"仅适用/前提/要求/不支持"等明示边界句 | 适用边界段 | SC-1 必含 |
| `source_conclusion` | 原文作者明确下的结论/核心论断 | TLDR / 论断位 | SC-1 必含 |
不进 must_cover（先进 inventory 不强制）：config_kv / parameter / 数字阈值。语义争议项不进，留 LLM segment + grader 维度 2/3 把关。

---

## 分期与押后决策

| 阶段 | 内容 | 依赖 | 位置理由 |
|------|------|------|---------|
| **P0** | scanner + must_cover 代码直出 + SG-must-cover（含全调用点审计） | 无（不碰模型） | 命根子；用代码兜住覆盖治"漏+编"，与模型无关 |
| **P1** | 机器回查；provenance 细分；**grader degraded fail-closed 护栏（不换 checker，覆盖 repair loop）** | P0 | 夯硬确定性 + 堵 degraded 放水洞（与换不换模型无关，本期就该修） |
| **P2** | How 回归测试三类语料；语义型 How 抖动兜底 | P0/P1 | 防退化 + 稳定复现 |
| **押后（原子单元）** | 模型对调：checker→deepseek **同期** maker→codex/gpt-5.4 + `maker_model != checker_model` 硬测试 | P0/P1 验证后 | 改动大（动 extract strict 抽取）；**任一单切都破异族（问题2），必须同期** |

---

## 成功标准（SC）

- [ ] **SC-1（P0-1 scanner 捞全，golden 覆盖三类）**：`source_ops_scanners/` 建立 + test-fresh golden 清单就位，scanner 对 test-fresh 的 inventory ⊇ golden set
  - 前置：P0-1 先产出 `tests/quality/fixtures/test-fresh-golden-ops.json`（人工标注 test-fresh raw 的 **source_operation + source_boundary + source_conclusion 三类** must_cover 项 + 各自 raw_anchor），作为验收基准
  - verify (method: test)：`PYTHONPATH=src python -m pytest tests/quality/test_source_ops_scanners.py -q`，断言 inventory 对三类 must_cover 集合分别 ⊇ golden set
  - expect：exit 0；三类 inventory ⊇ golden（**缺 golden fixture → SC-1 标 not_run，不得标 pass**）
- [ ] **SC-2（P0-2 must_cover 代码渲染）**：`_build_body` 把 must_cover 项代码直接渲染进对应节，删掉 LLM segment 仍在
  - verify (method: test)：含 must_cover 的 fixture 断言对应节包含每个 must_cover 字面量；mock 掉语义 segment 后 must_cover 仍渲染
  - expect：must_cover 项 100% 出现在目标节
- [ ] **SC-3（P0-3 SG-must-cover fail-closed + 调用点审计）**：`run_candidate_self_gate` 加 SG-must-cover；raw_content 缺失也 fail-closed；全调用点已审计
  - verify (method: test)：① "原文有操作但正文漏渲染" → 返回 `dimension="SG-must-cover"` Defect ② 不漏 → 不产 Defect ③ **`raw_content=None` → 产 fail-closed Defect（不静默跳过）** ④ **调用点审计**：静态断言所有生产调用点（`distill.py:1481` / `repair.py:1125,1248` / `runner.py:629` / `smoke_validator.py:441`）要么传真实 raw、要么有显式 fail-closed 测试
  - expect：漏/无法验证 → fail-closed；5 处位置（4 类文件）全部传真实 raw 或显式 fail-closed
- [ ] **SC-4（P1 机器回查）**：进正文的命令字面量必须能精确回查回 raw；编造命令被挡
  - verify (method: test)：候选含"原文没有的命令"→ fail；含"原文有的命令"→ pass 且可定位 raw 行
  - expect：编造命令被挡、真实命令可追溯
- [ ] **SC-5（P1 grader degraded fail-closed 护栏，覆盖 repair loop）**：接线 `MAX_GRADER_RETRIES` + repair loop / runner / promote 全部拒收 degraded PASS（**本期不换 checker**）
  - verify (method: test)：① mock grader 首次坏 JSON → 断言发生重试 ② 重试耗尽 → suspended 或 `fallback_used=True` 标 degraded ③ **repair loop**：构造"repair 后 `grader_result.fallback_used=True` 且 `defects=[]`" → 断言 `run_repair_loop` **不判收敛**（repair.py:1290 不走 converged） ④ **下游**：runner `_persist_grader_pass` 不写 fallback PASS；promote `_read_grader_result` 对 fallback_used/suspended 的 PASS 文件返回 not-ok
  - expect：重试生效；degraded 在 repair / runner / promote 三处都 fail-closed，不被当收敛 PASS
- [ ] **SC-6（P2 回归测试，含语料就位）**：test-fresh + 纯观点博客 + 陷阱样本三类
  - 前置：P2-8 子计划明确"纯观点博客""陷阱样本"两类语料的来源/创建方式 + 落地 `tests/quality/fixtures/regression/`
  - verify (method: test)：三类语料文件存在 + 各自断言（有操作→捞全 / 无 How→fail-closed / 枚举带偏→抽核心）
  - expect：语料就位 + 三类通过（**语料缺失 → 标 not_run，不得标 pass**）
- [ ] **SC-7（FORBIDDEN 不动，hash 到函数体级）**：grader 打分逻辑 + 10-dimensions 量规未改判定规则
  - verify (method: command)：对 grader.py 的 `_build_grader_prompt` / `_build_grader_schema` / `_parse_grader_output` **三个函数的函数体文本**（AST 或 def 行范围提取，**排除** `MAX_GRADER_RETRIES` 重试控制流改动）+ `10-dimensions.md` 全文，分别算 sha256，改动前后比对
  - expect：三函数体 hash + 量规 hash 改动前后**完全不变**（本期不动 L42，护栏改的是 repair/runner/promote + 重试循环，不碰这三个打分函数体）
- [ ] **SC-8（全量绿 + 异族断言）**：`python -m pytest tests/quality/ tests/loops/ -q -m "not integration" && ruff check src/akb2/quality/`
  - 附加：断言本期收尾时 `DISTILL_MODEL_FAMILY != PRIMARY_GRADER_MODEL`（维持异族，问题2）
  - expect：PASS（integration deselected）+ lint 0 error + maker≠checker
- [ ] **SC-9（端到端达黄金验收 · 计划最终判据）**：test-fresh 真文章经链路产出的成品，过那道**已有运行时守卫**的全部门槛——grader 10 维量规全 pass + gate CLI 10/10 + lint 0 findings
  - verify (method: command)：`AKB2_AUTO_PROMOTE=0 PYTHONPATH=src python -m akb2.quality.runner --once --raw-dir <test-fresh 目录> --vault-root /tmp/akb2-how-p0/vault --staging-dir /tmp/akb2-how-p0/staging`（隔离）→ 对产出候选页跑 `python -m akb2.quality.grader`（10 维）+ `akb2_gate_check`（10 硬检查）+ `akb2_lint`
  - expect：grader **10 维全 pass** + gate 10/10 + lint 0
  - **诚实分流（fail 时按维度）**：fail 落 How 维（1 锚定 / 2 How 密度 / 3 How 可执行）→ P0/P1 机制有漏，回修；fail 落语义 5 维（4 证据诚实 / 6 TLDR / 7 新颖 / 8 边强度 / 9 Future Questions）→ **非本计划机制缺陷**，触发"换 maker 评估"（押后单元提前 deepseek→codex/gpt-5.4）；**任何情况不得为过 SC-9 降量规**
  - 依赖：step-1~6（全 P0+P1）落地后执行（由 step-8 收尾段或单独收尾跑）；SC-9 是"验整车上路"，区别于 SC-1~8 的"验零件"

---

## 执行步骤

### P0 · 把确定性机制装进链路（命根子，三件一体）

**P0-1 建 scanner 独立模块 + golden 基准（三类）**
- 新建 `src/akb2/quality/source_ops_scanners/`：`__init__.py` + 每类一个 scanner 函数，输入 raw 文本 → 输出 evidence item（`kind` / `literal` / `raw_anchor_span`）
- 起步 scanner：`cli_commands`、`code_blocks`、`api_signatures`、`numbered_steps`、`mcp_protocol`（**吸收现有 `_SOURCE_PROTOCOL_COMMANDS` L2412**）；并出 `source_boundary` / `source_conclusion` 识别（明示边界句 / 结论句）
- 每项带 `raw_anchor_span` 供 P1 回查
- **先产出 golden fixture** `tests/quality/fixtures/test-fresh-golden-ops.json`（**三类 must_cover** + raw_anchor），作 SC-1 基准
- **复用**：现有 `_raw_source_how_steps`（distill L2612）扫描逻辑迁入并扩展
- 测试：`tests/quality/test_source_ops_scanners.py`
- **检查点**：单独 commit

**P0-2 must_cover 代码直接渲染进正文**
- `distill.py:_build_body`（L2851）：在 How 段渲染处（`how_text` 生成，约 L2949 *待实现核实*）插入 must_cover 代码直出——operation→How / boundary→适用边界 / conclusion→TLDR/论断位
- must_cover 标记在 compose 阶段产生（scanner 跑在 `_build_body` 之前），挂到传入数据上
- **代码直接摆，LLM 无选择权**
- 测试：SC-2

**P0-3 SG-must-cover 覆盖检查 + fail-closed + 调用点审计**
- `checks_structural.py:run_candidate_self_gate`（L977）在 `return defects`（L1087）前追加 SG-must-cover：内部用 scanner 扫 `raw_content` 得 must_cover → 比对正文 → 缺失项产 `Defect(dimension="SG-must-cover")`
- **raw_content 为 None 时**（签名 `raw_content: str | None = None`，L979）：**不静默跳过**，产 `Defect`
- **调用点审计（Codex 问题3）**：self-gate 真实调用点 = `distill.py:1481`（compose）/ `repair.py:1125,1248`（repair loop）/ `runner.py:629` / `smoke_validator.py:441`——**注意 promote 不调 self-gate（原计划误写已修正）**。逐个保证传真实 raw 或显式 fail-closed；旧 raw-optional 测试语义（`tests/quality/test_checks_structural.py`）与新 fail-closed 语义分开测，避免假红
- **复用**：现有 `Defect` 数据类（L35）+ 现有 SG 检查模式（L993-1086）
- 测试：SC-3
- **检查点**：P0-2 + P0-3 一起 commit

### P1 · 夯硬确定性 + degraded 护栏（不换模型）

**P1-4 机器回查**：用 P0-1 的 `raw_anchor_span` 精确回查 raw。测试 SC-4。
**P1-5 provenance 细分**：`source_operation`/`source_fact`/`akb_interpretation` 加标注层——只加标注，不改量规 schema（守 SC-7）。
**P1-6 grader degraded fail-closed 护栏（本期不换 checker，Codex 问题1）**：
- 接线 `MAX_GRADER_RETRIES`（grader L46）做解析失败重试循环（不动 L42 模型常量）
- **repair loop**（`repair.py:1261-1294`）：repair 后二次评分，`fallback_used=True` 或 `suspended=True` 都**不判收敛**（现仅检 `suspended` L1270）
- **runner**（`runner.py:600` `_persist_grader_pass`）：fallback_used 时不写 PASS
- **promote**（`promote.py:183-194` `_read_grader_result`）：对 fallback_used/suspended 的 PASS 文件返回 not-ok
- **复用** GraderResult 字段 `fallback_used`/`suspended`（grader L77/65）
- 测试：SC-5。**检查点**：单独 commit。

### P2 · 稳定复现 + 防退化
**P2-7** 语义型 How 抖动兜底：关键段多取（同段产 2 次取交集）/ 连续漏同类 → fail-closed。
**P2-8** How 回归三类语料，落 `tests/quality/fixtures/regression/`：test-fresh（已有）+ 纯观点博客（raw/ 取无 How 观点文或人工构造）+ 陷阱样本（大量次要操作 + 核心藏结论，验抽核心不被带偏）。测试 SC-6。

### 押后（原子单元，P0/P1 验证后单独计划）
模型对调：`grader.py:42` checker→deepseek **同期** `distill.py:52` maker→codex/gpt-5.4（动 `extract_claims` strict 抽取 → 自由文本）+ 加 `maker_model != checker_model` 硬测试 + 处理 deepseek strict 遵循度。**禁止单切任一侧**（破异族）。

### P3 · 顺手清旧 bug（不优先）
锚点漂移 / 占比超标 / 对照表写死 / 路径双层嵌套——多数已修，剩余顺手清。

---

## 验证（端到端）

1. **单测**：`PYTHONPATH=src python -m pytest tests/quality/ -q -m "not integration"`
2. **scanner 捞全（SC-1）**：对 test-fresh raw 跑 scanner，比对 golden（三类），inventory ⊇ golden
3. **degraded 护栏（SC-5）**：mock degraded GraderResult，断言 repair 不收敛 + runner 不写 PASS + promote 拒收
4. **端到端隔离跑**：`AKB2_AUTO_PROMOTE=0 PYTHONPATH=src python -m akb2.quality.runner --once --raw-dir <test-fresh 目录> --vault-root /tmp/akb2-how-p0/vault --staging-dir /tmp/akb2-how-p0/staging`（**必带 `--staging-dir`**），确认 How 段含原文操作 0 漏、compose 不返回 None
4b. **端到端达黄金（SC-9 · 最终判据）**：对上一步产出候选页跑 grader 10 维 + gate 10/10 + lint 0；fail 按维度分流（How 维 1/2/3 → 回修 P0/P1 机制；语义维 4/6/7/8/9 → 触发换 maker 评估，不降量规）
5. **FORBIDDEN + 异族**：grader 三函数体 + 10-dimensions sha256 不变；`DISTILL_MODEL_FAMILY != PRIMARY_GRADER_MODEL`
6. **lint**：`ruff check src/akb2/quality/`

---

## 关键文件（核实状态标注）

**亲读核实（可信）**：
- `src/akb2/quality/checks_structural.py`：`run_candidate_self_gate` L977（签名 raw_content 默认 None L979）；7 道 SG 检查 L993-1086；`return defects` L1087（接入点）；`Defect` L35
- `src/akb2/quality/distill.py`：`DISTILL_MODEL_FAMILY="deepseek-v4-pro"` L52；`extract_claims` L415；`compose_candidate` L1411（self-gate 调用 L1481）；`_build_body` L2851；`_SOURCE_PROTOCOL_COMMANDS` L2412；`_raw_source_how_steps` L2612
- `src/akb2/quality/repair.py`：repair loop 二次评分 `run_grader_detailed` 调用 + 收敛判断 **L1261-1294**（仅检 suspended L1270，漏 fallback_used）；self-gate 调用 L1125/1248。**注：L494 的 `assert maker!=checker` 是 `_claim_to_how_step` 生成的示例命令字符串（非执行断言），异族保护实际靠 SC-8 断言**
- `src/akb2/quality/runner.py`：self-gate 调用 L629；`_persist_grader_pass` **L600**（写 status=PASS，记 fallback_used 但未用于阻断）
- `src/akb2/quality/promote.py`：`_read_grader_result` **L183-194**（仅看 `status=="PASS"` L194，不看 fallback_used）；**不调 self-gate**
- `src/akb2/quality/smoke_validator.py`：self-gate 调用 L441
- `src/akb2/quality/grader.py`：`PRIMARY_GRADER_MODEL="gpt-5.4"` L42（**本期不动**）；`MAX_GRADER_RETRIES=2`（未接线）L46；`run_grader_detailed` L315（primary L341 / gemini 兜底 L363）；`GraderResult` L65（`fallback_used`/`suspended`）
- `src/akb2/quality/llm_router.py`：`route_llm` L434（`strict_schema` 默认 True；strict 发送 L496-504）

**Explore agent 转述（待实现核实）**：`_build_body` 的 `how_text` 生成约 L2949、三级筛选约 L2879-2886

**新建**：`src/akb2/quality/source_ops_scanners/`、`tests/quality/test_source_ops_scanners.py`、`tests/quality/fixtures/test-fresh-golden-ops.json`、`tests/quality/fixtures/regression/`

**FORBIDDEN（只读，不改判定）**：`grader.py` 评分逻辑、`skillpack/quality/rubric/10-dimensions.md`

**测试基建复用**：`tests/quality/conftest.py` 的 `make_mock_route_llm` fixture；正/负样本断言模式

**测试语料**：`/Users/praise/Obsidian/Praise Space/AKB2.0/raw/test-fresh-entry--raw--2026-06-21.md`

**分支提醒**：当前 `experiment/deepseek-v4-pro-beta-strict-distill`；目标分支若为 `feature/main-pipeline` 需复核
