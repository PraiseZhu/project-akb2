# t1-fix-livefloor — SC-1 live floor 子契约落地

- task_id: t1-fix-livefloor
- 分支: feature/loop1-calibration-harness-fix
- 日期: 2026-06-27

## 修复内容

SC-1 live floor 子契约（master-plan.md:41）要求：execute 期按 reachable live 正样本数 n 计算
`effective_live_positive_pass_floor` + 写 `live_positive_pass_floor_reason` 到报告。当前
calibration.py 只用常量 `LIVE_POSITIVE_PASS_FLOOR=0.9`，报告无这 2 字段。

### 规则实现（master-plan.md:41）

| n | floor | reason 模板 |
|---|-------|------------|
| n<5 | 0.9 | `n={n}<5, floor=0.9 样本不足需补 live 或重探` |
| 5<=n<10 | 0.8 | `5<=n={n}<10, 下调到 0.8 容忍 1 条公网漂移` |
| n>=10 | 0.9 | `n={n}>=10, floor=0.9 未下调` |

任何情况下 floor 永不低于 0.8（防御性 clamp）。

### 改动

1. `src/akb2/quality/calibration.py`
   - 新增 `_compute_effective_live_floor(n) -> tuple[float, str]`（:644 前）
   - `_compute_statistics` 在 `live_positive_intake_raw_failures` 之后调用上述函数，
     写入 `effective_live_positive_pass_floor` + `live_positive_pass_floor_reason`
   - `_init_report` 增加两字段默认值（保证报告顶层始终携带）
   - `factory_acceptance`（:762-768）用 `report["effective_live_positive_pass_floor"]`
     替代常量 `LIVE_POSITIVE_PASS_FLOOR`（n=15 时 effective=0.9，不改变当前行为）
   - stderr 打印显示 effective floor + reason

2. `tests/test_calibration_harness_diagnostics.py`
   - `test_effective_live_floor_calculation`（parametrize 8 case：n=0/3/4/5/9/10/15/50）
   - `test_effective_live_floor_in_statistics_report`（n=15 满仓场景断言字段在场 + 值 0.9）
   - `test_effective_live_floor_midrange_downgrade`（n=7 断言下调到 0.8）

## SC 验收

### SC-1 live floor 子契约 — pass

真跑校准（PYTHONPATH=src python3 -m akb2.quality.calibration ...）exit=0，报告顶层字段：

```
effective_live_positive_pass_floor= 0.9
live_positive_pass_floor_reason= n=15>=10, floor=0.9 未下调
positive_live_total= 15
positive_live_passed= 0
positive_live_pass_rate= 0.0
live_positive_intake_raw_failures= 0
factory_acceptance= False
```

- effective_live_positive_pass_floor=0.9（n=15>=10 → 0.9，符合规则）
- live_positive_pass_floor_reason 非空（"n=15>=10, floor=0.9 未下调"）
- 值域 [0.8, 0.9] ✓
- n>=10 时为 0.9 ✓

### 测试

```
pytest tests/test_calibration_harness_diagnostics.py -q
→ 41 passed（含 10 新增 live floor 测试 + 31 回归）
```

### ruff

```
ruff check src/akb2/quality/calibration.py tests/test_calibration_harness_diagnostics.py
→ No issues found
```

## 诚实声明

- `effective_live_positive_pass_floor=0.9`（n=15，符合 n>=10 规则，未下调）
- `factory_acceptance` 仍 **False**：根因在 admission 包A（positive_live_pass_rate=0.0，
  15 条 live 全 rejected rounds=0，源失效 + 抓取 harness 元数据 bug，不在本任务范围）。
  本任务仅落地 live floor 子契约（effective floor + reason 字段），不改变当前
  factory_acceptance 行为（n=15 时 effective=0.9 == 原常量 0.9，纯字段落地无阈值变化）。
- 本任务不触碰包 A（admission/distill/checks_structural/grader/repair.py）。

## files_modified

- `src/akb2/quality/calibration.py`
- `tests/test_calibration_harness_diagnostics.py`

## 回复贴

```yaml
task_id: t1-fix-livefloor
exec_status: completed
sc_acceptance:
  - sc_ref: SC-1
    status: pass
    evidence: "effective_live_positive_pass_floor=0.9 (n=15>=10, reason='n=15>=10, floor=0.9 未下调'); pytest 41 passed (10 新增 live floor 测试); ruff clean; 真跑校准 exit=0; factory_acceptance=False 根因 admission 包A (positive_live_pass_rate=0.0) 非本任务范围"
files_modified:
  - src/akb2/quality/calibration.py
  - tests/test_calibration_harness_diagnostics.py
summary: "live floor 子契约落地：calibration.py 新增 _compute_effective_live_floor(n) 按 n<5→0.9 / 5<=n<10→0.8 / n>=10→0.9 计算有效 floor（永不低于 0.8），_compute_statistics 产出 effective_live_positive_pass_floor + live_positive_pass_floor_reason 两字段，factory_acceptance 用 effective floor 替代常量；10 新增测试 + 31 回归全过；n=15 满仓真跑 effective=0.9，factory_acceptance 仍 False 根因在 admission 包A 不在本次范围"
```
