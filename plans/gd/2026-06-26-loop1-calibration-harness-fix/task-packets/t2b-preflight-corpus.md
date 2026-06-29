# Task Packet: t2b-preflight-corpus

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md (v1.2.0)
TEMPLATE_KIND: gd-task-packet

> 自包含合约：子 agent 只读本 packet + `required_context`，不读其他对话内容。

---

## 1. 标识

```yaml
task_id: t2b-preflight-corpus
agent_role: implementer
parent_step: step-2-intake-corpus
parent_plan: plans/gd/2026-06-26-loop1-calibration-harness-fix/step-2-intake-corpus.md
created_at: 2026-06-26T07:06:31Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: 让 AKB2 循环1 能跑一次可信的满仓校准（宪法 v1.2.0 原则 3/4/6）
CHAIN_GOAL:   只修测试侧 harness，报告能区分死链/元数据失败/抽取失败三类
PHASE_GOAL:   corpus 全 URL 经 preflight 标出死链/重定向/canonical 漂移/正文过短/元数据缺失；corpus↔main_sources 对齐
TASK_GOAL:    建 preflight 模块产 HTTP 层分类报告；corpus 死链替换 + fixture/snapshot 双层 + per-entry feed 元数据扩展（供 step 1 构造 ExtractionContext）+ alignment 测试
```

---

## 3. 依赖与并发

```yaml
blocked_by: []
can_parallel_with:
  - t2a-scrapling-fix
required_context:
  # 注：owned 的 evals/main-pipeline-quality/(corpus.yaml 等) 为本 packet 自有，按标准 §6 不列入
  - config/main_sources.yaml                  # round-2 P2：只读(alignment/推导)，已移出 owned，不写 production 源
  - src/akb2/quality/intake/models.py
  - src/akb2/quality/intake/extract.py        # t2a owned，跨 lane 只读 UA/抽取契约
  - src/akb2/quality/calibration.py           # t1 owned，跨 lane 只读 fixture: 分发与 _load_fixture_content 契约
```

---

## 4. 路径权限

```yaml
owned_paths:
  - src/akb2/quality/intake/preflight.py
  - evals/main-pipeline-quality/
  - tests/test_corpus_preflight.py
  # config/main_sources.yaml 已移出 owned（round-2 P2）：alignment/推导只读，不写 production 源；放 required_context，path-audit 不再把它当可写，杜绝漂移执行器为对齐而扩源
forbidden_paths:
  - 旧 /rev artifacts
  - "/Users/praise/.claude/**"
  - src/akb2/quality/calibration.py
  - src/akb2/quality/intake/extract.py
  - src/akb2/quality/admission.py
  - src/akb2/quality/distill.py
  - src/akb2/quality/checks_structural.py
  - src/akb2/quality/grader.py
  - src/akb2/quality/repair.py
```

---

## 5. 成功标准（SC）

- [ ] SC-3（preflight 分类 + bad-list 闭合断言 + soft-404，B3-3）：preflight 对 corpus 全 URL 产 per-entry HTTP 层分类报告（`http_status/final_url/content_type/redirect/canonical_drift/soft_404/short_body/missing_metadata` + 回显 expected/source）+ 汇总计数，CLI exit 0；**修复后 expected=ingest 非 fixture entry 中 `http_status>=400 或 short_body 或 soft_404` 计数==0**（死链 + soft-404 + 正文过短一并清零，以 `assert not bad` 闭合形式落 §7 verify，非仅 CLI 字段查）；`soft_404` 由 title/body not-found pattern 或 canonical/final_url 域漂移派生；`canonical_drift` 要么 ==0 要么具名登记且不计 live pass
- [ ] SC-7（双层 + 对齐 + 9 领域 + live 层代表性，B3-5）：corpus 含「fixture/snapshot + live health」双层（快照落 `fixtures/<name>.md`、corpus 用 `fixture:<name>` 引用，step-1 改 loader 按名读，见 step-1 SC-1a）；live 正样本 domain 对齐 main_sources.yaml——**aligned_count ≥ 配额且 ≥2 真实命中，不齐 live 项的 off_corpus_rationale 须具名真实 main_sources slug（非裸文本）**，确定性锚/无源页走 fixture: 不计 live 对齐；**替换后 9 领域配比守恒（M5）**；**新增 live 层代表性（B3-5）：每个非豁免领域 `expected=ingest 且 source=live` 计数≥1；某领域全 fixture 须在 corpus 顶层 `fixture_only_domains` allowlist 具名且不计 live health**；per-entry 含 feed_title/feed_author/feed_published/source_publisher 供 step 1 消费

---

## 6. 交付物

