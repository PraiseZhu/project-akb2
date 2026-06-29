# 包 B 满仓校准「测试 rig」可信化 master plan v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md  (v1.2.0；AKB2 无 gd-v7 goal，按 plans/gd/2026-06-12-content-quality-loop 先例以 constitution + lead brief 为目标权威)
TEMPLATE_KIND: gd-master-plan

日期：2026-06-26
状态：draft
负责人：Claude 执行；Codex 可选 cross-review
dispatch_id：akb2-loop1-calibration-harness-fix

---

## 1. 目标链

```text
PROJECT_GOAL: 让 AKB2 循环1（知识库主质量管线）能跑一次可信的满仓校准，作为收口测试的前提；质量基线锚定 docs/constitution.md v1.2.0 原则 3（fail-closed 门控）/4（失败可见）/6（黄金样张质量锚定）。
CHAIN_GOAL:   把满仓校准从「测试 rig 失真导致 0% 误判」修成可信的正式验收测试——只修测试侧 harness（ExtractionContext 缺失、raw frontmatter 截断、报告诊断字段、corpus 死链），不动生产 admission/distill/checks_structural/grader/repair。
PHASE_GOAL:   包 B 交付——校准报告能区分死链/元数据失败/抽取失败三类；**报告 live/fixture 两层评分物理分离（独立计数与通过率），factory_acceptance 在 0.9/1.0 阈值之上额外绑 live 层下限，fixture 不补偿 live health**；3 篇元数据失败 URL（crewai/aider/MCP）保持 live，补 ExtractionContext 后 extraction_success 由 False→True（保存 HTML 注入 extract_content + 真实 feed_* + 真实 _apply_feed_fallback 可复现）；corpus 全 URL 经 preflight 标出死链/重定向/soft-404；活语料端到端薄切片能流过 intake_raw 进入判定。
```

> 关键前提（lead brief + 6/25 校准考古）：上次满仓校准 0% 通过的主因是**测试 rig 本身坏了**，不是生产管线坏了——16 篇 intake_raw 失败 ≈ 13 死链（测试语料过期 404，生产拉活 RSS 不受影响）+ 3 篇校准 harness bug（缺 ExtractionContext）+ 1 抽取器失败（Scrapling API 失配）。本包修 rig，不修生产 intake。

---

## 2. Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS（分号分隔）：`测试 rig 镜像生产抽取契约（ExtractionContext + 完整 raw frontmatter）; 报告诊断字段三分类正确（dead_link/metadata_failure/extraction_failure）; 不越界改生产 admission/distill/grader/repair（包 A 边界）; Scrapling fallback 修复不回归 docling/签名兜底; corpus 双层（确定性快照 vs live 探活）与 main_sources 对齐`
- Domain-specific notes：本 plan 只改测试侧 harness 与抽取器调用点 + 语料 + preflight；**禁止**触碰包 A（admission/distill/checks_structural/grader/repair.py）；诊断字段全部从既有 `ExtractResult` 派生（extract.py 零功能改动，仅 Scrapling 调用点修复）；HTTP 层字段由 preflight 产出、报告侧读取合并。

---

## 3. 成功标准（SC）

> Anti-fill：每条 SC 绑定命令 / 路径 / 输出断言 / 测试用例之一。下方命令均为 execute 阶段真实验收命令（cwd = AKB2 仓库根）。

> **SC 子编号约定（machine artifact 适配）**：下方人读 SC 用 `SC-1a / SC-2a / SC-2b` 等子编号；`dispatch_map.json` 的 `sc_ref` schema 仅允许 `^SC-[0-9]+$`，故子编号在机器工件中归并到父 numeric SC（同一 `sc_ref` 下多条 verify），不丢断言。

