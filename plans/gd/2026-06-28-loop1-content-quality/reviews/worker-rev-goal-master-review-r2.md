VERDICT: REQUIRES_CHANGES

# Round 2 复审范围

- 复核目标：`plans/gd/2026-06-28-loop1-content-quality/master-plan.md` 的 §3 / §4 / §10。
- 实测命令：已重跑 master consistency preflight；已实际运行新增 SC-6a/6b/6c pytest；已实跑 SC-8 verifier 命令与 SC-9a..SC-9e pytest selectors。
- 结论：R1 的 P1#1、P1#2、P2 owned_paths、P3 NON_GOALS 已消解；P1#3 与 P1#4 仍有机器可执行残留，因此不能 APPROVED。

# R1 finding 处置状态

- P1#1 缺 inventory：已消解。`gd-step-plan-inventory` + `gd-wave-matrix` 已存在，preflight 实测 `PREFLIGHT_PASSED`。
- P1#2 content-lane SC 未硬绑：§3 已补 SC-6a/6b/6c，`pytest` selector 实测通过。
- P1#3 SC-8 无机器可判目标集：部分消解。§3/§10 已引入 `decision-manifest.json` 与 verifier 脚本路径，但机器 verify 未真正包含 `gd-review-suite-controller.py --kind combined` 命令；且当前 verifier 脚本不存在，命令实跑失败。
- P1#4 SC-9 无真命令：未消解到可执行层。SC-9a..9e 已拆成 pytest 命令，但当前全部 0 tests selected / exit 5，且 step-4 inventory 未声明任何测试 deliverable。
- P3 NON_GOALS：已消解。§4 已补 embedding 不做最终归组、v2 v1 不改写已入库 wiki 页。

# Findings

## [P1] SC-8 的 Codex combined review 仍不是机器 verify；当前 verifier 命令也不可运行

位置：
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:49`
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:208`
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:213`
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:217`

问题：
§3 写了 `gd-review-suite-controller.py --kind combined ...` 的路线描述，但 §10 机器 verify 只有：

```text
python3 scripts/verify_loop1_reachable_ceiling.py --factory reports/calibration/factory.json --decisions reports/loop1-audit/decision-manifest.json
```

`expect` 里写了“另 Codex combined review APPROVED”，但这不是第二条可执行 verify。实际运行 verifier 命令也失败：

```text
can't open file '.../scripts/verify_loop1_reachable_ceiling.py': [Errno 2] No such file or directory
```

虽然 §10 把 `scripts/verify_loop1_reachable_ceiling.py` 列入 owned_paths/deliverables，这只能说明未来可能产出；当前 master 仍没有把“创建该脚本”的实现动作写进 step-3 plan，也没有把 combined review 命令作为机器 verify 项纳入 inventory。

可直接落地的修法：
1. 在 `step-3-p2-classified-remediation.md` 与 master §10 明确加入 `scripts/verify_loop1_reachable_ceiling.py` 的创建动作、输入 schema、输出断言。
2. 在 master §10 的 SC-8 `verify` 数组增加第二条真实 combined review 命令，例如：

```json
{
  "sc_ref": "SC-8",
  "method": "command",
  "cmd": "python3 /Users/praise/AI-Agent/Claude/projects/Project\\ GD/scripts/gd-review-suite-controller.py --target-set-id loop1-content-quality-p2 --kind combined --cwd /Users/praise/AI-Agent/Claude/projects/akb2-integ-wt --target execution_artifact=plans/gd/2026-06-28-loop1-content-quality/reports/<execution-result>.json --target code_diff=plans/gd/2026-06-28-loop1-content-quality/reports/<diff>.patch --out-dir plans/gd/2026-06-28-loop1-content-quality/reviews/codex-p2 --live-transport --compat-v1",
  "expect": "aggregate_bucket == codex_approved / GD_REVIEW_DECISION == APPROVED"
}
```

如果 `<execution-result>` / `<diff>` 是 P2 产物，也要在 step-3 deliverables 里列出它们，避免 verify 指向未定义 artifact。

