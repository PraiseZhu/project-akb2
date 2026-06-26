# Step 1: 校准核心 harness — calibration.py 镜像生产抽取契约 + 诊断三分类 + 完整 raw frontmatter v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md (v1.2.0)
TEMPLATE_KIND: gd-step-plan

日期：2026-06-26
状态：draft
负责人：Claude 执行；Codex 可选 cross-review
lane：t1-calibration-core（来源 child proposal：child-proposals/t1-calibration-core-proposal.md）

---

## 1. 目标链（继承 + 当前 task goal）

```text
PROJECT_GOAL: 让 AKB2 循环1 能跑一次可信的满仓校准（宪法 v1.2.0 原则 3/4/6）
CHAIN_GOAL:   只修测试侧 harness，不动生产 admission/distill/checks_structural/grader/repair
PHASE_GOAL:   报告能区分死链/元数据失败/抽取失败三类；3 篇元数据失败 URL 补 ExtractionContext 后 extraction_success False→True；活语料端到端薄切片流过 intake_raw
TASK_GOAL:    把 calibration.py 测试侧抓取从「裸调 extract_content 丢弃 ExtractResult、只回 content」改成「镜像 orchestrator 构造 ExtractionContext + 保留全部诊断字段 + 产出含 9 字段 frontmatter 的完整 raw_content」，覆盖 SC-1/SC-2/SC-4/SC-6
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：`镜像生产抽取调用形态（含 ExtractionContext）; 诊断字段三分类映射正确 + 全 7 键 base dict 初始化; live/fixture 分层评分物理分离 + factory_acceptance 绑 live 下限（fixture 不补偿）; SC-2b 3 具名 URL 保存 HTML 注入走真实路径; 不越界改 extract/包A; raw_content frontmatter 9 字段完整（FRONTMATTER_FIELDS）可被 distill 消费; per-entry feed 元数据 + raw-html 依赖 step 3 扩展`
- Domain-specific notes：只改 `calibration.py` + 新增直测；诊断字段全部从 `ExtractResult` 派生（extract.py 零改动）；构造 ctx 所需 per-entry feed 元数据来自 step 3 扩展后的 corpus.yaml。

---

## 3. 前置条件

- blocked_by：`step 3`（corpus 扩展 per-entry feed 元数据 + `fixtures/raw-html/<slug>.html` 保存 HTML；SC-1 端到端报告与 SC-2b 强依赖；SC-2a/4/6/1a 直测用构造 ctx/fixture 解耦）
- 必须 baseline / artifact：`src/akb2/quality/calibration.py`（改：抓取契约 + 三分类 + 9 字段 frontmatter + **分层评分/factory live 下限**）、`extract.py`/`orchestrator.py`/`raw_archiver.py`（含 FRONTMATTER_FIELDS）/`models.py`（只读）、step 3 产出的扩展 corpus.yaml + `fixtures/raw-html/` + preflight 报告（运行时数据输入）
- Hard-stop：若 `ExtractResult` 缺任一诊断字段（与已核实事实不符）→ 停止（意味着需改 extract.py，越界本 lane）

---

## 4. 成功标准（SC，本 step 内）

> Anti-fill 规则 A：每条 SC 绑定命令/路径/断言/测试之一。

- [ ] SC-1（诊断字段 + live/fixture 分层评分 + factory live 下限，B3-1/B3-2）：报告 `entries[]` **每条**含完整诊断键集 `{failure_class, extract_method, word_count, metadata_complete, missing_metadata, extract_error, source}`（base result dict 初始化、全 6 路径携带）、`source ∈ {fixture, live}`；报告顶层产出 `positive_live_total/positive_live_passed/positive_live_pass_rate/positive_fixture_total/positive_fixture_passed/positive_fixture_pass_rate/live_positive_intake_raw_failures`；`factory_acceptance` = 混池 `positive_pass_rate>=0.9` AND `negative_reject_rate==1.0` AND `positive_live_total>=LIVE_POSITIVE_QUOTA` AND `live_positive_intake_raw_failures==0` AND `positive_live_pass_rate>=LIVE_POSITIVE_PASS_FLOOR`；**且 ≥3 条 `source=='live'` 正样本真实越过 intake_raw**（先重新生成报告再断言；fixture 不补偿 live health，round-2 P2 + B3-1/B3-2）
  - verify(assertion，前提：网络 + DeepSeek/GPT key): `PYTHONPATH=src python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json && python3 -c "import json;d=json.load(open('reports/calibration/factory.json'));E=d['entries'];KEYS={'failure_class','extract_method','word_count','metadata_complete','missing_metadata','extract_error','source'};assert all(KEYS<=set(e) for e in E);assert all(e['source'] in ('fixture','live') for e in E);assert sum(1 for e in E if e.get('expected')=='ingest' and e.get('source')=='live' and e.get('failure_segment')!='intake_raw')>=3;assert {'positive_live_total','positive_live_pass_rate','positive_fixture_pass_rate','live_positive_intake_raw_failures'}<=set(d);assert d['live_positive_intake_raw_failures']==0"`
  - expect: 退出码 0（全 7 键 + source∈{fixture,live} + ≥3 live(source=='live') ingest 越过 intake_raw + 分层字段在场 + live 无 intake_raw 卡死；corpus 须保留 ≥3 条真实可达 live 正样本，见 step-2 死链替换策略）
- [ ] SC-1a（fixture 加载，cross-review P1#M1）：`_load_fixture_content` 按 `fixture:<name>` 从 `evals/main-pipeline-quality/fixtures/<name>.md` 读取（非硬编码 google-io）；任一非 google-io fixture entry 返回非空正文且 `failure_segment != "intake_raw"`
  - verify(test): `pytest tests/test_calibration_harness_diagnostics.py -k fixture_load -q`
  - expect: passed
- [ ] SC-2a（ctx 回填机制无条件 mock，B3-6）：同一缺 author/published 的 `ExtractResult` ± ctx 跑两次 `_apply_feed_fallback`/extract_content，断言不传 ctx 为 `extraction_success=False`、传 ctx（feed 字段构造）翻 `True`；不走 fixture 路径、不硬编码非空 ctx（fixture 路径直接 return 正文、绕过 extract_content 无此字段）
  - verify(test): `pytest tests/test_calibration_harness_diagnostics.py -k extraction_context -q`（测试体含同一 ExtractResult ± ctx 的 False→True 对照断言；`-k extraction_context` 不匹配 SC-2b 的 `extraction_context_named_urls` 之外的 case 时用 `and not named_urls` 收窄）
  - expect: passed
- [ ] SC-2b（3 具名 URL crewai/aider/MCP 可复现 False→True，B3-6）：三条 corpus entry 保持 **live**（带真实 feed_*），各读 t2b 保存的 `evals/main-pipeline-quality/fixtures/raw-html/<slug>.html` 注入 `extract_content` 抽取器底层（monkeypatch fetch、不发网络），走**真实** extract_content 决策树 + **真实** corpus `feed_*` 构造 ctx + **真实** `_apply_feed_fallback`，断言每条 `extraction_success` 不传 ctx=False、传 ctx=True（不是 generic mock，是具名页可复现验证；恢复 master「3 篇 False→True」目标）
  - verify(test): `pytest tests/test_calibration_harness_diagnostics.py -k extraction_context_named_urls -q`（参数化 3 case：crewai/aider/mcp）
  - expect: passed（3 case 全 False→True）
- [ ] SC-4：活语料端到端薄切片 ≥1 正样本 `failure_segment != "intake_raw"`
  - verify(test): `pytest tests/test_calibration_harness_diagnostics.py -k e2e_thin_slice -q`
  - expect: passed
- [ ] SC-6（完整 9 字段 frontmatter，B3-7）：传给 `_run_pipeline_segments` 的 `raw_content` frontmatter 含 `raw_archiver.FRONTMATTER_FIELDS` 全部 9 字段（artifact_type/source_slug/source_url/source_title/source_authors/source_publisher/source_published_at/fetched_at/fetched_via）
  - verify(test): `pytest tests/test_calibration_harness_diagnostics.py -k raw_frontmatter -q`（测试 `from src.akb2.quality.intake.raw_archiver import FRONTMATTER_FIELDS` 后断言 `set(produced_keys) >= set(FRONTMATTER_FIELDS)`，非硬编码 4 字段子集）
  - expect: passed

---

## 5. 非目标

- 不改 extract.py 抽取决策树 / `_validate_metadata` / `_apply_feed_fallback`。
- 不改包 A、corpus.yaml、main_sources.yaml、preflight.py。
- 不产出 HTTP 层字段本身（读 step 3 preflight 报告合并；缺失写 null）。
- 不**放宽** `positive_pass_rate>=0.9` / `negative_reject_rate==1.0` 数值；新增 live 层下限属纯收紧（B3-1，见 SC-1 / Step.5）。

---

## 6. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/calibration.py — 新增 _build_extraction_context(entry, config)（紧邻 _fetch_url_content 前）
  WHAT:  按 orchestrator.py:214-227 形态，从 corpus entry 读 feed_title/feed_author/feed_published/source_publisher/source_slug + url 构造并返回 ExtractionContext（from src.akb2.quality.intake.models import ExtractionContext），缺字段回退空串
  WHY:   无 context 时 crewai/aider/MCP 的 author/published 抽不到 → _validate_metadata fail → extraction_success=False（SC-2 根因）
  VERIFY: pytest tests/test_calibration_harness_diagnostics.py -k extraction_context -q → passed

Step.2
  WHERE: src/akb2/quality/calibration.py — _fetch_url_content 调用点（约 :156 / :206-232）
  WHAT:  改为 extract_content(url, failures_path=..., extraction_config=_build_extraction_context(entry, config))，返回完整 ExtractResult（不再只回 result.content）；fixture: 分支改读目录（见 Step.2b）
  WHY:   现状丢弃 ExtractResult 的 method/title/author/published/word_count/metadata_complete/extraction_success/error/raw_metadata，致报告无诊断（SC-1）且不传 context（SC-2）
  VERIFY: pytest tests/test_calibration_harness_diagnostics.py -k extraction_context -q → passed

Step.2b (SC-1a，cross-review P1#M1)
  WHERE: src/akb2/quality/calibration.py — _load_fixture_content（:235-264）
  WHAT:  从「硬编码只认 google-io、其余 return None」改为「按 fixture_name 从 evals/main-pipeline-quality/fixtures/<fixture_name>.md 读取文件内容」；google-io 迁为该目录下的 fixtures/google-io-2026-news.md（或保留内联兼容）；文件缺失 return None
  WHY:   现状非 google-io 的 fixture: entry（黄金样张/概念正样本/负样本快照）全 return None → fetch_failed → 卡 intake_raw，确定性质量锚永不进判定、factory 恒 FAIL，而 SC-7 只验快照文件存在不验运行时能加载 → 绿门掩盖坏 rig
  VERIFY: pytest tests/test_calibration_harness_diagnostics.py -k fixture_load -q → passed

Step.3
  WHERE: src/akb2/quality/calibration.py — _process_corpus_entry（:111-203）+ 新增 _classify_failure_class()
  WHAT:  **先在 `_process_corpus_entry` 的 base result dict（:111-123）初始化全部诊断键的安全默认值**——`failure_class=None`、`extract_method`/`word_count`/`metadata_complete`/`extraction_success`/`extract_error`/`missing_metadata` + `source`（fixture|live），使**全部 6 条返回路径**（成功 / 死链早退 / 抽取失败 / fixture 成功 / 负样本 / ImportError 早退）都携带这些键：fixture 路径填 `extract_method="fixture"`、`extraction_success=True`、`source="fixture"`；live 路径 `source="live"`；负样本/ImportError 填默认。**再**在 ExtractResult 分支按真实结果派生覆盖：failure_class——HTTP 死链(读 step 3 preflight http_status>=400/unreachable)→dead_link；extraction_success=False 且 word_count>=MIN 但 metadata_complete=False→metadata_failure；word_count<MIN/全失败→extraction_failure；成功→None。preflight 缺失时 http 字段 null、dead_link 退化按 ExtractResult.error。
  WHY-FIX(round-2 P1)：M1 把黄金样张/概念正样本/crewai-aider-MCP/负样本共 8~10 条搬进 fixture: 路径，这些 entry 不经 extract_content 故无 ExtractResult；若只在 ExtractResult 分支补字段，SC-1 的 `all(... for e in entries)` 会在 fixture/负样本/ImportError entry 上 KeyError/AssertionError → 头号 e2e SC 恒挂。故必须在 base dict 初始化，不能只在 ExtractResult 分支补
  WHY:   SC-1 三分类；现状只有 failure_attribution 粗二分，无法区分死链 vs 元数据失败 vs 抽取失败
  VERIFY: python3 -c "import json;d=json.load(open('reports/calibration/factory.json'));assert all('failure_class' in e and 'extract_method' in e for e in d['entries'])" → 退出码 0

Step.4
  WHERE: src/akb2/quality/calibration.py — 新增 _compose_raw_content(extract_result, ctx)，改传给 _run_pipeline_segments 的 raw_content
  WHAT:  复用/等价拼装 raw_archiver 9 字段 frontmatter（artifact_type/source_slug/source_url/source_title/source_authors/source_publisher/source_published_at/fetched_at/fetched_via）+ result.content 成完整 raw markdown；source_* 优先取 result、缺失从 ctx 补；字段名集合**从 raw_archiver.FRONTMATTER_FIELDS 取**（不硬编码，B3-7：SC-6 测试 import 同一常量断言 superset）
  WHY:   SC-6——现状只回裸正文，distill 收到的 raw_content 缺 source_* 字段产生 raw_content 缺失类信号
  VERIFY: pytest tests/test_calibration_harness_diagnostics.py -k raw_frontmatter -q → passed

Step.5 (SC-1 分层评分 + factory live 下限，B3-1)
  WHERE: src/akb2/quality/calibration.py — 顶层新增具名常量 LIVE_POSITIVE_QUOTA=3 / LIVE_POSITIVE_PASS_FLOOR=0.9（紧邻 :41 DEFAULT_TIMEOUT_PER_ENTRY_SEC）；改 _compute_statistics（:357-393）；改 run_calibration 的 factory_acceptance 计算（:447-450）
  WHAT:  (1) _compute_statistics 在现有混池 positive_pass_rate 之外，按 `e['source']` 拆 live/fixture 两层，写报告顶层 positive_live_total / positive_live_passed / positive_live_pass_rate / positive_fixture_total / positive_fixture_passed / positive_fixture_pass_rate；并统计 live_positive_intake_raw_failures = sum(1 for e in entries if e['expected']=='ingest' and e['source']=='live' and e['failure_segment']=='intake_raw')。(2) factory_acceptance 改为：混池 positive_pass_rate>=0.9 AND negative_reject_rate==1.0 AND positive_live_total>=LIVE_POSITIVE_QUOTA AND live_positive_intake_raw_failures==0 AND positive_live_pass_rate>=LIVE_POSITIVE_PASS_FLOOR
  WHY:   B3-1——现状 :362-365 positive_pass_rate 混 live+fixture 算、:447-450 factory 直读混池率；大批正样本 fixture 化后 fixture 通过照样灌高 positive_pass_rate、掩盖 live 层窄/失败。「双层物理分离」必须落在评分侧，否则只是语料分层、评分仍混池
  VERIFY: 见 SC-1 verify（重生成报告后断言分层字段在场 + live_positive_intake_raw_failures==0 + ≥3 live 越过 intake_raw）

Step.6
  WHERE: tests/test_calibration_harness_diagnostics.py（新建）
  WHAT:  6 组直测 extraction_context（SC-2a）/ extraction_context_named_urls（SC-2b，参数化 crewai/aider/mcp 各读 fixtures/raw-html/<slug>.html 注入抽取器底层走真实路径）/ fixture_load（SC-1a）/ e2e_thin_slice（SC-4）/ raw_frontmatter（SC-6，import raw_archiver.FRONTMATTER_FIELDS 断言 superset）/ failure_class（SC-1 三分类 + 分层计数）；extraction_context（SC-2a）组用纯 mock ExtractResult ± ctx 无条件对照（不依赖真实日期）；extraction_context_named_urls（SC-2b）组从 t2b 落地后 corpus 真实 feed_* 构造 ctx 并断言 feed_published 非空（M4，非硬编码）；fixture_load 组断言非 google-io fixture 经 _load_fixture_content 返回非空且不卡 intake_raw（M1）；新增 stratified 组：构造含 fixture+live entry 的报告断言 positive_live_pass_rate 不被 fixture 通过抬高、factory_acceptance 在 live 全失败时为 False（B3-1 反向断言）；mock 边界设在 extract_content 内部抽取器返回值 + _run_pipeline_segments 入参截获，断言绑行为（extraction_success 翻转 / 不卡 intake_raw / 9 字段 frontmatter 出现 / 三分类正确 / fixture 正文加载 / 分层率不被 fixture 灌绿）
  WHY:   SC-1/1a/2a/2b/4/6 verify 是 pytest，断言须绑可观测行为非「能跑通」（宪法变更纪律 + review 规则 A/C）；B3-1 需反向断言 fixture 不补偿 live
  VERIFY: pytest tests/test_calibration_harness_diagnostics.py -q → 全 passed
```

