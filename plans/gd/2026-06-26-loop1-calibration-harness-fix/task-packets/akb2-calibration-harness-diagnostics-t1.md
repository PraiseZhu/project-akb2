# Task Packet: akb2-calibration-harness-diagnostics-t1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md (v1.2.0)
TEMPLATE_KIND: gd-task-packet

> 自包含合约：子 agent 只读本 packet + `required_context` 列出的文件，不读其他对话内容。

---

## 1. 标识

```yaml
task_id: akb2-calibration-harness-diagnostics-t1
agent_role: implementer
parent_step: step-1-calibration-core
parent_plan: plans/gd/2026-06-26-loop1-calibration-harness-fix/step-1-calibration-core.md
created_at: 2026-06-26T07:06:31Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: 让 AKB2 循环1 能跑一次可信的满仓校准（宪法 v1.2.0 原则 3/4/6）
CHAIN_GOAL:   只修测试侧 harness，不动生产 admission/distill/checks_structural/grader/repair
PHASE_GOAL:   报告区分死链/元数据失败/抽取失败三类；3 篇元数据失败 URL 补 ExtractionContext 后 extraction_success False→True；活语料端到端薄切片流过 intake_raw
TASK_GOAL:    把 calibration.py 测试侧抓取改成镜像 orchestrator 的 ExtractionContext 构造 + 保留全部 ExtractResult 诊断字段 + 产出含 9 字段 frontmatter 的完整 raw_content，并新增直测，覆盖 SC-1/SC-2/SC-4/SC-6
```

---

## 3. 依赖与并发

```yaml
blocked_by:
  - t2b-preflight-corpus            # SC-1 端到端报告 + SC-2b 需扩展后的 corpus.yaml(per-entry feed 元数据) + fixtures/raw-html/<slug>.html；SC-2a/SC-4/SC-6/SC-1a 直测用构造 ctx/fixture 解耦
can_parallel_with: []               # 本 lane 单 packet
required_context:
  # 注：owned 的 calibration.py 为本 packet 自有，执行时直接读改，按标准 §6 不列入 required_context（避免与 owned_paths 重叠）
  - src/akb2/quality/intake/extract.py
  - src/akb2/quality/intake/orchestrator.py
  - src/akb2/quality/intake/raw_archiver.py     # 含 FRONTMATTER_FIELDS（9 字段，SC-6 import 断言锚，B3-7）
  - src/akb2/quality/intake/models.py
  - evals/main-pipeline-quality/corpus.yaml   # t2b 扩展后版本：只读 per-entry feed 元数据字段契约 + fixtures/ 文件命名契约 + fixtures/raw-html/<slug>.html(SC-2b 注入源，B3-6)
  - docs/constitution.md
```

---

## 4. 路径权限

```yaml
owned_paths:
  - src/akb2/quality/calibration.py
  - tests/test_calibration_harness_diagnostics.py
forbidden_paths:
  - 旧 /rev artifacts
  - "/Users/praise/.claude/**"
  - src/akb2/quality/intake/extract.py
  - src/akb2/quality/intake/preflight.py
  - evals/main-pipeline-quality/
  - config/main_sources.yaml
  - src/akb2/quality/admission.py
  - src/akb2/quality/distill.py
  - src/akb2/quality/checks_structural.py
  - src/akb2/quality/grader.py
  - src/akb2/quality/repair.py
```

读写分层：写入仅 `calibration.py` + `tests/test_calibration_harness_diagnostics.py`；读取仅 `required_context` + 公共只读资源 + 运行时 t2b preflight 报告（数据输入，非源码写入）。`corpus.yaml` 仅供读取字段契约（t2b 拥有写入）。

---

## 5. 成功标准（SC）

- [ ] SC-1（诊断 + 分层评分 + factory live 下限，B3-1/B3-2）：报告 `entries[]` 每条含**完整诊断键集** `{failure_class, extract_method, word_count, metadata_complete, missing_metadata, extract_error, source}`、`source ∈ {fixture, live}`；在 base result dict 初始化、全 6 条返回路径（成功/死链早退/抽取失败/fixture/负样本/ImportError）都携带（防 all() KeyError，round-2 P1）；报告顶层产出 `positive_live_total/positive_live_passed/positive_live_pass_rate/positive_fixture_*/live_positive_intake_raw_failures`；`factory_acceptance` AND live 层下限（`positive_live_total>=LIVE_POSITIVE_QUOTA=3` + `live_positive_intake_raw_failures==0` + `positive_live_pass_rate>=LIVE_POSITIVE_PASS_FLOOR=0.9`，fixture 不补偿）；**先重新生成报告再断言、且 ≥3 条 `source=='live'` 正样本越过 intake_raw**（防 fixture 灌绿，round-2 P2 + B3-1/B3-2）
- [ ] SC-1a（fixture 加载，M1）：`_load_fixture_content` 改按 `fixture:<name>` 从 `evals/main-pipeline-quality/fixtures/<name>.md` 读取（非硬编码 google-io）；非 google-io fixture entry 返回非空且不卡 intake_raw
- [ ] SC-2a（ctx 回填机制无条件 mock，B3-6）：同一缺 author/published 的 ExtractResult ± ctx 跑两次，断言不传 ctx False、传 ctx 翻 True；不走 fixture 路径、不硬编码非空 ctx（round-2 P2）
- [ ] SC-2b（3 具名 URL 可复现，B3-6）：crewai/aider/MCP 保持 live（真实 feed_*），各读 t2b 落地的 `evals/main-pipeline-quality/fixtures/raw-html/<slug>.html` 注入 extract_content 抽取器底层、走真实 extract_content + 真实 feed_* + 真实 `_apply_feed_fallback`，断言每条 extraction_success False→True（恢复「3 篇 False→True」具名可复现，非 generic mock）
- [ ] SC-4：活语料端到端薄切片 ≥1 正样本 `failure_segment != "intake_raw"`
- [ ] SC-6（完整 9 字段，B3-7）：传给 `_run_pipeline_segments` 的 `raw_content` frontmatter 含 `raw_archiver.FRONTMATTER_FIELDS` 全部 9 字段（import 同一常量断言 superset，非硬编码 4 字段子集）

