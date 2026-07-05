# F22 intake 每源每轮限额 + 轮转公平 + 空死因诊断报告

工单 F22:intake 发现侧加"每源每轮限额"(修单源垄断)+ 顺带查 3 篇 admission failed 空死因。分支 `feat/intake-per-source-cap`(worktree `_akb2-source-cap-wt`,从 origin/main e0905fe 开)。未 push、未建 PR、未装载。

## 1. 限额+轮转设计(挂点 file:line/常量/取舍)

### 现状根因(账本证据)

- 账本:今天 4 轮抓 78 篇,**78/78 全 openai-blog**;openai-blog 历史存量大排 main_sources.yaml 前面,每轮 30 名额全被它垄断;75/75 被 admission 拒(闸门正确,料不行)。
- 代码根因:`orchestrator.py:333 for source in sources`(按 yaml 顺序遍历)+ `:377 for article in articles`(单源文章遍历)+ `:378-380 全局 limit 检查`——openai-blog discover 返回 100+ 篇,前 30 archived 后 total_processed=30 触发全局 limit break,其余 163 源没轮到。
- `RATE_LIMIT_PER_RUN=50`(`orchestrator.py:66`)是**跨轮 daily**兜底,但全局 `--limit 30` < 50,单源当次最多 30(受全局 limit 限制,不受 RATE_LIMIT 限制)。

### 修复 1:每源每轮上限 PER_SOURCE_PER_RUN_CAP=5

- **挂点**:`orchestrator.py:67` 新增 `PER_SOURCE_PER_RUN_CAP: int = 5`;`orchestrator.py:187` `_process_article` 在原 RATE_LIMIT 检查**前**加 cap 检查(`if source_daily_count >= PER_SOURCE_PER_RUN_CAP: skipped_rate_limit`)。
- **取舍**:不调小 RATE_LIMIT_PER_RUN(50),因为 RATE_LIMIT 注释为"跨轮 daily 兜底",调小影响 quarantine 逻辑语义;新增 PER_SOURCE_PER_RUN_CAP=5 更严(5<50,先触发),RATE_LIMIT=50 兜底防 cap 调高。两个检查都返回 skipped_rate_limit, cap 5 先触发。
- **效果**:limit 30 + cap 5 下每轮至少 6 源有份(30/5)。

### 修复 2:轮转公平 _source_rotation_offset

- **挂点**:`orchestrator.py:286` 新增 `_source_rotation_offset(num_sources, interval_s=7200)`(按时间轮转,`(int(time.time())//7200)%num`);`run_intake` 加 `source_offset: int | None = None` 参数,`None` 时算时间 offset,`sources = sources[source_offset:] + sources[:source_offset]`(`orchestrator.py:run_intake 内`)。
- **CLI**:`orchestrator.py:_build_parser` 加 `--source-offset N`(默认 None=自动算,0=不轮转);`main` 传 `source_offset=args.source_offset`。
- **取舍**:选轮转起点(非 round-robin 取料)——侵入最小(只改 sources 顺序,不改 articles 遍历);round-robin 要重组 articles 顺序,改动大。轮转每 7200s(2h,与 intake 定时器 StartInterval 对齐)+1,163 源 / 6 源每轮 ≈ 27 轮周期全覆盖。
- **效果**:cap 解决垄断(每轮 6 源有份),轮转解决后排源饿死(不固定前 6 源)。

### 不动标准

- admission/gate/rubric 零改动;state dedup(archived 终态)语义不变;--limit 30 全局有界不变;RATE_LIMIT_PER_RUN=50 不动(兜底)。

## 2. 空死因根因(file:line)——诊断结果:误判,非缺口

- **账本证据**:3 篇 openai-blog(009013928220/4a3442a5ca8c/baeb7ae75753),content_hash = 137786e6b266040e/b4fab182c5720605/7063138bae6fc5cc。
- **admission 事件**(L4377/L4402/L4433):`passed=True`(admission **通过了**,不是 abort/failed),`error_class=None`/`error=None` 是**正常的**(通过无 error),keys = `content_lane/lane_reason/passed`。
- **失败在下游 distill**(有完整归因):
  - L4379 distill_compose `error_class='extract_failed'` `error='compose_self_gate_failed: A2-frontmatter@related_concepts 0<2'`
  - L4404 distill_extract `error_class='extract_failed'` `error='concept deterministic fallback fail-closed: 7组件/0 eligible 边'`
  - L4435 distill_compose `error_class='extract_failed'` `error='compose_self_gate_failed + SG-how-density 0<3'`
