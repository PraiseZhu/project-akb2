# Child Proposal — t1-calibration-core

> child_planner 子 agent 输出（dispatch_id=akb2-loop1-calibration-harness-fix, track=t1-calibration-core）。
> 本文件由主 agent 从 child planner 最终消息原样落盘，供 `gd-validate-child-proposal.py` 校验与主 agent 合并。

## 第 1 部分：step plan 草稿

```text
# Step 1: 校准 harness 镜像生产抽取契约 + 携带诊断 + 完整 raw frontmatter v1
GD_STANDARD: Project GD/prompts/gd-review-standard.md
TEMPLATE_KIND: gd-step-plan
```

TASK_GOAL：把 calibration.py 测试侧抓取从「裸调 extract_content 丢弃 ExtractResult、只回 content」改成「镜像 orchestrator 构造 ExtractionContext + 保留全部诊断字段 + 产出含 9 字段 frontmatter 的完整 raw_content」，覆盖 SC-1/SC-2/SC-4/SC-6。

实现步骤（WHERE/WHAT/WHY/VERIFY）：

- **Step.1** WHERE: `calibration.py` 新增 `_build_extraction_context(entry, config)`。WHAT: 按 orchestrator.py:214-227 形态从 corpus entry 读 feed_title/feed_author/feed_published/source_publisher/source_slug + url 构造 `ExtractionContext`。WHY: 无 context 时 crewai/aider/MCP 的 author/published 抽不到 → `_validate_metadata` fail → extraction_success=False（SC-2 根因）。VERIFY: `pytest tests/test_calibration_harness_diagnostics.py -k extraction_context -q`。
- **Step.2** WHERE: `calibration.py` `_fetch_url_content` 调用点（约 :156, :206-232）。WHAT: 改为 `extract_content(url, failures_path=..., extraction_config=_build_extraction_context(entry, config))`，返回完整 `ExtractResult`（不再只回 `result.content`）；`fixture:` 分支保持。WHY: 现状丢弃 ExtractResult 的诊断字段（SC-1）且不传 context（SC-2）。VERIFY: 同上。
- **Step.3** WHERE: `calibration.py` `_process_corpus_entry`（:111-203）。WHAT: 从 ExtractResult 派生写 entry: extract_method/word_count/metadata_complete/extraction_success/extract_error/missing_metadata；新增 `_classify_failure_class()`：HTTP 死链(读 t2 preflight 报告 http_status>=400/unreachable)→`dead_link`；extraction_success=False 且 word_count>=MIN 但 metadata_complete=False→`metadata_failure`；word_count<MIN/全失败→`extraction_failure`；成功→None。preflight 缺失时 http 字段 null、dead_link 退化按 ExtractResult.error。WHY: SC-1 三分类。VERIFY: `python3 -c "import json;d=json.load(open('reports/calibration/factory.json'));assert all('failure_class' in e and 'extract_method' in e for e in d['entries'])"`。
- **Step.4** WHERE: `calibration.py` 新增 `_compose_raw_content(extract_result, ctx)`。WHAT: 复用/等价拼装 raw_archiver 的 9 字段 frontmatter（artifact_type/source_slug/source_url/source_title/source_authors/source_publisher/source_published_at/fetched_at/fetched_via）+ result.content 成完整 raw markdown 作 raw_content。WHY: SC-6——现状只回裸正文，distill 收到的 raw_content 缺 source_* 字段产生 raw_content 缺失类信号。VERIFY: `pytest tests/test_calibration_harness_diagnostics.py -k raw_frontmatter -q`。
- **Step.5** WHERE: `tests/test_calibration_harness_diagnostics.py`（新建）。WHAT: 4 组直测 extraction_context / e2e_thin_slice / raw_frontmatter / failure_class，断言绑定可观测行为（extraction_success 翻转 / 不卡 intake_raw / frontmatter 字段出现 / 三分类正确）。WHY: SC-2/4/6 verify 是 pytest，断言须绑行为非「能跑通」。VERIFY: `pytest tests/test_calibration_harness_diagnostics.py -q`。

