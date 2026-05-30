# Plan: AKB2 社交日报 v3 — 按账号抓取重建 + 与后半段完美对接 + 结构精炼

日期：2026-05-31
状态：draft（待用户批准后执行）
负责人：Claude Code（worktree `akb2-briefing-zh-pipeline`，分支 `worktree-akb2-briefing-zh-pipeline` → 最终并入 `feature/automation-pipeline-v2`）
计划方式：manual single-controller（GD `/gd plan` 正式多 step dispatch 门控因 `gd-build-dispatch-map.py` / `gd-validate-planning-dispatch-log.py` 两脚本从未实现而跑不通，故不套 GD 重型流程，按本地计划文档走）

---

## Context（为什么做这件事）

中文化（v1）解决"读不懂"；v2（curious-brewing-unicorn.md，已实现并 Codex APPROVED）把结构改成"核心讨论为骨架 + 嵌套推文 + 日期过滤"。但真实跑出来内容是 **AI 女友广告 / hentai / 加密狗血**——因为整条 intake 的**唯一真实抓取路径是关键词广搜**（`twitter search "AI OR LLM OR AGI" -t latest`）。

关键事实（已核对代码）：
- `intake/l0_collector.py`：**唯一真抓**，走关键词搜索（`_fetch_real`），失败降级 fixture。
- `intake/l0_prime_collector.py` / `l3_collector.py`：**fixture 切片占位 stub**（读 `sample.json` 的 `[:3]` / `[-5:]`），不真抓。
- `intake/l4_collector.py`：arxiv stub，`enabled: false`。
- `config/curator-accounts.yaml`：101 个策展博主 handle（留档），**未接入任何抓取**；仅 22 个进了 `scoring.py` 的 `AUTHORITY_WHITELIST`（只打分加权）。

**原设计被实现倒置**：原计划 L0 = 账号清单时间线（权重 1.0）为主路径，关键词只是 L4 fallback；实现成了关键词为唯一主路径，账号清单从没驱动过抓取。

用户已锁定本轮范围与决策（见下）。核心是把抓取从"关键词广搜"改成"按策展账号抓时间线"，并把已实现的后半段结构按新偏好精炼。

---

## 对接契约（"完美对接"的精确定义）

后半段消费 intake 的**唯一接口**是这个 dict 形状（已核对）：

```jsonc
{
  "tweets": [
    {
      "id": "string",
      "text": "string",
      "author": "@handle",
      "created_at": "ISO8601",
      "url": "https://x.com/<handle>/status/<id>",
      "metrics": {"likes": int, "retweets": int, "replies": int}
    }
  ],
  "_fallback_used": true   // 可选；为 true 时 pipeline 跳过产出层日期过滤
}
```

- `pipeline.run_intake`（pipeline.py:115）subprocess 调 collector，传 `--since/--until`，读回上述 dict。
- 下游 `run_scoring`→`run_synthesis`→translate→audit→render **只认这个形状**。
- **结论**：只要重建后的 intake 仍产出这个 dict 形状，后半段**一行不用改**。这就是"完美对接"——无 schema 变更、无下游改动。

---

## Review 对齐

REVIEW_DOMAIN：app_code

REVIEW_FOCUS：
- 账号抓取正确性（按 curator 清单逐账号抓时间线、跨账号按 id 去重、空账号/失败账号不拖垮整批、保持对接 dict 形状）
- 抓取边界与确定性（rate-limit/账号数/单账号条数有硬上限；pacing 用确定性代码不靠模型；时间窗 Asia/Shanghai 一致；全部失败才降级 fixture 并打 `_fallback_used`）
- 结构精炼正确性（核心讨论不限字数；每条核心讨论挂 2-3 条高分支撑推文带原文链接 + 全文 500 字截断、去折叠；translate/audit 同步去除字数/折叠相关逻辑且不崩）

