# Merge Report — content-lane-quality-plan-a（planning 阶段）

日期：2026-06-26 ｜ 主 agent：Claude（lead controller）

## 1. Dispatch 概况

- dispatch_id: `content-lane-quality-plan-a`
- 阶段：plan（多 agent planning dispatch）
- capability probe: `CHILD_AGENT_CAPABILITY: available`（Agent/Task 工具在工具列表）
- child_agent_count: 2 ｜ max_parallel: 2 ｜ wave: w1（t1-foundations ∥ t2-concept）

## 2. Child 草稿与校验

| track | child job | 产出 | proposal 校验 |
|-------|-----------|------|--------------|
| t1-foundations | step-1 车道地基 + step-2 量规外置 step plan 草稿 + task packet | step-1-lane-foundation.md / step-2-rubric-externalize.md | `gd-validate-child-proposal.py` exit 0 ✓ |
| t2-concept | step-3 concept 判定核心 step plan 草稿 + task packet | step-3-concept-judgment.md | `gd-validate-child-proposal.py` exit 0 ✓ |

## 3. Merge Gates（dispatch-rules §4）

| 门 | 结果 | 说明 |
|----|------|------|
| G1 child 输出格式合规 | PASS | 两份 proposal 均过 gd-validate-child-proposal.py（schema 合规） |
| G2 owned_paths 不越界/不重叠 | PASS | **planning 提案 owned = 计划 .md only**（t1 owns step-1/step-2 计划文件；t2 owns step-3 计划文件——round-3 A3-10 已把 t2 提案误塞的执行期 src/tests 路径收回，消除与 required_context 的 distill/checks_structural/grader/runner.py 重叠）；**执行期代码 owned_paths**（step-1: admission/distill/**distill_worker**/grader/repair/runner + 新增测试；step-2: rubric.yaml/grader；step-3: distill/checks_structural/grader/runner/integrity_floor/weighted_score + 新建）在三步 §7 task packet，跨步在 distill/grader/runner.py 有交集 → 已声明 step-1→2→3 单链 blocked_by，全串行不并发，无并行越界 |
| G3 每 SC 绑可执行 verify | PASS | step-1 SC-1..7 / step-2 SC-1..6 / step-3 SC-3a..g 均绑 command/test/assertion |
| G4 task packet 自包含 | PASS | 三 packet 均含完整目标链 + required_context + owned/forbidden，无「见上文」 |
| G5 wave 全完才合并 | PASS | w1 两 child 均 completed 后主 agent 合并 |
| G6 reviewer 冲突仲裁 | N/A | 两 child 无冲突（owned 计划文件不重叠；step-3 显式 blocked_by step-1/step-2，依赖关系一致） |

## 4. 合并结果

- 主 agent 已将 2 份 child 草稿合并落盘为：master-plan.md + step-1-lane-foundation.md + step-2-rubric-externalize.md + step-3-concept-judgment.md（各含 task packet）。
- final_decision: **APPROVED**（planning 阶段 closure-eligible）
- blocking_buckets: []
- 一处对齐：t2 提到的旧 `2026-06-25-loop1-content-lane-refactor/plan.md`（5 阶段二值版）已被本包（加权+地板模型）取代，旧 plan 留档不动；本包为权威。

## 5. 残余说明

- 执行阶段（/gd execute）需另出 execution dispatch map。执行波形以 master §5/§5a 为准——**全串行 wave1=step-1，wave2=step-2(blocked_by step-1)，wave3=step-3(blocked_by step-1+2)，max_parallel=1**（三步代码 owned 在 grader/distill/runner.py 重叠，并行会并发写同文件 + step-2 消费 step-1 未就绪的 content_lane 形参；round-2 修正旧「step-1 ∥ step-2」并行表述）。**round-3 A3-9：本 planning dispatch_map.json 的 `max_parallel_execution` 已由 2 改 1，与全串行执行契约一致；execution dispatch map MUST 断言 `max_parallel_execution==1` + step-1→2→3 单链 blocked_by。**
- step-3 的 integrity_floor.py/weighted_score.py 是否独立成文件 vs 并入 grader，执行期按 import 环依赖定（不影响判定语义与 SC）。

## 6. round-3 真异族 Codex 审后修订（2026-06-26）

前两轮 Claude 同族审只逮到传播/一致性问题；round-3 GPT-5.5 戳穿核心设计洞。本包据此连贯重做（非逐行打补丁）：

- **A3-1**（step-1+3）：content_lane 穿过抽取子进程边界（`distill_worker.py` 入 step-1 owned + JSON payload）；step-3 lane-aware `_validate_extract_output`（concept 改判 H3≥3，真实 worker 路径验）。
- **A3-2**（step-3）：完整性地板补 `claim_anchor_rate_floor` + `evidence_honesty_floor` 二值 A 层硬门（不止 F4）。
- **A3-5**（step-3）：distill schema 加 `mechanism_components`/`causal_edges` 结构字段，F4 只消费结构字段不反解 H3 标签。
- **A3-6**（step-2 前置 + step-3）：`RUBRIC_DIMENSIONS_BY_LANE` 从 `_load_rubric_spec()` 动态派生，concept 维名逐字含 `(H3)`/`(H2)`，不硬编码。
- **A3-7**（step-2）：SC-1 改 exact-set 比较（旧无空格 How 名永不相交=假绿）。
- **A3-8**（step-1 loader + step-3 wire）：图谱 resolve 生产路径真传 known_concepts/pending_queue（runner :672），monkeypatch-raise 纯代码硬证（弃「不含 route_llm 字符串」弱检查）。
- **A3-9**（dispatch_map + master §5a）：max_parallel_execution 2→1。
- **A3-10**（本文件 + t1/t2 提案）：提案 task_packets 声明为 planning 摘要（非 §6 执行 packet）；t2 owned 收回为 plan-only 消重叠。
- **Decision-1**（step-2 floor 标签 + step-3）：F3 两层——确定性锚定地板 + 异族 grader entailment 硬门拒 poisoned-trace。
- **Decision-2**（step-1）：vault wiki_references 接进生产 `run_admission` novelty，洗稿生产链真拒。
