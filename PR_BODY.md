# AKB2 CI 基线对齐：43 条 stale red 修复 + harvest 1 条残余保留 + p0 存量重排队

## 意图契约

<!-- pr-intent:start -->
目标: 两件收尾一起排：先把合进 main 后仍停在旧 proposal 口径的存量稿用既有 requeue CLI 再入队（名单从生产 quality-ledger 现算，账本现算 32 篇，不把 31 当常数）；再把 CI/本地 44 红里可修的 43 条按簇对齐到新主链路，harvest 1 条（test_skill_md_template_expansion）标残余保留红。
优先级: p0-ops-requeue-31 / p1-ci-stale-suite
非目标: 不改 CI 配置；不向 vault 写 .py/.sh；harvest 残余 tests/harvest/test_smoke_manual.py 不改绿；本轮不开 PR、不 mark ready、不 merge；不改 requeue.py / backfill_requeue.py / runner.py / ledger.py 生产语义
验收: 19 条 SC 全 verified（approve-exec ledger akb2-ci-requeue-20260815）；e2e 114 passed + TESTS_STRICT_OK、harvest 三视图干净；intent-check status ∈ {OK, REBUILT}；size/format 闸 --base 4a62c487a7610ed0fb5a67971903576680698b25，final 验收凭据绑定最终 HEAD（以 goal-dir 的 final-e2e-report.json / final-presubmit/* / final-verdict.json / final-gpt-review.json 落盘值为准，不在仓内文本硬编码 final SHA）
<!-- pr-intent:end -->

## 变更说明

基线：#80（4a62c487a7610ed0fb5a67971903576680698b25）合入 main 后，主链路已按新口径收敛（准入单次判定、rubric 现用 5 维、runner 不再调用 run_repair_loop），但 CI 与本地仍有 44 条测试红在对齐旧口径。本 PR 分两部分收尾：

### p0 — 存量稿生产重排队（p0-ops-requeue-31）

- 从生产 `quality-ledger.jsonl` 现算待重排队名单：2026-08-05 后 finalize 且 mode≠active / 未写 wiki active、vault/raw 仍在的存量稿，**账本现算 32 篇**（`list_sha256=71764ef1…`，round_id `p0-20260815T190840Z-1488e9a2`），不把 31 当常数。
- 用既有 `python3 -m akb2.quality.requeue` 在 Praise-Mini #80（4a62c487）上按白名单分批入队（4 批：10/10/10/2，单批 ≤10，带 `--reason` 与显式 `--vault-root`）。
- 只往账本 append `requeue` 事件：既有 CLI 无 `run_promote`、无 wiki 写入，批次收据 `wrote_wiki=false`。
- 语义护栏：在 4a62c487 detached worktree 上跑既有 `tests/quality/test_requeue.py`（11 passed，无 skipped/xfailed/deselected）。
- 运维侧：Praise-Mini `com.akb2.main-pipeline` bootout + bootstrap + launchctl print 成功，program 绑定 4a62c487 检出。

### p1 — 43 条可修基线红对齐 + 1 条残余（p1-ci-stale-suite）

按簇对齐到新主链路，只改测试与 fixture，生产代码语义不动：

| 簇 | 内容 |
|----|------|
| admission 统一 fixture | conftest 三次 `{pass,reason}` mock 改单次 `_ADMISSION_UNIFIED_SCHEMA`；late-how 窗口断言消费单次判定 |
| novelty 确定性 | 洗稿负样本走确定性 slug/url 预筛，不再期望 novelty LLM 拒因；exact 重复仍在 LLM 之前拒 |
| rubric 现用 5 维 | concept 加权分 / 冷启动冻结 / canonical 维名测试改到 rubric.yaml 现用 5 维；附可逆旧 7 维 mutation 验证（预测红集恰 1 条，还原再绿） |
| repair 测试退役 | 删除/改写把 `run_repair_loop` 当绿路径的 4 个测试文件；runner 不调用该符号由 `TestRepairPathAbsent` 锁住 |
| grader 辅助对齐 | fallback / antifill 测试对齐现行 API 与挂起符号 |
| launchctl 假 reader | env-parity CLI 在无 launchctl 主机走注入假 reader，Linux 0 skip / 0 deselected 地绿 |

**harvest 残余**：`tests/harvest/test_smoke_manual.py::test_skill_md_template_expansion` 是 44 红里接受保留的 1 条残余，本 PR 不改 harvest 测试或生产语义（worktree / staged / base...HEAD 三视图均干净）。

## 验收证据

- 19 条 SC 全部 verified（approve-exec ledger `akb2-ci-requeue-20260815`）
- e2e：strict pytest 簇 114 passed（`TESTS_STRICT_OK`，先在 wave 集成 tip e3de4f9 上取得，final HEAD 复跑见 goal-dir final-e2e-report.json）；g5 退役后 `tests/quality/test_repair_loop.py` 缺失属预期（rc=4 已记录）；harvest 三视图干净
- intent-check：PR body marker 区块与 `.pr-intent.md` 工作副本 digest 一致（status ∈ {OK, REBUILT}）
- size / format 闸：`--base 4a62c487a7610ed0fb5a67971903576680698b25`（本 run 口径；本地 origin/main 已漂移，对其跑 size 闸会误计堆叠 diff）
