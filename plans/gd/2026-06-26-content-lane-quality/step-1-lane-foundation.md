# Step 1: 车道地基（content_lane 字段 + 分类器 + 全链穿线 + 抽取子进程过界 + 准入新颖度接线）

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md v1.2.0 + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-step-plan

日期：2026-06-26 ｜ 状态：draft（child_planner t1 起草 + 主 agent 合并；round-3 真异族 Codex 审后修订：A3-1 抽取子进程穿线 / Decision-2 洗稿生产接线）

## 1. 目标链

```text
PROJECT_GOAL: 让循环1 从命令清单库变成能同时收录概念型与操作型知识的概念图谱知识库（spec: specs/content-lane-quality/spec.md）
CHAIN_GOAL:   把宪法 v1.2.0「完整性地板 + 车道感知加权质量分」实现为循环1 判定代码（原则 6/8）
PHASE_GOAL:   产出包 A『改标准』计划套装
TASK_GOAL:    给循环1 引入 content_lane（concept/procedural）字段与准入分类器，把该字段穿到 distill/抽取子进程/checks_structural/grader/repair/runner/ledger/candidate frontmatter；并把 vault 既有概念/wiki 摘要装载进准入 novelty（使洗稿在生产链真被拦）。本 step 不改任何判分**算法**（旧判分结果不变）；novelty 算法不动，仅补它生产期一直缺的 wiki_references 输入
```

## 2. Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：`lane 分类决定性（concept/procedural 二值 + 混合归 procedural + LLM 不可用 fail-closed，不静默 unknown）; how_tendency 从拒收门改路由信号（非命令内容不再被它拒）; lane 真正穿过抽取子进程边界（不止 compose；否则概念正样本在 extract 阶段被 H1 门拒、走不到 compose——A3-1）; 准入 novelty 在生产 runner 路径真激活（wiki_references 不再恒空默认 pass、洗稿真被拦——Decision-2）; 旧判分零回归（admission/grader/repair/checks 既有 pass/fail 与缺陷集不变；golden 样张仍过）; 字段穿线完整性（每个下游节点能读到 lane，无静默丢字段）`
- 边界说明：lane 字段本 step 仅被「记录与传递」，不被「消费用于改判定」。任何改 grader 维度判定/repair 触发/checks 缺陷生成的行为改动属越界，留给 step-3。**例外（本 step 显式承担的两处生产接线）**：①把 content_lane 穿过抽取子进程（lane-aware extract 校验本身仍是 step-3 填，本 step 只让 lane 能到 `_validate_extract_output`，行为不变）②把 wiki_references 接进 `_run_admission_segment`（novelty 算法不动，只补输入）——这两处是「让既有判定拿到它该拿的输入」，不是「改判定算法」。

## 3. 前置条件

- blocked_by：`—`（包 A 第一步）
- baseline：`src/akb2/quality/{admission,distill,distill_worker,checks_structural,grader,repair,runner}.py`、`src/akb2/loops/{ledger,paths}.py`、现有全绿测试基线
- 现状锚点（round-3 实读，供执行对齐）：
  - `runner.py:404 _run_distill_segment(raw_path, raw_content, config)` → `:420 _run_extract_claims_worker(raw_content)` → 子进程 `distill_worker.py:25` `sys.stdin.read()` → `:27 extract_claims(raw_content)`：**lane 全程缺席**，`compose_candidate`（:444）才拿到 lane（step-1 旧稿只穿到这里）。
  - `distill.py:460 extract_claims(raw_content, route_llm_fn=None)`；`:1380 _validate_extract_output(data)` 在 `:1448-1458` 硬卡 `executable_steps < HOW_STEPS_MIN_EXECUTABLE(=3)`；`:1509 build_deterministic_extract_result(raw_content, raw_path)` 在 `:1521` 也走 `_validate_extract_output`。三处均无 lane。
  - `runner.py:396 run_admission(raw_path, raw_content, ledger_path)` 不传 `wiki_references`；`admission.py:406 _check_novelty(body, wiki_references, route_llm_fn)` 在 `:416` `wiki_references` 空即 **默认 pass**——故生产链洗稿从不触发 novelty。