- [ ] SC-1（报告诊断字段 + live/fixture 分层评分 + factory live 下限，B3-1/B3-2）：校准报告 `entries[]` **每条**携带完整诊断键集 `{failure_class, extract_method, word_count, metadata_complete, missing_metadata, extract_error, source}`，`source ∈ {fixture, live}`，可区分 `dead_link` / `metadata_failure` / `extraction_failure`；诊断键在 base result dict 初始化、全 6 条返回路径（成功/死链早退/抽取失败/fixture/负样本/ImportError）都携带（防 all() KeyError，round-2 P1）。**报告顶层额外产出 live/fixture 两套独立计数与率：`positive_live_total` / `positive_live_passed` / `positive_live_pass_rate` / `positive_fixture_total` / `positive_fixture_passed` / `positive_fixture_pass_rate` / `live_positive_intake_raw_failures`（B3-1）。`factory_acceptance` 在保留混池 `positive_pass_rate>=0.9` + `negative_reject_rate==1.0` 之上，额外 AND 三个 live 层下限：`positive_live_total >= LIVE_POSITIVE_QUOTA(=3)`、`live_positive_intake_raw_failures == 0`、`positive_live_pass_rate >= LIVE_POSITIVE_PASS_FLOOR`（纯收紧，fixture 通过不补偿 live health）。**
  - execute 期 live floor 契约：`LIVE_POSITIVE_PASS_FLOOR` 默认值保持 `0.9` 不改；按 preflight/reachable live 正样本数 `n` 计算报告内 `effective_live_positive_pass_floor`，`n<5` 不下调（默认 0.9 的实际效果要求全过，样本太小则补 live 或重探），`5<=n<10` 可下调到 `0.8`（最多容忍 1 条公网漂移），`n>=10` 回到 `0.9`；任何情况下 floor 永不低于 `0.8`，且恒保 `LIVE_POSITIVE_QUOTA>=3`、`live_positive_intake_raw_failures==0`，下调原因必须写入报告（如 `live_positive_pass_floor_reason`），fixture 仍不得补偿 live health。
  - verify(assertion，前提：网络 + DeepSeek/GPT key)：先**重新生成报告再断言**——`PYTHONPATH=src python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json && python3 -c "import json;d=json.load(open('reports/calibration/factory.json'));E=d['entries'];KEYS={'failure_class','extract_method','word_count','metadata_complete','missing_metadata','extract_error','source'};assert all(KEYS<=set(e) for e in E),'missing diag keys';assert all(e['source'] in ('fixture','live') for e in E),'bad source';assert sum(1 for e in E if e.get('expected')=='ingest' and e.get('source')=='live' and e.get('failure_segment')!='intake_raw')>=3,'live floor';assert {'positive_live_total','positive_live_pass_rate','positive_fixture_pass_rate','live_positive_intake_raw_failures'}<=set(d),'missing stratified fields';assert d['live_positive_intake_raw_failures']==0,'live stuck at intake_raw'"` → 退出码 0（全 7 键 + source∈{fixture,live} + ≥3 live(source=='live') ingest 越过 intake_raw + 分层字段在场 + live 无 intake_raw 卡死；防 fixture 灌绿 + 防读陈旧文件假过 + 防混池掩盖 live，B3-1/B3-2）
- [ ] SC-1a（fixture 加载，cross-review P1#M1）：`_load_fixture_content` 按 `fixture:<name>` 从 `evals/main-pipeline-quality/fixtures/<name>.md` 读取（非硬编码 google-io）；任一非 google-io fixture entry 返回非空正文且 `failure_segment != "intake_raw"`。
  - verify(test)：`pytest tests/test_calibration_harness_diagnostics.py -k fixture_load -q` → passed
- [ ] SC-2a（ctx 回填机制无条件 mock，B3-6）：同一缺 author/published 的 `ExtractResult` ± `ExtractionContext` 跑两次 `_apply_feed_fallback`/extract_content，断言不传 ctx 为 `extraction_success=False`、传 ctx（feed 字段构造）翻 `True`；不走 fixture 路径、不硬编码非空 ctx。
  - verify(test)：`pytest tests/test_calibration_harness_diagnostics.py -k extraction_context -q` → passed
- [ ] SC-2b（3 具名 URL 可复现 before/after，B3-6）：对 `crewai`/`aider`/`MCP` 三条 corpus entry（保持 live、带真实 feed_*），各用 t2b 保存的 raw HTML（`fixtures/raw-html/<slug>.html`）注入 `extract_content` 抽取器底层、走**真实** extract_content 决策树 + **真实** corpus `feed_*` 构造 ctx + **真实** `_apply_feed_fallback`，断言每条 `extraction_success` 不传 ctx=False、传 ctx=True（恢复 master 目标「3 篇 False→True」可测；execute 期若 re-probe 某页实为死链则降级该页到 fixture + SC-2a 机制覆盖并登记，B3-6 contingency）。
  - verify(test)：`pytest tests/test_calibration_harness_diagnostics.py -k extraction_context_named_urls -q` → passed（3 个 case：crewai/aider/mcp 各断言 False→True）
