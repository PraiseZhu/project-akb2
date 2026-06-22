# AKB2 抓取链路专项 GD 计划（CQL Intake v1）

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-master-plan

日期: 2026-06-14
状态: draft（2026-06-17 实测修订：本计划范围已交付，archived 0→4，非阻塞；当前阻塞在下游 repair，见文末修订）
负责人: Claude 执行; Codex cross-review

> 规格输入: 计划审查反馈（2026-06-14）7 finding + 3 P3 风险；蓝本 `/Users/praise/.claude/plans/structured-puzzling-moore.md`（已废弃）
> 设计权威: `src/akb2/quality/intake/*.py`（代码事实）；旧 step-2 SC-2.4/SC-2.5（硬断言复用）

---

## 1. 目标链

```text
PROJECT_GOAL: AKB2 内容质量循环（循环1）全链上线，以 76 个源自动供料。
CHAIN_GOAL: 把抓取链路（discovery→extract→archive→orchestrator）从占位 stub 接成生产可用，补全数据契约和幂等机制。
PHASE_GOAL: 本计划只覆盖抓取链路：抽取三工具接真、ExtractionContext 数据契约、article_id 唯一身份、intake state ledger、orchestrator CLI、source_validator live；active 入库/修宪/launchd/Mac mini 在后续计划处理。
TASK_GOAL: 全部 SC 通过（pytest 绿 + lint 绿），orchestrator CLI 可 dry-run 无错，source_validator 可产 sources-ledger.jsonl，raw 文件名含 article_id 短码，重跑幂等。
```

REVIEW_DOMAIN: ai_infra
REVIEW_FOCUS: 抽取数据契约(F2 ExtractionContext/fallback); article_id 唯一身份与幂等归档(F3); 三工具真实调用 defuddle/scrapling/docling(F4 SC 可执行); raw 9 字段/锚点 fail-closed(F5); source_validator 76 源硬断言(F6); intake state ledger 状态外置(F7); import gate 探测(P3)

---

## 2. 非目标

- active 自动入库与 vault 修宪（后续计划）
- launchd plist com.akb2.intake.plist（本轮只交付 CLI，plist 留部署计划）
- Mac mini 部署配置与 24h daemon smoke（部署计划）
- 抓取链路以外模块修改（admission/distill/grader/repair/promote/runner/calibration）
- corpus 语料换链、出厂校准
- Gate 5 接线、第五闸修复

---

## 3. 成功标准（SC）

- [ ] SC-1（数据契约）: `ExtractionContext` dataclass ≥8 字段（source_slug/source_publisher/feed_title/feed_author/feed_published/url/content_type/content_hash）；`extract_content(url, extraction_config)` fallback 顺序：`title ← extracted.title or context.feed_title`、`author ← extracted.author or context.feed_author`、`published ← extracted.published or context.feed_published`、`publisher ← extracted.publisher or context.source_publisher`；四者皆缺且元数据不齐时 fail-closed 不产 raw 文件
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_extraction_context.py -q`
  - expect: exit 0; "extractor 缺 title 但 feed_title 存在→可归档；author/published/publisher 同理 fallback；四者皆缺 fail-closed 断言通过"

- [ ] SC-2（defuddle 接真）: `_extract_via_defuddle` 调 `defuddle parse <tmpfile> --json -m` 而非返回模拟标题；word_count 基于 markdown 正文计算（非 HTML 字符数）
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_extract.py::TestExtractViaDefuddle -q`
  - expect: exit 0; "content 字段无 HTML 标签；title 非写死字符串 'Defuddle Extracted Title'"

