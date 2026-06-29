# 计划包 b 一致性审查

verdict: REQUIRES_CHANGES

## findings

### P1 — SC-1 live floor 子契约未落到执行报告与 verify

file: `plans/gd/2026-06-26-loop1-calibration-harness-fix/master-plan.md:41`

issue: master-plan SC-1 明确要求 execute 期按 reachable live 正样本数计算并在报告内写出 `effective_live_positive_pass_floor`，下调原因写入 `live_positive_pass_floor_reason`。当前执行实现只保留常量 `LIVE_POSITIVE_PASS_FLOOR=0.9` 并直接用于 `factory_acceptance`，生成报告没有 `effective_live_positive_pass_floor` / `live_positive_pass_floor_reason` 字段，batch SC-1 verify 也未断言这两个字段。

evidence:
- 真跑 SC-1 端到端命令，calibration 退出 `1`，并重新写出 `reports/calibration/factory.json`。
- `reports/calibration/factory.json:8-14` 有 `positive_live_total=15`、`positive_live_pass_rate=0.0`、`live_positive_intake_raw_failures=0`，但无 `effective_live_positive_pass_floor` / `live_positive_pass_floor_reason`；文件末尾 `factory_acceptance=false`。
- `src/akb2/quality/calibration.py:63-64` 只定义 `LIVE_POSITIVE_QUOTA=3` / `LIVE_POSITIVE_PASS_FLOOR=0.9`；`src/akb2/quality/calibration.py:762-768` 直接用 `positive_live_pass_rate >= LIVE_POSITIVE_PASS_FLOOR`。
- `batch-wave2.json:26-31` 的 SC-1 assertion 结果为 `FAIL/exit_code=1`，但命令只断言 7 个诊断键、source、live 越过 intake_raw、分层字段、`live_positive_intake_raw_failures==0`，未覆盖 master-plan 第 41 行的 effective floor 字段。

impact: 计划包的权威 SC-1 子契约未被实现，也未被执行验收捕获。虽然当前 `n=15` 时有效 floor 仍应为 0.9，未构成阈值放宽，但这是 master SC 与执行报告/verify 的一致性缺口。

minimal_fix: 在 `calibration.py` 报告中显式产出 `effective_live_positive_pass_floor` 和 `live_positive_pass_floor_reason`；按 master-plan 第 41 行规则计算，并把 batch/task-packet SC-1 verify 加上字段存在和值域断言。

acceptance: 重跑 `PYTHONPATH=src python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json || true` 后，单独断言 `effective_live_positive_pass_floor` 在报告中、`positive_live_total=15` 时值为 `0.9`、reason 记录为未下调或等价原因；更新后的 batch SC-1 verify 覆盖该断言。

## consistency_summary

### 1. SC 覆盖

result: 部分通过。

真跑 lead 指定命令输出：

```text
w1: [('SC-5', 'PASS'), ('SC-5', 'PASS'), ('SC-3', 'PASS'), ('SC-7', 'PASS')]
w2: [('SC-1', 'FAIL'), ('SC-1', 'PASS'), ('SC-2', 'PASS'), ('SC-2', 'PASS'), ('SC-4', 'PASS'), ('SC-6', 'PASS')]
```

master-plan SC-1~7 的父级 `sc_ref` 均在 batch verify_results 中出现；`SC-1a/SC-2a/SC-2b` 按 master-plan 第 38 行约定归并到父 `SC-1/SC-2`，不是遗漏。缺口是 SC-1 内部的 effective live floor 字段未被 verify 覆盖，见 P1。

### 2. 边界

result: 通过。

`git diff --name-only d563a59 HEAD` 显示变更集中在计划包、calibration/extract/preflight、corpus/fixtures、tests、reports，以及 `.gitignore`、`CLAUDE.md`、`docs/constitution.md`。逐项检查不修改清单均未变更：

```text
src/akb2/quality/admission.py unchanged
src/akb2/quality/distill.py unchanged
src/akb2/quality/checks_structural.py unchanged
src/akb2/quality/grader.py unchanged
src/akb2/quality/repair.py unchanged
src/akb2/quality/runner.py unchanged
config/main_sources.yaml unchanged
```

`constitution/CLAUDE` 属 `execution-dispatch-map.json` 的 `overrides_rationale` 声明治理前提，不按源码 lane 越界处理。

### 3. owned_paths

result: 通过。

按 `execution-dispatch-map.json` track.owned_paths 对 `git diff --name-only d563a59 HEAD` 归属匹配：

- `t2a-scrapling-fix`: `src/akb2/quality/intake/extract.py`、`tests/test_extract_scrapling_fallback.py`
- `t2b-preflight-corpus`: `src/akb2/quality/intake/preflight.py`、`evals/main-pipeline-quality/**`、`tests/test_corpus_preflight.py`
- `akb2-calibration-harness-diagnostics-t1`: `src/akb2/quality/calibration.py`、`tests/test_calibration_harness_diagnostics.py`