- Hard-stop：执行前 `pytest -m 'not integration'` 非全绿则停（无法区分回归来源）

## 4. 成功标准（SC）

- [ ] SC-1：`AdmissionResult` 暴露 `content_lane`，取值仅 concept/procedural（无 unknown/None 合法终值）。verify(test): `pytest tests/test_admission_lane.py -k "result_exposes_lane or lane_enum_closed" -q` → 2 passed
- [ ] SC-2：lane 分类器判对 concept/procedural/mixed（mixed→procedural）。verify(test): `pytest tests/test_admission_lane.py -k classifier_cases -q` → 3 passed
- [ ] SC-3：how_tendency 不再因缺命令拒收纯概念内容（改路由信号，不进 passed=False）。verify(test): `pytest tests/test_admission_lane.py -k concept_not_rejected_by_how -q` → passed
- [ ] SC-4：lane 分类器在 LLM 不可用时 fail-closed（拒收+落账），不静默归车道。verify(test): `pytest tests/test_admission_lane.py -k lane_llm_unavailable_fail_closed -q` → passed
- [ ] SC-5：`content_lane` 出现在 candidate frontmatter。verify(test): `pytest tests/test_distill_lane_threading.py -k frontmatter_carries_lane -q` → passed
- [ ] SC-6：lane 贯穿全链——grader/repair/runner 入口可见并向 ledger 写出（账本事件含 details.content_lane）。verify(test): `pytest tests/test_runner_lane_threading.py -k lane_visible_in_ledger -q` → passed
- [ ] SC-7（零回归）：既有判分行为不变。verify(command): `pytest -m 'not integration' -q && pytest -m proposal_mode -q` → 均 passed，通过数不减
- [ ] SC-8（A3-1 抽取子进程过界）：content_lane 真正穿过 extract 子进程边界——`_run_extract_claims_worker` 向子进程传的是携 content_lane 的 JSON payload（非裸 raw_content），`distill_worker.main()` 必须 `json.loads(sys.stdin.read())` 后转给 `extract_claims(..., content_lane=...)`；`extract_claims`/`build_deterministic_extract_result`/`_validate_extract_output` 均接 `content_lane` 形参（本 step 仅透传、不改 H1 门行为）。verify(test): `pytest tests/test_distill_lane_threading.py -k worker_payload_carries_lane -q` → passed；`pytest tests/test_distill_lane_threading.py -k worker_main_forwards_lane -q` → passed；`pytest tests/test_distill_lane_threading.py -k extract_funcs_accept_lane -q` → passed；verify(assertion): `python3 -c "import ast,pathlib; t=ast.parse(pathlib.Path('src/akb2/quality/distill_worker.py').read_text()); src=ast.unparse(t); assert 'json.loads(sys.stdin.read())' in src and 'content_lane' in src and 'extract_claims(raw_content, content_lane=' in src"` → no output
- [ ] SC-9（Decision-2 准入新颖度生产接线）：生产 runner 准入段装载 vault wiki_references 并喂给 `run_admission`，洗稿在生产路径真被 novelty 拦（不再恒默认 pass）；loader 可注入并返回 typed status，生产 `configured_but_empty/load_error` 必须 ledger 可见并 hold/reject；golden 样张仅在显式 `allow_empty_index` fixture 下空索引仍过（零回归）。verify(test): `pytest tests/test_admission_novelty_wiring.py -k "production_loads_wiki_refs or rewrite_rejected_in_runner_path or production_empty_index_holds_or_rejects or empty_index_preserves_golden_with_explicit_allow_empty" -q` → 4 passed

## 5. 非目标

- 不改任何维度判定阈值/权重/通过条件（step-2/step-3 的事）
- 不让 grader 按 lane 选量规、repair 按 lane 改策略（仅传字段）
- **不改 lane-aware extract 校验的判定本身**——本 step 只让 lane 到达 `_validate_extract_output`，把「concept 允许 H1=0、改判 H3≥3」的逻辑留给 step-3 Step.1（本 step `_validate_extract_output` 行为对所有 lane 与旧版等价）
- **不改 novelty 算法**——只补 `wiki_references` 输入；阈值/prompt/判定逻辑不动
- 不新建概念型黄金样张（沿用 addy-osmani）；不碰抓取层/源失效