- [ ] SC-3（scrapling 接真）: `_extract_via_scrapling` 调 `scrapling.Fetcher` 或 `StealthyFetcher`；browser_mode=True 时走 playwright 浏览器渲染；返回真实 title/author/content 而非写死占位
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_extract.py::TestExtractViaScrapling -q`
  - expect: exit 0; "自适应和浏览器模式各覆盖 1 个 fixture；title 非 'Scrapling adaptive Title'"

- [ ] SC-4（docling 接真）: `_extract_via_docling` 调 `docling.document_converter.DocumentConverter().convert()`；file_bytes 落临时 PDF；返回 export_to_markdown() 内容而非 bytes 原文 decode
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_extract.py::TestExtractViaDocling -q`
  - expect: exit 0; "content 非 bytes.decode() 原始 PDF 文本；word_count 基于 markdown 正文"

- [ ] SC-5（article_id 唯一身份与幂等归档）: article_id = sha256(canonical_url).hexdigest()[:12]；raw 文件名 `<source_slug>--<article_id>--raw--<date>.md`；归档成功写入 intake state ledger；同源同日两篇不同 article_id 产两个文件；同输入重跑 ledger 已有 article_id 则跳过不产新文件
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_orchestrator.py -q -k "article_id or idempotent"`
  - expect: exit 0; "同源同日两篇产 2 个不同文件名；重跑同 URL 仅 1 个文件（幂等）"

- [ ] SC-6（raw 归档 fail-closed）: frontmatter 9 字段齐（artifact_type/source_slug/source_url/source_title/source_authors/source_publisher/source_published_at/fetched_at/fetched_via）；§A-§Z 锚点 assert 全命中；任一不满足不产 raw 文件落 failure 签名；签名含 details/sample_error 字段
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_raw.py -q`
  - expect: exit 0; "≥22 tests passed；frontmatter 缺字段→无文件断言、锚点未命中→无文件断言、failure 签名有 details 字段断言全过"

- [ ] SC-7（orchestrator dry-run）: `python -m akb2.quality.intake.orchestrator --dry-run --limit 3 --sources-config config/main_sources.yaml` 退出码 0；stdout 含 discovered/skipped/archived 计数；不写任何 raw 文件（archived=0）；不打真网（httpx/scrapling 被 mock 为 fail-on-call）
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && PYTHONPATH=src python -m akb2.quality.intake.orchestrator --dry-run --limit 3 --sources-config config/main_sources.yaml 2>&1 | tee /tmp/dry-run-out.txt && grep -q 'archived=0' /tmp/dry-run-out.txt && echo PASS`
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_orchestrator.py -q -k "dry_run_no_network or no_network_no_raw"`
  - expect: PASS + exit 0; archived=0；网络调用触发时测试失败，正常 dry-run 通过（`test_dry_run_no_network_no_raw` 断言）

- [ ] SC-8（intake state ledger）: orchestrator 写/读 reports/intake/intake-state.jsonl；字段含 run_id/source_slug/article_id/last_fetch_at/daily_count/quarantine_status/status；rate_limit 超限 status=skipped_rate_limit；quarantine 命中 status=skipped_quarantine 不写 raw
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_orchestrator.py -q -k "rate_limit or quarantine or ledger"`
  - expect: exit 0; "rate_limit 超限跳过断言、quarantine 命中不写 raw 断言、ledger 记录 run_id 和 status 断言全过"

- [ ] SC-9（source_validator mocked 单测）: 单测默认跑（-m 'not integration'）；覆盖 dry-run 结构、CLI 可用、ledger 字段完整
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_source_validator.py -q -m "not integration"`
  - expect: exit 0

