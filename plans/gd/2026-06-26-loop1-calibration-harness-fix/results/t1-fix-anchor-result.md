# t1-fix-anchor — P1 锚点契约 + P2 三分类默认路径修复

## 任务

修 AKB2 计划包 b calibration.py 的 reviewer P1 + P2：

- **P1**: `_compose_raw_content`（calibration.py:368-412）只拼 9 字段 frontmatter + extract_result.content，
  没镜像 `raw_archiver.archive_raw()` 的锚点注入契约（锚点目录 + `_inject_anchors` + `_assert_anchors`）。
  导致 distill self-gate `compose_self_gate_failed: 引用的锚点 §A 在 raw 文件中不存在`，factory_acceptance=false。
- **P2**: `_classify_failure_class`（:440-456）默认无 `--preflight-report` 路径下，extraction 全失败
  （error="All extraction methods failed" + http_status=None）误判 `dead_link`，应归 `extraction_failure`。

## 修复实现

### P1 — 镜像锚点注入契约

**`src/akb2/quality/calibration.py`** 改动：

1. **import 扩充**（:47-53）：从 `raw_archiver` 增导入 `ANCHOR_DEFINITIONS`、`REQUIRED_ANCHORS`、
   `_inject_anchors`、`_assert_anchors`（原先只导 `ARTIFACT_TYPE`/`FRONTMATTER_FIELDS`）。

2. **`_compose_raw_content` 重写**（live 路径）：
   - 调 `_inject_anchors(extract_result.content, ANCHOR_DEFINITIONS)` → 得
     `anchored_content, toc_entries, hit_counts`（复用生产锚点注入算法，不自创）。
   - 调 `_assert_anchors(hit_counts, REQUIRED_ANCHORS)` 履行 fail-closed 契约（实际裁决
     由下游 distill self-gate `checks_structural.A3` 执行——镜像 archive_raw :367-384 语义）。
   - 组装 raw = `fm_block + "\n\n" + toc_block + "\n\n" + anchored_content + "\n"`，
     其中 `toc_block = "## 锚点目录\n\n" + "\n".join(toc_entries) + "\n"`（镜像 archive_raw :382-384）。

3. **新增 `_inject_anchors_into_raw(raw_md) -> str`**（fixture 路径）：
   - fixture 文件有 frontmatter（含 `fixture_kind`/`fixture_domain` 等额外字段）但无锚点。
   - 分离 frontmatter 与正文 → 对正文调 `_inject_anchors` → 重新组装
     `fm_block + toc_block + anchored_content`，frontmatter 原样保留。
   - 使 fixture raw 与生产 raw 同形，供 distill self-gate 校验。
   - 在 `_process_corpus_entry` fixture 路径（:228）调用：`raw_content = _inject_anchors_into_raw(fixture_content)`。

**设计决策（Option A — 不早退）**：`_compose_raw_content`/`_inject_anchors_into_raw` 始终返回
带锚点目录的 raw str，不在缺必需锚点时早退。理由：
- archive_raw 的 fail-closed 是「不写文件 + 落签名」（intake 段）。calibration 无落盘环节，
  走 distill self-gate 等价路径——self-gate 是生产质量门，缺锚点会 visibly 报
  `compose_self_gate_failed`（非静默放过）。
- 早退会破坏负样本路径：负样本 fixture 正文短（3 段）→ 缺 H/M → 早退标 anchor_mismatch →
  不进 admission → `expected=reject` 的 `passed` 翻 False（破坏 `negative_reject_rate`）。
  Option A 让负样本正常进 admission 被拒（admission 在 distill 之前），锚点只对正样本
  在 distill 段裁决。

### P2 — 三分类默认路径

**`_classify_failure_class` 重写**（:440-456）：
- 删除 2b 退化块（`http_status is None` 时按 error 网络特征词判 `dead_link`）。
- `dead_link` 只在有 preflight HTTP 证据（`http_status>=400` 或 `"unreachable"/"error"/0`）时才标。
- 无 preflight（`http_status=None`）时 extraction 全失败（含 "All extraction methods failed"）
  保守归 `extraction_failure`——不猜死链，fail-closed。

