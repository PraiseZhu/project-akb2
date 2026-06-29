```yaml
template_kind: gd-execution-result
result_id: t1-calibration-core-result
task_id: akb2-calibration-harness-diagnostics-t1
parent_step: step-1-calibration-core
parent_plan: plans/gd/2026-06-26-loop1-calibration-harness-fix/step-1-calibration-core.md
executor_role: claude_subagent
executed_at: 2026-06-26T14:58:00Z
exec_status: completed_with_constraint
sc_acceptance:
  - sc_ref: SC-1
    status: not_run
    evidence: ""
    not_run_reason: "DEEPSEEK_API_KEY 未设置（本机为功能开发环境，管线部署在 mac mini），端到端校准 distill/grader 段无法运行致 positive_live_pass_rate=0.0（全 15 live 正样本在 admission 段被拒）；另 1 条 live URL (blog.continue.dev/continue-agent-loop) 抓取正文过短致 live_positive_intake_raw_failures=1（corpus 问题非代码缺陷）。实跑报告已验证全 20 条 entry 携带 7 诊断键 + source∈{fixture,live}、分层字段在场、14/15 live 正样本越过 intake_raw、factory_acceptance 正确返回 False；SC-1 诊断字段+三分类+分层评分+factory live 下限机制由 9 条 failure_class/stratified 直测 + 实跑报告字段验证覆盖"
  - sc_ref: SC-2
    status: pass
    evidence: |
      SC-2a: pytest tests/test_calibration_harness_diagnostics.py -k "extraction_context and not named_urls" -q → 3 passed
        test_extraction_context_without_ctx — 不传 ctx: metadata_complete=False ✓
        test_extraction_context_with_ctx — 传 ctx: metadata_complete=True ✓
        test_extraction_context_false_to_true_flip — 同一 base ±ctx False→True 翻转 ✓
      SC-2b: pytest tests/test_calibration_harness_diagnostics.py -k extraction_context_named_urls -q → 3 passed
        test_extraction_context_named_urls[crewai] — 注入 crewai.html 走真实 extract_content ±ctx False→True ✓
        test_extraction_context_named_urls[aider] — 注入 aider.html 走真实 extract_content ±ctx False→True ✓
        test_extraction_context_named_urls[mcp] — 注入 mcp.html 走真实 extract_content ±ctx False→True ✓
    not_run_reason: ""
  - sc_ref: SC-4
    status: pass
    evidence: |
      pytest tests/test_calibration_harness_diagnostics.py -k e2e_thin_slice -q → 1 passed
        test_e2e_thin_slice_live_positive — live 正样本（mock 抽取器返回完整元数据 + mock pipeline 成功）越过 intake_raw，failure_segment != "intake_raw"，passed=True ✓
    not_run_reason: ""
  - sc_ref: SC-6
    status: pass
    evidence: |
      pytest tests/test_calibration_harness_diagnostics.py -k raw_frontmatter -q → 2 passed
        test_raw_frontmatter_via_pipeline — 截获 _run_pipeline_segments 入参 raw_content，import raw_archiver.FRONTMATTER_FIELDS 断言 set(produced_keys)>=set(FRONTMATTER_FIELDS) 全 9 字段 ✓
        test_raw_frontmatter_direct — 直接测 _compose_raw_content 产 9 字段 ✓
    not_run_reason: ""
files_added:
  - tests/test_calibration_harness_diagnostics.py
files_modified:
  - src/akb2/quality/calibration.py
files_unchanged_in_scope: []
owned_paths_writes_only: true
forbidden_paths_touched: []
out_of_scope_writes: []
handoff:
  result_path: plans/gd/2026-06-26-loop1-calibration-harness-fix/results/t1-calibration-core-result.md
  status_field: completed_with_constraint
  summary: "calibration.py 镜像生产抽取契约（ExtractionContext + 保留 ExtractResult 诊断字段）+ 三分类（dead_link/metadata_failure/extraction_failure）+ 9 字段 raw frontmatter（FRONTMATTER_FIELDS 不硬编码）+ live/fixture 分层评分 + factory live 下限（LIVE_POSITIVE_QUOTA/LIVE_POSITIVE_PASS_FLOOR）；SC-2a/2b/4/6 直测全 pass，SC-1 机制由 9 条 failure_class/stratified 直测覆盖，SC-1 端到端因 DEEPSEEK_API_KEY 未设置标记 not_run"
  blockers: "SC-1 端到端 not_run：DEEPSEEK_API_KEY 未设置（本机功能开发环境）+ 1 条 corpus URL (blog.continue.dev) 抓取正文过短；均非代码缺陷，在 mac mini 管线部署环境 + corpus 死链替换后可跑"
known_limitations:
  - "SC-1 端到端 assertion 需 DEEPSEEK_API_KEY + GPT-5.4 key（本机功能开发环境不提供）；在 mac mini 管线部署环境跑可验证 ≥3 live 正样本真实越过 intake_raw + live_positive_intake_raw_failures==0"
  - "实跑发现 blog.continue.dev/continue-agent-loop 抓取正文过短（Scrapling adaptive content too short），属 corpus 死链替换候选；不影响代码正确性（failure_class=extraction_failure 正确分类）"
```
