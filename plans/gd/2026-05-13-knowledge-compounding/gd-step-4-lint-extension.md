# Step 4: lint 规则扩展

日期：2026-05-14 | blocked_by：step 1 | can_parallel_with：step 3 | SC：SC-3

## 目标

修改 akb2_lint.py：
1. 新增单文件位置参数支持（让 `python3 akb2_lint.py --version v0.3 <file>` 可运行）
2. R6/R7/R11/R14 加 query-result guard 避免误报
3. R16（TEMPLATE-DIRECTORY-MISMATCH）加 query-result 兼容：在 `TEMPLATE_DIRECTORY_MAP` 中为 query-result 增加 canonical 目录或添加豁免条件
4. 新增 R23(derived_from 存在且 slug 有效)/R24(confidence 枚举)/R25(query 非空)

## forbidden_paths

- `src/akb2/gate/**`（gate 属 step 3）
- `toolbox/**`
- `~/.claude/**`

## blocked_by

step 1

## can_parallel_with

step 3

## required_context

- `plans/gd/2026-05-13-knowledge-compounding/gd-step-4-lint-extension.md`
- `plans/gd/2026-05-13-knowledge-compounding/gd-step-1-query-result-template.md`（query-result schema）

## owned_paths

- `src/akb2/lint/akb2_lint.py`
- `tests/test_lint.py`
- `tests/fixtures/compound/valid-query-result.md`
- `tests/fixtures/compound/invalid-missing-derived.md`
- `tests/fixtures/compound/invalid-bad-confidence.md`
- `tests/fixtures/compound/invalid-empty-query.md`
- `tests/fixtures/compound/invalid-bad-derived-slug.md`

## deliverables

- `src/akb2/lint/akb2_lint.py`（v0.3：单文件位置参数 + R6/R7/R11/R14 guard + R16 query-result 兼容 + R23/R24/R25）
- `tests/test_lint.py`（5 个新测试）
- `tests/fixtures/compound/valid-query-result.md`
- `tests/fixtures/compound/invalid-missing-derived.md`
- `tests/fixtures/compound/invalid-bad-confidence.md`
- `tests/fixtures/compound/invalid-empty-query.md`
- `tests/fixtures/compound/invalid-bad-derived-slug.md`

## 实现步骤

0. **单文件位置参数支持**
   - WHERE: `src/akb2/lint/akb2_lint.py` argparse 块
   - WHAT: 增加 `paths` 位置参数列表（`nargs='*'`）；若提供，对每个 path 单独运行 lint；lint 结果有 P1/P0 时 exit 1，否则 exit 0；不影响现有 `--vault-root` 模式
   - VERIFY: `python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/valid-query-result.md` → exit 0（不报 `unrecognized arguments`）

0b. **R16 query-result 兼容**
   - WHERE: `src/akb2/lint/akb2_lint.py` `TEMPLATE_DIRECTORY_MAP` 或 `rule_r16_template_dir_mismatch()` 函数
   - WHAT: 为 `query-result` 在 `TEMPLATE_DIRECTORY_MAP` 中增加 `staging/candidate` 或添加条件 `if template_type=='query-result': return []`（R16 豁免）
   - VERIFY: `python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/valid-query-result.md 2>&1 | grep -c TEMPLATE-DIRECTORY-MISMATCH` → 0

1. **R6 guard**
   - WHERE: `src/akb2/lint/akb2_lint.py` `rule_r6_body_quality_missing_structure()` 函数开头
   - WHAT: 加 `if ctx.get('template_type')=='query-result': return []`（1 行）
   - VERIFY: `python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/valid-query-result.md 2>&1 | grep -c R6` → 0

2. **R7 guard**
   - WHERE: `src/akb2/lint/akb2_lint.py` `rule_r7_source_grounded_missing_field()` 函数开头
   - WHAT: 加 `if ctx.get('template_type')=='query-result': return []`（1 行）
   - VERIFY: `python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/valid-query-result.md 2>&1 | grep -c R7` → 0

3. **R11 guard**
   - WHERE: `src/akb2/lint/akb2_lint.py` `rule_r11_graph_role_missing()` 函数开头
   - WHAT: 加 `if ctx.get('template_type')=='query-result': return []`（1 行）
   - VERIFY: `python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/valid-query-result.md 2>&1 | grep -c R11` → 0

4. **R14 guard**
   - WHERE: `src/akb2/lint/akb2_lint.py` `rule_r14_active_missing_related()` 函数开头
   - WHAT: 加 `if ctx.get('template_type')=='query-result': return []`（1 行）
   - VERIFY: `python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/valid-query-result.md 2>&1 | grep -c R14` → 0

5. **R23 新增**
   - WHERE: `src/akb2/lint/akb2_lint.py` v0.3 规则区域（R22 之后）
   - WHAT: 新增 `rule_r23_query_result_derived_from_exists()`，输出 rule_id 为 `QUERY-RESULT-DERIVED-FROM-MISSING`，检查 derived_from 数组非空且每个 slug 对应 wiki 页面文件存在
   - VERIFY: `python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/invalid-missing-derived.md 2>&1 | grep -c 'QUERY-RESULT-DERIVED-FROM-MISSING'` → >= 1

