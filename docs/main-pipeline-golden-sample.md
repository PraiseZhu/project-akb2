# 主链路黄金样张 — 14 步验收基准

> 来源:2026-06-11 手动入库 Addy Osmani《Loop Engineering》全程实录。
> 用途:主链路(Loop A 抓取入库)自动化的每一步验收基准。写管线代码时,每步的测试断言对照本清单的"标准"列;黄金样张文件是回归测试的期望输出。
> 样张状态:已升格 active,gate 10/10 pass,lint 0 findings。

## 样张文件清单(回归对照物)

| 角色 | 路径(vault 相对) |
|------|------------------|
| raw 归档 | `raw/addy-osmani-loop-engineering--raw--2026-06-11.md` |
| wiki 候选(保留) | `staging/candidate/manual-source-intake-2026-06-11/addy-osmani-loop-engineering.md` |
| active 页 | `wiki/sources/addy-osmani-loop-engineering.md` |
| manifest | `manifests/ingest/2026-06-11.jsonl` |
| 索引行 | `wiki/sources/00-Index.md` §4 blog / 2026-06 |
| 概念回边 ×3 | `wiki/concepts/concept-{harness-engineering,agent-collaboration,context-engineering}.md` 图谱关系表末行 |

## 14 步清单

### 阶段 1:抓取(步骤 1-2)→ 自动化归 Loop A intake

| # | 动作 | 输入 | 标准(验收) | 输出 | 本次样张证据 |
|---|------|------|-----------|------|-------------|
| 1 | **抓取原文** | 源 URL | ① 用无认证通道(defuddle/Docling/yt-transcript),禁 WebFetch 认证站;② 拿到正文 markdown + 元数据(title/author/published/wordCount);③ 抓取失败显式报错,不静默跳过 | 全文 markdown + 元数据 JSON | defuddle 抓取 2,588 词;元数据 `published: 2026-06-07` 自动提取 |
| 2 | **raw 锚点化落地** | 步骤 1 输出 | ① 文件名 `<slug>--raw--<YYYY-MM-DD>.md`;② frontmatter 9 字段(artifact_type/source_slug/source_url/source_title/source_authors/source_publisher/source_published_at/fetched_at/fetched_via);③ 原文标题加 §A-§Z 锚点,**每个锚点替换必须 assert 命中**(防原文改版静默漏锚);④ 带锚点目录表;⑤ 英文原文不翻译;⑥ 传播数据等时点信息标注实测时间 | `raw/<slug>--raw--<date>.md` | §A-§J 10 锚点,assert 全命中;6.5M 浏览数标注"CDN 实测于 2026-06-11" |

### 阶段 2:提炼(步骤 3-4)→ 自动化归 DeepSeek 两段式

| # | 动作 | 输入 | 标准(验收) | 输出 | 本次样张证据 |
|---|------|------|-----------|------|-------------|
| 3 | **知识提炼** | raw 文件 | ① 每个核心论点带 `[raw §X]` 锚点引用,**无锚点的论断不许出现**;② How 内容单独成节(本次:"一个完整 loop 的形状(How 模板)");③ 区分 source_grounded 与 akb_interpretation 两种 provenance;④ 证据地图含"未覆盖"列(诚实暴露盲区) | wiki 页正文草稿 | 5 论点全带锚点;证据地图 6 行含未覆盖列 |
| 4 | **候选页组装** | 步骤 3 草稿 | ① 模板 10 节齐全:TLDR/关键发现/入选理由/证据地图/核心论点/论证与解释/适用边界/图谱关系/Future Questions/维护记录;② frontmatter:`status: candidate` + **`gate_decision: null`(不许预填 active)** + `source_refs` ≥1 + `future_questions` 2-3 条 + `related_concepts` ≥2;③ 图谱关系表每条边带关系类型+证据;④ 落 `staging/candidate/<batch>/` | 候选 .md | 首版违反②被 gate 拦(见步骤 5),这是本清单最易踩的坑 |

### 阶段 3:门控(步骤 5-8)→ 自动化归 gate CLI(已存在,确定性)

| # | 动作 | 输入 | 标准(验收) | 输出 | 本次样张证据 |
|---|------|------|-----------|------|-------------|
| 5 | **gate 首跑(预期可拦)** | 候选批次目录 | ① dry-run 模式;② fail-closed:任何硬检查不过 → `needs_repair` + 可定位的 `repair_actions`,不放行 | gate 提议报告 | 首跑拦截:`fix:HC-05-EVIDENCE`(缺 source_refs)+ `fix:HC-09-NO-AUTO-ACTIVE`(预填了 active) |
| 6 | **定点修复** | repair_actions | ① 只修被点名项,不重写整页(宪法 P2:收敛优先于重跑);② 修复轮次有界(对齐 audit_fix 模式,max 3 轮) | 修订后候选 | 仅改 frontmatter 2 处,正文未动 |
| 7 | **gate 复跑至 pass** | 修订候选 | ① `proposed_gate_decision: active` + `decision_confidence: high` + `graph_readiness: real_link`;② 达不到 → 回步骤 6;轮次耗尽 → 留 staging,**不入库**(fail-closed) | pass 报告 | 第 2 轮即 pass(10/10 硬检查) |
| 8 | **manifest 落账** | pass 的候选 | ① `--write-manifest --allow-l3-manifest-write` 双 flag 显式授权;② jsonl 记录含 10 项硬检查逐项结果(可审计);③ manifest 只记提议,不改 active | `manifests/ingest/<date>.jsonl` | 1 条记录,hard_checks 数组完整留痕 |