```yaml
deliverables:
  - path: src/akb2/quality/intake/preflight.py
    kind: file
    must_exist: true
    description: preflight CLI 模块，产 reports/calibration/preflight.json（HTTP 层分类报告，含 soft_404 字段，诊断非门，B3-3）
  - path: evals/main-pipeline-quality/corpus.yaml
    kind: file
    must_exist: true
    description: 死链替换后 + per-entry feed 元数据字段 + fixture: 双层引用 + replaced_reason/off_corpus_rationale + 顶层 fixture_only_domains allowlist（B3-5）；crewai/aider/MCP 保持 live 带真实 feed_*（B3-6）
  - path: evals/main-pipeline-quality/fixtures/
    kind: directory
    must_exist: true
    description: 质量信号锚点的固定正文快照（黄金样张源 + 1 概念正样本 + 2 负样本）；子目录 raw-html/ 存 crewai/aider/mcp 真实 HTML 供 step-1 SC-2b 注入（B3-6）
  - path: tests/test_corpus_preflight.py
    kind: file
    must_exist: true
    description: alignment（corpus↔main_sources）+ 双层存在 + per-entry 元数据 + live 层代表性（每非豁免领域≥1 live，fixture_only_domains 具名豁免，B3-5）四组断言
```

---

## 7. 验证（Anti-fill 硬约束）

```yaml
verify:
  - sc_ref: SC-3
    method: assertion
    cmd: "PYTHONPATH=src python3 -m akb2.quality.intake.preflight --corpus evals/main-pipeline-quality/corpus.yaml --out reports/calibration/preflight.json && python3 -c \"import json;r=json.load(open('reports/calibration/preflight.json'));E=r['entries'];assert all({'http_status','final_url','content_type','redirect','canonical_drift','soft_404','short_body','missing_metadata'}<=set(e) for e in E);bad=[e for e in E if e.get('expected')=='ingest' and e.get('source')!='fixture' and ((e.get('http_status') or 0)>=400 or e.get('short_body') or e.get('soft_404'))];assert not bad, bad\""
    expect: "exit 0；entries[] 含 8 个分类字段 + 回显 expected/source；ingest live 集 http_status>=400/short_body/soft_404 清零（bad-list 闭合断言，非仅字段查，B3-3）"
  - sc_ref: SC-7
    method: test
    cmd: "pytest tests/test_corpus_preflight.py -k alignment -q"
    expect: "passed（双层 + 对齐 + 9 领域配比 + live 层代表性每非豁免领域≥1 live，B3-5）"
```

---

## 8. Handoff 输出

```yaml
handoff_output:
  result_path: plans/gd/2026-06-26-loop1-calibration-harness-fix/results/t2b-preflight-corpus-result.md
  status_field: <见 gd-execution-status.schema.json>
  summary: preflight 模块 + corpus 双层/死链替换/per-entry feed 元数据扩展 + alignment 测试通过
  blockers: 无
```

---

## 9. 跨 lane 契约（step 1 下游依赖）

本 packet 在 `corpus.yaml` 每条正样本 entry 扩展可选字段 `feed_title` / `feed_author` / `feed_published` / `source_publisher` / `source_slug`（字段名严格对齐 `models.py` 的 `ExtractionContext`）。step 1 的 `akb2-calibration-harness-diagnostics-t1` packet 在 execute DAG 里 **`blocked_by` 本 packet**——step 1 读这些字段组装 ctx 传给 `extract_content(..., extraction_config=ctx)`。preflight 报告的 HTTP 层字段供 step 1 报告 step 读取合并；ExtractResult 的 method/word_count/metadata_complete/error 由 step 1 报告侧派生，不在本 packet 重复产出。

**Fixture 加载契约（cross-review M1）**：本 packet 落 `evals/main-pipeline-quality/fixtures/<name>.md` 快照文件（黄金样张 + 概念正样本 + 2 负样本），corpus 用 `fixture:<name>` 引用。**`_load_fixture_content` 的「按名读 fixtures/ 目录」改造属 step-1(t1) owned（calibration.py），本 packet 不改 calibration.py**；二者经 step-1 `blocked_by` 本 packet 串接：本 packet 先落文件，step-1 读。

**SC-2b 保存 HTML 契约（B3-6，反转 round-2 M4）**：crewai/aider/MCP **保持 live**（corpus 带真实 feed_*，不改 fixture:）；本 packet 另落 `evals/main-pipeline-quality/fixtures/raw-html/<slug>.html`（slug ∈ {crewai, aider, mcp}）= 这三页真实 HTML，捕获时正文在、author/published 缺（metadata_complete=False）。step-1(t1) SC-2b 读这三个 HTML 注入 extract_content 抽取器底层、走真实路径断言 ±ctx False→True。`fixtures/raw-html/` 与 `fixtures/<name>.md` 用途不同、不混用。contingency：execute 期 re-probe 若某页实为死链 → 该页降级 `fixture:`、SC-2b 该 case 退化为 SC-2a 机制覆盖并登记（round-2 P3）。

---

## 10. 范围禁令

- 禁止写入 `calibration.py`/`extract.py`（t2a owned）/包 A。
- preflight 是诊断工具，**不得** fail-closed 阻断校准（原则 4 失败可见，但 preflight 本身不是门）。
- 禁止访问 `/Users/praise/.claude/**`；禁止启动 daemon/hook/cron；禁止用对话上下文替代 `required_context`。