## 6. 实现步骤

```text
Step.1  WHERE: admission.py AdmissionResult dataclass
  WHAT: 加 content_lane: str = "" 字段 + 常量 LANE_CONCEPT/LANE_PROCEDURAL
  VERIFY: python3 -c "from src.akb2.quality.admission import AdmissionResult; print('content_lane' in AdmissionResult.__dataclass_fields__)" → True
Step.2  WHERE: admission.py 新增 _classify_content_lane(body, route_llm_fn)
  WHAT: 轻量 LLM 分类 concept/procedural（含可执行操作即 procedural，混合归 procedural）；schema 校验；路由异常→fail-closed 标记
  VERIFY: pytest tests/test_admission_lane.py -k classifier_cases -q → 3 passed
Step.3  WHERE: admission.py run_admission() + how_tendency 调用处
  WHAT: 三判定后调分类器得 lane；how_tendency 由「计入 all_passed 的拒收门」改「路由信号」——concept 车道下其 fail 不使 passed=False；分类器不可用→fail-closed；赋值 content_lane
  VERIFY: pytest tests/test_admission_lane.py -k "concept_not_rejected_by_how or lane_llm_unavailable_fail_closed" -q → 2 passed
Step.4  WHERE: distill.py compose_candidate() + _build_frontmatter()
  WHAT: compose_candidate 增形参 content_lane=""，透传 frontmatter 插入 content_lane 键
  VERIFY: pytest tests/test_distill_lane_threading.py -k frontmatter_carries_lane -q → passed
Step.5  WHERE: grader.py run_grader_detailed/run_grader + repair.py run_repair_loop
  WHAT: 各增形参 content_lane=""（默认空保旧调用兼容）；仅记入返回/日志，不参与判定分支
  VERIFY: python3 -c "import inspect,src.akb2.quality.grader as g,src.akb2.quality.repair as r; print('content_lane' in inspect.signature(g.run_grader_detailed).parameters and 'content_lane' in inspect.signature(r.run_repair_loop).parameters)" → True
Step.6  WHERE: distill.py(extract_claims / _validate_extract_output / build_deterministic_extract_result / compose_candidate) + distill_worker.py + runner.py(_run_extract_claims_worker / _run_distill_segment)
  WHAT:【A3-1 抽取子进程过界】给 extract_claims 增 content_lane: str=""（透传给 _validate_extract_output、build_deterministic 同增）；_validate_extract_output 增 content_lane 形参但**行为不变**（H1≥3 门对所有 lane 维持旧判，lane-aware 改判属 step-3）。distill_worker.main() 改为读 JSON payload {"raw_content":..., "content_lane":...}（向后兼容：纯字符串 stdin 视为 content_lane=""）→ extract_claims(raw_content, content_lane=lane)。_run_extract_claims_worker(raw_content, content_lane) 把 payload 编码为 JSON 经 stdin 传子进程。_run_distill_segment 增 content_lane、known_concepts、pending_queue 形参（从 pipeline/loader 传入），透传给 _run_extract_claims_worker + build_deterministic_extract_result + compose_candidate；compose 内第一道 self-gate 也必须拿到 known_concepts/pending_queue，不能只在 runner 第二道 self-gate 接线。
  VERIFY: pytest tests/test_distill_lane_threading.py -k worker_payload_carries_lane -q → passed；pytest tests/test_distill_lane_threading.py -k worker_main_forwards_lane -q → passed；pytest tests/test_distill_lane_threading.py -k extract_funcs_accept_lane -q → passed；python3 -c "import ast,pathlib; src=ast.unparse(ast.parse(pathlib.Path('src/akb2/quality/distill_worker.py').read_text())); assert 'json.loads(sys.stdin.read())' in src and 'extract_claims(raw_content, content_lane=' in src" → no output
Step.7  WHERE: runner.py _run_admission_segment/_run_distill_segment/_run_quality_segments/_run_pipeline_segments
  WHAT: admission 段返回携 lane；pipeline 串 lane 到 _run_distill_segment（→ 抽取子进程 + compose_candidate）+ quality 段（再传 grader/repair）
  VERIFY: grep -n "content_lane" src/akb2/quality/runner.py | wc -l → ≥4
Step.8  WHERE: runner.py 新增 _load_vault_concept_index() + _run_admission_segment + pipeline 传递;（vault 解析经 loops/paths.get_akb2_root/get_staging_root，符合宪法 P5）
  WHAT:【Decision-2 洗稿生产接线】新增确定性 loader（纯文件扫描、无 LLM），返回 `{status, counts, wiki_references, known_concepts, pending_queue, source_paths}`：known_concepts = glob `<akb2_root>/wiki/concepts/*.md` slug 集；pending_queue = 读 `<staging>/pending/concept-backlinks.jsonl` 的 concept slug；wiki_references = `<akb2_root>/wiki/**/*.md` 各页 TLDR 摘要（≤5，_check_novelty 只用前 5），**排除当前处理源自身 slug**（防自比对误判）。status ∈ `{ok, first_run_no_index, configured_but_empty, load_error}`：`ok` 正常喂 novelty；`first_run_no_index` 或显式 bootstrap/test `allow_empty_index` 才允许空索引默认 pass；生产 `configured_but_empty/load_error` → `_run_admission_segment` 写 ledger `novelty_index_unavailable` 并返回 hold/degraded/reject，不静默 pass。loader 可 monkeypatch 注入（测试用）。known_concepts/pending_queue 一并向后传，供 compose 内第一道 self-gate 与 runner 第二道 self-gate 的图谱 resolve 复用。
  VERIFY: pytest tests/test_admission_novelty_wiring.py -k "production_loads_wiki_refs or rewrite_rejected_in_runner_path or production_empty_index_holds_or_rejects or empty_index_preserves_golden_with_explicit_allow_empty" -q → 4 passed
Step.9  WHERE: admission.py _log_rejection + runner _log_event
  WHAT: ledger 事件 details 增 content_lane 键
  VERIFY: pytest tests/test_runner_lane_threading.py -k lane_visible_in_ledger -q → passed
Step.10 WHERE: tests/test_{admission_lane,distill_lane_threading,runner_lane_threading,admission_novelty_wiring}.py 新建 + 全量回归
  WHAT: 新建测试覆盖 SC-1..6/8/9；跑全量回归对账 SC-7（含 proposal_mode 注入空 wiki_references，确保 golden 样张零回归）
  VERIFY: pytest -m 'not integration' -q && pytest -m proposal_mode -q → 均 passed 不减
```

