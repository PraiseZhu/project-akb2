# Step 2: 抽取与语料 rig — Scrapling 修复 + corpus 双层 + preflight + 元数据扩展 v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md (v1.2.0)
TEMPLATE_KIND: gd-step-plan

日期：2026-06-26
状态：draft
负责人：Claude 执行；Codex 可选 cross-review
lane：t2-intake-corpus（来源 child proposal：child-proposals/t2-intake-corpus-proposal.md）

> 本 step plan 覆盖 master plan 的 **Step 2（Scrapling 修复）** 与 **Step 3（corpus 双层 + preflight + 元数据扩展）** 两个逻辑 step，对应两个 task packet（t2a-scrapling-fix / t2b-preflight-corpus），可并行（owned_paths 不重叠）。

---

## 1. 目标链（继承 + 当前 task goal）

```text
PROJECT_GOAL: 让 AKB2 循环1 能跑一次可信的满仓校准（宪法 v1.2.0 原则 3/4/6）
CHAIN_GOAL:   只修测试侧 harness，报告能区分死链/元数据失败/抽取失败三类
PHASE_GOAL:   corpus 全 URL 经 preflight 标出死链/重定向/canonical 漂移/正文过短/元数据缺失；corpus↔main_sources 对齐
TASK_GOAL:    (a) extract.py Scrapling fallback API 修复（scrapling 0.4.8 正确 API）；(b) corpus 死链替换 + fixture/snapshot 双层 + per-entry feed 元数据扩展（供 step 1 构造 ExtractionContext）；(c) preflight guard 产 HTTP 层分类报告。覆盖 SC-3/SC-5/SC-7
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：`Scrapling 修复不回归 docling/签名兜底; 测试 rig 双层确定性 vs 探活解耦; corpus↔main_sources 对齐可核对; preflight 分类字段齐全; per-entry feed 元数据契约对 step 1 可消费`
- Domain-specific notes：只动 extract.py 的 Scrapling 调用点 + 新建 preflight + 语料；Scrapling 修复必须保证 fallback 失败仍走 docling/签名（原则 3 fail-closed 不回归）。

---

## 3. 前置条件

- blocked_by：`—`（本 lane 是 step 1 下游契约的提供方，自身不被阻塞）
- 必须 baseline / artifact：`extract.py`、`corpus.yaml`、`main_sources.yaml`、`models.py`（ExtractionContext 契约）
- Hard-stop：若 scrapling 实测 API 与计划不符（已实测：`Fetcher.get` / `StealthyFetcher.fetch` 均 classmethod）→ 停止改 pin 方案

---

## 4. 成功标准（SC，本 step 内）

- [ ] SC-3（preflight 分类 + bad-list 闭合断言 + soft-404，B3-3）：preflight 对 corpus 全 URL 产 per-entry HTTP 层分类报告（`http_status/final_url/content_type/redirect/canonical_drift/short_body/soft_404/missing_metadata` + 回显 `expected/source`）+ 汇总，CLI exit 0；**且修复后 expected=ingest 的非 fixture entry 中 `http_status>=400 或 short_body 或 soft_404` 计数==0**（死链 + soft-404 + 正文过短一并清零）；`soft_404` 由 title/body not-found pattern（`404`/`not found`/`page not found`/`no longer available`，大小写不敏感）**或** canonical/final_url 域漂移派生（不只靠 http_status>=400+short_body）；`canonical_drift` 要么 ==0、要么每条具名登记且不计 live pass
  - verify(command+assertion): `PYTHONPATH=src python3 -m akb2.quality.intake.preflight --corpus evals/main-pipeline-quality/corpus.yaml --out reports/calibration/preflight.json && python3 -c "import json;r=json.load(open('reports/calibration/preflight.json'));E=r['entries'];assert all({'http_status','final_url','content_type','redirect','canonical_drift','short_body','soft_404','missing_metadata'}<=set(e) for e in E),'missing fields';bad=[e for e in E if e.get('expected')=='ingest' and e.get('source')!='fixture' and ((e.get('http_status') or 0)>=400 or e.get('short_body') or e.get('soft_404'))];assert not bad, bad"`
  - expect: exit 0；entries[] 含 8 个分类字段 + 回显 expected/source；ingest live 集 `http_status>=400 / short_body / soft_404` 均==0（canonical_drift 若存在须已解决或具名登记）
- [ ] SC-5：Scrapling fallback 用正确 API，抛错时 fail-closed 回落
  - verify(test): `pytest tests/test_extract_scrapling_fallback.py -q`
  - expect: passed
- [ ] SC-7（双层 + 对齐 + 9 领域配比 + live 层代表性，cross-review M3/M5 + B3-5）：corpus 双层存在 + corpus↔main_sources 对齐（最低配额 + 具名 proxy）+ 9 领域配比守恒 + **live 层代表性**
  - verify(test): `pytest tests/test_corpus_preflight.py -k alignment -q`
  - expect: passed（live 正样本 aligned_count≥配额且≥2 真实命中；每个 off_corpus_rationale 具名一个存在的 main_sources slug；确定性锚/无源页走 fixture: 不计 live 对齐；替换后 9 领域计数==基线；**新增 B3-5：每个非豁免领域 `expected=ingest 且 source=live` 计数≥1；某领域全 fixture 必须在 corpus `fixture_only_domains` allowlist 具名且不计 live health**）

---

## 5. 非目标

- 不改包 A；不改 calibration.py（step 1 owned，只供 corpus 字段 + preflight 报告）。
- 不 pip install / 升级 / pin scrapling（实测 0.4.8 可用）。
- 不为概念源放宽质量门。

---

## 6. 实现步骤

```text
Step.1 (SC-5) WHERE: extract.py:241-249 _extract_via_scrapling
  WHAT: Fetcher().fetch(url)→Fetcher.get(url)；StealthyFetcher().fetch(url)→StealthyFetcher.fetch(url)（去实例化消 deprecation）；保留 :252 getattr(page,"html_content",..) or str(page.body or "") 与 :273 except Exception 返回空 dict 语义
  WHY:  Fetcher 无 .fetch → 非浏览器分支恒抛异常被吞成空 dict → 抽取静默失败（1 篇抽取失败根因）
  VERIFY: pytest tests/test_extract_scrapling_fallback.py -q → passed

