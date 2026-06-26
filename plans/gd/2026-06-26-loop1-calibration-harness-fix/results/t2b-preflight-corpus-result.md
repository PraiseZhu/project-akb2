```yaml
template_kind: gd-execution-result
result_id: t2b-preflight-corpus-result
task_id: t2b-preflight-corpus
parent_step: step-2-intake-corpus
parent_plan: plans/gd/2026-06-26-loop1-calibration-harness-fix/step-2-intake-corpus.md
executor_role: claude_subagent
executed_at: 2026-06-26T14:25:09Z
exec_status: completed
sc_acceptance:
  - sc_ref: SC-3
    status: pass
    evidence: >
      Preflight: 20 entries (15 live, 5 fixture)
      dead_links=0 redirects=3 soft_404=0
      SC-3 ASSERT PASS (all 8 fields present in every entry;
      bad-list empty: 0 live ingest entries with http_status>=400 or short_body or soft_404)
    not_run_reason: ""
  - sc_ref: SC-7
    status: pass
    evidence: >
      pytest tests/test_corpus_preflight.py -k alignment -q → 8 passed
      (nine_domain_counts_conserved / two_layer_exists / live_positives_aligned_count=5≥2
      / off_corpus_live_positives_have_rationale (all proxy slugs real)
      / per_entry_feed_metadata / live_layer_representativeness (all 9 domains ≥1 live)
      / fixture_only_domains_allowlist_declared / raw_html_snapshots_exist)
      ruff check src/akb2/quality/intake/preflight.py tests/test_corpus_preflight.py → No issues found
    not_run_reason: ""
files_added:
  - src/akb2/quality/intake/preflight.py
  - evals/main-pipeline-quality/fixtures/loop-engineering-golden.md
  - evals/main-pipeline-quality/fixtures/ag2-captain-agent-concept.md
  - evals/main-pipeline-quality/fixtures/vercel-ai-sdk-nosource.md
  - evals/main-pipeline-quality/fixtures/google-io-2026-news.md
  - evals/main-pipeline-quality/fixtures/x-plagiarism-negative.md
  - evals/main-pipeline-quality/fixtures/raw-html/crewai.html
  - evals/main-pipeline-quality/fixtures/raw-html/aider.html
  - evals/main-pipeline-quality/fixtures/raw-html/mcp.html
  - tests/test_corpus_preflight.py
files_modified:
  - evals/main-pipeline-quality/corpus.yaml
files_unchanged_in_scope: []
owned_paths_writes_only: true
forbidden_paths_touched: []
out_of_scope_writes: []
handoff:
  result_path: plans/gd/2026-06-26-loop1-calibration-harness-fix/results/t2b-preflight-corpus-result.md
  status_field: completed
  summary: "preflight 模块 + corpus 12 死链替换/5 fixture 双层/per-entry feed 元数据 + 8 项 alignment 测试全部通过"
  blockers: "none"
known_limitations:
  - "reports/calibration/preflight.json 为 SC-3 verify 命令产物 (preflight CLI 输出), 非源码交付物, 不列入 files_added"
  - "3 条 redirect (同域内 trailing-slash 规范化) 非 soft_404, preflight 正确未标"
```