Anti-fill：全部步骤为具体字段增列/形参增列/赋值透传/新建 loader 函数/断言，非「完善/优化/增强」。

## 7. Execution Checkpoint：lane-foundation

- agent_role: implementer ｜ blocked_by: [] ｜ can_parallel_with: [] ｜ execution_packet: `content-lane-quality-execution`（本节是单一 execution packet 内部 checkpoint，不单独作为 §6 task packet 调度）
- owned_paths: `src/akb2/quality/{admission,distill,distill_worker,grader,repair,runner}.py`、`src/akb2/quality/__init__.py`、`tests/test_admission_lane.py`、`tests/test_distill_lane_threading.py`、`tests/test_runner_lane_threading.py`、`tests/test_admission_novelty_wiring.py`
- forbidden_paths: `skillpack/quality/rubric/**`、`docs/constitution.md`、`src/akb2/briefing/**`、`/Users/praise/.claude/**`
- required_context: `specs/content-lane-quality/spec.md`、`docs/constitution.md`、`src/akb2/loops/ledger.py`、`src/akb2/loops/paths.py`、`src/akb2/quality/checks_structural.py`、`src/akb2/quality/promote.py`（pending 队列路径 `<staging>/pending/concept-backlinks.jsonl` 来源，供 loader 对齐；owned 的 admission/distill/distill_worker/grader/repair/runner.py 为本 packet 自有，执行时直接读改，按标准 §6 不重列入 required_context 以免与 owned_paths 重叠）
- deliverables: `src/akb2/quality/{admission,distill,distill_worker,grader,repair,runner}.py`、`src/akb2/quality/__init__.py`（如需导出常量）、`tests/test_admission_lane.py`、`tests/test_distill_lane_threading.py`、`tests/test_runner_lane_threading.py`、`tests/test_admission_novelty_wiring.py`、step-1-result.md
- verify: 本 step §4 SC-1~9 的 verify 命令（lane 字段+enum 闭合 / 分类器 / how 不拒概念 / LLM 不可用 fail-closed / frontmatter 携 lane / 全链穿到 ledger / 零回归对账 / 抽取子进程过界 / novelty 生产接线）
- 说明：穿线链跨 7 文件互相依赖（lane 从准入产生逐段透传 + 抽取子进程过界 + vault loader），不可并行切分，单 packet 串行内部 checkpoint commit。
- handoff: result → `plans/gd/2026-06-26-content-lane-quality/results/step-1-result.md`