Step.2 (SC-5) WHERE: tests/test_extract_scrapling_fallback.py（新建）
  WHAT: monkeypatch scrapling 禁网络，3 类直测：(a) adaptive 成功断言走 Fetcher.get；(b) browser 路径断言走 StealthyFetcher.fetch；(c) 抛错→返回 {}→extract_content 不崩→fail-closed（method==FAILED、给 failures_path 时落签名）
  WHY:  断言绑可观测行为 + 焊死正确方法名（未来改回 .fetch 会红）
  VERIFY: pytest tests/test_extract_scrapling_fallback.py -q → passed

Step.3 (SC-7 / SC-3 前置 / SC-2b 前置) WHERE: evals/main-pipeline-quality/corpus.yaml + 新建 fixtures/ + fixtures/raw-html/
  WHAT: (1) 死链替换：按 preflight 报告判定（http_status>=400 / canonical_drift / short_body / soft_404）替换为稳定 canonical URL，每条加 replaced_reason；替换后 9 领域计数守恒（M5）；(2) 双层：黄金样张源 + 1 概念正样本 + 2 负样本落本地快照 `fixtures/<name>.md`，corpus 用 `fixture:<name>` 引用（step-1 改 `_load_fixture_content` 按名读该目录，见 step-1 SC-1a，**非沿用旧硬编码**）；其余 live 走 preflight。**live 正样本须保留 ≥3 条真实可达（非 fixture）以满足 SC-1 live 地板（round-2 P2，防 fixture 灌绿）；fixture 文件名精确枚举（黄金样张 / 概念正样本 / 2 负样本 / 无源页各一），t1 loader 按 `fixture:<name>`→`fixtures/<name>.md` 精确读、不依赖可选内联兜底（round-2 P3）。** (3) **crewai/aider/MCP 保持 live（B3-6，反转 round-2 误退 fixture）**：corpus 这三条填真实 `feed_*`（publisher/author/published 从源页/main_sources 推导），并在 `fixtures/raw-html/<slug>.html` 保存这三页真实 HTML（捕获时正文在、author/published 缺，metadata_complete=False），供 step-1 SC-2b 注入；这三页可达正文足 → 满足 SC-3 ingest live 清零、计入 SC-1 live；execute 期 re-probe 若某页实死链 → 降级该页 `fixture:` 且 SC-2b 该 case 退化登记（round-2 P3 contingency）；(4) per-entry feed 元数据扩展：每条增 feed_title/feed_author/feed_published/source_publisher/source_slug（对齐 models.py ExtractionContext）；(5) **`fixture_only_domains` allowlist（B3-5）**：corpus 顶层声明哪些领域允许全 fixture（无 live 源/确定性锚领域），SC-7 据此豁免 live 层代表性断言、且这些领域不计 live health
  WHY:  上次 0% 主因 ~13 死链清零质量信号；双层把确定性与探活解耦；per-entry 元数据是 step 1 构造 ctx 唯一数据源；raw-html 是 SC-2b 具名 URL 复现的注入源；fixture_only_domains 防「9 领域配比守恒但 live 只剩 2-3 领域」（B3-5）
  VERIFY: python3 -c "import yaml; d=yaml.safe_load(open('evals/main-pipeline-quality/corpus.yaml')); e=d['entries']; assert any(x['url'].startswith('fixture:') for x in e); assert all(('feed_title' in x or x['url'].startswith('fixture:') or x.get('expected')=='reject') for x in e if x.get('expected')=='ingest'); assert 'fixture_only_domains' in d; import os; assert all(os.path.exists(f'evals/main-pipeline-quality/fixtures/raw-html/{s}.html') for s in ('crewai','aider','mcp')); print('PASS')"