- [ ] SC-3（preflight 分类 + bad-list 闭合断言 + soft-404，B3-3）：corpus 全 URL 经 preflight 产 per-entry 分类报告（`http_status/final_url/content_type/redirect/canonical_drift/short_body/soft_404/missing_metadata` + 回显 `expected/source`）+ 汇总；**修复后 expected=ingest 的非 fixture entry 中 `http_status>=400 或 short_body 或 soft_404` 计数==0**；`soft_404` 由 title/body not-found pattern（`404`/`not found`/`page not found`/`no longer available`，大小写不敏感）**或** canonical/final_url 域漂移派生（不只靠 http_status>=400+short_body）；`canonical_drift` 要么 ==0、要么每条具名登记且不计 live pass。
  - verify(command+assertion)：`PYTHONPATH=src python3 -m akb2.quality.intake.preflight --corpus evals/main-pipeline-quality/corpus.yaml --out reports/calibration/preflight.json && python3 -c "import json;r=json.load(open('reports/calibration/preflight.json'));E=r['entries'];assert all({'http_status','final_url','content_type','redirect','canonical_drift','short_body','soft_404','missing_metadata'}<=set(e) for e in E),'missing fields';bad=[e for e in E if e.get('expected')=='ingest' and e.get('source')!='fixture' and ((e.get('http_status') or 0)>=400 or e.get('short_body') or e.get('soft_404'))];assert not bad, bad"` → exit 0（字段齐全 + ingest live `http_status>=400/short_body/soft_404` 清零）
- [ ] SC-4：活语料端到端薄切片流过 intake_raw 进入判定（≥1 正样本 `failure_segment != "intake_raw"`）。
  - verify(test)：`pytest tests/test_calibration_harness_diagnostics.py -k e2e_thin_slice -q` → passed
- [ ] SC-5：Scrapling fallback API 修复（无 `'Fetcher' object has no attribute 'fetch'`），fallback 失败仍 fail-closed 回落 docling/签名，路径被测试覆盖。
  - verify(test)：`pytest tests/test_extract_scrapling_fallback.py -q` → passed
- [ ] SC-6（完整 9 字段 frontmatter，B3-7）：校准向 distill 传的 raw frontmatter 含 `raw_archiver.FRONTMATTER_FIELDS` 全部 9 字段（artifact_type/source_slug/source_url/source_title/source_authors/source_publisher/source_published_at/fetched_at/fetched_via），消除 raw_content 缺失类信号。
  - verify(test)：`pytest tests/test_calibration_harness_diagnostics.py -k raw_frontmatter -q` → passed（测试 `from src.akb2.quality.intake.raw_archiver import FRONTMATTER_FIELDS` 后断言 `set(produced_keys) >= set(FRONTMATTER_FIELDS)`）
- [ ] SC-7（双层 + 对齐 + 9 领域配比 + **live 层代表性**，B3-5）：corpus「固定 fixture/snapshot + live URL health」双层存在；live 正样本 domain 对齐 main_sources（aligned_count≥配额且≥2 真实命中，不齐 live 项 `off_corpus_rationale` 具名真实 main_sources slug）；替换后 9 领域计数==基线；**新增 live 层代表性：每个非豁免领域 `expected=ingest 且 source=live` 计数 ≥1；某领域全 fixture 必须在 corpus 显式 `fixture_only_domains` allowlist 具名且不计 live health**。
  - verify(test)：`pytest tests/test_corpus_preflight.py -k alignment -q` → passed

---

## 4. 非目标（NON_GOALS）

- 不改包 A：`src/akb2/quality/{admission,distill,checks_structural,grader,repair}.py` 的任何判定逻辑（那是包 A「改标准」范围）。
- 不改生产 intake 主链路抽取决策树 / `_validate_metadata` / `_apply_feed_fallback`（诊断字段已全在 ExtractResult；Scrapling 仅修调用点）。
- 不**放宽** `positive_pass_rate>=0.9` / `negative_reject_rate==1.0` 两项阈值数值；`factory_acceptance` 在保留这两项基础上**新增** live 层下限（`LIVE_POSITIVE_QUOTA=3` + `live_positive_intake_raw_failures==0` + `positive_live_pass_rate>=LIVE_POSITIVE_PASS_FLOOR`），纯收紧不放松，fixture 仅作确定性锚不补偿 live health（B3-1）。
- 不碰 Obsidian vault；不注册自动化触发器；不 pip install / 不升级依赖（scrapling 0.4.8 实测可用，纯 call-site 修复）。
- 本计划包是产物，仅出计划不改生产代码。

---

## 5. Step 拆分

