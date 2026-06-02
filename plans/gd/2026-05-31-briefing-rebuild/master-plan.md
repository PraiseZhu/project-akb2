# AKB2 社交日报 v3 — 按账号抓取重建 + 后半段对接 + 结构精炼 v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-master-plan

日期：2026-05-31
状态：draft
负责人：Claude 执行；Codex 可选 cross-review
TARGET_PROJECT_ROOT：/Users/praise/AI-Agent/Claude/projects/Project AKB2/.claude/worktrees/akb2-briefing-zh-pipeline
计划方式：manual single-controller（revision=21 child planner dispatch；GD 旧 v1.1 probe/dispatch_map 幻影脚本已被 stage-dispatch-ledger 模型取代，本套件用现存自洽 validator）

> **参数调整记录（2026-05-31，用户决策 + 实跑验证后定稿）**：
> - 获取层 `fetch_tier`：**30** = `en` 19（实跑确认活跃）+ `zh` 11（5 确认 + 6 待复验）（议程：child 草稿 30 → 议过 80 → 用户定 40 → 实跑验证收到 30）
> - 候补层 `reserve_tier`：**69**（名单已落 `config/curator-accounts.yaml`；含实跑降级的 10 个空窗/离题账号；accounts 101 全集留档）
> - **实跑验证结论（twitter CLI，2026-05-31）**：
>   - 机制 ✓ + 内容质量 ✓ 优（Anthropic $65B / karpathy→Anthropic / vLLM v0.22 / 中文 AI 数字员工 等真干货）
>   - **限流坐实**：pacing 2.5s 跑到第 ~34 个触发 429 → 获取层控 30、**pacing 提到 3s**
>   - 抓取 cmd 必须 `--exclude retweets --exclude replies`（可重复 flag；不加混入大量 @回复 碎片噪音）
>   - **语言按每条推文 `lang` 字段处理**（en→翻译；zh→跳翻译只点题），不靠账号分类猜（CLI 每条带 lang）
>   - 降级 10 个：LangChainAI/LlamaIndex/ilyasut/dario_amodei/drjimfan/soumithchintala/baoyu/indigo11/hanxiao_2017（15 天空窗）+ jianshuo（离题）
> - 候补语义：**失败自动顶替** — 获取层某账号失败/0 条时 collector 自动从候补池补抓
> - 全局上限 `global_max`：400（30 账号 × cap 10 = 300 < 400，不截断，留硬天花板）
> - 简报长度（用户确认"长一点没问题"）：孤儿区 `AKB2_ORPHAN_TOP_K` **10**；核心讨论条数目标 **5-8 条**
> - 字数限制：仅支撑推文 500 字截断；核心讨论 summary / 执行摘要 **不限字数**
> - 不变：cap 10、截断 500、支撑 top2-3；**变：pacing 1.5s → 3s；时间窗 N 2 → 3（`AKB2_INTAKE_WINDOW_DAYS=3`，2026-05-31 用户定）**
> - 时间窗 N=3 理由：实跑发现低频大佬重磅消息（karpathy 加入 Anthropic，14.9万赞）是 7 天前；N=2 会漏，N=3 接住更多近期重磅，"今日"成色仍可控
> - 新增必修：**SC-14 白名单对齐**（P0-a）+ **SC-15 推文级语言感知**（P0-b，改为按 tweet.lang 不按账号）+ **SC-16 去回复噪音**（`--exclude replies`），见下方 SC 列表
> - 代价：单次 ~30 次 twitter search（pacing 3s ~90s + 抓取，总运行 ~2-3 min），rate-limit 由候补顶替兜底

---

## 1. 目标链