Domain-specific notes（app_code）：
- changed files：`intake/`（新账号 collector + l0 降级为 fallback）、`config/curator-accounts.yaml`（加 fetch tier）、`config/intake_sources.yaml`、`pipeline.py`（run_intake 接账号 collector）、`synthesis.py`（去 summary 字数下限）、`translate.py`（去 core_excerpt）、`pipeline.py` 渲染（500 字截断/去折叠）、`audit.py`（去句数检查）、`docs/deepseek-synthesis-prompt-v1.md`、测试
- API contracts：intake 对接 dict 形状**不变**；渲染输出形态变化（截断替代折叠）
- data schema：无 schema 变更（复用 social_trends_v1_1 + supporting_tweet_ids）
- tests：新增账号抓取/去重/上限测试；改 translate/audit/render 测试（去折叠、去句数）
- security/performance：抓取调用量 = tier账号数 × 单账号上限，必须有硬天花板 + pacing；不记录 cookie/key；raw 日志脱敏
- deployment runtime：仍由 daily_briefing.sh / LaunchAgent 调用；不改调度

---

## 目标与验收

用户目标：
- 日报抓取改为**按策展博主清单抓时间线**（不再关键词广搜），内容回到 AI 圈真实高质量讨论
- 与已实现的后半段无缝对接（不改 scoring/synthesis/translate/audit/render 的对接契约）
- 结构精炼：核心讨论由模型提炼、不限字数；每条核心讨论下挂 2-3 条高分支撑推文，带**原文链接** + **全文（超 500 字截断）**，**去掉折叠**

成功标准：

### P1 抓取重建
- [ ] **SC-1** 新增按账号抓取 collector：读 `curator-accounts.yaml` 的 fetch tier，逐账号 `twitter search --from <handle> -t latest --since <date-N> --until <date+1> --json -n <cap>`
- [ ] **SC-2** 跨账号合并去重（按 tweet `id`），输出仍为对接契约 dict 形状（`tweets` + 可选 `_fallback_used`）
- [ ] **SC-3** 硬边界：fetch tier 账号数上限、单账号条数上限 `cap`、账号间 pacing（确定性 sleep）、全局总条数上限；均可 env 覆盖
- [ ] **SC-4** 降级语义：单账号失败/空 → **从候补池（reserve_tier 18）自动顶替补抓**，并 stderr 告警，不中断整批；候补耗尽且**全部账号失败** → 降级关键词搜索（旧 L0），仍失败 → fixture 并标 `_fallback_used`
- [ ] **SC-5** `curator-accounts.yaml` 分层：`fetch_tier` **80** 个获取层 + `reserve_tier` **18** 个候补层（98 净可用 = 80 + 18；人工核对 handle 拼写、剔除 3 个疑似 OCR 错误、修正 mckaywrigley）
- [ ] **SC-6** `pipeline.run_intake` 切到账号 collector（保持 `--since/--until` 传参与返回形状不变；关键词 collector 保留为 fallback）

### P2 结构精炼（在 v2 已实现结构上做 delta）
- [ ] **SC-7** 核心讨论 summary **去字数下限**（synthesis prompt 去"≥3 句/3-5 句"硬约束，改为"由模型据推文充分提炼、不限字数"）
- [ ] **SC-8** 每条核心讨论下渲染 **2-3 条**高分支撑推文（按评分取该主题 supporting_tweet_ids 的 top 2-3）
- [ ] **SC-9** 支撑推文渲染：显示**原文链接**（`url`）+ **全文**，**超过 500 字截断**（附"…（已截断）"），**移除折叠**（`<details>`/core_excerpt 折叠块）
- [ ] **SC-10** translate 去 `core_excerpt`（不再需要折叠核心段，全文截断展示）；仍保证展示推文有中文 `zh_point`；LLM 失败降级不崩
- [ ] **SC-11** audit 去 summary 句数检查（与 SC-7 一致）；保留 zh_point 中文检查、supporting_tweet_ids 存在性检查

### 全局
- [ ] **SC-12** `pytest tests/briefing/` 全绿；新增覆盖账号抓取/去重/上限/降级 + 结构 delta（去折叠/500 字截断/去句数）
- [ ] **SC-13** 真实 key + 真实 cookie 跑一次：内容为策展账号真实推文、核心讨论挂支撑推文带链接全文、无折叠

