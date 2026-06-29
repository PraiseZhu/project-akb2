---
template_kind: gd-execution-result
result_id: content-lane-quality-step-1-2
task_id: content-lane-quality-execution
parent_step: step-1,step-2
parent_plan: plans/gd/2026-06-26-content-lane-quality/master-plan.md
executor_role: claude_subagent
executed_at: 2026-06-26T14:42:18Z
exec_status: completed_with_constraint
sc_acceptance:
  # ---------- step-1 SC-1..9 ----------
  - sc_ref: SC-1 (step-1)
    status: pass
    evidence: "pytest tests/test_admission_lane.py -k 'result_exposes_lane or lane_enum_closed' -q → 2 passed；AdmissionResult.__dataclass_fields__ 含 content_lane=True；LANE_CONCEPT/LANE_PROCEDURAL 常量就位（取值仅 concept/procedural，passed=False 时为空，无 unknown/None 合法终值）"
    not_run_reason: ""
  - sc_ref: SC-2 (step-1)
    status: pass
    evidence: "pytest tests/test_admission_lane.py -k classifier_cases -q → 3 passed（concept 无命令→LLM 判 concept；procedural 含命令→确定性判 procedural；mixed 机制+命令→procedural，spec FR-002 从严）"
    not_run_reason: ""
  - sc_ref: SC-3 (step-1)
    status: pass
    evidence: "pytest tests/test_admission_lane.py -k concept_not_rejected_by_how -q → passed（纯概念内容 how_tendency 判定本身 fail，但 concept 车道下不使 passed=False；how_tendency 由拒收门改路由信号）"
    not_run_reason: ""
  - sc_ref: SC-4 (step-1)
    status: pass
    evidence: "pytest tests/test_admission_lane.py -k lane_llm_unavailable_fail_closed -q → passed（无命令证据 + LLM 抛 RouterConfigError → passed=False + content_lane='' + ledger failed_checks 含 content_lane，不静默归车道）"
    not_run_reason: ""
  - sc_ref: SC-5 (step-1)
    status: pass
    evidence: "pytest tests/test_distill_lane_threading.py -k frontmatter_carries_lane -q → passed（_build_frontmatter 写入 content_lane 键 = 透传车道）"
    not_run_reason: ""
  - sc_ref: SC-6 (step-1)
    status: pass
    evidence: "pytest tests/test_runner_lane_threading.py -k lane_visible_in_ledger -q → passed（admission 拒收落账 details.content_lane；另验 grader/repair/_run_quality_segments 入口均接 content_lane 形参 + pipeline admission 通过事件携 lane）"
    not_run_reason: ""
  - sc_ref: SC-7 (step-1 零回归)
    status: pass
    evidence: "排除非我 owned 的 intake/extract.py + corpus.yaml 预存改动后：我的 5 个新测试 + tests/quality/ 全套 = 721 passed / 仅剩 2 个已登记豁免的 rubric-hash 守护失败（见 known_limitations）。隔离验证我的 6 源文件改动对既有 admission/grader/distill/runner 测试零回归（admission 25 passed、grader 32 passed）。proposal_mode 在本机 HEAD 即 0 passed/1 skipped（环境 golden-replay skip，非我引起，passed 数不减）"
    not_run_reason: ""
  - sc_ref: SC-8 (step-1 抽取子进程过界)
    status: pass
    evidence: "pytest tests/test_distill_lane_threading.py -k 'worker_payload_carries_lane or worker_main_forwards_lane or extract_funcs_accept_lane' → 各 passed；assertion: python3 -c \"import ast,pathlib; src=ast.unparse(...); assert 'json.loads(sys.stdin.read())' in src and 'content_lane' in src and 'extract_claims(raw_content, content_lane=' in src\" → SC-8 assertion OK"
    not_run_reason: ""
  - sc_ref: SC-9 (step-1 准入新颖度生产接线)
    status: pass
    evidence: "pytest tests/test_admission_novelty_wiring.py -k 'production_loads_wiki_refs or rewrite_rejected_in_runner_path or production_empty_index_holds_or_rejects or empty_index_preserves_golden_with_explicit_allow_empty' -q → 5 passed（_load_vault_concept_index 装载 wiki_references/known_concepts；洗稿在 runner 路径被 novelty 拦；生产 configured_but_empty/load_error → ledger novelty_index_unavailable + reject；first_run 空索引 golden 零回归通过）"
    not_run_reason: ""
  # ---------- step-2 SC-1..6 ----------
  - sc_ref: SC-1 (step-2)
    status: pass
    evidence: "rubric.yaml 可解析；concept lanes.dimensions 维名 exact-equal 7 个预期全名 + 权重和==10.0 + 含 threshold/integrity_floor；顶层含 rubric_version/lanes/integrity_floor/dimensions/threshold。SC-1 exact 校验命令 → True"
    not_run_reason: ""
  - sc_ref: SC-2 (step-2)
    status: pass
    evidence: "grep -nE '^(RUBRIC_DIMENSIONS|REQUIRED_RUBRIC_VERSION)\\b[^=]*=\\s*(\\[|\"1\\.0\\.0\")' src/akb2/quality/grader.py | wc -l → 0（RUBRIC_DIMENSIONS 从 _rubric_dimension_names() 派生、REQUIRED_RUBRIC_VERSION 从 _load_rubric_spec() 读，不写死）"
    not_run_reason: ""
  - sc_ref: SC-3 (step-2)
    status: pass
    evidence: "pytest tests/test_grader_rubric_external.py -k rubric_change_changes_scoring -q → passed（monkeypatch RUBRIC_DIR 指向 tmp rubric.yaml：weight 1.0→3.0 使 weighted_total 增大、threshold 6.5→9.0、改回复原；weighted_total=Σ(score×spec_weight)、threshold 从 spec 读，不改 .py 即生效）"
    not_run_reason: ""
  - sc_ref: SC-4 (step-2)
    status: pass
    evidence: "pytest tests/test_grader_rubric_external.py -k 'schema_extended or missing_location_rejected or dual_unavailable_suspended' -q → 3 passed（schema 含 weighted_total/threshold/integrity_floor + 维度 score；缺 location 整批拒 FR-006；双模型不可用挂起 FR-008）"
    not_run_reason: ""
  - sc_ref: SC-5 (step-2 零回归)
    status: pass
    evidence: "同 step-1 SC-7：grader 用例 32 passed 零回归（_parse_grader_output 放宽 dim.keys 容忍可选 score、扩展字段全可选，旧 mock 响应解析不变）。weighted_total 仅记录不参与本 step 判定（10 维 pass/fail 与缺陷集对同稿件不变）"
    not_run_reason: ""
  - sc_ref: SC-6 (step-2 version_match)
    status: pass
    evidence: "pytest tests/test_grader_rubric_external.py -k version_match -q → passed（REQUIRED_RUBRIC_VERSION == spec rubric_version == 1.1.0；_load_rubric_text 验 md 版本对齐 1.1.0）"
    not_run_reason: ""
  - sc_ref: SC-6 (step-2 primary_canary_real)
    status: not_run
    evidence: "pytest tests/test_grader_rubric_external.py -k primary_canary_real → 1 skipped"
    not_run_reason: "真机 canary 需 gpt-5.4 gateway + AKB2_GRADER_CANARY_LIVE=1；本机无 gpt-5.4 网关（属 step-3 真机门）。测试以 skipif 标注，本机 skip 不计 pass（fail-closed 语义）。memory 记载本机 gpt-5.4 经 tapsvc 网关有 401 风险，canary 实跑须在部署机验"
