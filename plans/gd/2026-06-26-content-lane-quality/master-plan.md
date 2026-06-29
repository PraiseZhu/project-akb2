# 循环1 内容类型分流标准（包 A 改标准） v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md v1.2.0 + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-master-plan

日期：2026-06-26 ｜ 状态：draft（多 agent planning dispatch 产出：2 child_planner + 主 agent 合并）｜ 负责人：Claude

## 1. 目标链

```text
PROJECT_GOAL: 让循环1 从事实上的命令清单库变成能同时收录概念型与操作型知识的概念图谱知识库（spec: specs/content-lane-quality/spec.md）
CHAIN_GOAL:   把宪法 v1.2.0 认可的「完整性地板（硬 pass/fail）+ 车道感知加权质量分（过阈不排序）」实现为循环1 判定代码（原则 6/8）
PHASE_GOAL:   产出并实现包 A『改标准』——车道地基 / 量规外置 / concept 判定三步，使一篇纯概念内容能走完循环1 判定段达标，且操作内容零回归
```

## 2. Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：`车道分流决定性（concept/procedural + 混合归 procedural + LLM 不可用 fail-closed）; lane 真穿抽取子进程边界（A3-1，否则概念正样本 extract 阶段被拒）; 完整性地板四成员齐全且不可补偿（锚定/诚实/F3/F4 都有硬门、地板 fail 直接拒加权分不算——A3-2）; F4 消费结构字段不反解 H3 标签（A3-5）; F3 两层（确定性锚定 + 异族 grader entailment 拒 poisoned-trace——Decision-1）; 洗稿生产链真拒（vault wiki_references 接进生产 novelty——Decision-2）; 图谱 resolve 生产真传 known/pending + 纯代码硬证（A3-8）; 旧 procedural 行为零回归; 标准与代码分离（改量规文件即改判分 + grader 维名动态派生不硬编码——A3-6）`
- Domain-specific notes：本包是「让概念内容能入库」的核心改造，最大风险是 concept 车道退化成全绿后门 + 地板被加权分补偿——靠 step-3 的地板四成员 early-return（消费结构字段）+ F3 异族 entailment 硬门 + 对抗假阳性 fixture 钉死。round-3 真异族 Codex 戳穿前两轮同族审漏掉的核心设计洞（lane 没穿子进程 / 地板只实现 F4 / F4 可数标签糊编 / F3 只验引用 / 图谱生产恒不 resolve / 维名硬编码），已连贯重做（见 dispatch/merge-report §6）。

## 3. 成功标准（SC，包级，对齐 spec SC-001~005 + 两个范围决策）

- [ ] SC-1（概念入库，spec SC-001）：一篇纯概念 fixture（0 命令、机制清晰）走完 admission→compose→self-gate→grader 达 proposal_passed。verify: `pytest tests/quality/test_concept_judgment_e2e.py -k concept_positive -q` → passed
- [ ] SC-2（操作零回归，spec SC-002）：既有 procedural 黄金样张重跑达标、判定等价。verify: `pytest -m proposal_mode -q && pytest -m 'not integration' -q` → 均 passed 不减
- [ ] SC-3（负样本必拒，spec SC-003）：机制糊清单 / 无据读后感 / 洗稿三类各被对应判定拦下。verify: `pytest tests/quality/test_concept_judgment_e2e.py -k negatives -q` → passed
- [ ] SC-4（假阳性拒，spec SC-004）：对抗样本（踩线 F4 + 堆辅助分）被拒、加权分未补偿。verify: `pytest tests/quality/test_integrity_floor.py -k adversarial -q` → passed
- [ ] SC-5（标准与代码分离，spec SC-005）：改量规文件 weight/threshold 即改判分，不改判定代码。verify: `pytest tests/test_grader_rubric_external.py -k rubric_change_changes_scoring -q` → passed
- [ ] SC-6（洗稿在生产链真被拦，Decision-2）：生产 runner 准入段装载 vault wiki_references 喂 novelty，洗稿在生产路径被拦（非仅 fixture 手传 wiki_references）；生产索引 `configured_but_empty/load_error` 必须 ledger 可见并 hold/reject，不静默放行；golden 样张仅在显式 `allow_empty_index` fixture 下空索引仍过（零回归）。verify: `pytest tests/test_admission_novelty_wiring.py -k "rewrite_rejected_in_runner_path or production_empty_index_holds_or_rejects or empty_index_preserves_golden_with_explicit_allow_empty" -q` → passed（step-1 SC-9）
- [ ] SC-7（F3 推导成立由异族 grader 硬判，Decision-1）：poisoned-trace（挂真实但与解读无关的 anchor）被异族 grader(GPT-5.4) entailment 地板拒，生产模型 deepseek 不自证；隔离 canary 必须证明模型族为 PRIMARY_GRADER_MODEL 且 `interpretation_entailment=false`。verify: `pytest tests/quality/test_interpretation_traceability.py -k "poisoned_trace or entailment or poisoned_trace_real_canary or entailment_model_is_gpt54" -q` → passed（step-3 SC-3c 层②；real canary 不可用时 fail-closed/skip-not-run 不计 SC-7 pass）

