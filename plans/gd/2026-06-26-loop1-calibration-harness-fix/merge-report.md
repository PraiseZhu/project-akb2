# Merge Report — akb2-loop1-calibration-harness-fix (stage=plan)

GD_STANDARD: Project GD/prompts/gd-review-standard.md
日期：2026-06-26
主 agent 角色：dispatch + merge + final gate（不执行实质规划工作）

---

## 1. Dispatch 概要

- dispatch_id：`akb2-loop1-calibration-harness-fix`
- stage：`plan`
- CHILD_AGENT_CAPABILITY：`available`（probe_method=tool_list_inspection；evidence=`Agent 工具在当前工具列表`；由两次真实 child dispatch 返回结果作为 runtime proof）
- child_agent_count：2
- max_parallel：2（wave w1 内 t1/t2 两个 child_planner 并行）
- dispatch_map.json：`gd-validate-dispatch.py` exit 0（tracks=2, waves=1）

## 2. Child Jobs

| job_id | track | proposal 文件 | sha256 | gd-validate-child-proposal | status |
|--------|-------|--------------|--------|----------------------------|--------|
| job-t1-calibration-core | t1-calibration-core | child-proposals/t1-calibration-core-proposal.md | acf79c68a7d5728aff8dc77e5665122e558a2766e4d00a210b62e202541603c6 | 校验通过（proposal_id=...-t1-calibration-core-cp01） | completed |
| job-t2-intake-corpus | t2-intake-corpus | child-proposals/t2-intake-corpus-proposal.md | ac1914a4ad81d8fc995dd45462591c12fc5ae4b8b01521059224d8fdd4142423 | 校验通过（proposal_id=...-t2-intake-corpus-cp01） | completed |

## 3. Merge Gates（dispatch-rules §4）

| 门 | 条件 | 结果 |
|----|------|------|
| G1 | child 输出格式合规（proposal block + step/task-packet 草稿） | PASS（两份 proposal 均过 gd-validate-child-proposal.py）|
| G2 | child 提议 task packet owned_paths 全在本 lane 边界内，不越界 | PASS（t1 owned calibration.py+test；t2 owned extract.py/preflight.py/evals/test；`config/main_sources.yaml` 仅 required_context 只读、非 owned，round-2 P2 + B3-4；无越界包 A）|
| G3 | 提议无裸 VERDICT/GD_REVIEW_DECISION 字段 | PASS（计划工件用 SC+verify，无 review verdict 字段）|
| G4 | 每条 SC 绑定可执行 verify | PASS（7 条 SC 全绑命令/断言/测试）|
| G5 | 并行 wave w1 两 child 均返回后再合并 | PASS（两 child 均 output_status=completed 后合并）|
| G6 | 冲突时主 agent 写仲裁理由 | 见 §4 |

## 4. 跨 lane 冲突仲裁（G6）

识别到两处潜在重叠，主 agent 仲裁如下（surface conflict，不平均化）：

1. **extract.py 归属**：item 1（诊断字段）一度看似需要改 extract.py 加 http_status/final_url/content_type。仲裁：`ExtractResult`（extract.py:78-92）**已含** method/word_count/metadata_complete/extraction_success/error，诊断字段全部可由 t1 在 calibration.py 报告侧从 ExtractResult 派生；HTTP 层三字段改由 t2 的 preflight 产出、报告侧读取合并。→ **extract.py 由 t2 独占（仅 Scrapling 调用点修复）**，t1 不碰 extract.py。两 lane owned_paths 无重叠（gd-validate-dispatch.py 已证）。

2. **corpus 元数据契约**：t1 构造 `ExtractionContext` 所需 per-entry feed 元数据自然存于 corpus.yaml（t2 拥有）。仲裁：corpus per-entry feed 元数据扩展归 **t2（t2b-preflight-corpus）**；t1 的 `akb2-calibration-harness-diagnostics-t1` packet 在 execute DAG `blocked_by` t2b。文件所有权清晰（t2 写 corpus，t1 只读字段契约），数据单向流动。

无 reviewer verdict 冲突（本阶段为 planning merge，非 review）。

## 5. 合并产物

- master-plan.md（7 SC + step 拆分 + execution Wave Matrix）
- step-1-calibration-core.md（lane t1）
- step-2-intake-corpus.md（lane t2，含 t2a/t2b 两 packet）
- task-packets/akb2-calibration-harness-diagnostics-t1.md
- task-packets/t2a-scrapling-fix.md
- task-packets/t2b-preflight-corpus.md