files_added:
  - skillpack/quality/rubric/rubric.yaml
  - tests/test_admission_lane.py
  - tests/test_distill_lane_threading.py
  - tests/test_runner_lane_threading.py
  - tests/test_admission_novelty_wiring.py
  - tests/test_grader_rubric_external.py
files_modified:
  - src/akb2/quality/admission.py
  - src/akb2/quality/distill.py
  - src/akb2/quality/distill_worker.py
  - src/akb2/quality/grader.py
  - src/akb2/quality/repair.py
  - src/akb2/quality/runner.py
  - skillpack/quality/rubric/10-dimensions.md
  - docs/allowed-residuals.md
files_unchanged_in_scope:
  - src/akb2/quality/__init__.py
owned_paths_writes_only: true
forbidden_paths_touched: []
out_of_scope_writes: []
handoff:
  result_path: plans/gd/2026-06-26-content-lane-quality/results/step-1-2-execution-result.md
  status_field: completed_with_constraint
  summary: "step-1 车道地基（content_lane 字段+确定性优先分类器+全链穿线+抽取子进程 JSON payload 过界+vault novelty 生产接线）与 step-2 量规外置（rubric.yaml 真相源+grader 去硬编码读 spec+schema 扩展）全部 SC 达标，旧判分零回归；唯一 constraint：rubric 合法升 v1.1.0 致 2 个非 owned 的历史 hash 守护测试失效（已登记豁免），及真机 gpt-5.4 canary 本机 skip"
  blockers: "none（step-3 由 Child 2 接续：lane-aware _validate_extract_output 改判 / 完整性地板四成员 / F3 两层 entailment / 图谱 resolve 消费 known/pending / RUBRIC_DIMENSIONS_BY_LANE 从 concept 维名动态派生）"