## 8. 边界

修改：admission.py（lane 字段+分类器+how 路由化+novelty 接线）/ distill.py（compose_candidate 签名 + frontmatter 增键 + extract_claims/build_deterministic/_validate_extract_output 增 content_lane 透传）/ distill_worker.py（JSON payload 解析 + 转 content_lane）/ grader.py（仅增形参）/ repair.py（仅增形参）/ runner.py（穿线 + 抽取子进程过界 + vault loader + 准入接线）/ __init__.py（如需导出常量）/ 4 个新测试文件。
不修改：skillpack/quality/rubric/**、任何维度判定阈值/权重、`_validate_extract_output` 的 H1 门**判定**（lane-aware 改判属 step-3）、novelty **算法**、docs/constitution.md、src/akb2/briefing/**、/Users/praise/.claude/**。

## 9. 风险与防护

| 风险 | 防护 |
|------|------|
| how_tendency 改路由误放宽 procedural | how_tendency fail 仅在 lane=concept 时豁免；procedural 车道仍拒；procedural 缺命令负样本钉死 |
| 增形参破坏既有调用点 | 形参默认 ""；SC-7 全量回归对账 |
| 分类器静默归车道 | schema 校验 + 非 concept/procedural 即 fail-closed（SC-4），不设默认 |
| 子进程 stdin 协议改 JSON 破坏旧调用 | 向后兼容：纯字符串 stdin 视为 content_lane=""；SC-8 验过界 + SC-7 全量回归 |
| lane 只穿到 compose、漏穿抽取子进程（A3-1 原洞） | SC-8 在真实 worker 路径验 payload 携 lane + worker.main 转发 + 三抽取函数接 lane；不靠「compose 拿到就算穿好」 |
| novelty 接线后 golden 样张被自比对误判为洗稿 | loader 排除当前源 slug；proposal_mode 仅通过显式 `allow_empty_index` fixture 才允许空 wiki_references → SC-7 golden 零回归；洗稿拒由 SC-9 独立注入重复索引验 |
| vault loader 让 proposal_mode 测试依赖真 vault（破坏 hermetic） | loader 可 monkeypatch；测试一律注入受控索引，不读真 vault；生产空/错索引写 ledger 并 hold/reject，只有 first-run/bootstrap/test allow 才空索引通过 |
| frontmatter 增键触发 lint P1 | 仅加在 candidate 阶段；必要时本 step 内补 lint allowlist，不扩到判分 |

## 10. Assumptions

- 混合归 procedural 是 spec FR-002 从严要求；lane 分类用 gemini-flash 轻判（裁决仍 gpt-5.4，符合 P7）；旧调用兼容靠形参默认值；proposal_mode 回放是 procedural 零回归锚点。
- vault loader 读 `wiki/concepts/*.md` slug、`wiki/**/*.md` TLDR、`<staging>/pending/concept-backlinks.jsonl`（promote.py:846 写入处）经 `loops/paths` 解析路径，纯文件 IO 无 LLM；loader 返回 typed status。生产 `configured_but_empty/load_error` 必须 ledger 可见并 hold/reject；只有 `first_run_no_index` 或显式 bootstrap/test `allow_empty_index` 才空索引默认 pass，避免洗稿在索引异常时静默放行。
- A3-1 的「lane-aware extract 改判」与 SC「concept H1==0 仍 extract success」属 step-3（本 step 只让 lane 过界、行为不变）；本 step 的 SC-8 只验过界与形参就位。