除计划包工件、reports 运行产物、`.gitignore`、`CLAUDE.md`、`docs/constitution.md` 外，没有执行 track owned 外的源码/测试/语料改动。

### 4. deliverables

result: 通过。

task-packets §6 的 must_exist 交付物均出现在 batch `deliverables_produced`，且本地复核存在与类型匹配：

- `src/akb2/quality/calibration.py`
- `tests/test_calibration_harness_diagnostics.py`
- `src/akb2/quality/intake/extract.py`
- `tests/test_extract_scrapling_fallback.py`
- `src/akb2/quality/intake/preflight.py`
- `evals/main-pipeline-quality/corpus.yaml`
- `evals/main-pipeline-quality/fixtures/`
- `tests/test_corpus_preflight.py`

controller/ledger/result/merge hash 也已复算匹配。

### 5. Step 实现

result: 通过，另有 P1 所述 SC-1 子契约遗漏。

`step-1-calibration-core.md` §6 的 6 个实现动作均在 `src/akb2/quality/calibration.py` 落地：

- `_build_extraction_context`: `calibration.py:123-138`
- `_fetch_url_content` 调用 `extract_content(..., extraction_config=ctx)`: `calibration.py:316-340`
- `_load_fixture_content` 按 `fixtures/<name>.md` 读取: `calibration.py:348-373`
- `_process_corpus_entry` base 诊断键与 `_classify_failure_class`: `calibration.py:141-313`, `calibration.py:481-518`
- `_compose_raw_content` 9 字段 frontmatter + anchors: `calibration.py:376-440`
- `_compute_statistics` live/fixture 分层 + `factory_acceptance` AND live floor: `calibration.py:644-700`, `calibration.py:762-768`

### 6. waves 一致

result: 通过。

`execution-dispatch-map.json` waves 为 `w1=[t2a-scrapling-fix,t2b-preflight-corpus]`、`w2=[akb2-calibration-harness-diagnostics-t1]`；`batch-wave1.json` 与 `batch-wave2.json` 的 `task_results` 对应一致。

### 7. NON_GOALS

result: 通过。

- 包 A 源码未改。
- `config/main_sources.yaml` 未改。
- diff 中没有实际 `pip install` / `pip3 install` / `python -m pip` 执行痕迹，只有计划文本中的禁止语句。
- `positive_pass_rate>=0.9` / `negative_reject_rate==1.0` 未放宽；`factory_acceptance` 仍保留两项阈值并 AND `positive_live_total>=3`、`live_positive_intake_raw_failures==0`、`positive_live_pass_rate>=0.9`。

### 8. SC-1 端到端

result: 与计划前提一致，不作为包 B 缺陷。

本轮真跑 SC-1 端到端命令，calibration 退出 `1`，报告显示：

```text
factory_acceptance=False
positive_pass_rate=0.0
negative_reject_rate=1.0
positive_live_total=15
positive_live_passed=0
positive_live_pass_rate=0.0
live_positive_intake_raw_failures=0
live_beyond_intake_raw=15
per_segment_failures={'admission': 9, 'distill_compose': 9}
```

这证明 15 条 live 正样本都越过 intake_raw，失败在 admission/distill 后续门；`batch-wave2.json` 也诚实记录 SC-1 authority verify 为 `FAIL/exit_code=1`。该 FAIL 符合 master-plan SC-1 的外部前提风险：端到端依赖网络、key、以及包 A admission/content-lane 改造，不是包 B harness 一致性缺陷。

## commands_run

```text
git status --short --branch
git rev-parse --abbrev-ref HEAD && git rev-parse HEAD
git diff --name-only d563a59 HEAD
python3 -c "import json; ... batch-wave1/batch-wave2 verify_results ..."
pytest tests/test_extract_scrapling_fallback.py -q
pytest tests/test_corpus_preflight.py -k alignment -q
pytest tests/test_calibration_harness_diagnostics.py -q
ruff check src/akb2/quality/calibration.py src/akb2/quality/intake/extract.py src/akb2/quality/intake/preflight.py tests/test_calibration_harness_diagnostics.py tests/test_extract_scrapling_fallback.py tests/test_corpus_preflight.py
PYTHONPATH=src python3 -m akb2.quality.intake.preflight --corpus evals/main-pipeline-quality/corpus.yaml --out reports/calibration/preflight.json && python3 -c "..."
PYTHONPATH=src python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json && python3 -c "..."
python3 -c "复核 deliverables 存在/类型、ledger/result/merge hash、owned_paths 归属"
```

## verification_results

```text
pytest tests/test_extract_scrapling_fallback.py -q -> 6 passed
pytest tests/test_corpus_preflight.py -k alignment -q -> 8 passed
pytest tests/test_calibration_harness_diagnostics.py -q -> 31 passed, 2 warnings
ruff check ... -> All checks passed
preflight SC-3 command -> exit 0; 20 entries, 15 live, 5 fixture, bad=0
calibration SC-1 authority command -> exit 1; factory_acceptance=False; live_positive_intake_raw_failures=0
```