Step.4 (SC-3) WHERE: src/akb2/quality/intake/preflight.py（新建模块 + __main__ CLI）
  WHAT: 读 corpus，对非 fixture URL 发 HTTP（follow_redirects，复用 extract.py UA），产 per-entry http_status/final_url/content_type/redirect/canonical_drift/**soft_404**/short_body/missing_metadata + 汇总 dead_links/redirects/soft_404 计数；fixture: 跳过网络标 source=fixture；**每条 entry 回显 corpus 的 expected 字段 + source(fixture/live)**（供 SC-3 ingest 死链清零断言）；**`soft_404` 派生（B3-3）**：(a) title/body 命中 not-found pattern（`404`/`not found`/`page not found`/`no longer available`，re.IGNORECASE）→ True；或 (b) `final_url` 域 ≠ 请求域 / canonical_drift 指向异域 → True；CLI --corpus --out 写 JSON exit 0（诊断非门，不 fail-closed 阻断）
  WHY:  HTTP 层探活归本 lane；上次报告把三类混成一个 fetch_failed 无法区分，致误判 0% 是管线坏；**soft-404（200 OK 但实为「页面已删」占位页）只靠 http_status>=400+short_body 抓不到（B3-3）**，须加 pattern/域漂移判定
  VERIFY: PYTHONPATH=src python3 -m akb2.quality.intake.preflight --corpus evals/main-pipeline-quality/corpus.yaml --out reports/calibration/preflight.json && python3 -c "import json; r=json.load(open('reports/calibration/preflight.json')); assert all(k in r['entries'][0] for k in ('http_status','final_url','content_type','redirect','canonical_drift','soft_404','short_body','missing_metadata')); print('PASS')"

