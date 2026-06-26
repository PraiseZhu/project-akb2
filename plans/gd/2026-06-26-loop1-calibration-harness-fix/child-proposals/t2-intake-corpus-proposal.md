# Child Proposal — t2-intake-corpus

> child_planner 子 agent 输出（dispatch_id=akb2-loop1-calibration-harness-fix, track=t2-intake-corpus）。
> 本文件由主 agent 从 child planner 最终消息原样落盘，供 `gd-validate-child-proposal.py` 校验与主 agent 合并。

## 第 1 部分：step plan 草稿

```text
# Step T2: AKB2 满仓校准「抽取与语料」rig 修复 v1
GD_STANDARD: Project GD/prompts/gd-review-standard.md
TEMPLATE_KIND: gd-step-plan
```

TASK_GOAL：(a) extract.py Scrapling fallback API 修复；(b) corpus 死链替换 +「固定 fixture/snapshot + live URL health」双层 + corpus↔main_sources 对齐；(c) preflight guard 产 http 层分类报告；(d) corpus per-entry feed 元数据扩展供 t1 构造 ExtractionContext。覆盖 SC-3/SC-5/SC-7。

**实测确认（非假设）**：scrapling 0.4.8 — `Fetcher.get(url)`（classmethod, HTTP）、`StealthyFetcher.fetch(url)`（classmethod, 浏览器）均存在；`Fetcher` **无** `.fetch`（只有 `.get`），故现报 `'Fetcher' object has no attribute 'fetch'`。Response 含 `html_content`/`body`。→ 纯 call-site 修复，**无需 pin 版本**。

实现步骤（WHERE/WHAT/WHY/VERIFY）：

- **Step.1（SC-5）** WHERE: `extract.py:241-249` `_extract_via_scrapling`。WHAT: `Fetcher().fetch(url)`→`Fetcher.get(url)`；`StealthyFetcher().fetch(url)`→`StealthyFetcher.fetch(url)`（去实例化消 deprecation）；保留 :252 `getattr(page,"html_content",..) or str(page.body or "")` 与 :273 `except Exception` 返回空 dict 语义。WHY: Fetcher 无 .fetch → 非浏览器分支恒抛异常被吞成空 dict → 抽取静默失败。VERIFY: `pytest tests/test_extract_scrapling_fallback.py -q`。
- **Step.2（SC-5）** WHERE: `tests/test_extract_scrapling_fallback.py`（新建）。WHAT: monkeypatch scrapling 禁网络，3 类直测：(a) adaptive 成功断言走 `Fetcher.get`；(b) browser 路径断言走 `StealthyFetcher.fetch`；(c) 抛错→ `_extract_via_scrapling` 返回 {}、extract_content 不崩、fail-closed（method==FAILED、给 failures_path 时落签名）。WHY: 断言绑可观测行为 + 焊死正确方法名（未来改回 .fetch 会红）。VERIFY: 同上。
- **Step.3（SC-7 / SC-3 前置）** WHERE: `evals/main-pipeline-quality/corpus.yaml` + 新建 `evals/main-pipeline-quality/fixtures/`。WHAT: (1) 死链替换：按 preflight 报告判定后替换为稳定 canonical URL，每条加 replaced_reason；(2) 双层：黄金样张源 + 1 概念正样本 + 2 负样本落本地快照（fixtures/，corpus 用 `fixture:` 引用，沿用 calibration `_load_fixture_content`），其余 live 走 preflight；(3) per-entry feed 元数据扩展：每条增 feed_title/feed_author/feed_published/source_publisher/source_slug（字段名对齐 models.py ExtractionContext）。WHY: 上次 0% 主因 ~13 死链清零质量信号；双层把确定性与探活解耦；per-entry 元数据是 t1 构造 ctx 唯一数据源。VERIFY: yaml 断言含 fixture 层 + 正样本 feed_title 存在。
- **Step.4（SC-3）** WHERE: `src/akb2/quality/intake/preflight.py`（新建模块 + `__main__` CLI）。WHAT: 读 corpus，对非 fixture URL 发 HTTP（follow_redirects），产 per-entry http_status/final_url/content_type/redirect/canonical_drift/short_body/missing_metadata + 汇总 dead_links/redirects 计数；CLI `--corpus --out` 写 JSON exit 0（诊断非门，不阻断）。WHY: 把死链/元数据失败/抽取失败三类分开，区分「语料过期」vs「抽取器坏」vs「源元数据不全」。VERIFY: `python3 -m akb2.quality.intake.preflight --corpus evals/main-pipeline-quality/corpus.yaml --out reports/calibration/preflight.json` + 字段断言。
- **Step.5（SC-7）** WHERE: `tests/test_corpus_preflight.py`（新建，含 alignment）。WHAT: alignment 断言正样本 domain 命中 main_sources 某 source domain，不命中须带 off_corpus_rationale；双层存在断言 ≥1 fixture entry 且快照文件存在；per-entry 元数据断言。WHY: SC-7 corpus↔main_sources 对齐固化为可回归断言。VERIFY: `pytest tests/test_corpus_preflight.py -k alignment -q`。