known_limitations:
  - "rubric_hash_guard：tests/quality/test_provenance_split.py::TestRubricHashUnchanged 2 个测试因 step-2 Step.5 合法升 10-dimensions.md v1.0.0→v1.1.0（+ Step.1 机器可读源注释）而 fail。该测试不在本 packet owned_paths（属 step-3/主 agent 范围），故未改其 _KNOWN_RUBRIC_HASH；新 md sha256=5f2791b9e4f3256e3c43355d9216998a53c335e9bce6fe4d03eee2891f9544c8，已在 docs/allowed-residuals.md 登记豁免。非判分回归（grader pass/fail 与缺陷集不变），仅 md 字节 hash 漂移。"
  - "工作区预存非我 owned 改动：src/akb2/quality/intake/extract.py(M) + intake/preflight.py(??) + evals/main-pipeline-quality/corpus.yaml(M) + tests/test_corpus_preflight.py(??) + tests/test_extract_scrapling_fallback.py(??) 属包 B（loop1-calibration-harness-fix）工件，task packet 明令不碰。全量 not-integration 含这些工件时另有 6 个失败（test_corpus_preflight 4 + test_extract_scrapling_fallback 2），经隔离 stash 证明全部由这些非我 owned 改动引起，与 step-1/2 无关。"
  - "compose_candidate 已接收 known_concepts/pending_queue 形参（step-3 预留 + 向后传给 runner），但本 step 保持 compose 内 self-gate 调用首行字面 run_candidate_self_gate(full_page, raw_content=raw_content) 不变，零回归现有源码审计测试 test_checks_structural SC-3.3；把 known/pending 喂给该 self-gate 的图谱 resolve 判定消费属 step-3 SC-3e（A3-8）。"
  - "distill_worker stdin 协议：主路径字面 json.loads(sys.stdin.read())（满足 SC-8 assertion + 生产 runner 已升级传 JSON payload）；向后兼容覆盖 JSON 字符串形态（json.dumps(raw)→str），非 JSON 裸文本 stdin → fail-closed 返回 extract_worker_invalid_stdin（stdin 单次读取无法重读裸文本；生产无裸文本调用方）。"
  - "proposal_mode 在本机 HEAD 即 0 passed/1 skipped（golden-replay 环境 skip，非我引起）；SC-7/SC-5 的 proposal_mode 部分以本机 skip 状态对账，passed 数未减。"
---

# step-1+2 执行结论（中文摘要）

## 做了什么

