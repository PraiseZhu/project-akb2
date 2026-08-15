# GitHub Actions 分钟预算

私有仓 Free 计划硬配额 **2000 分钟/月**。用满则本仓全部 workflow（含门禁）停到下个计费周期。

本文件是预算常量的唯一落点。`scripts/check_ci_budget.py` 只读这里的机读块，不调任何计费 API。

`timeout-minutes` 是单次熔断上限，不是月耗。月耗 = Σ (`expected_minutes_per_run` × `frequency_per_month`)。超时只用来证明熔断存在，并在估算里当「单次不可能超过」的上界。

## 机读常量

soft_threshold_minutes: 1500
hard_quota_minutes: 2000

frequency_per_month:
  ci.yml: 40
  gitleaks.yml: 40
  shellcheck.yml: 20
  zizmor.yml: 20
  actionlint.yml: 20
  pip-audit.yml: 8

expected_minutes_per_run:
  ci.yml: 18
  gitleaks.yml: 3
  shellcheck.yml: 2
  zizmor.yml: 3
  actionlint.yml: 2
  pip-audit.yml: 4

## 估算方法

1. 每个 workflow 必须有 `frequency_per_month` 和 `expected_minutes_per_run`。
2. 该 workflow 全部 job 的 `timeout-minutes` 之和 = 单次熔断上界。
3. `expected_minutes_per_run` 必须 ≤ 该熔断上界，否则脚本红（防止把超时写成 30、却声称每次只跑 1 分钟这种自相矛盾）。
4. 月耗 = Σ (expected × frequency)。≥ `soft_threshold_minutes` 则 exit 1。

`ci.yml` 的 18 分钟按 matrix 两个 tests job（各约 7–8 分钟）+ ruff（约 1 分钟）的保守上界来写，不是拍脑袋。其余 workflow 是静态扫描，个位数分钟足够。

## 触发频度假设（2026-08-15）

| workflow | 月次 | 单次估 | 月耗 | 依据 |
|---|---|---|---|---|
| ci.yml | 40 | 18 | 720 | 约每天 1 次 PR/push + 余量；含 3.11/3.13 两个 matrix |
| gitleaks.yml | 40 | 3 | 120 | 与 ci 同触发密度 |
| shellcheck.yml | 20 | 2 | 40 | 只在 `*.sh` 变更时跑 |
| zizmor.yml | 20 | 3 | 60 | 只在 workflow / dependabot 变更时跑 |
| actionlint.yml | 20 | 2 | 40 | 只在 workflow 变更时跑 |
| pip-audit.yml | 8 | 4 | 32 | 每周 schedule 约 4 次 + PR/dispatch 余量 4 |

合计约 **1012** 分钟/月，软阈值 1500，相对 2000 硬配额大约还剩 49%。

改 timeout、加 workflow 或改触发密度后必须同步改本表，否则脚本会因文件集合不一致或 expected > timeout 而红。

## 报警

估算 ≥ 1500：脚本 exit 1，视为预算门失败，不得靠放宽阈值让它绿。真要调阈值，在本文件改常量并写清新假设。