- [ ] SC-10（source_validator live—真网 integration 门）: --live 模式产 sources-ledger.jsonl；76 行、unique_urls==config_urls、必填字段（feed/url/reachable/latest_post/trial_fetch）齐、reachable≥70；同次运行 run_id 一致；以 run_id 原子写入不混旧记录
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && AKB2_LIVE_VALIDATE=1 PYTHONPATH=src python -m akb2.quality.intake.source_validator --live --out reports/source-validation/sources-ledger.jsonl && python -c "import json,yaml; cfg=yaml.safe_load(open('config/main_sources.yaml'))['sources']; c_urls={s.get('feed_url') or s.get('listing_url') for s in cfg}-{None,''}; rs=[json.loads(l) for l in open('reports/source-validation/sources-ledger.jsonl')]; assert len(rs)==76,'rows!=76'; u={r['url'] for r in rs}; assert u==c_urls,'url mismatch'; req_fields={'feed','url','reachable','latest_post','trial_fetch'}; bad=[i for i,r in enumerate(rs) if not req_fields.issubset(r.keys())]; assert not bad,f'missing fields at rows {bad}'; ok=sum(1 for r in rs if r.get('reachable')); assert ok>=70,f'reachable={ok}<70'; ok_fields=sum(1 for r in rs if r.get('reachable') and r.get('trial_fetch') and r.get('latest_post')); assert ok_fields>=60,f'reachable+nonempty_trial_fetch+latest_post={ok_fields}<60'; run_ids={r.get('run_id') for r in rs}; assert len(run_ids)==1,f'mixed run_ids: {run_ids}'; print('PASS')"`
  - expect: PASS; 76 行 ledger，unique_urls==config_urls，必填字段齐，reachable≥70，reachable 源 trial_fetch+latest_post 非空≥60，run_id 一致

- [ ] SC-11（工程门）: 抓取链路全量测试 + lint 绿；无真网调用（integration 标记 deselected）
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/ tests/loops/ -q && ruff check src/akb2/quality/intake/ tests/quality/ && echo PASS`
  - expect: PASS; 全部测试通过，ruff 0 issues，4 deselected 为 integration 真网项

---

## 4. Step 拆分

| Step | 名称 | owned_paths | blocked_by | 主要 SC |
|------|------|-------------|-----------|---------|
| 1 | 数据契约与身份契约 | `src/akb2/quality/intake/models.py`(新建), `extract.py`(API 扩展), `tests/quality/test_extraction_context.py`(新建) | — | SC-1, SC-5(partial) |
| 2 | 抽取三工具接真 | `src/akb2/quality/intake/extract.py`, `tests/quality/test_intake_extract.py`(修改) | step-1 | SC-2, SC-3, SC-4 |
| 3 | orchestrator + intake state ledger | `src/akb2/quality/intake/orchestrator.py`(新建), `reports/intake/`, `.gitignore`(补 reports/intake/ 排除), `tests/quality/test_intake_orchestrator.py`(新建) | step-1,2 | SC-5(full), SC-7, SC-8 |
| 4 | raw 归档契约补全 | `src/akb2/quality/intake/raw_archiver.py`, `tests/quality/test_intake_raw.py`(修改) | step-1 | SC-6 |
| 5 | source_validator live + discovery listing | `src/akb2/quality/intake/source_validator.py`, `discovery.py`, `reports/source-validation/sources-ledger.jsonl`(运行时产出), `tests/quality/test_source_validator.py`(新建) | step-1 | SC-9, SC-10 |
| 6 | 工程门 + import gate + pyproject | `pyproject.toml`, `src/akb2/quality/intake/__init__.py`, `docs/` | step-2~5 | SC-11 |

### 4.5 Task Packet 契约字段速查（GD §6 required fields）