**死链替换策略/判定准则**：(1) 死链=preflight http_status>=400 或 canonical_drift 或 short_body；github blob/main/docs 形态优先怀疑。(2) 选源优先级：官方文档站 canonical 页 > GitHub blob；域名在 main_sources 已登记优先（满足 SC-7 对齐）；带 RSS/Atom feed 的源优先（贴近生产链路）；feed 元数据可从 main_sources 推导填入。(3) 领域配比守恒，同领域内替换。(4) 负样本 x.com/fixture 语义不动。(5) 无法对齐项加 off_corpus_rationale 文档化豁免。

非目标：不改包 A；不改 calibration.py（t1 owned，只供 corpus 字段 + preflight 报告）；不接 DAG wave；不 pip install/pin scrapling；不为概念源放宽质量门。

## 第 2 部分：候选 task packet 列表

- **task_id: t2a-scrapling-fix** | implementer | owned_paths: `src/akb2/quality/intake/extract.py`, `tests/test_extract_scrapling_fallback.py` | blocked_by: [] | can_parallel_with: t2b-preflight-corpus | required_context: extract.py / models.py | SC-5 | 范围禁令：禁写 calibration.py/包A/evals/main_sources；保留 except 兜底 + fail-closed 回落。
- **task_id: t2b-preflight-corpus** | implementer | owned_paths: `src/akb2/quality/intake/preflight.py`, `evals/main-pipeline-quality/`, `config/main_sources.yaml`, `tests/test_corpus_preflight.py` | blocked_by: [] | can_parallel_with: t2a-scrapling-fix | required_context: corpus.yaml / main_sources.yaml / models.py / extract.py / calibration.py | SC-3, SC-7 | **跨 lane 契约**：在 corpus.yaml 每条正样本扩展 feed_title/feed_author/feed_published/source_publisher/source_slug（对齐 models.py ExtractionContext）；t1「校准侧构造 ExtractionContext」step 在 execute DAG `blocked_by` 本 packet。preflight HTTP 层字段供 t1 报告 step 读取；ExtractResult method/word_count/metadata_complete/error 由 t1 报告侧派生不重复。范围禁令：禁写 calibration.py/extract.py(t2a)/包A；preflight 不得 fail-closed 阻断校准。

## 第 3 部分：机器可读 proposal block

<!-- gd-child-plan-proposal-json:start -->
```json
{
  "proposal_id": "akb2-loop1-calibration-harness-fix-t2-intake-corpus-cp01",
  "parent_dispatch_id": "akb2-loop1-calibration-harness-fix",
  "parent_track_id": "t2-intake-corpus",
  "agent_role": "child_planner",
  "output_status": "completed",
  "summary_cn": "实测确认 scrapling 0.4.8 Fetcher 只有 classmethod get 无 fetch,故纯 call-site 修 API 不 pin;再建 preflight 模块产 HTTP 层死链分类报告,corpus 死链替换+fixture 双层+per-entry feed 元数据(t1 下游构造 ExtractionContext 依赖)。",
  "task_packets": [
    {
      "task_id": "t2a-scrapling-fix",
      "owned_paths": ["src/akb2/quality/intake/extract.py", "tests/test_extract_scrapling_fallback.py"],
      "required_context": ["src/akb2/quality/intake/extract.py", "src/akb2/quality/intake/models.py"]
    },
    {
      "task_id": "t2b-preflight-corpus",
      "owned_paths": ["src/akb2/quality/intake/preflight.py", "evals/main-pipeline-quality/", "config/main_sources.yaml", "tests/test_corpus_preflight.py"],
      "required_context": ["evals/main-pipeline-quality/corpus.yaml", "config/main_sources.yaml", "src/akb2/quality/intake/models.py", "src/akb2/quality/intake/extract.py", "src/akb2/quality/calibration.py"]
    }
  ],
  "sc_refs": ["SC-3", "SC-5", "SC-7"],
  "verify": [
    {"sc_ref": "SC-3", "method": "command", "cmd": "python3 -m akb2.quality.intake.preflight --corpus evals/main-pipeline-quality/corpus.yaml --out reports/calibration/preflight.json"},
    {"sc_ref": "SC-5", "method": "test", "cmd": "pytest tests/test_extract_scrapling_fallback.py -q"},
    {"sc_ref": "SC-7", "method": "test", "cmd": "pytest tests/test_corpus_preflight.py -k alignment -q"}
  ],
  "blocked_reason": null
}
```
<!-- gd-child-plan-proposal-json:end -->
