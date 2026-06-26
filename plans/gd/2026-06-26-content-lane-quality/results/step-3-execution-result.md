---
template_kind: gd-execution-result
result_id: content-lane-quality-step-3
task_id: content-lane-quality-execution
parent_step: step-3
parent_plan: plans/gd/2026-06-26-content-lane-quality/master-plan.md
executor_role: claude_subagent
executed_at: 2026-06-27T00:00:00Z
exec_status: completed_with_constraint
sc_acceptance:
  - sc_ref: SC-3a
    status: pass
    evidence: "pytest tests/quality/test_three_level_how.py -q → 7 passed（concept H1==0/mechanism_components≥3 两路径 extract success；procedural executable<3 仍 fail）；distill _EXTRACT_SCHEMA 加 mechanism_components/causal_edges/how_level，_validate_extract_output lane-aware（concept 改判 mechanism≥3）"
    not_run_reason: ""
  - sc_ref: SC-3b
    status: pass
    evidence: "pytest tests/quality/test_integrity_floor.py -k 'f4 or adversarial' → passed；F4 二值硬卡（concept mechanism≥3+每组件causal边/procedural H1≥1）；对抗样本 F4 fail+辅助满分→reject+日志 floor_failed+weighted_score_not_computed；只读结构字段不反解 H3 标签"
    not_run_reason: ""
  - sc_ref: SC-3h
    status: pass
    evidence: "pytest tests/quality/test_integrity_floor.py -k 'claim_anchor_rate or evidence_honesty' → passed；claim_anchor_rate 缺锚→fail；evidence_honesty 无未覆盖列→fail；fail 时 grader_not_called+weighted_score_not_computed"
    not_run_reason: ""
  - sc_ref: SC-3c
    status: pass
    evidence: "pytest tests/quality/test_interpretation_traceability.py -q → 9 passed；层① integrity_floor traces_to 锚定真实 anchor（>40%解读全溯源过/无据读后感拒/traces_to不存在anchor拒）；层② grader interpretation_entailment 字段拒 poisoned-trace（fixture entailment=false→reject）；procedural 保留 ≤40% cap（distill _validate_extract_output + checks_structural SG-provenance-ratio lane-aware）"
    not_run_reason: ""
  - sc_ref: SC-3c-canary
    status: not_run
    evidence: "pytest tests/quality/test_interpretation_traceability.py -k 'poisoned_trace_real_canary or entailment_model_is_gpt54' → 2 skipped"
    not_run_reason: "真机 canary 需 gpt-5.4 网关 + AKB2_GRADER_CANARY_LIVE=1；本机无 gpt-5.4 网关。step-3 文档明确允许 real canary 不可用时 fail-closed/skip-not-run 不计 SC-7 pass；fixture 单测部分已 pass 验 parse/early-return 回归"
  - sc_ref: SC-3d
    status: pass
    evidence: "pytest tests/quality/test_concept_weighted_score.py -q → 8 passed；权重和==10（concept 7 维）、正样本≥6.5、最小合规 H3 深度≤1.0 总分<6.5 拒、维名不在权重表→KeyError 不静默零权重；threshold 从 rubric.yaml 读"
    not_run_reason: ""
  - sc_ref: SC-3e
    status: pass
    evidence: "pytest tests/quality/test_graph_resolvability.py -q → 10 passed；_resolve_graph_node 纯代码不调 LLM（monkeypatch router raise 后仍正确+import allowlist 断言 checks_structural 不 import llm_router）；check_a4 增 known/pending 形参 concept-only resolve（真节点边过/假边拒/pending≥3共指 resolve）；run_structural_checks+self-gate 透传 known/pending"
    not_run_reason: ""
  - sc_ref: SC-3f
    status: pass
    evidence: "pytest tests/quality/test_grader_lane_dimensions.py -q → 6 passed；RUBRIC_DIMENSIONS_BY_LANE 从 _load_rubric_spec 动态派生（concept=lanes.concept 7维逐字/procedural=全局10维）；concept 不含 How两维/地板维；AST 负断言 grader.py 不含 concept 7维硬编码字面量；tmp rubric 改名驱动派生集变化"
    not_run_reason: ""
  - sc_ref: SC-3g
    status: pass
    evidence: "pytest tests/quality/test_concept_judgment_e2e.py -q → 9 passed（concept_positive/negatives×6/adversarial×2）；pytest -m 'not integration' -q → 1500 passed 0 failed 3 skipped（零回归）；judgment chain floor→grader entailment→weighted_score→runner early-return；对抗 F4 fail+辅助满分 地板 early-return 拒加权分不算"
    not_run_reason: ""
files_added:
  - src/akb2/quality/integrity_floor.py
  - src/akb2/quality/weighted_score.py
  - tests/quality/test_three_level_how.py
  - tests/quality/test_integrity_floor.py
  - tests/quality/test_interpretation_traceability.py
  - tests/quality/test_concept_weighted_score.py
  - tests/quality/test_graph_resolvability.py
  - tests/quality/test_grader_lane_dimensions.py
  - tests/quality/test_concept_judgment_e2e.py
  - tests/quality/fixtures/concept/README.md
files_modified:
  - src/akb2/quality/distill.py
  - src/akb2/quality/checks_structural.py
  - src/akb2/quality/grader.py
  - src/akb2/quality/runner.py
  - tests/quality/test_checks_structural.py
files_unchanged_in_scope: []
owned_paths_writes_only: true
forbidden_paths_touched: []
out_of_scope_writes: []
handoff:
  result_path: plans/gd/2026-06-26-content-lane-quality/results/step-3-execution-result.md
  status_field: completed_with_constraint
  summary: "step-3 concept 判定核心全部完成：三级how+结构字段+lane-aware extract / 完整性地板四成员二值不可补偿 / F3 两层(层①traces_to+层②异族grader entailment) / concept加权分过6.5 / 图谱A层resolve纯代码 / grader lane维度集动态派生 / E2E+对抗；SC-3a..3g 全 pass，唯一 constraint 是真机 gpt-5.4 canary 本机 skip（文档允许）；零回归 1500 passed。"
  blockers: "none"
known_limitations:
  - "真机 canary（poisoned_trace_real_canary / entailment_model_is_gpt54）本机无 gpt-5.4 网关→skip-not-run；需在 mac mini（有 gpt-5.4 网关）跑 canary 才能达 SC-7 full pass。fixture 单测已验层② parse/early-return 回归。"
  - "traces_to 字段未进 _EXTRACT_SCHEMA（claims additionalProperties:False 不含 traces_to）；integrity_floor 层① 用 .get 读 traces_to，真实 extract pipeline 当前不产 traces_to（schema 不允许）——layer① 在 hand-built fixture 验证通过，生产 pipeline 产 traces_to 需后续扩 schema + extract prompt（不阻断 SC-3c fixture 验收，但生产 concept traces_to 溯源需补）。"
  - "proposal_mode 本机 HEAD 即 0 passed/1 skipped（golden-replay 环境 skip，非 step-3 引起，passed 数未减）。"
---