> 动词均指向具体动作（新增函数/改调用点/派生字段/拼装 frontmatter/写断言），无「完善/优化/系统性/全面/增强」泛化。

---

## 7. Task Packet 拆分

| task_id | agent_role | owned_paths | blocked_by | can_parallel_with |
|---------|-----------|------------|-----------|-------------------|
| akb2-calibration-harness-diagnostics-t1 | implementer | `src/akb2/quality/calibration.py`, `tests/test_calibration_harness_diagnostics.py` | t2b-preflight-corpus | — |

> 单 task packet：4 个 SC 强耦合于同一抓取改造，拆多 packet 会让 calibration.py 写入冲突，违反 owned_paths 唯一写入约束。详见 `task-packets/akb2-calibration-harness-diagnostics-t1.md`。

---

## 8. 边界（修改 / 不修改）

修改：`src/akb2/quality/calibration.py`、`tests/test_calibration_harness_diagnostics.py`（新建）
不修改：`extract.py`/`preflight.py`（t2）、`evals/main-pipeline-quality/`、`config/main_sources.yaml`、包 A、`/Users/praise/.claude/**`、其他 step owned_paths

---

## 9. 风险与防护

| 风险 | 防护 |
|------|------|
| 误改 extract.py 补诊断 | 诊断字段已全在 ExtractResult；全部从 result 派生；extract.py 列 forbidden |
| SC-1 端到端报告 t2 corpus 未扩展 | blocked_by step 3；SC-2/4/6 直测解耦 |
| HTTP 字段缺失 | dead_link 退化按 error；http 字段 null，不阻断 SC-1 断言 |
| 复用 raw_archiver 私有函数耦合 | 优先 import 复用；不稳定则等价拼装 9 字段，不改 raw_archiver |
| mock 焊死实现细节脆测 | mock 边界设在 extract_content 返回值 + _run_pipeline_segments 入参，断言绑行为非调用次数 |

