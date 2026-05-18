# Step 3: gate 简化路径

日期：2026-05-14 | blocked_by：step 1 | can_parallel_with：step 4 | SC：SC-4

## 目标

修改 akb2_gate_check.py，让 template_type=query-result 的候选页跳过 HC-03/HC-05，HC-04 枚举接受 query-result，HC-07 接受 derived_from 作为 graph link 替代。

## owned_paths

- `src/akb2/gate/akb2_gate_check.py`
- `tests/test_gate.py`
- `tests/fixtures/gate/staging/candidate/candidate-query-result.md`

## forbidden_paths

- `src/akb2/lint/**`（lint 属 step 4）
- `src/akb2/index/**`（index 属 step 2）
- `~/.claude/**`
- `toolbox/**`

## blocked_by

step 1

## can_parallel_with

step 4

## required_context

- `plans/gd/2026-05-13-knowledge-compounding/gd-step-3-gate-simplification.md`
- `plans/gd/2026-05-13-knowledge-compounding/gd-step-1-query-result-template.md`（query-result schema）
- `src/akb2/gate/akb2_gate_check.py`（现有 HC-01..HC-10 实现）

## deliverables

- `src/akb2/gate/akb2_gate_check.py`（修改后：HC-04 枚举 + query-result 跳过 HC-03/HC-05 + HC-07 derived_from）
- `tests/test_gate.py`（追加 2 个 query-result gate 测试）
- `tests/fixtures/gate/staging/candidate/candidate-query-result.md`（合法 query-result 候选页 fixture；路径 staging/candidate/ 满足 gate --input 参数约束）

## 实现步骤

1. **HC-04 枚举扩展**
   - WHERE: `src/akb2/gate/akb2_gate_check.py` `check_template_type()` 约 line 461，12 枚举列表
   - WHAT: 末尾追加 `"query-result"`
   - VERIFY: `python3 -c "import sys; sys.path.insert(0,'.'); from src.akb2.gate.akb2_gate_check import check_template_type; print(check_template_type({'template_type':'query-result'})[0])"` → `True`

2. **HC-03/HC-05 跳过**
   - WHERE: `src/akb2/gate/akb2_gate_check.py` `run_hard_checks()` 约 line 540，checks 列表组装
   - WHAT: 如果 `fm.get('template_type')=='query-result'`，从 checks 列表移除 HC-03-SOURCE-TYPE 和 HC-05-EVIDENCE
   - VERIFY: `/Users/praise/.pyenv/versions/3.13.12/bin/python3 src/akb2/gate/akb2_gate_check.py check --akb-root tests/fixtures/gate --input staging/candidate/candidate-query-result.md --dry-run 2>&1 | grep -c 'HC-03\|HC-05'` → 0

3. **HC-07 derived_from 替代**
   - WHERE: `src/akb2/gate/akb2_gate_check.py` `check_graph_link()` 约 line 494
   - WHAT: 增加条件：`template_type=='query-result'` 且 `derived_from` 非空列表 → 返回 `(True, ...)`
   - VERIFY: `/Users/praise/.pyenv/versions/3.13.12/bin/python3 src/akb2/gate/akb2_gate_check.py check --akb-root tests/fixtures/gate --input staging/candidate/candidate-query-result.md --dry-run 2>&1 | grep -c 'HC-07.*fail'` → 0

4. **创建 fixture**
   - WHERE: `tests/fixtures/gate/staging/candidate/candidate-query-result.md`
   - WHAT: 合法 query-result 候选页，frontmatter 含 template_type:query-result + derived_from:[有效slug] + query:"非空" + confidence:high + page_status:candidate + future_questions（2-3 条具体问题，满足 HC-06）；正文含 `## TLDR` 段落（满足 HC-08）
   - VERIFY: `test -f tests/fixtures/gate/staging/candidate/candidate-query-result.md && grep -q 'future_questions' tests/fixtures/gate/staging/candidate/candidate-query-result.md && grep -q '## TLDR' tests/fixtures/gate/staging/candidate/candidate-query-result.md`

5. **追加测试**
   - WHERE: `tests/test_gate.py`
   - WHAT: 新增 `test_query_result_gate_pass()` 断言合法 query-result 通过 gate；新增 `test_query_result_no_derived_from_fail()` 断言缺 derived_from 的 query-result fail gate
   - VERIFY: `python3 -m pytest tests/test_gate.py -v` all passed

## 验证

```bash
# preflight: verify check subcommand exists and python3.13 runs the gate without TypeError
/Users/praise/.pyenv/versions/3.13.12/bin/python3 src/akb2/gate/akb2_gate_check.py --help 2>&1 | grep -q "{check}\|check.*Run L3 gate" && echo "PASS: check subcommand available" || echo "FAIL: check subcommand missing"

# gate dry-run with new CLI and correct fixture layout
/Users/praise/.pyenv/versions/3.13.12/bin/python3 src/akb2/gate/akb2_gate_check.py check --akb-root tests/fixtures/gate --input staging/candidate/candidate-query-result.md --dry-run
python3 -m pytest tests/test_gate.py -v
```

## 非目标

- 不修改 HC-01/02/06/08/09/10
- 不修改 lint（属 step 4）
