VERDICT: REQUIRES_CHANGES

## 覆盖维度

- 审查目标：`step-4-v2-multisource-distill.md`、`tests/quality/test_multisource_distill.py`、`tests/quality/test_raw_gate.py`。
- 派发/边界：v2-1..v2-4 的 `blocked_by`、`owned_paths`、raw_gate 接线前置约束、缓发 packet 决策。
- 跨步骤一致性：master §5 / §10 inventory / wave matrix 与 step-1..4 §7/§8 的 owned_paths、blocked_by、wave 顺序。
- 测试脚手架：按 lead 指定命令实跑，并读取测试确认是否函数级 `importorskip` 诚实 skip。

## 实跑结果

工作目录：`/Users/praise/AI-Agent/Claude/projects/akb2-integ-wt`

| 命令 | exit | 输出摘要 |
|---|---:|---|
| `python3 -m pytest -q tests/quality/test_multisource_distill.py -k synthesis` | 1 | `/usr/bin/python3` / Xcode Python 3.9.6：`No module named pytest` |
| `python3 -m pytest -q tests/quality/test_raw_gate.py` | 1 | `/usr/bin/python3` / Xcode Python 3.9.6：`No module named pytest` |
| `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -q tests/quality/test_multisource_distill.py -k synthesis` | 0 | `1 skipped, 3 deselected` |
| `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -q tests/quality/test_raw_gate.py` | 0 | `3 skipped` |
| `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -q tests/quality/test_multisource_distill.py -k incoherent` | 0 | `1 skipped, 3 deselected` |
| `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -q tests/quality/test_multisource_distill.py -k fast_path_1to1` | 0 | `1 skipped, 3 deselected` |
| `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -q tests/quality/test_multisource_distill.py -k no_wiki_mutation` | 0 | `1 skipped, 3 deselected` |
| `python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-master-plan-consistency.py" plans/gd/2026-06-28-loop1-content-quality/master-plan.md` | 0 | `PREFLIGHT_RESULT: PREFLIGHT_PASSED (0 errors, 0 warning(s))` |

## 已核实通过

- raw_gate 前置于现有 admission 的修正表达清楚：`step-4-v2-multisource-distill.md:45` 明确禁止照 child 草稿把 raw_gate 接在 admission 后，并要求 raw pool 前置门在现有 admission 之前，或拆 `raw_relevance` / `page_admission`。
- v2 task 依赖顺序清楚：`step-4-v2-multisource-distill.md:47-53` 写明 v2-1 blocked_by P0，v2-2 blocked_by v2-1，v2-3 blocked_by v2-2/P0，v2-4 blocked_by v2-3。
- 测试脚手架不是 `assert True` 假绿：`tests/quality/test_multisource_distill.py:21-54` 与 `tests/quality/test_raw_gate.py:19-41` 每个测试函数首行使用 `pytest.importorskip(...)`；模块未建时 selector 命中并 skip，模块建好后会继续执行后续断言。
- 用例名与 master SC-9 selector 对得上：`synthesis`、`incoherent`、`fast_path_1to1`、`no_wiki_mutation` 都能各命中 1 个 `test_multisource_*` 测试；`test_raw_gate.py` 三个 raw gate 测试能被 master SC-9c 的文件级命令命中。
- wave 顺序对 step-1 / step-4 的重叠做了隔离：master §5 指出 step-1 与 step-4 都触及 `src/akb2/quality/` 不可并行（`master-plan.md:71-78`）；master §10 wave matrix 把 step-1 放 w1，把 step-4 放 w2（`master-plan.md:250-258`）。
- P1/P2/v2 packet 缓发仍合理：P1 依赖 P0 trace，P2 依赖 P1 定性，v2 packet 依赖 P0 后的 grader/admission 形态；`step-4-v2-multisource-distill.md:55` 明确 v2 packet 待 P0 出口后 materialize，防漂移。

## findings

### [P2] step-4 的 owned_paths / 边界未同步新增 v2 测试脚手架

- 位置：`step-4-v2-multisource-distill.md:47-60`，`master-plan.md:224-235`。
- 问题：master §10 的 `step-4-v2` inventory 已把 `tests/quality/test_multisource_distill.py` 与 `tests/quality/test_raw_gate.py` 列入 owned_paths 和 deliverables；但 step-4 §7 task table 仍只列 `raw_gate.py/runner.py/concept_routing.py/multisource_distill.py/evals/calibration.py`，§8 修改边界也只列 `src/akb2/quality/{raw_gate,concept_routing,multisource_distill}.py`、`runner.py`、`calibration.py`、`evals/main-pipeline-quality/`，漏掉两个新增测试文件。
- 影响：execute 阶段若以 step-4 §7/§8 派生 task packet，两个已经建立的 SC-9 验收脚手架不在 step-4 的修改边界内；这和 master §10 的权威 inventory 不一致，会让 owned_paths 审计出现漂移。
- 可落地修法：同步 step-4 §7/§8：把 `tests/quality/test_raw_gate.py` 归入 v2-1 或 v2-4（按你们希望由 raw_gate 实现者还是 eval harness 维护决定），把 `tests/quality/test_multisource_distill.py` 归入 v2-3 或 v2-4；§8 修改边界追加这两个测试文件。
- 验收：`rg -n "test_multisource_distill|test_raw_gate" step-4-v2-multisource-distill.md master-plan.md` 显示 step-4 §7/§8 与 master §10 一致；`gd-validate-master-plan-consistency.py master-plan.md` 仍 exit 0。

### [P2] master §10 的 v2 pytest 命令在当前默认 shell 下不可直接运行

- 位置：`master-plan.md:239-243`，实跑输出：`python3 -m pytest -q tests/quality/test_multisource_distill.py -k synthesis` 与 `python3 -m pytest -q tests/quality/test_raw_gate.py` 均 exit 1，报 `/usr/bin/python3: No module named pytest`。
- 问题：lead 指定的裸 `python3 -m pytest ...` 在当前 worker shell 解析到 Xcode `/usr/bin/python3`，没有 pytest，未进入测试收集；而本项目其它 P0 packet 已多处使用 `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 ...` 作为可靠测试解释器。
- 影响：按 master §10 原命令复制执行不会得到预期的 `exit 0 skipped`，会先因解释器环境失败。虽然用项目 pyenv Python 重跑能得到诚实 skip，但当前 verify 命令本身不够可执行。
- 可落地修法：把 master §10 的 SC-9a..9e verify 命令，以及 step-4 §57/§10 中对这些命令的引用，统一改成 `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest ...`（或绝对解释器 `/Users/praise/.pyenv/versions/3.13.12/bin/python3 -m pytest ...`）。
- 验收：直接复制更新后的 SC-9a/SC-9c 命令，输出分别为 `1 skipped, 3 deselected` 与 `3 skipped`，exit 0。

## residual risk

- master §10 的 wave matrix 能阻止 step-3 与 step-4 并行，但 step-3 `blocked_by` 只写 `step-2-p1`，而 step-3 与 step-4 在 `src/akb2/quality/`、`evals/main-pipeline-quality/` 上有重叠。只要 execute dispatch 严格按 wave 执行就安全；如果后续工具只按 `blocked_by` 自动排程，应把 wave 顺序也纳入 dispatch map 生成校验。