6. **R24 新增**
   - WHERE: `src/akb2/lint/akb2_lint.py` v0.3 规则区域
   - WHAT: 新增 `rule_r24_query_result_confidence_invalid()`，输出 rule_id 为 `QUERY-RESULT-CONFIDENCE-INVALID`，检查 confidence ∈ {high, medium, low}
   - VERIFY: `python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/invalid-bad-confidence.md 2>&1 | grep -c 'QUERY-RESULT-CONFIDENCE-INVALID'` → >= 1

7. **R25 新增**
   - WHERE: `src/akb2/lint/akb2_lint.py` v0.3 规则区域
   - WHAT: 新增 `rule_r25_query_result_query_empty()`，输出 rule_id 为 `QUERY-RESULT-QUERY-EMPTY`，检查 query 非空字符串
   - VERIFY: `python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/invalid-empty-query.md 2>&1 | grep -c 'QUERY-RESULT-QUERY-EMPTY'` → >= 1

8. **创建 fixture: valid-query-result.md**
   - WHERE: `tests/fixtures/compound/valid-query-result.md`
   - WHAT: 合法 query-result 页面，frontmatter 含 template_type:query-result + derived_from:[存在的slug] + query:"非空问题" + confidence:high
   - VERIFY: `test -f tests/fixtures/compound/valid-query-result.md`

9. **创建 fixture: invalid-missing-derived.md**
   - WHERE: `tests/fixtures/compound/invalid-missing-derived.md`
   - WHAT: query-result 页面缺 derived_from 字段
   - VERIFY: `test -f tests/fixtures/compound/invalid-missing-derived.md`

10. **创建 fixture: invalid-bad-confidence.md**
    - WHERE: `tests/fixtures/compound/invalid-bad-confidence.md`
    - WHAT: query-result 页面 confidence 值为 "very-high"（非法枚举）
    - VERIFY: `test -f tests/fixtures/compound/invalid-bad-confidence.md`

11. **创建 fixture: invalid-empty-query.md**
    - WHERE: `tests/fixtures/compound/invalid-empty-query.md`
    - WHAT: query-result 页面 query 字段为空字符串
    - VERIFY: `test -f tests/fixtures/compound/invalid-empty-query.md`

11b. **创建 fixture: invalid-bad-derived-slug.md**
    - WHERE: `tests/fixtures/compound/invalid-bad-derived-slug.md`
    - WHAT: query-result 页面 derived_from 含不存在的 slug（如 `["nonexistent-page-xyz"]`），验证 R23 slug 有效性检查
    - VERIFY: `test -f tests/fixtures/compound/invalid-bad-derived-slug.md`

12. **追加测试: valid pass**
    - WHERE: `tests/test_lint.py`
    - WHAT: 新增 `test_query_result_valid_no_p0_p1()`，断言 valid-query-result.md lint exit 0 无 P0/P1
    - VERIFY: `python3 -m pytest tests/test_lint.py::test_query_result_valid_no_p0_p1 -v`

13. **追加测试: R23 报错**
    - WHERE: `tests/test_lint.py`
    - WHAT: 新增 `test_query_result_missing_derived_r23()`，断言 invalid-missing-derived.md 报 R23
    - VERIFY: `python3 -m pytest tests/test_lint.py::test_query_result_missing_derived_r23 -v`

14. **追加测试: R24 报错**
    - WHERE: `tests/test_lint.py`
    - WHAT: 新增 `test_query_result_bad_confidence_r24()`，断言 invalid-bad-confidence.md 报 R24
    - VERIFY: `python3 -m pytest tests/test_lint.py::test_query_result_bad_confidence_r24 -v`

15. **追加测试: R25 报错**
    - WHERE: `tests/test_lint.py`
    - WHAT: 新增 `test_query_result_empty_query_r25()`，断言 invalid-empty-query.md 报 R25
    - VERIFY: `python3 -m pytest tests/test_lint.py::test_query_result_empty_query_r25 -v`

16. **追加测试: R23 slug 无效报错**
    - WHERE: `tests/test_lint.py`
    - WHAT: 新增 `test_query_result_bad_derived_slug_r23()`，断言 invalid-bad-derived-slug.md 报 R23（slug 指向不存在页面）
    - VERIFY: `python3 -m pytest tests/test_lint.py::test_query_result_bad_derived_slug_r23 -v`

## 验证

```bash
python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/valid-query-result.md
# exit 0
python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/invalid-missing-derived.md 2>&1 | grep 'QUERY-RESULT-DERIVED-FROM-MISSING'
# 期望: 输出含 QUERY-RESULT-DERIVED-FROM-MISSING
python3 -m pytest tests/test_lint.py -v
```

## 非目标

- 不修改 R1-R5/R8-R10/R12-R13/R15-R22 逻辑
- 不修改 gate（属 step 3）
