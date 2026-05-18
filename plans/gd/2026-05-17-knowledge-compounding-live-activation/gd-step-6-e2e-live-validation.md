# Step 6: E2E live 链路验证

日期：2026-05-17 | blocked_by：step 1, 2, 3, 4, 5 | can_parallel_with：— | SC：SC-7

## 目标

新建 tests/test_live_compound_e2e.py，用 pytest tmp_path 构造临时 vault，模拟"问→答→存→提升→重检索"全链路，断言各环节正确。

## owned_paths

- `tests/test_live_compound_e2e.py`

## forbidden_paths

- `src/akb2/**`（只调用，不改实现）
- `${WORKSPACE}/skills/**`（只读 skill 配置）
- `${AKB2_ROOT}/**`（用 tmp_path 临时 vault）
- `~/.claude/**`

## blocked_by

step 1, step 2, step 3, step 4, step 5（依赖全部代码就绪）

## can_parallel_with

（无；E2E 是 W4 单独 wave）

## required_context

- `plans/gd/2026-05-17-knowledge-compounding-live-activation/gd-master-knowledge-compounding-plan3.md`（SC-7 完整验收要求）
- `src/akb2/promotion/promote.py`（promotion 接口）
- `src/akb2/backlinks/update_backlinks.py`（backlinks 接口）
- `src/akb2/lint/akb2_lint.py`（Plan 2 v3 lint）
- `src/akb2/gate/akb2_gate_check.py`（Plan 2 v3 gate）
- `src/akb2/index/generate_index.py`（Plan 2 v3 index）

## deliverables

- `tests/test_live_compound_e2e.py`（含 test_full_loop + test_promote_blocks_conflict + test_backlinks_after_save）

## 实现步骤

1. **E2E 主测试 test_full_loop**
   - WHERE: `tests/test_live_compound_e2e.py`
   - WHAT: 用 pytest tmp_path 构造临时 vault：
     - `tmp_path/wiki/synthesis/`（含 3 个 active 源页：source-a/source-b/source-c）
     - `tmp_path/staging/candidate/`（初始空）
     - `tmp_path/data/backlinks.jsonl`（初始空）
   - 模拟 save 流程：
     - 构造 candidate frontmatter（template_type=query-result, derived_from=[source-a, source-b], query="测试问题", confidence=high, page_status=candidate, future_questions=[...], + ## TLDR 正文）
     - 写入 `tmp_path/staging/candidate/query-result-test.md`
     - 调 `python3 -m src.akb2.lint.akb2_lint --version v0.3 <file>` 断言 exit 0
     - 调 `python3 src/akb2/gate/akb2_gate_check.py check --akb-root <tmp_path> --input staging/candidate/query-result-test.md --dry-run` 断言 exit 0
   - 模拟 promote：
     - 调 `python3 src/akb2/promotion/promote.py --apply --staging-root <tmp_path>/staging/candidate --wiki-root <tmp_path>/wiki`
     - 断言 `tmp_path/wiki/synthesis/query-result-test.md` 存在
     - 断言新文件 frontmatter `page_status: active`
   - 模拟 backlinks 更新：
     - 调 `python3 src/akb2/backlinks/update_backlinks.py --rebuild --wiki-root <tmp_path>/wiki --backlinks-file <tmp_path>/data/backlinks.jsonl`
     - 断言 backlinks.jsonl 含两行：source-a 和 source-b 各自 referenced_by 含 query-result-test
   - 模拟重检索：
     - 调 `python3 src/akb2/index/generate_index.py --dry-run --vault-root <tmp_path>` 断言输出含 "query-result-test"
   - VERIFY: `python3 -m pytest tests/test_live_compound_e2e.py::test_full_loop -v` PASSED

2. **冲突测试 test_promote_blocks_conflict**
   - WHERE: `tests/test_live_compound_e2e.py`
   - WHAT: 临时 vault 中 wiki/synthesis/ 已有 same-slug 文件 → promote --apply 必须 exit 1，不覆盖
   - VERIFY: `python3 -m pytest tests/test_live_compound_e2e.py::test_promote_blocks_conflict -v` PASSED

3. **反向索引测试 test_backlinks_after_save**
   - WHERE: `tests/test_live_compound_e2e.py`
   - WHAT: 构造 2 个 query-result 共引一个 source-a → rebuild backlinks 后 source-a 的 referenced_by 长度=2
   - VERIFY: `python3 -m pytest tests/test_live_compound_e2e.py::test_backlinks_after_save -v` PASSED

## 验证

```bash
python3 -m pytest tests/test_live_compound_e2e.py -v
# 期望: test_full_loop PASSED, test_promote_blocks_conflict PASSED, test_backlinks_after_save PASSED
```

## 非目标

- 不测试 skill 的 AskUserQuestion 交互（skill 是文档不是可测试代码；交互测试属手动验收）
- 不测试 log.md 写入（属 manual smoke；fcntl 行为测试属 future）
- 不触碰真实 vault（pytest tmp_path 严格隔离）
- 不调用 MCP（test 走 subprocess CLI 接口）