## [P1] SC-9a..9e pytest selectors 当前没有任何测试承载，所谓“真实 pytest”实跑为 0 selected

位置：
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:50-54`
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:225-238`

问题：
SC-9a..9e 拆分方向正确，但当前命令不是可执行验收，因为没有任何测试命中。实测：

```text
python3 -m pytest -k 'multisource and synthesis' -q
=> 1641 deselected, exit 5

python3 -m pytest -k raw_gate -q
=> 1641 deselected, exit 5

python3 -m pytest -k 'multisource and incoherent' -q
=> 1641 deselected, exit 5

python3 -m pytest -k fast_path_1to1 -q
=> 1641 deselected, exit 5

python3 -m pytest -k 'multisource and no_wiki_mutation' -q
=> 1641 deselected, exit 5
```

并且 `rg` 未找到 `tests/` 或 `src/akb2/quality/` 中已有 `multisource/raw_gate/fast_path_1to1/no_wiki_mutation/incoherent` 测试锚点。§10 step-4 owned_paths 也只包含 `src/akb2/quality/` 与 `evals/main-pipeline-quality/`，deliverables 只列 `src/akb2/quality/multisource_distill.py`，没有任何测试文件或 eval fixture deliverable。

可直接落地的修法：
把 step-4 inventory 与 step-4 plan 改成“实现必须产出可命中的测试/fixture”，例如：

```json
"owned_paths": [
  "src/akb2/quality/",
  "evals/main-pipeline-quality/",
  "tests/quality/test_multisource_distill.py",
  "tests/quality/test_raw_gate.py"
],
"deliverables": [
  {"path": "src/akb2/quality/multisource_distill.py", "kind": "file", "must_exist": true},
  {"path": "tests/quality/test_multisource_distill.py", "kind": "file", "must_exist": true},
  {"path": "tests/quality/test_raw_gate.py", "kind": "file", "must_exist": true}
]
```

并把 SC-9 verify 从纯 `-k` 选择器改为显式测试文件 + selector：

```text
python3 -m pytest -q tests/quality/test_multisource_distill.py -k "synthesis"
python3 -m pytest -q tests/quality/test_multisource_distill.py -k "incoherent"
python3 -m pytest -q tests/quality/test_raw_gate.py
python3 -m pytest -q tests/quality/test_multisource_distill.py -k "fast_path_1to1"
python3 -m pytest -q tests/quality/test_multisource_distill.py -k "no_wiki_mutation"
```

# 已核对通过

- `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-master-plan-consistency.py" plans/gd/2026-06-28-loop1-content-quality/master-plan.md`：
  `PREFLIGHT_RESULT: PREFLIGHT_PASSED (0 errors, 0 warning(s))`
- `pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "concept_positive"`：
  `1 passed, 9 deselected`
- `pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "negatives"`：
  `7 passed, 3 deselected`
- `pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "adversarial"`：
  `2 passed, 8 deselected`
- §4 NON_GOALS 已补：embedding 不做最终归组；v2 v1 不改写已入库 wiki 页。
- Step 1 owned_paths 已从不存在的 `compose.py` 修为 `distill.py`，并补 `src/akb2/quality/source_ops_scanners/raw_verify.py`。

# 实测命令清单

```bash
python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-master-plan-consistency.py" plans/gd/2026-06-28-loop1-content-quality/master-plan.md
pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "concept_positive"
pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "negatives"
pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "adversarial"
python3 scripts/verify_loop1_reachable_ceiling.py --help
python3 -m pytest -k 'multisource and synthesis' -q
python3 -m pytest -k raw_gate -q
python3 -m pytest -k 'multisource and incoherent' -q
python3 -m pytest -k fast_path_1to1 -q
python3 -m pytest -k 'multisource and no_wiki_mutation' -q
rg -n "def test_.*(multisource|raw_gate|fast_path_1to1|no_wiki_mutation|incoherent)|multisource|raw_gate|fast_path_1to1|no_wiki_mutation" tests src/akb2/quality -g '*.py'
```
