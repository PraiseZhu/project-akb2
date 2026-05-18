# Step 6 Execution Output
step_id: step-6
exec_status: completed

## Deliverables
- [x] tests/test_live_compound_e2e.py — 3 个测试（test_full_loop / test_promote_blocks_conflict / test_backlinks_after_save）

## Verify Results
- test_full_loop: PASSED
- test_promote_blocks_conflict: PASSED
- test_backlinks_after_save: PASSED

## Pytest Output
```
============================= test session starts ==============================
platform darwin -- Python 3.13.12, pytest-9.0.3, pluggy-1.6.0 -- /Users/praise/.pyenv/versions/3.13.12/bin/python3
cachedir: .pytest_cache
rootdir: /Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/Project AKB2/.claude/worktrees/knowledge-compounding-v3
configfile: pyproject.toml
plugins: anyio-4.12.1
collecting ... collected 3 items

tests/test_live_compound_e2e.py::test_full_loop PASSED                   [ 33%]
tests/test_live_compound_e2e.py::test_promote_blocks_conflict PASSED     [ 66%]
tests/test_live_compound_e2e.py::test_backlinks_after_save PASSED        [100%]

============================== 3 passed in 0.28s ===============================
```

## SC Acceptance
SC-7: pass
evidence: pytest all passed — 3 passed in 0.28s, 0 failed

## Findings
none

## 实现说明

### test_full_loop 关键设计决策
- 候选页 frontmatter 包含 `gate_status: pass` 以绕过 gate subprocess 调用（promote.py 优先读 frontmatter 的 gate_status）
- lint 使用 `--version v0.3 --vault-root tmp_path` 使 R23（derived_from 有效性检查）能找到 wiki/synthesis/ 下的源页
- gate check 的 `--akb-root` 指向 tmp_path，`--input` 为相对路径 `staging/candidate/query-result-test.md`
- promote 的 `--staging-root` 指向 staging/candidate/，`--wiki-root` 指向 wiki/
- backlinks 的 `--wiki-root` 指向 wiki/（脚本扫描 wiki/synthesis/*.md）
- index 的 `--vault-root` 指向 tmp_path（脚本在 wiki/ 下搜索 active 页）

### 候选页 frontmatter 满足 gate 硬检查清单
- HC-01: 在 staging/candidate/ 路径下 ✓
- HC-02: page_status=candidate ✓
- HC-03: 跳过（query-result 模板）✓
- HC-04: template_type=query-result（有效枚举）✓
- HC-05: 跳过（query-result 模板）✓
- HC-06: future_questions 含 2 项 ✓
- HC-07: derived_from 非空满足 graph link ✓
- HC-08: 正文含 ## TLDR ✓
- HC-09: gate_decision 未预填 active ✓