Step.5 (SC-7) WHERE: tests/test_corpus_preflight.py（新建，含 alignment）
  WHAT: alignment 断言——live 正样本 aligned_count≥配额(≥2 真实命中 main_sources domain)，不命中的 live 项 off_corpus_rationale 必须具名一个真实存在的 main_sources slug（非裸文本）；确定性锚/无源页走 fixture: 不计 live 对齐；9 领域配比守恒断言（替换后各领域计数==基线，M5）；双层存在断言 ≥1 fixture entry 且快照文件存在；per-entry 元数据断言；**live 层代表性断言（B3-5）**：按 domain 统计 `expected=ingest 且 source 非 fixture`(live) 计数，断言**每个不在 `fixture_only_domains` allowlist 的领域 live 计数≥1**；`fixture_only_domains` 内每个领域必须真实存在于 corpus 且确为全 fixture（具名豁免、不计 live health）；读 main_sources 仅做对齐核对（只读、不写，B3-4）
  WHY:  SC-7 corpus↔main_sources 对齐固化为可回归断言；生产从 main_sources 拉活 RSS，corpus 脱节则测不到真实链路；**9 领域配比守恒 ≠ live 覆盖守恒——大批转 fixture 后比例还在但 live 可能只剩 2-3 领域（B3-5），故须按 domain 守 live 覆盖**
  VERIFY: pytest tests/test_corpus_preflight.py -k alignment -q → passed