- **结论**:**没有"空死因缺口"**——admission 节点 passed=True(通过,不该有 error),失败在 distill 有完整 error_class+error(宪法 P4 失败可见性满足)。工单"admission abort 空死因"判断有误:admission 不是 abort(通过),abort 在 distill(有归因)。
- **不动刀**(工单"若是大坑只报告"):无需修 admission ledger 写入逻辑——admission 通过时 error_class=None 是正确行为,不该记 error。

## 3. 改动说明

`src/akb2/quality/intake/orchestrator.py`:
1. `import time`(轮转用)
2. `PER_SOURCE_PER_RUN_CAP: int = 5`(常量,行 67)
3. `_process_article` 行 187 前加 cap 检查(skipped_rate_limit,在 RATE_LIMIT 前)
4. `_source_rotation_offset(num_sources, interval_s=7200)`(辅助函数,行 286 前)
5. `run_intake` 加 `source_offset: int | None = None` 参数 + 内部轮转(sources[offset:]+sources[:offset])
6. CLI `--source-offset N` + main 传参

**不动**:RATE_LIMIT_PER_RUN=50(兜底)/admission/gate/rubric/state dedup/--limit 语义。

## 4. 测试清单+结果

新增 `tests/quality/test_intake_per_source_cap.py`(12 测试):

| 类 | 测试 | 覆盖 |
|----|------|------|
| TestPerSourceCap | test_cap_constant_is_5 | PER_SOURCE_PER_RUN_CAP=5 |
| TestPerSourceCap | test_cap_less_than_rate_limit | cap < RATE_LIMIT(cap 先触发) |
| TestPerSourceCap | test_process_article_cap_triggers | source_daily_count>=cap → skipped_rate_limit |
| TestPerSourceCap | test_process_article_cap_not_triggered | source_daily_count<cap → 不 skipped |
| TestRunIntakeCap | test_single_source_capped_at_5 | 单源 10 篇 + limit 30 + cap 5 → archived=5 |
| TestRunIntakeCap | test_multiple_sources_share_limit | 3 源各 10 篇 → 每源 archived<=5,总=15(多源有份) |
| TestRunIntakeCap | test_global_limit_not_exceeded | 10 源各 10 篇 + limit 30 → archived<=30 |
| TestSourceRotation | test_rotation_offset_function | _source_rotation_offset 返回 (time//interval)%num |
| TestSourceRotation | test_rotation_offset_zero_for_no_sources | num=0 → 0(不崩) |
| TestSourceRotation | test_rotation_offset_in_range | offset 在 [0,num) |
| TestSourceRotation | test_rotation_changes_first_source | offset=1 vs 0 → 首源不同(src-b vs src-a) |
| TestSourceRotation | test_rotation_back_sources_not_starved | offset=5 → src-f(后排源)被处理(ledger 含 src-f) |

**全量 pytest -m 'not integration'**:**2289 passed, 16 skipped**(2277 + 12 新)。ruff 改动文件全过。测试全 mock(不打真网),tmp_path 隔离不写 vault;无管道(显式退出码);无路径含空格问题(用 tmp_path + mock)。

## 5. 范围外(如实列)

- **不动标准**:admission/gate/rubric/五闸/state dedup/--limit 语义
- **不碰**:live daemon/intake 定时器(重装 lead 做)
- **Task 2 空死因**:误判(admission passed=True,失败在 distill 有完整归因),不动刀
- **RATE_LIMIT_PER_RUN=50**:不动(跨轮 daily 兜底,调小影响 quarantine 语义;新增 cap=5 替代当次每源上限作用)
- **轮转周期**:163 源 / 6 源每轮 ≈ 27 轮周期,cap+limit 下每天 4 轮,~40 天全覆盖后排源(比固定顺序强;若 lead 认为该更快覆盖,调小 cap 或调大 limit)

## 6. 一句总判

每源每轮限额 PER_SOURCE_PER_RUN_CAP=5 + 轮转 _source_rotation_offset(按时间轮转起点)修单源垄断(openai-blog 78/78 → 每轮 6 源有份 + 后排源不饿死),挂点 orchestrator.py:67/187/286/run_intake,12 新测试 + 全量 2289 passed;空死因诊断结果:误判(admission passed=True 不是 abort,失败在 distill 有完整 error_class+error,宪法 P4 满足),不动刀;未 push 未建 PR,等 lead 验收 + 重装。