| Step | forbidden_paths（禁改） | can_parallel_with | required_context | deliverables |
|------|------------------------|-------------------|-----------------|--------------|
| 1 | 见§5 全局 forbidden_paths | — | `intake/extract.py`（现有接口） | `intake/models.py`（新建）、`test_extraction_context.py`（新建） |
| 2 | `intake/models.py`（只读）; 见§5 | step-4 | `intake/models.py`（step-1产出） | `intake/extract.py`（修改）、`test_intake_extract.py`（修改） |
| 3 | `intake/extract.py`、`intake/raw_archiver.py`、`intake/source_validator.py`、`intake/discovery.py`（只读）; 见§5 | — | `intake/models.py`（step-1）、`intake/extract.py`（step-2产出） | `intake/orchestrator.py`（新建）、`test_intake_orchestrator.py`（新建）、`reports/intake/`（运行时目录）、`.gitignore`（补 reports/intake/ 排除） |
| 4 | `intake/extract.py`、`intake/orchestrator.py`、`intake/discovery.py`、`intake/source_validator.py`（只读）; 见§5 | step-2 | `intake/models.py`（step-1）、`intake/raw_archiver.py`（现有） | `intake/raw_archiver.py`（修改）、`test_intake_raw.py`（修改） |
| 5 | `intake/extract.py`、`intake/orchestrator.py`、`intake/raw_archiver.py`（只读）; 见§5 | step-3、step-4 | `intake/models.py`（step-1）、`config/main_sources.yaml` | `intake/source_validator.py`（修改）、`intake/discovery.py`（修改）、`test_source_validator.py`（新建）、`reports/source-validation/sources-ledger.jsonl`（运行时产出） |
| 6 | `intake/extract.py`~`intake/source_validator.py`（只读）; 见§5 | — | `pyproject.toml`（现有）、step-2~5 产出 | `pyproject.toml`（修改）、`intake/__init__.py`（修改）、`docs/runbooks/`（新建） |

---

## 5. 边界

修改：
- `src/akb2/quality/intake/`（全目录：extract.py/raw_archiver.py/discovery.py/source_validator.py 修改；models.py/orchestrator.py 新建）
- `tests/quality/test_intake_extract.py`、`test_intake_raw.py`（修改）
- `tests/quality/test_extraction_context.py`、`test_intake_orchestrator.py`、`test_source_validator.py`（新建）
- `reports/intake/`（新建目录，step-3 负责；`.gitignore` 同步排除）
- `.gitignore`（step-3 负责补 `reports/intake/` 排除规则；verify: `git check-ignore reports/intake/intake-state.jsonl`）
- `reports/source-validation/sources-ledger.jsonl`（step-5 live 模式运行时产出；verify: SC-10 命令断言 76 行）
- `pyproject.toml`（scrapling/docling 补入 dependencies）

不修改（forbidden_paths）：
- `src/akb2/quality/{admission,distill,grader,repair,promote,runner,calibration}.py`
- `src/akb2/loops/`、`schema/`、`skillpack/`、`launchd/`
- `plans/gd/2026-06-12-content-quality-loop/`（旧计划只读）
- vault 内容
- `~/.claude/`、`~/.codex/`

---

## 6. Step 详细任务包

### Step-1: 数据契约与身份契约

```text
WHERE: src/akb2/quality/intake/models.py（新建）; src/akb2/quality/intake/extract.py:181（扩展 extraction_config 入参）; tests/quality/test_extraction_context.py（新建）
WHAT: 定义 ExtractionContext dataclass（≥8 字段）；article_id = sha256(canonical_url).hexdigest()[:12]；extract_content extraction_config 有 feed 元数据时 fallback（author/published 从 config 填补）；两者皆缺且 _validate_metadata 失败→不产 raw，落 fetch_fail 签名
WHY: F2 数据契约：当前 extract_content 无 context 入参，metadata 硬判定因缺 author/published 大量 fail-closed，但 discovery 拿到 feed 元数据可回填；F3：article_id 唯一化防文件名碰撞
VERIFY: python -m pytest tests/quality/test_extraction_context.py -q
```

Hard-stop: extract.py 改动导致 test_intake_extract.py 红 → 修复现有测试再进 step-2。

成功标准：SC-1（第 3 节）

### Step-2: 抽取三工具接真