## 6. Final Decision

```text
final_decision: APPROVED
blocking_buckets: []
```

理由：两 child proposal 均 validator 通过、owned_paths 无重叠、anti-fill 合规（无 generic 泛化动词作唯一内容）、7 条 SC 全绑可执行 verify、跨 lane 依赖已在 DAG 显式声明。plan suite 三类工件（master/step/task-packet）齐备。

---

## 7. Round-3 异族 Codex 审修订（2026-06-26）

round-3 真异族 Codex(GPT-5.5) 审戳穿前两轮 Claude 同族审未逮的 7 处设计洞（B3-1~B3-7），已连贯重做受影响部分：

| finding | 实质 | 落点 |
|---------|------|------|
| B3-1 [P1] | live/fixture 评分混池：`calibration.py:362-365` positive_pass_rate 混算、`:447-450` factory 直读混池率 → fixture 灌绿掩盖 live | master/step-1/t1packet SC-1 + step-1 新增 Step.5（_compute_statistics 分层 + factory AND live 下限 LIVE_POSITIVE_QUOTA/PASS_FLOOR/live_positive_intake_raw_failures==0）；master NON_GOAL 改「不放宽阈值、新增 live 下限纯收紧」 |
| B3-2 [P1] | SC-1 断言只查 2 键 + 读陈旧工件 + live 地板 `source!='fixture'` | SC-1 verify 改：先重生成 → 全 7 键 superset + `source in {fixture,live}` + 地板 `source=='live'`，同步 master/step-1/t1packet/dispatch_map |
| B3-3 [P1] | SC-3 bad-list 强断言只在 step、未落 packet/dispatch；soft-404 漏检 | bad-list `assert not bad` 闭合落 t2b packet §7 + dispatch_map t2 SC-3 + master §8；preflight 新增 `soft_404`（title/body pattern 或域漂移） |
| B3-4 [P1] | main_sources 仍被 master step表/dispatch_map/merge-report 当 owned | 三处移除 owned 归属（只读 required_context）；master §8 + dispatch 加一致性断言；本表上方 G2 已改 |
| B3-5 [P2] | 9 领域守恒 ≠ live 覆盖守恒 | SC-7 增 live 层代表性（每非豁免领域≥1 live + `fixture_only_domains` 具名 allowlist），落 step-2/t2b packet/dispatch |
| B3-6 [P2] | SC-2 退化 generic mock、丢 3 具名 URL 保证 | SC-2 拆 SC-2a（保留 mock 机制）+ SC-2b（crewai/aider/MCP 保持 live + 保存 HTML 注入 extract_content 真实路径 False→True），反转 round-2 M4 fixture 化 |
| B3-7 [P2] | SC-6 只验 4 字段（raw_archiver 实为 9 字段 fail-closed） | SC-6 改 import `raw_archiver.FRONTMATTER_FIELDS` 断言 `set(keys)>=set(FRONTMATTER_FIELDS)` 全 9 字段 |

**工件权威分层（杜绝 round-2 翻车点：只改 step 没传播）**：
- 权威（已逐条修订 + 互相一致 + 进执行验收链）：`master-plan.md` §3/§8、`step-1`/`step-2`、`task-packets/*.md` §7、`dispatch_map.json`（已重跑 `gd-validate-dispatch.py` exit 0）。
- 历史快照（round-1 规划事件记录，不随 round-3 SC 更新，从执行验收链排除，不编辑以保 hash 链与「原样落盘」诚实性）：`child-proposals/t1-*.md`、`child-proposals/t2-*.md`（其内 owned_paths/verify 反映 round-1，已被 dispatch_map + task-packet 取代；如 t2 proposal 内 `config/main_sources.yaml` owned 归属已失效，B3-4）；`controller-report.json`、`stage-dispatch-ledger.json`。
- **hash 链处置**：child-proposal 不编辑→其 sha256（acf79c… / ac1914…）在 §2 与 stage-dispatch-ledger 仍有效；本 merge-report.md 本体 round-3 已改→`stage-dispatch-ledger.main_agent_merge.merge_report_hash` 与 `controller-report.batch_ledgers[].hash` 已同步 re-stamp 指向当前内容（见两文件）。