```text
PROJECT_GOAL: 在 Claude Code 中建设 /gd Goal-Driven 多 Agent 主链路，提升复杂任务的计划、审查、执行、验收效率，并通过 Codex 作为 cross-review sidecar 降低填表式计划与执行遗漏风险。
CHAIN_GOAL:   用 shared core 固定目标链、SC、任务包、review contract 和 anti-fill 标准，保证后续 /gd command、multi-agent dispatch、execution review、Codex cross-review 都引用同一套契约。
PHASE_GOAL:   AKB2 社交日报抓取从"关键词广搜"重建为"按策展博主清单抓时间线"，与现有后半段（scoring→synthesis→translate→audit→render）无缝对接不改对接 dict 形状；并把结构精炼为核心讨论不限字数提炼、每条挂 2-3 条高分支撑推文带原文链接+全文500字截断去折叠。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`app_code`
- REVIEW_FOCUS（3-5 项分号分隔）：`账号抓取正确性（逐账号 --from 抓时间线、按 id 去重、保持对接 dict 形状）; 抓取边界确定性（tier/cap/全局上限/pacing 用确定性代码 + Asia/Shanghai 时区一致 + fail-visible 三级降级）; 渲染结构精炼（去折叠+500字截断+每主题 top2-3 带原文链接）; synthesis/audit 去字数下限对齐（prompt 去≥3句 + audit 去句数检查）; translate 去 core_excerpt 不崩（LLM 双失败降级）`
- Domain-specific notes：对接 dict 形状（`{"tweets":[{id,text,author,created_at,url,metrics}], "_fallback_used"?}`）为本套件不可变契约；无 schema 变更（复用 social_trends_v1_1 + supporting_tweet_ids）；本套件 supersede 旧 v2 计划（curious-brewing-unicorn.md）的折叠/summary≥3句/audit句数检查三项决策。

---

## 3. 成功标准（SC）

> Anti-fill：每条 SC 绑定命令 / 路径 / 测试断言。

- [ ] SC-1：account_collector 读 curator fetch tier 逐账号抓时间线（`--from` 无需关键词）— `pytest tests/briefing/test_account_collector.py::test_account_collect_reads_tier_and_fetches -q`
- [ ] SC-2：跨账号按 id 去重，输出对接契约 dict 形状 — `pytest tests/briefing/test_account_collector.py::test_account_collect_dedup -q`
- [ ] SC-3（含 F2 backfill 预算）：硬边界 tier/cap/全局上限 + 确定性 pacing；**总 fetch 调用预算**（active+backfill ≤ tier_max + 小额 backfill 余量，到顶即停）防 backfill 遇空账号级联触发限流（实跑 ~34 次触 429）；均 env 可调 — `pytest tests/briefing/test_account_collector.py -q -k "caps or budget"`
- [ ] SC-4（A 实跑修正：区分失败 vs 真空）：降级链分两类——**失败**(限流429/报错/超时/handle 无效)→候补池自动顶替；**真空**(ok 但 0 条，账号没发)→**接受当天少几条，不触发顶替**（候补也大概率空，补不回内容）；候补耗尽且全失败→关键词→fixture 标 `_fallback_used`。诚实预期：**有效日活账号 << 30**（N=3 窗多数账号当天无原创），简报条数随真实内容浮动 — `pytest tests/briefing/test_account_collector.py -q -k "fallback or backfill or empty_not_backfill"`
- [ ] SC-5：curator 分层选定（fetch_tier **30**（19 en + 11 zh）+ reserve_tier **69**；剔 3 OCR 错、修 mckaywrigley；名单已落 curator-accounts.yaml）— `pytest tests/briefing/test_curator_tier.py::test_curator_fetch_tier_loaded -q`
- [ ] SC-6：run_intake 切 account_collector，保持传参/返回形状，env 可回退 — `pytest tests/briefing/test_pipeline_intake_wire.py -q`
- [ ] SC-7：synthesis 去 summary 字数下限（prompt 去≥3句；`_validate_quality` 无长度门控）— `pytest tests/briefing/test_synthesis.py -q -k validate_quality` 且 `! grep -E "≥3 句|3-5 句" docs/deepseek-synthesis-prompt-v1.md`
- [ ] SC-8：每主题渲染按评分 top 2-3 支撑推文 — `pytest tests/briefing/test_pipeline_render.py -q -k "cap or top"`
- [ ] SC-9：支撑推文显示原文链接 + 全文 500 字截断 + 移除折叠 — `pytest tests/briefing/test_pipeline_render.py -q -k "no_fold or truncate or link"`
- [ ] SC-10：translate 去 core_excerpt；展示推文有 zh_point；LLM 双失败降级不崩 — `pytest tests/briefing/test_translate.py -q`
- [ ] SC-11：audit 去句数检查；保留 zh_point 中文 + supporting_tweet_ids 存在性 — `pytest tests/briefing/test_audit.py -q`
- [ ] SC-12：`pytest tests/briefing/` 全绿（含结构 delta 新测）— `python -m pytest tests/briefing/ -q`
- [ ] SC-13：真实 key+cookie 单跑产出无折叠 + 含原文链接 + 内容为策展账号真实推文 — `AKB2_INTAKE_MODE=account python -m akb2.briefing.pipeline --date $(date +%F) --vault-root <tmp>` 后 grep 校验 + 人读
- [ ] **SC-14（P0-a 白名单对齐）**：账号模式下 `fetch_tier`（en+zh 30 个）账号在 author_authority 维度都享策展加权，不让 22 个英文白名单架空 30 策展 —— scoring 读 fetch_tier 作权威源（或白名单扩为 fetch_tier）— `pytest tests/briefing/test_scoring.py -q -k "authority and tier"`（断言 fetch_tier 内非白名单账号 author_authority > 0.3）
- [ ] **SC-15（P0-b 推文级语言感知，含 C 三类规则）**：按**每条推文 `lang` 字段**处理，三类——`en`→英→中翻译；`zh`→跳翻译只生成 zh_point；**其他语言(pt/ja/fr…)→译自任意语言到中文**；`zxx`/纯链接无文字→交 signal_density/topic_relevance 自然滤掉。中文/他语原文不触发英文子串校验 — `pytest tests/briefing/test_translate.py -q -k "lang"`（覆盖 en/zh/other/zxx 分支）
- [ ] **SC-16（去回复噪音 + D 二次兜底）**：抓取 cmd 含 `--exclude retweets --exclude replies`；**且**对返回结果二次过滤 text 以 `@` 开头的残留回复（实跑证实 CLI flag 不 100%，demishassabis 回复仍漏进）— `pytest tests/briefing/test_account_collector.py -q -k "exclude or replies or at_prefix"`
- [ ] **SC-17（B：topic_relevance 账号模式加固）**：账号模式无关键词预筛，topic_relevance 升为唯一 AI 相关闸门——策展账号的非 AI 推文(节日/个人/广告，如 AndrewYNg "Memorial Day"/jianshuo 博物馆)必须被压低分挡在核心讨论外；关键词表覆盖 en+zh AI 术语 — `pytest tests/briefing/test_scoring.py -q -k "topic and (offtopic or holiday or chinese)"`（断言非 AI 推文 topic_relevance 低、中文 AI 推文不被误杀）

---

## 4. 非目标（NON_GOALS）

- L1/L2 信任传播（Quote/Retweet/Reply 加权）、L0' X List、L3 arxiv/HF 抓取
- 自进化清单、新评分维度（Discovery_layer/Discussion_intensity）
- scoring 维度权重表与打分公式本体（SC-14/15 仅改 author_authority 权威源接 fetch_tier + topic_relevance 加中文关键词判定，不动权重/公式）、validator/schema、gate、调度（daily_briefing.sh/LaunchAgent）、secrets 常驻
- fetch tier 规模 / cap / 时间窗 N / 截断长度 / 支撑条数的最终调优（默认先行，env 可调）

---

## 5. Step 拆分

| Step | 名称 | owned_paths | blocked_by | can_parallel_with | 主要 SC |
|------|------|------------|-----------|-------------------|---------|
| 1 | P0 抓取重建（child-proposals/child-a-intake.md）| intake/、config/curator-accounts.yaml、config/intake_sources.yaml、pipeline.py(run_intake) | — | — | SC-1~SC-6 |
| 2 | P1 结构精炼 + P2 验收（child-proposals/child-b-structure.md）| synthesis.py、translate.py、audit.py、pipeline.py(渲染)、docs/deepseek-synthesis-prompt-v1.md | step 1（T9/T10 验收） | step 1 的 T5~T8 纯结构 delta 可与 step 1 起草并行 | SC-7~SC-13 |

---

## 5a. Dispatch Map / Wave Contract（MANDATORY）

```
DISPATCH_MAP_PATH: plans/gd/2026-05-31-briefing-rebuild/dispatch-map.json
VALIDATE_CMD: python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-dispatch.py plans/gd/2026-05-31-briefing-rebuild/dispatch-map.json
```

### Wave Matrix（执行期 child_executor 调度；max_parallel=2）

| Wave | Tracks（同 wave 可并行） | 并行前提 |
|------|------------------------|---------|
| w1 | t-intake-collector, t-curator-tier | owned 不重叠（intake/ vs config/）；blocked_by 空 |
| w2 | t-synthesis, t-translate | owned 不重叠（synthesis.py vs translate.py）；blocked_by 空 |
| w3 | t-audit, t-pipeline | owned 不重叠（audit.py vs pipeline.py）；t-audit blocked_by t-translate、t-pipeline blocked_by t-intake-collector/t-curator-tier/t-translate（均在 w1/w2） |
| w4 | t-verify | blocked_by 全部 6 track（全绿 + 真实跑验收）|

> 规则：同 wave ≤2 track；主 agent 先跑 VALIDATE_CMD（exit 0）再按 wave 顺序 dispatch；t-pipeline 合并 run_intake wire + 渲染改动以独占 pipeline.py，消除 P0/P2 同文件冲突。

---

## 6. 边界（修改 / 不修改）

修改：
- `src/akb2/briefing/intake/account_collector.py`（新增）、`intake/base.py`、`intake/l0_collector.py`
- `config/curator-accounts.yaml`（已落 fetch_tier 30 + reserve 69）、`config/intake_sources.yaml`
- `src/akb2/briefing/pipeline.py`、`synthesis.py`、`translate.py`、`audit.py`
- `src/akb2/briefing/scoring.py`（SC-14/15：author_authority 接 fetch_tier + topic_relevance 中文关键词；不动权重/公式）
- `docs/deepseek-synthesis-prompt-v1.md`
- `tests/briefing/**`、`plans/gd/2026-05-31-briefing-rebuild/**`

不修改：
- `/Users/praise/.claude/**`
- 真实 vault `/Users/praise/Library/Mobile Documents/.../AKB2.0/**`
- gate / validator / schema / scoring 权重表与打分公式 / 调度 / secrets
- 对接 dict 形状

> **dispatch-map（review 后已补 t-scoring，F1 已解决）**：新增 `t-scoring` track（owns `scoring.py` + `tests/briefing/test_scoring.py`，blocked_by t-curator-tier，sc_refs SC-14/SC-15/SC-17，wave w3 与 t-audit 并行）。现 **8 track / 5 wave，`gd-validate-dispatch.py` 通过**。
>
> **execute 前置（F3，review 提出）**：9 个 task packet 当前是 child-proposals 里的散文，非 `gd-task-packet.schema.json` 合规独立 .json。`/gd execute` agent_exec 需 schema-valid packet（含 sc_acceptance）。执行前需把它们物化为合规文件——属 execute setup，非 plan 缺陷。
>
> **实跑后预期附注（E/F/G/H，非 SC，设预期用）**：
> - E：简报长度随真实内容浮动，安静日就短（"长一点没问题"也变不出内容）
> - F：N=3 仍漏周更大佬（karpathy 加入 Anthropic 为 5/24，N=3 从 5/28 抓不到）；要接住需 N=7 但牺牲"今日"，N=3 是平衡点
> - G：候补池 69 个大多未验证活跃（含降级的 10 个空账号）；真要靠候补补内容需先验证候补也活跃——空账号进候补是死重
> - H：global_max=400 当前不触顶（30 账号实产 ~100 条），无害留作硬天花板

---

## 7. 风险与防护

| 风险 | 防护 |
|------|------|
| 抓取调用量炸 rate-limit | tier_max(30) + cap(10) + global_max(400) + pacing(3s) + 总 fetch 预算 五重天花板，全 env 可调；失败账号由候补顶替不空跑（实跑 ~34 触 429）|
| 策展账号近期无 AI 干货 → 内容稀 | 时间窗 N 可放宽 + 关键词 fallback 兜底不产空报 |
| t-pipeline 同文件 run_intake + 渲染双改 | 合并为单 track 独占 pipeline.py，消除并行冲突 |
| 去折叠后长推刷屏 | 500 字截断 + 每主题 2-3 条 |
| 删 core_excerpt 漏改 audit 检查 | t-audit blocked_by t-translate，同批清理 core_excerpt 检查 |

---

## 8. 测试计划

```bash
# 全部为可执行命令，给 PASS/FAIL 信号
python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-dispatch.py" plans/gd/2026-05-31-briefing-rebuild/dispatch-map.json
python -m pytest tests/briefing/ -q
test -f src/akb2/briefing/intake/account_collector.py
! grep -E "≥3 句|3-5 句" docs/deepseek-synthesis-prompt-v1.md
```

---

## 9. Assumptions

- twitter CLI 支持 `--from/-t latest/--since/--until/--json/-n`（已验证）。
- 对接 dict 形状以 `pipeline.run_intake`（pipeline.py:115）为准，重建只改产出来源不改形状。
- fetch_tier 30（19en+11zh）、reserve_tier 69（候补失败顶替）、cap 10、global 400、pacing 3s、N=3、截断 500、支撑 top2-3、孤儿 10 为默认值，env 可覆盖，真实跑后调。
- GD 模板/schema 在 GD 项目存在；本 worktree 不含 GD 运行时（`~/.claude/` 禁访问），child planner 按字段规范产出。