非目标（本轮明确不做）：
- L1/L2 信任传播层（Quote/Retweet/Reply 加权抓取）、L0' X List 抓取、L3 arxiv/HF — 留后续
- 自进化清单（self-evolving list）、Discovery_layer/Discussion_intensity 新评分维度 — 留后续
- scoring 评分算法本体（除 author_authority 因账号抓取自然更有意义外不动逻辑）
- gate / validator / schema / 调度 / secrets 常驻（P2 范围外）
- 时间窗 N、fetch tier 规模、cap/K 等参数的最终调优（先定默认，真实跑后调）

---

## 当前状态（已核对事实）

- intake 唯一真抓 = `l0_collector._fetch_real`（关键词）；`l0_prime`/`l3` 是 fixture 切片 stub；`l4` disabled
- `l0_collector` 已支持 `--since/--until/-t latest/--json/-n`（CLI flag 齐全）
- twitter CLI 已验证支持 `--from <handle>`（示例 `twitter search --from bbc --exclude retweets` 无需关键词）、`--since/--until/-t latest/--json/-n/-o`
- `run_intake`(pipeline.py:115) subprocess 调 l0_collector，传 `--since/--until`，读回 `{"tweets":[...]}`
- `_filter_recent`(pipeline.py:62) 产出层日期兜底；`_fallback_used` 为真时跳过（pipeline.py:475）
- `curator-accounts.yaml`：101 handle + 3 疑似 OCR 错误（mickaywiggley→mckaywrigley / somolicick / somonie）；无 tier 分层
- `scoring.AUTHORITY_WHITELIST`：22 名（账号抓取后 author_authority 维度更有意义）
- v2 已实现：核心讨论嵌套结构、长推折叠、summary ≥3 句约束、audit 句数检查 — 本轮 P2 **反转**折叠/句数这两项

> **本计划显式 supersede** `curious-brewing-unicorn.md` 的：SC-1（长推折叠 → 改 500 字截断去折叠）、SC-3（summary ≥3 句 → 去字数下限）、SC-8（audit 句数检查 → 移除）。其余 v2 决策保留。

---

## 代码执行方向

新增/修改：
- **新增** `intake/account_collector.py`（按账号抓时间线，核心新件）：
  - 读 `curator-accounts.yaml` fetch tier → 逐账号 subprocess `twitter search --from <h> -t latest --since --until --json -n <cap>`
  - 跨账号按 `id` 去重；账号间确定性 pacing sleep；全局条数上限
  - 复用 `l0_collector._twitter_cli_to_akb2` 的 adapter（提取到 base.py 共用，避免重复）
  - 降级链：账号全失败 → 关键词（旧 L0）→ fixture（标 `_fallback_used`）
  - CLI 与 l0_collector 对齐（`--output/--since/--until/--dry-run`），输出对接契约 dict
- **修改** `config/curator-accounts.yaml`：加 `fetch_tier`（80 获取层）+ `reserve_tier`（18 候补层，失败顶替用）；人工核对/去 OCR 错误；保留全集留档
- **修改** `config/intake_sources.yaml`：account source 配置（tier 引用、cap、pacing、上限、enabled）
- **修改** `pipeline.run_intake`：subprocess 目标改 `account_collector`（保持传参/返回形状；env 开关可回退 l0 关键词）
- **修改** `synthesis.py` + `docs/deepseek-synthesis-prompt-v1.md`：去 summary 字数下限；prompt 改"据支撑推文充分提炼、不限字数、禁编造 supporting_tweet_ids"
- **修改** `pipeline.py` 渲染（`build_briefing_markdown` + `build_temp_briefing_markdown`）：每主题挂 2-3 条支撑推文；推文显示原文链接 + 全文 500 字截断；移除折叠 `<details>`
- **修改** `translate.py`：去 `core_excerpt`；展示推文全文 + zh_point
- **修改** `audit.py`：去 summary 句数检查；保留 zh_point 中文 + supporting_tweet_ids 存在性

