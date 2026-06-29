# 计划包 b 一致性复审 v2

verdict: APPROVED

## findings

无 P1/P2。

## fixed_previous_findings

### P1 — SC-1 live floor 子契约未落到执行报告与 verify

status: fixed

evidence:
- `src/akb2/quality/calibration.py:647-673` 新增 `_compute_effective_live_floor(n)`，规则覆盖 `n<5 -> 0.9`、`5<=n<10 -> 0.8`、`n>=10 -> 0.9`，并防御性保证 floor 不低于 `0.8`。
- `src/akb2/quality/calibration.py:716-721` 在 `_compute_statistics` 中写出 `effective_live_positive_pass_floor` 与 `live_positive_pass_floor_reason`。
- `src/akb2/quality/calibration.py:803-808` 的 `factory_acceptance` 已用 `report["effective_live_positive_pass_floor"]` 替代常量 floor；本轮满仓 `n=15` 时 effective floor 为 `0.9`，不改变现有行为。
- `plans/gd/2026-06-26-loop1-calibration-harness-fix/batch-wave2.json` 的 SC-1 verify cmd 已包含 `effective_live_positive_pass_floor` / `live_positive_pass_floor_reason` 字段断言。
- `plans/gd/2026-06-26-loop1-calibration-harness-fix/stage-dispatch-ledger-execute-hotfix3.json` 锚定 `t1-fix-livefloor`，`controller-report-execute.json` 的 `batch_ledgers` 已包含 hotfix3，hash 复算匹配。

## verification

### lead 指定命令

```text
PYTHONPATH=src python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json ; echo "exit=$?"
-> exit=1
-> factory_acceptance=False（已知，根因 admission/下游包 A，不是本 P1 修复范围）
-> live floor reason: n=15>=10, floor=0.9 未下调

python3 -c "import json;d=json.load(open('reports/calibration/factory.json'));..."
-> effective= 0.9
-> reason= n=15>=10, floor=0.9 未下调
-> 字段断言 PASS

pytest tests/test_calibration_harness_diagnostics.py -q
-> 41 passed, 2 warnings

ruff check src/akb2/quality/calibration.py
-> All checks passed!

python3 -c "import json;b=json.load(open('.../batch-wave2.json'));..."
-> SC-1 cmd 含 effective floor 断言: True

python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-stage-dispatch-ledger.py plans/gd/2026-06-26-loop1-calibration-harness-fix/stage-dispatch-ledger-execute-hotfix3.json ; echo hf3=$?
-> LEDGER_VALID
-> hf3=0

python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-controller-report.py plans/gd/2026-06-26-loop1-calibration-harness-fix/controller-report-execute.json ; echo ctrl=$?
-> CONTROLLER_REPORT_VALID
-> ctrl=0
```

### 本轮报告字段

```text
factory_acceptance=False
positive_pass_rate=0.05555555555555555
negative_reject_rate=1.0
positive_live_total=15
positive_live_passed=0
positive_live_pass_rate=0.0
effective_live_positive_pass_floor=0.9
live_positive_pass_floor_reason=n=15>=10, floor=0.9 未下调
live_positive_intake_raw_failures=0
entries=20
live_beyond_intake_raw=15
segments={'admission': 7, None: 3, 'distill_compose': 10}
```

## consistency_checks

### 1. 报告字段

result: pass

`reports/calibration/factory.json` 本轮重跑后包含 `effective_live_positive_pass_floor=0.9` 与非空 `live_positive_pass_floor_reason`。`n=15>=10` 命中 master-plan 规则的 `0.9` 分支。

### 2. batch SC-1 verify

result: pass

`batch-wave2.json` 的 SC-1 verify cmd 含：

- `assert 'effective_live_positive_pass_floor' in d`
- `assert 'live_positive_pass_floor_reason' in d`
- `assert d['effective_live_positive_pass_floor']>=0.8`

### 3. hotfix3 ledger / controller

result: pass

`stage-dispatch-ledger-execute-hotfix3.json` 包含 `child_jobs[0].job_id == "t1-fix-livefloor"`，result hash 与 `results/t1-fix-livefloor-result.md` 实算匹配，merge hash 与 `merge-report-execute.md` 实算匹配。`controller-report-execute.json` 的 `batch_ledgers` 包含 hotfix3 且 hash 匹配。两个 validator 均 exit 0。

### 4. effective floor 规则

result: pass

代码实现与测试覆盖均满足规则：

- `n<5 -> 0.9`
- `5<=n<10 -> 0.8`
- `n>=10 -> 0.9`
- floor 防御性不低于 `0.8`

`tests/test_calibration_harness_diagnostics.py` 新增参数化覆盖 `0/3/4/5/9/10/15/50`，并覆盖 `_compute_statistics` 中 `n=15` 与 `n=7` 的报告字段。

### 5. factory_acceptance 使用 effective floor

result: pass

`factory_acceptance` 使用 `positive_live_pass_rate >= report["effective_live_positive_pass_floor"]`。本轮报告 `positive_live_total=15`，effective floor 为 `0.9`，与原常量一致，不改变 SC-1 端到端已知失败行为。

### 6. 新缺陷检查

result: pass

未发现新的 P1/P2。SC-1 端到端仍 `factory_acceptance=False`，但报告显示 `live_positive_intake_raw_failures=0`、`live_beyond_intake_raw=15`，失败段为 admission / distill_compose，符合已知包 A admission/下游质量门范围，不作为本次 P1 修复缺陷。

## notes

- 真跑 SC-1 会重写 `reports/calibration/factory.json`；本轮复审后该文件处于 modified 状态，未 revert。
- 工作区仍有既有未跟踪目录（content-lane 相关等），本复审未触碰。