| Step | 名称 | owned_paths | blocked_by | can_parallel_with | 主要 SC |
|------|------|------------|-----------|-------------------|---------|
| 1 | 校准核心 harness（calibration.py：ExtractionContext + 诊断三分类 + 完整 raw frontmatter） | `src/akb2/quality/calibration.py`, `tests/test_calibration_harness_diagnostics.py` | step 3 | — | SC-1, SC-2, SC-4, SC-6 |
| 2 | Scrapling fallback API 修复 | `src/akb2/quality/intake/extract.py`, `tests/test_extract_scrapling_fallback.py` | — | step 3 | SC-5 |
| 3 | corpus 双层 + preflight guard + per-entry feed 元数据扩展 | `src/akb2/quality/intake/preflight.py`, `evals/main-pipeline-quality/`, `tests/test_corpus_preflight.py`（`config/main_sources.yaml` **只读 required_context，不 owned**，B3-4） | — | step 2 | SC-3, SC-7 |

> 关键依赖（surface conflict）：step 1 构造 `ExtractionContext` 所需的 per-entry feed 元数据（feed_title/feed_author/feed_published/source_publisher）由 **step 3 扩展进 corpus.yaml** 供给，故 step 1 `blocked_by` step 3。step 1 的 SC-2a/SC-4/SC-6 直测用构造 ctx / fixture 解耦，可在 step 3 未完成时独立绿；SC-1 端到端报告生成与 SC-2b（3 具名 URL）依赖扩展后的 corpus 与保存 HTML。
>
> **跨 lane 契约 2（cross-review P1#M1）——fixture 加载**：step 3(t2b) 落 `evals/main-pipeline-quality/fixtures/<name>.md` 快照，step 1(t1) 须把 `_load_fixture_content` 从「硬编码只认 google-io」改为「按 `fixture:<name>` 从该目录读 `<name>.md`」（见 step-1 SC-1a）。否则黄金样张/概念正样本等确定性锚永远 return None → 卡 intake_raw、factory 恒 FAIL。二者经 step 1 `blocked_by` step 3 串接。
>
> **跨 lane 契约 3（B3-6）——SC-2b 保存 HTML 注入**：step 3(t2b) 在 `evals/main-pipeline-quality/fixtures/raw-html/<slug>.html` 保存 crewai/aider/MCP 三页真实 HTML（捕获时这三页正文在、author/published 缺，metadata_complete=False），并在 corpus 保持这三条为 **live**（带真实 `feed_*`）。step 1(t1) 的 SC-2b 测试读这三个 HTML 注入 `extract_content` 抽取器底层（monkeypatch fetch，不发网络），走真实 extract_content + 真实 feed_* + 真实 `_apply_feed_fallback`，断言 ±ctx 的 `extraction_success` False→True。此契约把「3 篇具名 URL False→True」从 round-2 误退化的 generic mock 恢复为可复现具名验证。`fixtures/raw-html/` 与 `fixtures/<name>.md`（`fixture:` 语料引用）是两类不同用途文件，不得混用。

---

## 5a. Dispatch Map / Wave Contract（MANDATORY）

### Dispatch Map 引用

```
PLANNING_DISPATCH_MAP_PATH: plans/gd/2026-06-26-loop1-calibration-harness-fix/dispatch_map.json
VALIDATE_CMD: python3 <GD_ROOT>/scripts/gd-validate-dispatch.py plans/gd/2026-06-26-loop1-calibration-harness-fix/dispatch_map.json   # 已 exit 0
```

> 上述 dispatch_map.json 是 **rev21 planning-stage** dispatch（2 个 child_planner lane，max_parallel_planning=2，已 validator 通过）。下方 Wave Matrix 是 **execution-stage** 编排，由 `/gd execute` 阶段据此 step DAG 另行 author 一份 execution dispatch map（mode=execute）。

### Wave Matrix（execution-stage，child executor）

| Wave | Steps（同 wave 可并行） | child_agent_count | 并行前提验证 |
|------|------------------------|-------------------|-------------|
| 1 | step-2, step-3 | 2 | owned_paths 不重叠（`extract.py` vs `preflight.py`/`evals/`/`config/`）；互不 blocked_by；can_parallel_with 双向声明 |
| 2 | step-1 | 1 | blocked_by step-3（需 corpus per-entry feed 元数据）；owned `calibration.py` 与 wave1 不重叠 |

> 规则：同 wave 内最多 2 个 step（`max_parallel=2` 硬上限）；串行 step 单独成 wave，`child_agent_count=1`；执行前先跑 execution dispatch map 的 `VALIDATE_CMD`（exit 0）再按 wave 顺序 dispatch。