```text
WHERE: src/akb2/quality/intake/extract.py（_extract_via_defuddle:123, _via_scrapling:140, _via_docling:159 + HTTP fetcher 入口）; tests/quality/test_intake_extract.py（TestExtractViaDefuddle/Scrapling/Docling 三个 class）
WHAT: defuddle→`defuddle parse <tmpfile> --json -m`；scrapling→`scrapling.Fetcher/StealthyFetcher`；docling→`DocumentConverter().convert()`；extract_content html is None 时 httpx 自动抓取；MIN_CONTENT_LENGTH 上调至 200；word_count 基于 markdown 正文；import gate 探测（importlib + log warning）；pyproject.toml 依赖修改归 step-6 所有，此处仅实现接口不修改 pyproject
WHY: 三方法全是占位 stub 返回模拟数据；无 HTTP fetcher 入口导致决策树分支 2/3 永远不进；P3 安装≠可导入需 import gate
VERIFY: python -m pytest tests/quality/test_intake_extract.py -q
```

成功标准：SC-2, SC-3, SC-4（第 3 节）

### Step-3: orchestrator CLI + intake state ledger

```text
WHERE: src/akb2/quality/intake/orchestrator.py（新建）; reports/intake/intake-state.jsonl（运行时产出）; tests/quality/test_intake_orchestrator.py（新建）
WHAT: 读 main_sources.yaml → discover_articles（listing 实现由 step-5 负责，此处只调用）→ extract_content（传 ExtractionContext）→ archive_from_extract_result；状态外置到 intake-state.jsonl（run_id/source_slug/article_id/last_fetch_at/daily_count/quarantine_status/status）；rate_limit 超限 skip；quarantine 命中 skip 不写 raw；CLI 入参 --dry-run/--limit/--sources-config/--state-file
WHY: F7 orchestrator 无状态 rate_limit/quarantine 失效；F3 article_id 需写 ledger 幂等
NOTE-dry-run-safety: discovery.py:344 listing 路径触发条件为 `elif listing_url and not feed_url`；config/main_sources.yaml 全部 76 源均有 feed_url（实测），故 listing 路径不触发。Step-5 完成前 dry-run 可独立验收；orchestrator 测试 mock discover_articles，与 listing 实现无关。
VERIFY: python -m pytest tests/quality/test_intake_orchestrator.py -q
```

成功标准：SC-5(full), SC-7, SC-8（第 3 节）

### Step-4: raw 归档契约补全

```text
WHERE: src/akb2/quality/intake/raw_archiver.py（archive_from_extract_result:408 扩展 source_metadata 参数; 文件名加 article_id）; tests/quality/test_intake_raw.py（补 details/sample_error 断言）
WHAT: raw 文件名 `<slug>--<article_id>--raw--<date>.md`（F3）；archive_from_extract_result 新增 source_metadata 参数（ExtractionContext）显式传 frontmatter；9 字段/§A-§Z/fail-closed 保留；failure 签名补 details/sample_error 字段（P3）
WHY: F3 文件名碰撞；F5 raw 归档契约进 DoD；P3 签名细节不足排障慢
VERIFY: python -m pytest tests/quality/test_intake_raw.py -q
```

成功标准：SC-5(partial), SC-6（第 3 节）

### Step-5: source_validator live + discovery listing

```text
WHERE: src/akb2/quality/intake/source_validator.py（_validate_source_live:67 接真）; src/akb2/quality/intake/discovery.py（listing 路径:343-348 接 scrapling）; tests/quality/test_source_validator.py（新建）
WHAT: source_validator live：真 trial-fetch 76 源；必填字段（feed/url/reachable/latest_post/trial_fetch）齐；以 run_id 原子写入 sources-ledger.jsonl（替换非追加）；失败记录保留；discovery listing 路径接 scrapling 列表页自适应（F6/P3 run_id 原子替换）
WHY: F6 live 是 skeleton；P3 jsonl 追加混旧记录；discovery listing 未实现
VERIFY: python -m pytest tests/quality/test_source_validator.py -q -m "not integration" && AKB2_LIVE_VALIDATE=1 PYTHONPATH=src python -m akb2.quality.intake.source_validator --live --out reports/source-validation/sources-ledger.jsonl && python -c "import json,yaml; cfg=yaml.safe_load(open('config/main_sources.yaml'))['sources']; c_urls={s.get('feed_url') or s.get('listing_url') for s in cfg}-{None,''}; rs=[json.loads(l) for l in open('reports/source-validation/sources-ledger.jsonl')]; assert len(rs)==76; u={r['url'] for r in rs}; assert u==c_urls; ok=sum(1 for r in rs if r.get('reachable')); assert ok>=70; run_ids={r.get('run_id') for r in rs}; assert len(run_ids)==1; print('PASS')"
```

