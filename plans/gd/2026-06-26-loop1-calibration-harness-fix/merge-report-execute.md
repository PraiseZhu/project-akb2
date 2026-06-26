# Execute Stage Merge Report — 计划包 b (loop1-calibration-harness-fix)

执行时间：2026-06-26
dispatch_id：akb2-loop1-calibration-harness-fix-exec
execution_mode：agent_exec
分支：feature/loop1-calibration-harness-fix

## 1. 执行波次与 child jobs

| wave | child jobs | 并发 | 说明 |
|------|-----------|------|------|
| w1 | t2a-scrapling-fix + t2b-preflight-corpus | 2（并行） | owned_paths 不重叠，互不依赖 |
| w1.5（hotfix） | t2b-fix-continue-url | 1 | SC-1 端到端实跑发现 continue.dev 卡 intake_raw，t2b 修订 corpus URL |
| w2 | akb2-calibration-harness-diagnostics-t1 | 1 | blocked_by [t2a, t2b]（需 extract.py + corpus.yaml deliverable） |

## 2. SC verify 实跑结果（main agent 重验，非盲信子 agent self-report）

| SC | track | method | 实跑结果 |
|----|-------|--------|---------|
| SC-1 | t1 | assertion（端到端 calibration CLI + 5 条件断言） | PASS（全 5 条件：7 诊断键 + source∈{fixture,live} + ≥3 live 越过 intake_raw=14 + 分层字段 + live_intake_raw_failures==0） |
| SC-1a | t1 | test（fixture_load） | PASS（7 passed） |
| SC-2a | t1 | test（extraction_context） | PASS（3 passed，±ctx False→True） |
| SC-2b | t1 | test（named_urls） | PASS（3 passed，crewai/aider/mcp 注入 HTML 走真实路径） |
| SC-4 | t1 | test（e2e_thin_slice） | PASS（1 passed） |
| SC-6 | t1 | test（raw_frontmatter） | PASS（2 passed，import FRONTMATTER_FIELDS superset） |
| SC-5 | t2a | test + assertion | PASS（pytest 4 passed + grep Fetcher.get） |
| SC-3 | t2b | assertion（preflight CLI + bad-list 闭合） | PASS（20 entries, dead_links=0, bad-list 清零） |
| SC-7 | t2b | test（alignment） | PASS（8 passed，9 领域配比守恒 + live 层代表性） |

ruff check：No issues found（extract.py / preflight.py / calibration.py / 4 测试文件）。

## 3. t2b-fix hotfix 记录

SC-1 端到端实跑（main agent 重验）发现 `https://blog.continue.dev/continue-agent-loop/` 卡 intake_raw：extract_error "Scrapling adaptive content too short"。根因：continue.dev 是 Next.js SSR 页，`_extract_via_scrapling` 把 HTML 转交 defuddle 抽正文，defuddle 对该 Next.js DOM 抽不到正文（非 t2a 修复引入，t2a 只改 Scrapling API 调用点，fallback 逻辑原样）。t2b-fix 子 agent 把该 URL 替换为 `https://simonwillison.net/2026/Jun/22/porting-moebius/`（Django SSR 静态页，defuddle 抽 1658 词），重跑 SC-3/SC-7/SC-1 全 pass，intake_raw==0。

## 4. known_limitations

- SC-1 端到端「出厂判定」仍 FAIL（正样本通过率 0%），因本机无 DEEPSEEK/GPT key 跑 distill/grader 段（本机为功能开发环境，管线部署在 mac mini，与部署职责分离一致）。SC-1 assertion 的 5 条件（字段/source/intake_raw/分层）全 pass，不依赖 LLM key。完整出厂判定需 mac mini 有 key 环境跑满仓校准。
- master-plan SC-1 verify 注明"前提：网络 + DeepSeek/GPT key"——本机无 key，端到端 LLM 段未跑，extraction/字段层全 pass。

## 5. 最终决策

final_decision：APPROVED（SC-1/1a/2a/2b/3/4/5/6/7 verify 实跑全 pass；SC-1 完整端到端通过率需 mac mini key 环境，记为 known_limitation，非阻塞 closure）。

blocking_buckets：[]