---

## 6. 边界（修改 / 不修改）

修改：
- `src/akb2/quality/calibration.py`
- `src/akb2/quality/intake/extract.py`（仅 `_extract_via_scrapling` 调用点）
- `src/akb2/quality/intake/preflight.py`（新建）
- `evals/main-pipeline-quality/`（corpus.yaml + fixtures/snapshots）
- `tests/test_calibration_harness_diagnostics.py`、`tests/test_extract_scrapling_fallback.py`、`tests/test_corpus_preflight.py`（新建）
- `reports/calibration/`（preflight.json / factory.json 产物，运行期写）

不修改：
- `config/main_sources.yaml`（**只读**：alignment/推导只读不写——不为测试扩 production 源，round-2 P2；如需扩源由用户单独发起、单独 packet）
- 包 A：`src/akb2/quality/{admission,distill,checks_structural,grader,repair}.py`
- `src/akb2/quality/intake/{orchestrator,raw_archiver,models}.py`（只读）
- Obsidian vault 任何路径
- `/Users/praise/.claude/**`

---

## 7. 风险与防护

| 风险 | 防护 |
|------|------|
| 为「补诊断字段」误改 extract.py（越界包边界）| 诊断字段已全在 ExtractResult（extract.py:78-92 已核实）；step 1 全部从 result 派生；extract.py 在 step 1 packet 列 forbidden_paths；extract.py 改动仅限 step 2 的 Scrapling 调用点 |
| 误把校准失败再次误判为「生产管线坏」 | SC-1 三分类 + SC-3 preflight 把死链/元数据失败/抽取失败分开标出（原则 4 失败可见）|
| Scrapling 修复让 fallback 失败时不再 fail-closed | SC-5 测试含「抛错→返回 {}→method==FAILED→落签名」不回归断言（原则 3）|
| 公网漂移再次把质量信号清零 | SC-7 双层：质量回归读本地快照（确定性），探活读 live（preflight）；二层物理分离 |
| step 1↔step 3 跨 lane 元数据契约字段名不一致 | 字段名严格对齐 `models.py` ExtractionContext；step 3 test 断言字段存在，step 1 blocked_by step 3 |

---

## 8. 测试计划

```bash
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2"
# SC-5
pytest tests/test_extract_scrapling_fallback.py -q
# SC-7（双层 + 对齐 + 9 领域 + live 层代表性）
pytest tests/test_corpus_preflight.py -k alignment -q
# SC-3（preflight + bad-list 闭合断言：ingest live 死链/soft-404/short_body 清零）
PYTHONPATH=src python3 -m akb2.quality.intake.preflight --corpus evals/main-pipeline-quality/corpus.yaml --out reports/calibration/preflight.json && \
python3 -c "import json;r=json.load(open('reports/calibration/preflight.json'));E=r['entries'];assert all({'http_status','final_url','content_type','redirect','canonical_drift','short_body','soft_404','missing_metadata'}<=set(e) for e in E);bad=[e for e in E if e.get('expected')=='ingest' and e.get('source')!='fixture' and ((e.get('http_status') or 0)>=400 or e.get('short_body') or e.get('soft_404'))];assert not bad, bad"
# SC-1a / SC-2a / SC-2b / SC-4 / SC-6（直测）
pytest tests/test_calibration_harness_diagnostics.py -q
# SC-1（端到端报告：先重生成 → 全 7 键 + source∈{fixture,live} + ≥3 live 越过 intake_raw + 分层字段 + live 无 intake_raw 卡死）
PYTHONPATH=src python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json && \
python3 -c "import json;d=json.load(open('reports/calibration/factory.json'));E=d['entries'];KEYS={'failure_class','extract_method','word_count','metadata_complete','missing_metadata','extract_error','source'};assert all(KEYS<=set(e) for e in E);assert all(e['source'] in ('fixture','live') for e in E);assert sum(1 for e in E if e.get('expected')=='ingest' and e.get('source')=='live' and e.get('failure_segment')!='intake_raw')>=3;assert {'positive_live_total','positive_live_pass_rate','positive_fixture_pass_rate','live_positive_intake_raw_failures'}<=set(d);assert d['live_positive_intake_raw_failures']==0"
# 一致性不变量（B3-4）：任何 owned_paths 不含 main_sources，且 required_context 至少一处含它
python3 -c "import json;m=json.load(open('plans/gd/2026-06-26-loop1-calibration-harness-fix/dispatch_map.json'));owned=[p for t in m['tracks'] for p in t['owned_paths']];req=[p for t in m['tracks'] for p in t['required_context']];assert not any('main_sources.yaml' in p for p in owned),'main_sources must not be owned';assert any('main_sources.yaml' in p for p in req),'main_sources must be in required_context';print('PASS main_sources read-only invariant')"
# 不回归
ruff check src/akb2/quality/calibration.py src/akb2/quality/intake/extract.py src/akb2/quality/intake/preflight.py
```