成功标准：SC-9, SC-10（第 3 节）

### Step-6: 工程门 + import gate

```text
WHERE: pyproject.toml（scrapling/docling 补 dependencies）; src/akb2/quality/intake/__init__.py（import gate 探测）; docs/runbooks/（playwright 前置）
WHAT: pyproject 补依赖；`_check_tool_available(name)` 用 importlib 探测，不可用 log warning 不抛（P3）；playwright 浏览器安装写 runbook；全量 pytest + ruff 绿
WHY: P3 已安装≠当前 Python 可导入，需运行时探测
VERIFY: python -m pytest tests/quality/ tests/loops/ -q && ruff check src/akb2/quality/intake/ tests/quality/ && echo PASS
```

成功标准：SC-11（第 3 节）

---

## 7. 执行路径（plan mode 退出后）

1. 把本文件内容落成 `Project AKB2/plans/gd/2026-06-14-cql-intake/master-plan.md`（正式 GD 计划，本文件是蓝本）
2. 跑 `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-review2-plan-target.py" --target plans/gd/2026-06-14-cql-intake/master-plan.md` → PLAN_TEMPLATE_STATUS: pass
3. `/review2 plan plans/gd/2026-06-14-cql-intake/master-plan.md` 或 `/gd 审计划`（Codex 交叉审查）
4. APPROVED 后 `/gd 执行`（按 step-1~6 顺序）

---

## 风险汇总

| 严重度 | 风险 | 应对 |
|--------|------|------|
| P1 | extract_content API 扩展破坏现有 test_intake_extract.py 断言 | Step-1 结束后立刻跑 test_intake_extract.py；红则修复再进 step-2（hard-stop） |
| P1 | scrapling browser_mode 需 playwright 浏览器，Mac mini 未必有 | import gate + runbook 系统前置；browser 模式 fixture mock，不依赖真浏览器跑测试 |
| P2 | docling PDF 元数据常缺 author/published | ExtractionContext fallback 兜底；fallback 仍缺→ fail-closed 不写 raw（SC-1 覆盖） |
| P3 | scrapling/docling 已安装≠当前 Python env 可导入 | import gate 探测（Step-6）；测试 mock 三个 _extract_via_* 不依赖真 import |
| P3 | source_validator jsonl 追加混旧记录 | Step-5 改为 run_id 原子写入（replace，非 append） |
| P3 | fetch_fail/archive_fail 签名排障慢 | Step-4 签名补 details/sample_error（SC-6 覆盖） |

---

## 实测修订（2026-06-17）

> 权威细节见 `docs/loops-design-decisions.md` §12。

- **本计划已达目标**：抓取链路出货，intake archived 0→4（openai Cloudflare 文章入库，raw 9 字段 frontmatter + 锚点齐），不是当前阻塞点。
- **下游阻塞在 repair**（非本计划范围，本计划 §2 非目标已划出 admission/distill/grader/repair/promote）：黄金样张卡端到端跑通 7 段后卡在 repair 子循环空转（3 轮不收敛）。修复在 Plan 1（`2026-06-12-content-quality-loop`）step-4 + back-half 计划，C+ 重写 repair 为 agent 爬坡。
- intake 层无需改动；后续若 C+ 需要"黄金样本喂进 repair 输入"，读取的是 vault 既有样张文件，与本抓取链路解耦。