### 阶段 4:升格(步骤 9-10)→ 五闸自动升格(修宪 2026-07-06)

> **修宪记录(2026-07-06,用户裁决原话「那就改宪法 我要自动化」)**:步骤 9 逐条人批废除,
> 由五闸自动升格全面接管(gate 10/10 + confidence + graph + lint 0 + GPT-5.4 复核 PASS,
> 承接 loops-design-decisions「取消逐条人批」既定方向);且冷启动 graph_pending
> (proposed_node_only)候选**同样允许 auto-active**——缺失概念落 pending 队列,由图谱
> 兜底链路(D8 pending 共指 → 图谱校准补边)补全。graph_pending 升格必落账(fail-visible)。
> 质量五闸与 wiki 内容标准零放宽。下表步骤 9 行保留为历史原文,以本注记 supersede。

| # | 动作 | 输入 | 标准(验收) | 输出 | 本次样张证据 |
|---|------|------|-----------|------|-------------|
| 9 | **人批** | gate pass 报告 + manifest | ① 不变量:没有自动 active,**任何管线代码不得实现此步**;② 批准指令显式(本次:"升") | 授权 | 用户单字"升" |
| 10 | **升格写入 active** | 批准 + 候选 | ① frontmatter 翻转:`status: active` + `gate_decision: active`,去掉"候选自评"前缀;② 维护记录追加升格行(含 gate 结果 + manifest 路径 + 批准依据);③ staging 候选**保留**(对照样张惯例);④ raw 不动 | `wiki/sources/<slug>.md` | 维护记录 2 行:初始创建 + 升格 |

### 阶段 5:图谱与收尾(步骤 11-14)→ 自动化归 Loop A 收尾段

| # | 动作 | 输入 | 标准(验收) | 输出 | 本次样张证据 |
|---|------|------|-----------|------|-------------|
| 11 | **索引更新** | active 页 | ① `00-Index.md` 对应分组(§4 blog/按月)插入条目行;② 条目是**一句人读摘要**(覆盖:来源+日期+核心内容+服务哪些概念+入库依据),不是裸文件名;③ 修订历史表追加行 | Index 2 处更新 | 2026-06 新分组 + 摘要行 |
| 12 | **概念页回链** | active 页的 related_concepts | ① 每个被引概念页的图谱关系表加回边(target/relation/strength/evidence 四列齐);② strength 按证据强度定(原文明示=strong,场景应用=medium),不一律 strong;③ 每页维护记录追加行;④ 引用不存在的概念 → 不建 wikilink(防 GRAPH-DEAD-LINK),落 pending 队列 | 3 概念页各 +1 边 | harness=strong(原文明示"楼上一层")/collaboration=strong/context=medium |
| 13 | **lint 全量** | 本次所有改动文件 | ① active 页 + 全部被改概念页都过 lint;② P0/P1 必须修;P2 同样修(本次维护记录溢出);③ 修复手法优先合并/精简,不删信息 | lint 报告 | 拦 2 项:gate_reason 166>150 字符(P1)、维护记录 6>5 条(P2) |
| 14 | **清零复验** | 修复后文件 | ① 全部文件 lint **0 findings**;② 此时才可声明完成(对齐执行完成合约) | 0 findings | `akb2-lint v0.2 — 2 file(s): 0 findings` |

## 自动化映射(写管线时的边界判定)

| 步骤 | 自动化 | 触发器 | fail-closed 落点 |
|------|--------|--------|------------------|
| 1-2 抓取 | ✅ Loop A | schedule | 抓取失败 → 记日志跳过,显式计数 |
| 3-4 提炼 | ✅ DeepSeek 两段式 | Loop A 串联 | 锚点 assert 失败 / 模板节缺失 → 不出候选 |
| 5-8 门控 | ✅ gate CLI(现成) | Loop A 串联 | needs_repair 且修复轮次耗尽 → 留 staging |
| 9 人批 | ✅ 已废除(修宪 2026-07-06,五闸自动升格含 graph_pending) | Loop A 串联 | 五闸任一不过 → 留 staging |
| 10-14 升格+图谱+lint | ✅ 批准后一键(本次手动脚本可直接固化) | 人批触发 | lint 非 0 → 不声明完成 |

## 已知坑(样张实测踩到的)

1. **候选页预填 `gate_decision: active` 必被 HC-09 拦** — 提炼模型的输出模板里必须写死 `gate_decision: null`
2. **`source_refs` 是硬指标(HC-05)** — 模板必填,至少含 raw 路径
3. **`gate_reason` ≤150 字符(lint P1)** — 提炼 prompt 要给字符上限
4. **维护记录 ≤5 条(lint P2)** — 回链脚本追加前先数行数,超了先合并同日条目
5. **概念页引用不存在节点不能用 wikilink** — 先查节点存在性,缺失的进 pending 队列(harness 页底部有现成格式)