---

## 9. Assumptions

- `ExtractResult`（extract.py:78-92）已含 method/title/author/published/content/word_count/metadata_complete/extraction_success/error/raw_metadata 全部诊断字段（已读确认）。
- 生产 `orchestrator.py:214-227` 以 `extract_content(url=..., extraction_config=ctx)` 形态调用，校准镜像此形态即可。
- `ExtractionContext`（models.py）字段：source_slug/source_publisher/feed_title/feed_author/feed_published/url/content_type/content_hash（已读确认）。
- scrapling 0.4.8：`Fetcher.get(url)` / `StealthyFetcher.fetch(url)` 均为 classmethod，`Fetcher` 无 `.fetch`（子 agent 实测确认）→ SC-5 为纯 call-site 修复。
- calibration.py 已有 `fixture:` 前缀分发（:217）；但 `_load_fixture_content`（:235-264）现状**仅硬编码 google-io、不读 fixtures/ 目录**——故 step 1 必须改它为从 `evals/main-pipeline-quality/fixtures/<name>.md` 按名读取（已列入 step-1 SC-1a）；corpus 新字段（feed 元数据）由 step 1 构造 ctx 时读取。原「不需 step 1 改代码即可消费 fixture」假设已作废（cross-review P1#M1）。
- `calibration.py:357-393` `_compute_statistics` 现状 `positive_pass_rate = positive_passed / len(positive_entries)` 不分 live/fixture；`:447-450` `factory_acceptance` 直接读混池率——B3-1 据此实证。step 1 在 `_compute_statistics` 增 live/fixture 分层计数与率、在 `factory_acceptance` AND live 层下限（calibration.py 是测试 rig 本体、t1 owned，非生产 admission/grader，改之不越界）。
- `raw_archiver.FRONTMATTER_FIELDS`（raw_archiver.py:45-55）是 9 字段元组、逐一 fail-closed（:258-268）——SC-6 据此改全 9 字段断言（B3-7）。

---

## 10. Round-3 修订记录与工件权威分层（异族 Codex 审）

> round-3 真异族 Codex(GPT-5.5) 审戳穿前两轮 Claude 同族审未逮的 7 处设计洞（B3-1~B3-7）。本节固化「哪个工件是权威、哪个是历史快照」，杜绝 round-2 翻车点（只改 step 没传播到机器工件）。

**权威工件（round-3 已逐条修订、互相一致、进入执行验收链）**：
- `master-plan.md`（本文件 §3 SC + §8 测试计划）— SC/verify 唯一权威人读源
- `step-1-calibration-core.md` / `step-2-intake-corpus.md` — 实现步骤权威
- `task-packets/*.md` §7 verify — 执行验收唯一权威机读源
- `dispatch_map.json` — 唯一被 `gd-validate-dispatch.py` 校验的机器工件；其 `tracks[].verify` 与 owned_paths 已 round-3 同步（`sc_ref` 受 schema `^SC-[0-9]+$` 约束，子编号归并父 SC）

**历史快照（round-1 规划事件记录、不随 round-3 SC 修订更新、从执行验收链排除）**：
- `child-proposals/t1-*.md` / `child-proposals/t2-*.md` — child_planner 原样落盘输出，sha256 由 `stage-dispatch-ledger.json` 锚定 round-1 规划事件；其内 owned_paths/verify 反映 round-1 状态，**不再是 owned_paths / SC 权威**（如 t2 proposal 内 `config/main_sources.yaml` owned 归属已被 dispatch_map + task-packet 的只读约定取代，B3-4）。不编辑（保 hash 链与「原样落盘」诚实性）。
- `controller-report.json` / `stage-dispatch-ledger.json` — round-1 规划 dispatch 执行账本，不编辑（保 hash 链）。

**main_sources 只读不变量（B3-4）**：任何权威工件的 `owned_paths` 均不含 `config/main_sources.yaml`；它只出现在 t2b 的 `required_context`（只读）。§8 测试计划末条一致性断言对 `dispatch_map.json` 机判此不变量。
