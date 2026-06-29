```yaml
template_kind: gd-execution-result
result_id: t2b-fix-continue-url-result
task_id: t2b-fix-continue-url
parent_step: step-2-intake-corpus
parent_plan: plans/gd/2026-06-26-loop1-calibration-harness-fix/step-2-intake-corpus.md
executor_role: claude_subagent
executed_at: 2026-06-26T15:17:27Z
exec_status: completed
sc_acceptance:
  - sc_ref: SC-3
    status: pass
    evidence: "preflight: 20 entries (15 live, 5 fixture), dead_links=0 redirects=2 soft_404=0; bad-list 闭合断言通过 → 'SC-3 PASS'"
    not_run_reason: ""
  - sc_ref: SC-7
    status: pass
    evidence: "pytest tests/test_corpus_preflight.py -k alignment -q → 8 passed (9 领域配比守恒 / 双层 / aligned_count≥2 / off_corpus rationale / per-entry feed 元数据 / live 层代表性 / fixture_only_domains / raw-html 快照)"
    not_run_reason: ""
  - sc_ref: SC-1
    status: pass
    evidence: "calibration CLI 端到端: factory.json live_positive_intake_raw_failures=0 → 'SC-1 intake_raw PASS'（continue.dev 卡死已消除；整体出厂仍 FAIL 因无 DeepSeek key 跑 distill/grader，本任务只验 extraction 层）"
    not_run_reason: ""
files_modified:
  - evals/main-pipeline-quality/corpus.yaml
files_added: []
owned_paths_writes_only: true
forbidden_paths_touched: []
out_of_scope_writes: []
handoff:
  result_path: plans/gd/2026-06-26-loop1-calibration-harness-fix/results/t2b-fix-continue-url-result.md
  status_field: completed
  summary: "方案 A：将 continue.dev (Next.js SSR, defuddle 抽不到正文) 替换为 simonwillison.net/2026/Jun/22/porting-moebius/ (Django SSR 静态页, defuddle 抽 1658 词, feed_published 回填 published 后 extraction_success=True)；Loop Engineering 领域 live 计数不变 (2 live + 1 fixture), aligned_count 5→6；SC-3/SC-7/SC-1 全 pass, intake_raw==0"
  blockers: "none"
known_limitations:
  - "整体出厂判定仍 FAIL（正样本通过率 0%），因本机无 DeepSeek key 跑 distill/grader 层；本任务范围仅 intake_raw extraction 层，已验证该层卡死==0"
  - "新 URL feed_published=2026-06-22 由 corpus entry 提供, defuddle 自身抽不到 published date, 依赖 ExtractionContext feed 回填机制（calibration.py L124-131 已镜像 orchestrator 构造, 生产链路同源）"
```