非目标：不改 extract.py 抽取决策树/`_validate_metadata`/`_apply_feed_fallback`（诊断字段已全在 ExtractResult）；不改包 A；不改 corpus.yaml/main_sources.yaml/preflight.py；不产出 HTTP 层字段本身；不调出厂阈值。

前置/风险：SC-1 端到端报告路径 blocked_by t2「corpus 扩展 per-entry feed 元数据」step；SC-2/4/6 直测用构造 ctx/fixture 解耦不阻塞。误改 extract.py 风险 → 全部从 ExtractResult 派生 + extract.py 列 forbidden。HTTP 字段缺失 → dead_link 退化按 error 判定、http 字段 null。

## 第 2 部分：候选 task packet 列表

- **task_id: akb2-calibration-harness-diagnostics-t1** | agent_role: implementer | owned_paths: `src/akb2/quality/calibration.py`, `tests/test_calibration_harness_diagnostics.py` | blocked_by: `t2-corpus-extend-per-entry-feed-metadata`（仅 SC-1 端到端报告路径需要）| can_parallel_with: t2 非 corpus-blocking 子任务 | required_context: calibration.py / extract.py / orchestrator.py / raw_archiver.py / models.py / corpus.yaml（只读字段契约）/ constitution.md | deliverables: calibration.py + tests/test_calibration_harness_diagnostics.py | SC: SC-1/SC-2/SC-4/SC-6 | verify 见 proposal block。
  - 范围禁令：禁写 extract.py/preflight.py/corpus.yaml/main_sources.yaml/包A；禁 `/Users/praise/.claude/**`；禁用对话上下文替代 required_context。

## 第 3 部分：机器可读 proposal block

<!-- gd-child-plan-proposal-json:start -->
```json
{
  "proposal_id": "akb2-loop1-calibration-harness-fix-t1-calibration-core-cp01",
  "parent_dispatch_id": "akb2-loop1-calibration-harness-fix",
  "parent_track_id": "t1-calibration-core",
  "agent_role": "child_planner",
  "output_status": "completed",
  "summary_cn": "把 calibration.py 测试侧抓取改为镜像 orchestrator 构造 ExtractionContext、保留 ExtractResult 全诊断字段并补三分类、产出含 9 字段 frontmatter 的完整 raw_content，新增直测覆盖 SC-1/2/4/6，不动生产 extract 与包 A。",
  "task_packets": [
    {"task_id": "akb2-calibration-harness-diagnostics-t1", "owned_paths": ["src/akb2/quality/calibration.py", "tests/test_calibration_harness_diagnostics.py"], "required_context": ["src/akb2/quality/calibration.py", "src/akb2/quality/intake/extract.py", "src/akb2/quality/intake/orchestrator.py", "src/akb2/quality/intake/raw_archiver.py", "src/akb2/quality/intake/models.py", "evals/main-pipeline-quality/corpus.yaml", "docs/constitution.md"]}
  ],
  "sc_refs": ["SC-1", "SC-2", "SC-4", "SC-6"],
  "verify": [
    {"sc_ref": "SC-1", "method": "assertion", "cmd": "python3 -c \"import json;d=json.load(open('reports/calibration/factory.json'));assert all('failure_class' in e and 'extract_method' in e for e in d['entries'])\""},
    {"sc_ref": "SC-2", "method": "test", "cmd": "pytest tests/test_calibration_harness_diagnostics.py -k extraction_context -q"},
    {"sc_ref": "SC-4", "method": "test", "cmd": "pytest tests/test_calibration_harness_diagnostics.py -k e2e_thin_slice -q"},
    {"sc_ref": "SC-6", "method": "test", "cmd": "pytest tests/test_calibration_harness_diagnostics.py -k raw_frontmatter -q"}
  ],
  "blocked_reason": null
}
```
<!-- gd-child-plan-proposal-json:end -->