**step-1 车道地基**（owned 6 源文件穿线）：
1. `admission.py`：`AdmissionResult` 加 `content_lane` 字段 + `LANE_CONCEPT/LANE_PROCEDURAL` 常量；新增 `_classify_content_lane`（**确定性证据优先**：含可执行操作即 procedural 无歧义、不调 LLM；纯概念无命令才交 gemini-flash 轻判；LLM 不可用或输出非法 → fail-closed 不静默归车道）；`how_tendency` 由拒收门改路由信号（concept 车道下 how fail 不使 passed=False，spec FR-003）；`_log_rejection` details 加 content_lane。
2. `distill.py`：`extract_claims`/`build_deterministic_extract_result`/`_validate_extract_output`/`compose_candidate`/`_build_frontmatter` 增 `content_lane` 透传（`_validate_extract_output` 行为不变，H1≥3 门对所有 lane 维持旧判）；frontmatter 写 content_lane 键。
3. `distill_worker.py`：main() 改读 JSON payload `{raw_content, content_lane}` → `extract_claims(raw_content, content_lane=...)`（A3-1 抽取子进程过界）。
4. `runner.py`：`_run_extract_claims_worker` 传携 lane 的 JSON payload（非裸 raw）；新增确定性 `_load_vault_concept_index` loader（纯文件扫描 vault wiki/concepts slug + TLDR + pending 队列，typed status，排除当前源 slug）；`_run_admission_segment` 喂 wiki_references 给 novelty + 生产空/错索引 hold/reject + ledger 可见（Decision-2）；lane 穿 admission→distill→grader→repair 全链 + ledger 事件携 content_lane。
5. `grader.py`/`repair.py`：`run_grader_detailed`/`run_grader`/`run_repair_loop` 增 content_lane 形参（仅记录，不参与判定）。

**step-2 量规外置**：
1. 新建 `skillpack/quality/rubric/rubric.yaml`：rubric_version 1.1.0 + lanes(concept 7 维权重和 10 / procedural 等价旧 10 维) + 顶层 dimensions/integrity_floor/threshold；concept 维名为 step-3 动态派生逐字真相源；全数值 calibration_status=initial。
2. `grader.py`：新增 `_load_rubric_spec()`，RUBRIC_DIMENSIONS/REQUIRED_RUBRIC_VERSION 改从 spec 派生（去硬编码 enum/版本号）；`_build_grader_schema` 扩展可选 score/weighted_total/threshold/integrity_floor；`_parse_grader_extensions` 用 spec weight×模型 score 算 weighted_total + 从 spec 读 threshold（改 YAML 即改判分）；prompt 注入 weight/floor 说明。
3. `10-dimensions.md`：版本升 1.1.0 + 顶部注「机器可读源为 rubric.yaml」。

## 唯一约束（exec_status = completed_with_constraint）

rubric 合法升 v1.1.0 使**非 owned** 的历史 hash 守护 `TestRubricHashUnchanged`（2 测试）失效——这是计划 Step.5 明确要求的 rubric 演进、非判分回归（grader 对同稿件 pass/fail 与缺陷集不变）。已在 `docs/allowed-residuals.md` 登记豁免并给出新 hash，待有权改 test_provenance_split.py 的 step-3/主 agent 同步 `_KNOWN_RUBRIC_HASH`。

## 关键设计决策（surface conflicts）

- **lane 不进 checks 列表**：现有 admission 测试硬断言 `len(checks)==3`，故 lane 存 `content_lane` 字段而非新增 check，零回归 checks 长度。
- **确定性证据优先的 lane 分类**：用 `source_ops_scanners.operations`（只数 source_operation，**不数** boundary/conclusion，避免纯概念误判 procedural）做 procedural 硬证据；这让现有 mock fixture（addy/news 样张含命令）走确定性判定不触发 LLM、**无需改 tests/quality/conftest.py**（不在 owned），同时满足 SC-4 fail-closed。
- **`_run_admission_segment` 返回 4 元组 + pipeline 容错解包**：兼容 test_runner_lifecycle mock 的 3 元组 return_value，真实路径取 lane（不改非 owned 测试）。