---

## 6. 交付物

```yaml
deliverables:
  - path: src/akb2/quality/calibration.py
    kind: file
    must_exist: true
    description: 测试侧抓取镜像 orchestrator 构造 ExtractionContext + 保留 ExtractResult 诊断字段 + 三分类（base result dict 初始化全 7 诊断键 + source 字段、全 6 路径携带防 all() KeyError）+ 9 字段 raw frontmatter（取 raw_archiver.FRONTMATTER_FIELDS）+ _load_fixture_content 改按名读 fixtures/ 目录（M1）+ **_compute_statistics live/fixture 分层计数与率 + factory_acceptance AND live 层下限（LIVE_POSITIVE_QUOTA/LIVE_POSITIVE_PASS_FLOOR/live_positive_intake_raw_failures==0，B3-1）**
  - path: tests/test_calibration_harness_diagnostics.py
    kind: file
    must_exist: true
    description: extraction_context(SC-2a) / extraction_context_named_urls(SC-2b 参数化 crewai/aider/mcp 注入保存 HTML) / fixture_load(SC-1a) / e2e_thin_slice(SC-4) / raw_frontmatter(SC-6 import FRONTMATTER_FIELDS) / failure_class(SC-1 三分类) / stratified(SC-1 分层率不被 fixture 灌绿 + factory live 全失败为 False) 直测，断言绑可观测行为
```

---

## 7. 验证（Anti-fill 硬约束）

```yaml
verify:
  - sc_ref: SC-1
    method: assertion
    cmd: "PYTHONPATH=src python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json && python3 -c \"import json;d=json.load(open('reports/calibration/factory.json'));E=d['entries'];KEYS={'failure_class','extract_method','word_count','metadata_complete','missing_metadata','extract_error','source'};assert all(KEYS<=set(e) for e in E);assert all(e['source'] in ('fixture','live') for e in E);assert sum(1 for e in E if e.get('expected')=='ingest' and e.get('source')=='live' and e.get('failure_segment')!='intake_raw')>=3;assert {'positive_live_total','positive_live_pass_rate','positive_fixture_pass_rate','live_positive_intake_raw_failures'}<=set(d);assert d['live_positive_intake_raw_failures']==0\""
    expect: "退出码 0（前提：网络+DeepSeek/GPT key；全 7 键 + source∈{fixture,live} + ≥3 live(source=='live') ingest 越过 intake_raw + 分层字段在场 + live 无 intake_raw 卡死，防 fixture 灌绿 + 防读陈旧文件假过 + 防混池掩盖 live）"
  - sc_ref: SC-1a
    method: test
    cmd: "pytest tests/test_calibration_harness_diagnostics.py -k fixture_load -q"
    expect: "passed"
  - sc_ref: SC-2a
    method: test
    cmd: "pytest tests/test_calibration_harness_diagnostics.py -k extraction_context -q"
    expect: "passed（无条件 mock ExtractResult ± ctx False→True 对照）"
  - sc_ref: SC-2b
    method: test
    cmd: "pytest tests/test_calibration_harness_diagnostics.py -k extraction_context_named_urls -q"
    expect: "passed（crewai/aider/mcp 各 case 注入保存 HTML 走真实路径 False→True）"
  - sc_ref: SC-4
    method: test
    cmd: "pytest tests/test_calibration_harness_diagnostics.py -k e2e_thin_slice -q"
    expect: "passed"
  - sc_ref: SC-6
    method: test
    cmd: "pytest tests/test_calibration_harness_diagnostics.py -k raw_frontmatter -q"
    expect: "passed（测试 import raw_archiver.FRONTMATTER_FIELDS 断言 set(keys)>=set(FRONTMATTER_FIELDS) 全 9 字段）"
```

---

## 8. Handoff 输出

```yaml
handoff_output:
  result_path: plans/gd/2026-06-26-loop1-calibration-harness-fix/results/t1-calibration-core-result.md
  status_field: <见 gd-execution-status.schema.json>
  summary: calibration harness 镜像生产抽取契约 + 诊断三分类 + 9 字段 raw frontmatter + live/fixture 分层评分/factory live 下限，SC-1/1a/2a/2b/4/6 verify 通过
  blockers: 若 t2b corpus 未扩展或 fixtures/raw-html/ 未落地导致 SC-1 端到端报告/SC-2b 无法跑，记录为 blocker（SC-1a/SC-2a/SC-4/SC-6 直测可独立通过）
```

---

## 9. 范围禁令

- 禁止写入 `extract.py`/`preflight.py`/`corpus.yaml`/`config/main_sources.yaml`/包 A 任一文件。
- 禁止读取未完成 task 的 owned_paths（仅可读 t2b 已落地的 corpus.yaml 字段契约 + `fixtures/raw-html/<slug>.html`(SC-2b 注入) + 运行时 preflight 报告）。
- 禁止访问 `/Users/praise/.claude/**`；禁止启动 daemon/注册 hook/改 cron；禁止用对话上下文替代 `required_context`。