---

## 10. 测试计划

```bash
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2"
pytest tests/test_calibration_harness_diagnostics.py -q
ruff check src/akb2/quality/calibration.py tests/test_calibration_harness_diagnostics.py
PYTHONPATH=src python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json && python3 -c "import json;d=json.load(open('reports/calibration/factory.json'));E=d['entries'];KEYS={'failure_class','extract_method','word_count','metadata_complete','missing_metadata','extract_error','source'};assert all(KEYS<=set(e) for e in E);assert all(e['source'] in ('fixture','live') for e in E);assert sum(1 for e in E if e.get('expected')=='ingest' and e.get('source')=='live' and e.get('failure_segment')!='intake_raw')>=3;assert {'positive_live_total','positive_live_pass_rate','positive_fixture_pass_rate','live_positive_intake_raw_failures'}<=set(d);assert d['live_positive_intake_raw_failures']==0"
```

---

## 11. Assumptions

- `ExtractResult` 已含全部诊断字段（已读确认）。
- `_run_pipeline_segments(raw_path, raw_content, config)` 接收 raw_content 字符串（calibration.py 已确认签名）。
- step 3 会把 per-entry feed 元数据写进 corpus.yaml 每条 entry（字段名对齐 models.py）。
- SC-2 拆两层（B3-6，反转 round-2 误退化的 generic-only mock）：
  - **SC-2a**（机制）验 `_apply_feed_fallback`（未改动生产代码）的 ctx 回填：mock 缺 author/published 的 ExtractResult，± ctx 跑两次断言 extraction_success False→True，**不依赖真实日期、不走 fixture、不硬编码非空 ctx**（round-2 P2 保留）。
  - **SC-2b**（具名 URL）恢复 master「3 篇 False→True」目标：crewai/aider/MCP 保持 **live**（带真实 feed_*），SC-2b 测试读 t2b 保存的 `evals/main-pipeline-quality/fixtures/raw-html/<slug>.html` 注入 extract_content 抽取器底层（monkeypatch fetch、不发网络），走真实 extract_content + 真实 feed_* + 真实 _apply_feed_fallback 断言每条 False→True。前提：t2b 捕获 HTML 时这三页正文在、author/published 缺（metadata_complete=False）。
  - contingency（round-2 P3）：t2b 执行前 re-probe 这 3 页当前 failure class；若某页实为死链/正文不可达 → 该页降级到 `fixture:` 承载、其 SC-2b case 退化为 SC-2a 机制覆盖并在结果中登记（不静默丢失「该页 False→True」声称）。
- 新增具名常量 `LIVE_POSITIVE_QUOTA=3` / `LIVE_POSITIVE_PASS_FLOOR=0.9` 在 calibration.py 顶层（B3-1）；值为校准可调，但 floor 必须 >0（live 不得全失败仍 factory pass）。
- `raw_archiver.FRONTMATTER_FIELDS`（9 字段元组）可 import 复用作 SC-6 断言锚（raw_archiver 在 required_context 只读，B3-7）。
- step 3(t2b) 落地两类 fixture 文件：`fixtures/<name>.md`（`fixture:` 语料引用，供 _load_fixture_content）+ `fixtures/raw-html/<slug>.html`（SC-2b 注入用），用途不混。