## 4. 非目标

- 不做 lane-aware repair（属条件包 C，看收口结果）；不做抓取层/语料修复（属包 B）；不新建概念黄金样张（沿用 addy-osmani）；不做升星聚合；不碰 briefing/**。

## 5. Step 拆分

| Step | 名称 | owned_paths（计划文件） | blocked_by | can_parallel_with | 主要 SC |
|------|------|------------------------|-----------|-------------------|---------|
| 1 | 车道地基（+抽取子进程过界 A3-1 +准入 novelty 接线 Decision-2） | step-1-lane-foundation.md | — | — | SC-1,SC-2,SC-6 |
| 2 | 量规外置（+SC-1 exact-set A3-7 +concept 维名逐字 A3-6 前置） | step-2-rubric-externalize.md | step-1 | — | SC-5 |
| 3 | concept 判定核心（+地板四成员 A3-2 +结构机制字段 A3-5 +F3 两层 Decision-1 +图谱 loader A3-8 +维名动态派生 A3-6） | step-3-concept-judgment.md | step-1, step-2 | — | SC-1,SC-3,SC-4,SC-7 |

> 注：step-1/step-2 的「计划起草」可并行（child_planner 已并行起草，owned 的是不同 plan .md 文件，无重叠）；但**执行期合并为单一 execution packet** `content-lane-quality-execution`，内部按 checkpoint 串行执行 step-1 → step-2 → step-3。三份 step 文档保留为章节化 checkpoint 规格，不作为互相独立的 §6 execution task packet；这样避免 `grader.py`/`distill.py`/`runner.py` 跨 packet owned_paths 重叠，同时保留全串行落地顺序。

## 5a. Dispatch Map / Wave Contract（MANDATORY）

```
DISPATCH_MAP_PATH: plans/gd/2026-06-26-content-lane-quality/dispatch/dispatch_map.json
VALIDATE_CMD: python3 /Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-dispatch.py plans/gd/2026-06-26-content-lane-quality/dispatch/dispatch_map.json
```

本 dispatch map 用于 **planning 阶段**（2 child_planner 起草 step 计划，已 exit 0 验证通过）。**执行阶段**（/gd execute）需另出 execution dispatch map，且只创建一个权威 execution packet：`content-lane-quality-execution`。该 packet 的 owned_paths 是 step-1/2/3 执行期路径并集（`admission/distill/distill_worker/checks_structural/grader/repair/runner/integrity_floor/weighted_score.py` + `rubric.yaml`/测试/fixtures），内部 checkpoint 顺序为 step-1 → step-2 → step-3，执行期 `max_parallel_execution=1`。执行 dispatch map 不再把三 step 拆成三个互相重叠 owned_paths 的 execution packet；若另有独立验证/审查 packet，其 owned_paths 必须与该 execution packet 两两不重叠。

> **A3-9 / A-7 修正**：planning dispatch_map 的 `max_parallel_execution` 已由 2 改 **1**（与全串行执行契约一致；gd-validate-dispatch.py 要求该字段为整数 [1,2]，故用 1 而非「not_applicable」字符串）。执行阶段另出的 execution dispatch map **MUST** 断言 `max_parallel_execution==1`，且权威执行形态为单 packet 内部 checkpoint 串行，不再制造跨 packet shared owned_paths。

### Authority Layer（planning snapshot vs execute verification）

`dispatch/t1-foundations-proposal.md` 与 `dispatch/t2-concept-proposal.md` 是 round-1 planning 快照，受 `stage-dispatch-ledger.json` hash 锚定，**不得编辑也不得作为执行验收源**。执行验收唯一权威为本 master 的 SC、各 step 文档的 SC/VERIFY、以及后续 `/gd execute` 生成的 execution dispatch map；proposal 内历史 verify 字段只保留审计用途。

### Wave Matrix（planning 阶段，已执行）

| Wave | Tracks（同 wave 可并行） | 并行前提验证 |
|------|------------------------|-------------|
| 1 | t1-foundations, t2-concept（child_agent_count=2, max_parallel=2） | can_parallel_with 双向声明 ✓；owned_paths（step 计划文件）不重叠 ✓；blocked_by 空 ✓；gd-validate-dispatch.py exit 0 ✓ |

## 6. 边界

修改（执行期，跨 3 step 汇总）：`src/akb2/quality/{admission,distill,distill_worker,checks_structural,grader,repair,runner,integrity_floor,weighted_score}.py`（含 step-1 新增 `distill_worker.py` 改动——A3-1 抽取子进程携 lane）、`skillpack/quality/rubric/rubric.yaml`（新建）、`skillpack/quality/rubric/10-dimensions.md`（仅加注释）、`tests/**`（新增测试 + fixtures/concept/）、`docs/allowed-residuals.md`（仅需登记豁免时）。
vault 读（只读，经 `config/vault.toml`/`loops/paths`，符合宪法 P5 单向 vault→工程）：step-1 `_load_vault_concept_index()` 读 `<akb2_root>/wiki/**`（concept slug + TLDR 摘要）、`<staging>/pending/concept-backlinks.jsonl`（known_concepts/pending_queue/wiki_references），供准入 novelty（Decision-2）与图谱 resolve（A3-8）；**不向 vault 写任何文件**。
不修改：`src/akb2/briefing/**`、`skillpack/quality/rubric/10-dimensions.md` 维度文本、`docs/constitution.md`（已 v1.2.0）、旧 /rev artifacts、`/Users/praise/.claude/**`、vault 写路径。

## 7. 风险与防护

| 风险 | 防护 |
|------|------|
| concept 车道退化成全绿后门 | universal 门全保留；step-3 多类负样本 + 对抗样本拒（SC-3/SC-4） |
| 地板被加权分补偿 | runner 先地板 early-return，任一地板成员 fail 加权分不算（step-3 SC-3b 断言日志） |
| 地板只实现 F4、锚定/诚实无硬门（A3-2） | step-3 显式 claim_anchor_rate_floor + evidence_honesty_floor 二值硬门（SC-3h） |
| F4 数 H3 标签糊编假机制（A3-5） | F4 只消费 mechanism_components/causal_edges 结构字段不反解（step-3 SC-3b） |
| concept 正样本在 extract 阶段被 H1 门拒、走不到 compose（A3-1） | step-1 把 lane 穿过抽取子进程 + step-3 lane-aware `_validate_extract_output`（concept 改判 H3≥3，真实 worker 路径验 SC-3a/SC-8） |
| F3 退化成只数引用不验推导（Decision-1） | F3 两层：确定性 traces_to 锚定 + 异族 grader entailment 硬判拒 poisoned-trace（SC-7/step-3 SC-3c） |
| 洗稿在生产链恒默认 pass（Decision-2） | step-1 把 vault wiki_references 接进生产准入 novelty；loader typed status 遇生产空/错索引必须 ledger 可见并 hold/reject，只有 `first_run_no_index` 或显式 bootstrap/test allow 才允许空索引默认 pass（SC-6/step-1 SC-9） |
| 图谱 resolve 生产恒不 resolve 被 fixture 掩盖（A3-8） | check_a4 增 known/pending 形参；runner 第二道 self-gate 与 compose 内第一道 self-gate 都必须收到 step-1 loader 实参；monkeypatch-raise 纯代码硬证（step-3 SC-3e） |
| procedural 被 lane 改动破坏 | 所有 lane 形参默认 procedural；SC-2 golden_replay 零回归 |
| 标准外置后 grader 静默降级 gemini 假 0 缺陷 | step-2 保留 gpt-5.4 canary 实跑守卫 |
| grader 维名硬编码破坏外置（A3-6） | RUBRIC_DIMENSIONS_BY_LANE 从 _load_rubric_spec 动态派生；step-3 SC-3f 用 AST 字符串常量负断言 + tmp rubric 改名行为测试防漏检 |

## 8. 测试计划

```bash
test -f plans/gd/2026-06-26-content-lane-quality/step-1-lane-foundation.md
test -f plans/gd/2026-06-26-content-lane-quality/step-2-rubric-externalize.md
test -f plans/gd/2026-06-26-content-lane-quality/step-3-concept-judgment.md
python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-dispatch.py plans/gd/2026-06-26-content-lane-quality/dispatch/dispatch_map.json
pytest tests/quality/test_concept_judgment_e2e.py -q   # 执行期
pytest -m 'not integration' -q                          # 零回归
```

## 9. Assumptions

- 沿用 addy-osmani 黄金样张锚定（不另立 concept 样张，用户 2026-06-26 决定）；宪法 v1.2.0 已认可分层模型（原则 6/8 已改）；权重/阈值是校准产物落量规文件（spec FR-010）；repair 收敛（lane-aware repair）属条件包 C，看收口结果；本包验证用 fixture，绕开实时抓取（spec Assumptions）。
- **round-3/4 真异族 Codex 审后两个范围决策**：①F3 拆两层——确定性锚定地板（checks_structural，pre-grader 二值）+ 异族 grader(GPT-5.4) entailment 硬门（拒 poisoned-trace，生产模型 deepseek 不自证，P7），并以 canary 证明 GPT-5.4 真判 poisoned-trace false；②洗稿接线——把生产 vault 概念/wiki 摘要传进 `run_admission` 的 novelty（动 admission 生产路径，step-1 影响面内），loader 空/错索引必须 typed status + ledger 可见 + hold/reject，使洗稿在生产链真被拦。两决策已落 step-1（②）/step-3（①）。
- **A3-* 设计洞修订归属**：A3-1/Decision-2 → step-1；A3-7/A3-6 前置 → step-2；A3-2/A3-5/A3-6/A3-8/Decision-1 → step-3；A3-9 → dispatch_map + 本 §5a；A3-10 → dispatch 子工件。