不修改：
- scoring 评分算法本体、validator/schema、gate、调度、secrets
- 对接契约 dict 形状（不变）

---

## 实现步骤

### 步骤 1：抓取可行性确认 + adapter 提取（SC-1 前置）
WHERE：`twitter --from` 真跑一条；`intake/base.py`
WHAT：真跑 `twitter search --from karpathy -t latest --since <d-2> --until <d+1> --json -n 5` 确认无需关键词即可拉账号时间线、字段齐全；把 `_twitter_cli_to_akb2` adapter 从 l0 提到 base.py 共用
WHY：账号抓取依赖 `--from` 行为与字段；adapter 复用避免两份漂移
VERIFY：真跑返回该账号近期推文 JSON；`pytest` 现有绿不破

### 步骤 2：account_collector 抓取 + 去重 + 上限 + 降级（SC-1~SC-4）
WHERE：`intake/account_collector.py`、`config/intake_sources.yaml`
WHAT：实现逐账号抓取、按 id 去重、账号间 pacing、全局上限、三级降级链；CLI 与 l0 对齐
WHY：核心新件，把抓取从关键词改为按账号
VERIFY：单测 `test_account_collect_dedup` / `test_account_collect_caps` / `test_account_collect_fallback_chain`（mock subprocess）；CLI 输出对接契约 dict

### 步骤 3：curator 分层（fetch_tier 80 + reserve_tier 18）人工核对（SC-5）
WHERE：`config/curator-accounts.yaml`
WHAT：加 `fetch_tier`（80 获取层）+ `reserve_tier`（18 候补层）；剔除 3 个疑似 OCR 错误、修正 mckaywrigley；标注分层依据（最不确定/最低优先的归候补）
WHY：101 全量 × 抓取超 rate-limit；80 获取 + 18 候补在覆盖面与限流间取平衡
VERIFY：fetch_tier=80、reserve_tier=18 均为有效 handle 列表；collector 能读到两层；单测断言加载

### 步骤 4：pipeline 接账号 collector（SC-6）
WHERE：`pipeline.run_intake`
WHAT：subprocess 目标改 account_collector；保持 `--since/--until` 与返回形状；env `AKB2_INTAKE_MODE=keyword` 可回退旧 L0
WHY：把重建的抓取接进主链路，保持对接契约
VERIFY：单测 `test_run_intake_uses_account_collector`（mock subprocess）；端到端 `--dry-run-intake` 不破

### 步骤 5：synthesis 去字数下限（SC-7）
WHERE：`synthesis.py`、`docs/deepseek-synthesis-prompt-v1.md`
WHAT：`_validate_quality` 去 summary 长度相关校验（保留非空/非空洞词）；prompt 改"充分提炼不限字数、禁编造 id"
WHY：用户要模型自由提炼、不限字数
VERIFY：单测 `test_validate_quality_no_length_floor`；prompt 人工核对

### 步骤 6：渲染去折叠 + 500 字截断 + 2-3 支撑推文（SC-8, SC-9）
WHERE：`pipeline.py`（`build_briefing_markdown` + temp 版）、`translate.py`
WHAT：每主题取 supporting_tweet_ids 按评分 top 2-3 渲染；推文显示 `url` 原文链接 + 全文（>500 字截断 + 标记）；移除 `<details>` 折叠；translate 去 `core_excerpt`
WHY：落地用户锁定的展示偏好
VERIFY：单测 `test_render_no_fold` / `test_render_truncate_500` / `test_render_links_present` / `test_render_top3_support` / `test_translate_no_core_excerpt`

### 步骤 7：audit 去句数检查（SC-11）
WHERE：`audit.py`
WHAT：删 summary 句数检查；保留 zh_point 中文 + supporting_tweet_ids 存在性
WHY：与 SC-7 去字数下限一致
VERIFY：单测 `test_audit_no_sentence_count`；旧句数断言改/删

### 步骤 8：收尾验证（SC-12, SC-13）
WHERE：`tests/briefing/`、tmp vault、真实 key+cookie 单跑
WHAT：`pytest tests/briefing/` 全绿修失效旧断言；真实跑确认账号内容 + 支撑推文链接全文无折叠
WHY：确认全链路与真实产出符合 SC
VERIFY：`pytest tests/briefing/ -q` 全绿；真实产出人读验收