```

### 死链替换策略与候选 canonical URL 判定准则

1. 判定：preflight `http_status>=400` 或 `canonical_drift` 或 `short_body` 或 **`soft_404`**（B3-3）即标替换候选；`github.com/.../blob/main/docs/*.md` 形态优先怀疑（仓库重构/分支改名 404 / soft-404）。
2. 选源优先级：官方文档站 canonical 页 > GitHub raw/blob；域名在 main_sources 已登记优先（满足 SC-7 对齐）；带 RSS/Atom feed 的源优先（贴近生产链路）；替换后 feed 元数据从 main_sources 推导填入。
3. 领域配比守恒（AI Agent 4 / Loop Eng 3 / …），同领域内替换；SC-7 测试断言替换后 9 领域计数 == 基线（M5，防部分替换缩小语料代表性）。
4. 负样本 `x.com/...`（洗稿型）、`fixture:google-io-2026-news`（新闻型）语义不动。
5. 无法对齐 main_sources 的正样本（cross-review M3）：(a) **确定性质量锚 / 真正无源且不可达页** → 改 `fixture:` 快照承载，不计入 live 对齐；其所属领域若因此全 fixture，须进 `fixture_only_domains` allowlist 具名豁免（B3-5）；(b) **crewai/aider/MCP 例外（B3-6 反转 round-2）**：这三页可达、正文足、仅元数据缺，**保持 live** + 真实 `feed_*` + 加 `off_corpus_rationale` 具名 main_sources slug 作代理；其 raw HTML 另存 `fixtures/raw-html/<slug>.html` 供 SC-2b 注入（live 与 raw-html 不矛盾：corpus 走 live、测试注入走保存 HTML）；(c) 其余 live 项加 `off_corpus_rationale`，**且必须具名一个真实存在的 main_sources slug 作为代理对象（非裸文本）**；(d) SC-7 设最低对齐配额（aligned_count ≥ 实际可对齐的 live 正样本数，且 ≥2 真实命中）。**决策（lead 2026-06-26）：不为满足测试扩 production `main_sources.yaml`（改变生产实际拉取源，超出"只修测试 rig"边界）；无源域名一律走 fixture: 或具名 proxy。** 如需改为扩 main_sources，由用户显式决定。

---

## 7. Task Packet 拆分

| task_id | agent_role | owned_paths | blocked_by | can_parallel_with |
|---------|-----------|------------|-----------|-------------------|
| t2a-scrapling-fix | implementer | `src/akb2/quality/intake/extract.py`, `tests/test_extract_scrapling_fallback.py` | — | t2b-preflight-corpus |
| t2b-preflight-corpus | implementer | `src/akb2/quality/intake/preflight.py`, `evals/main-pipeline-quality/`, `tests/test_corpus_preflight.py`（`config/main_sources.yaml` 改 required_context 只读，round-2 P2） | — | t2a-scrapling-fix |

> 详见 `task-packets/t2a-scrapling-fix.md`、`task-packets/t2b-preflight-corpus.md`。

---

## 8. 边界（修改 / 不修改）

修改：`extract.py`（仅 Scrapling 调用点）、`preflight.py`（新建）、`evals/main-pipeline-quality/`、`tests/test_extract_scrapling_fallback.py`、`tests/test_corpus_preflight.py`
不修改：`config/main_sources.yaml`（**只读**：alignment/推导只读不写，不扩 production 源，round-2 P2）、`calibration.py`（step 1）、包 A、`models.py`（ExtractionContext 契约只读）、`/Users/praise/.claude/**`

---

## 9. 风险与防护

| 风险 | 防护 |
|------|------|
| Scrapling 改 API 后浏览器分支回归 | Step.2(b) 断言 StealthyFetcher.fetch 被调用；(c) 抛错回落测试焊死 fail-closed |
| 死链替换误删领域配比 | 准则 3 同领域替换 + SC-7 alignment 守 domain↔main_sources |
| per-entry 元数据字段名与 ExtractionContext 不一致 | 字段名严格对齐 models.py；Step.5 断言字段存在 |
| preflight 真发网络在 CI 不稳定 | preflight 是 CLI 诊断（SC-3 命令），不进 pytest 默认集；test_corpus_preflight 走 mock/本地解析不发网络 |
| fixture 快照与生产正文漂移 | 快照仅用于质量回归确定性，live 探活单独走 preflight，双层物理分离 |

---

## 10. 测试计划

```bash
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2"
pytest tests/test_extract_scrapling_fallback.py -q
pytest tests/test_corpus_preflight.py -k alignment -q
# SC-3：preflight + bad-list 闭合断言（ingest live 死链/soft-404/short_body 清零）
PYTHONPATH=src python3 -m akb2.quality.intake.preflight --corpus evals/main-pipeline-quality/corpus.yaml --out reports/calibration/preflight.json && \
python3 -c "import json;r=json.load(open('reports/calibration/preflight.json'));E=r['entries'];assert all({'http_status','final_url','content_type','redirect','canonical_drift','soft_404','short_body','missing_metadata'}<=set(e) for e in E);bad=[e for e in E if e.get('expected')=='ingest' and e.get('source')!='fixture' and ((e.get('http_status') or 0)>=400 or e.get('short_body') or e.get('soft_404'))];assert not bad, bad"
pytest tests/ -k extract -q
ruff check src/akb2/quality/intake/preflight.py src/akb2/quality/intake/extract.py
```

---

## 11. Assumptions

- 实测确认 scrapling 0.4.8：`Fetcher.get` / `StealthyFetcher.fetch` 均 classmethod，Response 含 `html_content`/`body`，`Fetcher` 无 `.fetch` → SC-5 纯 call-site 修复。
- calibration.py 有 `fixture:` 前缀分发，但 `_load_fixture_content` 现状仅认 google-io——step-1(t1) 负责改它按名读 `fixtures/` 目录（M1，cross-review）；本 lane(t2b) 只负责落 `fixtures/<name>.md` 文件 + corpus `fixture:<name>` 引用 + replaced_reason / feed 元数据字段，不改 calibration.py。
- 本 lane(t2b) 另落 `fixtures/raw-html/<slug>.html`（crewai/aider/mcp 三页真实 HTML，捕获时正文在、author/published 缺）供 step-1 SC-2b 注入（B3-6）；这与 `fixtures/<name>.md` 用途不同（前者 SC-2b 注入、后者 `fixture:` 语料引用），不混用。
- 本 lane(t2b) 在 corpus 顶层声明 `fixture_only_domains` allowlist（B3-5），供 SC-7 live 层代表性断言豁免全 fixture 领域。
- step 1 负责把 corpus per-entry feed 元数据组装成 ExtractionContext；本 lane 只供字段。crewai/aider/MCP 保持 live（带真实 feed_*），不再 fixture: 承载（B3-6 反转 round-2 M4）。