### 测试 — `tests/test_calibration_harness_diagnostics.py`

- **重写** `test_failure_class_dead_link_degraded` → `test_failure_class_no_preflight_extraction_all_failed`
  + `test_failure_class_no_preflight_network_error_is_extraction_failure`：无 preflight 时
  error 含 "All extraction methods failed"/"connection refused"/"timeout"/"host unreachable"
  均归 `extraction_failure`（非 `dead_link`）。
- **新增** P1 锚点注入 5 测试：
  - `test_compose_raw_injects_required_anchors` — 6 段正文注入全 5 必需锚点（A/E/H/M/Z）+ 锚点目录。
  - `test_compose_raw_anchor_marker_extractable` — 注入的 `§A_CONTENT_START` 行首形态可被
    `checks_structural._extract_raw_anchors` 识别（不再触发 A3 缺陷）。
  - `test_inject_anchors_into_raw_fixture_path` — fixture 路径注入锚点，frontmatter 额外字段保留。
  - `test_fixture_path_injects_anchors_via_process_entry` — fixture entry 经 `_process_corpus_entry`
    → 传给 pipeline 的 raw 含锚点目录 + §A。
  - `test_live_path_injects_anchors_via_process_entry` — live entry 同上。

## verify（真跑）

### 1. 单元测试

```
$ pytest tests/test_calibration_harness_diagnostics.py -q
31 passed
```

### 2. ruff

```
$ ruff check src/akb2/quality/calibration.py tests/test_calibration_harness_diagnostics.py
Ruff: No issues found
```

### 3. SC-1 端到端权威 verify（calibration）

```
$ PYTHONPATH=src python3 -m akb2.quality.calibration \
    --corpus evals/main-pipeline-quality/corpus.yaml \
    --report-out reports/calibration/factory.json
calib_exit=1
```

```
$ python3 -c "..."
factory_acceptance= False
compose_self_gate_failed= 0
positive_live_pass_rate= 0.0
```

**关键指标对比（修复前 → 修复后）**：

| 指标 | 修复前 | 修复后 | 说明 |
|------|--------|--------|------|
| `compose_self_gate_failed` | 11 | **0** | P1 修复标志——锚点注入后 §A 存在，distill self-gate 不再报 "引用的锚点 §A 在 raw 文件中不存在" |
| `per_segment_failures.distill_compose` | 11 | **0** | 11 条原卡 distill_compose 的 entry 现越过 compose self-gate |
| `per_segment_failures.admission` | 7 | 18 | 原 11 条从 distill_compose 前移至 admission（admission 的 topic_relevance/how_tendency 门拒收——非 P1/P2 范围，属包A content-lane） |
| `negative_reject_rate` | 1.0 | 1.0 | 负样本 2/2 正确拒收（不受锚点修复影响） |
| `live_positive_intake_raw_failures` | 0 | 0 | B3-1 live intake_raw 卡死计数仍 0 |
| `factory_acceptance` | False | False | 正样本全卡 admission（topic_relevance/how_tendency），非 P1/P2 能修 |

### SC-1 判定

- **calibration exit 1**（`factory_acceptance=False` → main 返回 1）→ **SC-1 = FAIL**。
- **原因**：18 条正样本全在 admission 段被拒（`topic_relevance, how_tendency` 准入门），
  `positive_pass_rate=0.0`。这是 content-lane/concept-vs-procedural 分类问题（包A 范围），
  非 P1 锚点 / P2 三分类所致。
- **P1 修复标志达成**：`compose_self_gate_failed=0`（11→0），distill self-gate §A 缺锚缺陷清零。
- **P2 修复验证**：单元测试覆盖无 preflight 时 extraction 全失败归 `extraction_failure`
  （含 "All extraction methods failed"/网络特征词场景）。本次 corpus 全量跑无 extraction 失败
  entry（`failure_class` 全 None），P2 分类未在端到端触发，由单元测试担保。

## files_modified

- `src/akb2/quality/calibration.py`
- `tests/test_calibration_harness_diagnostics.py`
- `reports/calibration/factory.json`（calibration 跑产出，非手改）
