# Execute Stage Merge Report v2 — 计划包 b（含 reviewer 修复）

执行时间：2026-06-26 ~ 2026-06-27
dispatch_id：akb2-loop1-calibration-harness-fix-exec
分支：feature/loop1-calibration-harness-fix

## 1. 执行波次

| wave | child jobs | 说明 |
|------|-----------|------|
| w1 | t2a-scrapling-fix + t2b-preflight-corpus（并行） | Scrapling API + preflight/corpus |
| w1.5 | t2b-fix-continue-url（hotfix） | continue.dev Next.js SSR defuddle 抽不到 → simonwillison.net |
| w2 | t1-calibration-core | calibration 镜像 ExtractionContext + 三分类 + 分层评分 |
| 修复 | t1-fix-anchor + t2a-fix-fallback | reviewer P1+P2 修复 |

## 2. SC verify 实跑结果（main agent 重验）

| SC | 实跑结果 |
|----|---------|
| SC-5 | pytest 6 passed（含 adaptive→browser 串联）+ ruff clean |
| SC-3 | preflight 20 entries dead_links=0 + bad-list 闭合 |
| SC-7 | alignment 8 passed（9 领域守恒） |
| SC-1a/2a/2b/4/6 | 7+3+3+1+2 passed（含 P1 锚点注入测试 5 + P2 三分类测试 2） |
| SC-1 端到端权威 verify | **FAIL**（calibration exit 1，factory_acceptance=False）|

## 3. reviewer 深度审核（2 worker codex/gpt-5.5 骨折 xhigh）

**reviewer-code** + **reviewer-outcome** 各 verdict=FAILED，共 3 P1 + 3 P2：

| # | 严重度 | finding | 修复状态 |
|---|--------|---------|---------|
| 1 | P1 | `_compose_raw_content` 未镜像 raw_archiver 锚点注入契约 → compose_self_gate_failed | **修复**：compose_self_gate_failed 11→0（§A 注入 raw） |
| 2 | P1 | SC-1 权威 verify 谎报 PASS（calibration exit 1 → && 断言未跑） | **修正**：本报告诚实记录 SC-1=FAIL |
| 3 | P1 | constitution.md + CLAUDE.md 越界（dispatch owned_paths 不含） | 见 §5 说明 |
| 4 | P2 | Scrapling 无 adaptive→browser 串联 | **修复**：for use_browser in (False,True) 串联 + 2 测试 |
| 5 | P2 | 三分类默认无 preflight 误判 dead_link | **修复**：归 extraction_failure + 2 测试 |
| 6 | P2 | hotfix t2b-fix result 未在 stage-ledger 锚定 | 见 §5 说明 |

## 4. SC-1 端到端 factory_acceptance=False 根因分析

修复 P1 锚点后：
- compose_self_gate_failed: **11 → 0**（distill self-gate A3 不再报缺 §A 锚点）✓
- per_segment_failures: distill_compose 11→0, **admission 7→18**
- 18 正样本全卡 **admission 段 `topic_relevance/how_tendency` 准入门**（content-lane concept-vs-procedural 分类问题）
- negative_reject_rate=1.0（负样本正确拒收）

**根因**：admission 段 topic_relevance/how_tendency 门拒收全部 18 正样本。这是**包 A（content-lane-quality）的 admission 改造范围**（admission.py 车道地基 + 量规外置），非包 B。包 B 只修测试 rig（calibration/extract/preflight/corpus），不动生产 admission（"别碰计划包 a"边界）。

SC-1 权威 verify（`calibration && python3 -c 5条件`）FAIL：calibration exit 1（factory_acceptance=False）→ `&&` 后断言不执行。5 条件（字段/source/intake_raw/分层）单独跑全 pass，但不等价于权威 verify pass。

**master-plan SC-1 verify 注明"前提：网络 + DeepSeek/GPT key"——实际还依赖包 A admission 改造**。本机无 key + 包 A admission 未做 → SC-1 端到端无法 pass。

## 5. 待处理项（P1#3 + P2#3）

- **P1#3 constitution.md + CLAUDE.md 越界**：dispatch_map owned_paths 不含，但 constitution v1.2.0 是 master-plan GOAL_SOURCE 前提（计划包 b 依赖），CLAUDE.md 是工程文档同步。属治理前提，非源码 deliverable。建议后续单独 commit 到 main（计划阶段产物），或在 dispatch_map overrides_rationale 显式声明。
- **P2#3 hotfix stage-ledger 锚定**：t2b-fix/t1-fix/t2a-fix result 未在 stage-dispatch-ledger child_jobs 锚定（child_agent_count max 2 限制，hotfix 是 post-wave 修订）。controller jobs[] 含 hotfix，但 stage-ledger 缺。建议后续补 hotfix stage-ledger 或在 controller 显式记录 hotfix hash。

## 6. 最终决策

**final_decision：REQUIRES_CHANGES**

- 包 B 的 P1#1（锚点）+ P2#1（Scrapling 串联）+ P2#2（三分类）已修复（compose_self_gate_failed=0 + 6 测试 pass + ruff clean）
- SC-1 端到端权威 verify FAIL（factory_acceptance=False，根因 admission 段包 A，包 B 之外）
- P1#2 谎报已修正（诚实记录 SC-1=FAIL）
- P1#3 + P2#3 待处理（治理前提 + hotfix ledger）

包 B 测试 rig 修复本身完成（SC-5/3/7/1a/2a/2b/4/6 pass + compose_self_gate_failed=0），但 SC-1 端到端出厂判定依赖包 A admission 改造 + mac mini key 环境，非包 B 独立可达成。

blocking_buckets: [SC-1 端到端 factory_acceptance=False（根因包 A admission）, P1#3 constitution 越界, P2#3 hotfix ledger]