---

## 边界与失败模式

必须处理：
- 单账号抓取超时/失败/0 条 → 跳过该账号 + 告警，不中断整批
- 全部账号失败 → 降级关键词，再失败 → fixture 标 `_fallback_used`
- 同一推文被多账号引用（转推已 exclude，主要是同 id）→ 按 id 去重
- 账号抓取调用量 → fetch tier 上限 + 单账号 cap + 全局上限 + pacing（确定性代码，不靠模型）
- handle 拼写错/不存在账号 → tier 已人工核对；运行时该账号失败走单账号降级
- 推文 >500 字 → 截断 + "…（已截断）"标记；保留原文链接可点开全文
- 时区：since/until 统一 Asia/Shanghai；`--until` 用 date+1（CLI 上界 EXCLUSIVE，沿用 v2 修复）
- LLM（synthesis/translate/audit）失败 → 沿用现有降级，不崩

明确不处理：
- L1/L2 信任传播、X List、arxiv/HF（非目标）
- 自进化清单、新评分维度（非目标）
- fetch tier 规模 / cap / N / K 最终调优（默认先行）

风险与防护：
- 风险：抓取调用量炸 rate-limit → 防护：tier 上限 + cap + pacing + 全局上限，全部 env 可调
- 风险：策展账号近期无 AI 干货导致内容稀 → 防护：N 可放宽 + 关键词 fallback 兜底（不产空报）
- 风险：去折叠后长推刷屏 → 防护：500 字截断 + 每主题仅 2-3 条支撑
- 风险：改动大易回归 → 防护：TDD + 分步 commit + 每步可单独 review

---

## 测试计划

单元测试（mock subprocess / mock LLM / tmp_path）：
- test_account_collect_dedup（跨账号按 id 去重）
- test_account_collect_caps（单账号 cap + 全局上限生效）
- test_account_collect_fallback_chain（账号全失败 → 关键词 → fixture 标记）
- test_account_collect_skips_failed_account（单账号失败不中断）
- test_curator_fetch_tier_loaded（tier 加载、OCR 错误已剔除）
- test_run_intake_uses_account_collector（pipeline 接账号 collector）
- test_validate_quality_no_length_floor（去字数下限）
- test_render_no_fold / test_render_truncate_500 / test_render_links_present / test_render_top3_support
- test_translate_no_core_excerpt
- test_audit_no_sentence_count

集成/CLI：
- `python -m akb2.briefing.intake.account_collector --output <tmp> --since <d-2> --until <d+1>`（真实/ mock）
- `python -m akb2.briefing.pipeline --date <today> --vault-root <tmp> --dry-run-intake`
- `pytest tests/briefing/ -q` 全绿

测试隔离：tmp_path 造 vault；mock 所有 subprocess/LLM；绝不写真实 vault；不提交 secret。

暂不执行的验证：真实 key+cookie 全量跑（分支手动单跑读真实产出）；参数调优。

---

## 已定参数

> **参数权威源 = `plans/gd/2026-05-31-briefing-rebuild/master-plan.md` 的「参数调整记录」**（本文件不再重复维护，避免漂移）。
> 历经 80 → 40 → **30**（实跑验证后定稿）三代调整 + 实跑发现（限流 ~34、pacing 提到 3s、`--exclude replies`、按推文 lang 处理、降级 10 个空窗/离题账号）。
> 名单分层落 `config/curator-accounts.yaml`（fetch_tier 30 + reserve_tier 69）。
> 仍稳定的：截断 500（唯一字数限制）、核心讨论/执行摘要不限字数、孤儿 `AKB2_ORPHAN_TOP_K=10`、支撑 top 2-3。
> 时间窗 **N=3 天**（`AKB2_INTAKE_WINDOW_DAYS=3`，2026-05-31 定；实跑发现 N=2 会漏低频大佬重磅，如 karpathy 加入 Anthropic 是 7 天前）。
