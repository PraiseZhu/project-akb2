# /gd review code -- 执行结果静态字段校验

REVIEW_KIND: execution_plus_code
REVIEWER: claude
REVIEWED_AT: 2026-06-13T02:00:00Z
BASE_COMMIT: d9af73f
HEAD_COMMIT: 20f40ec

## EXEC_STATUS 一致性

| Step | result exec_status | controller status | 一致? |
|------|-------------------|-------------------|-------|
| e-step1 | completed | completed / APPROVED | 一致 |
| e-step2 | completed | completed / APPROVED | 一致 |
| e-step3 | completed | completed / APPROVED | 一致 |
| e-step4 | PASS | completed / APPROVED | 一致 |
| e-step5 | completed | completed / APPROVED | 一致 |
| e-step6 | completed | completed / APPROVED | 一致 |

注: e-step4 使用 `result: PASS` 而非 `exec_status: completed`，语义等价，但格式不统一。e-step2 使用嵌套 `meta.status` 路径。建议后续统一为顶层 `exec_status` 字段。

## SC Acceptance 完整性

### e-step1 (6 SCs)

| SC | status | evidence/not_run_reason 完整? | 问题 |
|----|--------|------------------------------|------|
| SC-s1 | pass | 完整 | -- |
| SC-s2 | pass | 完整 | -- |
| SC-s3 | pass | 完整 | -- |
| SC-s4 | pass | 完整 | -- |
| SC-s5 | pass | 完整 | -- |
| SC-s6 | pass | 完整 | -- |

### e-step2 (6 SCs)

| SC | status | evidence/not_run_reason 完整? | 问题 |
|----|--------|------------------------------|------|
| SC-2.1 | pass | 完整 | -- |
| SC-2.2 | pass | 完整 | -- |
| SC-2.3 | pass | 完整 | -- |
| SC-2.4 | pass | 完整 | -- |
| SC-2.5 | pass | 完整 | -- |
| SC-2.6 | pass | 完整 | -- |

注: e-step2 使用 `sc_id` 字段名而非 `sc_ref`，与其余 5 个 step 不一致。

### e-step3 (7 SCs)

| SC | status | evidence/not_run_reason 完整? | 问题 |
|----|--------|------------------------------|------|
| SC-3.1 | pass | 完整 | -- |
| SC-3.2 | pass | 完整 | -- |
| SC-3.3 | pass | 完整 | -- |
| SC-3.4 | pass | 完整 | -- |
| SC-3.5 | pass | 完整 | -- |
| SC-3.6 | pass | 完整 | -- |
| SC-3.7 | pass | 完整 | -- |

### e-step4 (6 SCs)

| SC | status | evidence/not_run_reason 完整? | 问题 |
|----|--------|------------------------------|------|
| SC-4.1 | pass | 完整 | -- |
| SC-4.2 | pass | 完整 | -- |
| SC-4.3 | pass | 完整 | -- |
| SC-4.4 | pass | 完整 | -- |
| SC-4.5 | pass | 完整 | -- |
| SC-4.6 | pass | 完整 | -- |

### e-step5 (5 SCs)

| SC | status | evidence/not_run_reason 完整? | 问题 |
|----|--------|------------------------------|------|
| SC-5.1 | pass | 完整 | -- |
| SC-5.2 | pass | 完整 | -- |
| SC-5.3 | pass | 完整 | -- |
| SC-5.4 | pass | 完整 | -- |
| SC-5.5 | pass | 完整 | -- |

### e-step6 (10 SCs)

| SC | status | evidence/not_run_reason 完整? | 问题 |
|----|--------|------------------------------|------|
| SC-6.1 | pass | 完整 | -- |
| SC-6.2-build-gate | pass | 完整 | -- |
| SC-6.3 | pass | 完整 | -- |
| SC-6.4 | not_run | 完整 (note) | 真 API 项，合理延期 |
| SC-6.5 | pass | 完整 | -- |
| SC-6.6 | pass | 完整 | -- |
| SC-6.7 | pass | 完整 | -- |
| SC-6.8 | pass | 完整 | -- |
| SC-6.9 | not_run | 完整 (note) | 真 API 项，合理延期 |
| SC-6.10 | not_run | 完整 (note) | 依赖 SC-6.4，合理延期 |

**Anti-fill 关键词扫描**: 全 6 个 result 文件中未发现 "目视确认"、"目视检查"、"看看是否正确"、"自检即可" 等 anti-fill 关键词。

**总体**: 所有 SC 的 status 字段均为合法枚举值，pass 均有非空 evidence，not_run 均有 not_run_reason。格式上存在 `sc_ref` vs `sc_id` 字段名不一致（e-step2），以及 e-step3 使用表格格式而其他 step 使用 YAML 列表格式的问题，但不影响数据完整性。

## Forbidden Paths 合规

| Track | forbidden_paths 数量 | 有文件触及? | 严重度 |
|-------|---------------------|------------|--------|
| e-step1 | 16 条规则 | 无 | -- |
| e-step2 | 16 条规则 | 无 | -- |
| e-step3 | 17 条规则 | 无 | -- |
| e-step4 | 16 条规则 | 无 | -- |
| e-step5 | 18 条规则 | 无 | -- |
| e-step6 | 12 条规则 | 无 | -- |

**验证方法**: `git diff d9af73f..HEAD --name-only` 产生 94 个变更文件。逐一对照每个 track 的 forbidden_paths 规则，无命中。

**总体**: 全部通过。禁止路径机制有效防止了跨 step 的文件污染。

## Owned Paths 越界检查

| Step | 越界文件 | 严重度 | 说明 |
|------|---------|--------|------|
| e-step2 | `tests/quality/conftest.py` | MEDIUM | step2 owned_paths 只列了 `test_intake_*.py`，不含 `conftest.py`。该文件为 pytest 共享 fixture，是 step2+step3 并行执行的实际需要 |
| e-step3 | `src/akb2/quality/__init__.py` | LOW | Python 包标记文件，创建新包的自然产物 |
| e-step3 | `tests/quality/__init__.py` | LOW | Python 包标记文件 |
| e-step3 | `tests/quality/fixtures/__init__.py` | LOW | Python 包标记文件 |
| e-step3 | `tests/quality/conftest.py` | MEDIUM | 同 e-step2 说明，并行 step 共享的 fixture 文件 |
| e-step5 | `tests/quality/fixtures/grader_result_pass.json` | MEDIUM | step5 owned_paths 仅含 `tests/quality/fixtures/vault_fixture/`，不含同级的 fixture JSON 文件 |
| e-step5 | `tests/quality/fixtures/grader_result_fail.json` | MEDIUM | 同上 |

**e-step6 额外发现**: e-step6 result 声称修改了 `pyproject.toml`（新增 pytest markers），但该文件不在 `git diff d9af73f..HEAD --name-only` 中，说明修改未落地或已在 base commit 中。此声称与实际不符。

**根因分析**: `__init__.py` 类越界是 dispatch_map 设计时的已知盲区 -- Python 新包的 `__init__.py` 是隐式必需品但未被显式列入 owned_paths。`conftest.py` 和 fixture JSON 文件则是并行 step 之间的共享依赖未被 declared_paths 机制覆盖。

## 代码质量扫描

### 文件行数

| 文件 | 行数 | 超 800? |
|------|------|---------|
| src/akb2/quality/promote.py | 1078 | **是** |
| src/akb2/quality/runner.py | 911 | **是** |
| src/akb2/quality/calibration.py | 631 | 否 |
| src/akb2/quality/checks_structural.py | 617 | 否 |
| src/akb2/quality/distill.py | 613 | 否 |
| src/akb2/quality/intake/raw_archiver.py | 441 | 否 |
| src/akb2/quality/admission.py | 413 | 否 |
| src/akb2/quality/intake/discovery.py | 392 | 否 |
| src/akb2/quality/grader.py | 348 | 否 |
| src/akb2/quality/repair.py | 347 | 否 |
| src/akb2/quality/intake/extract.py | 334 | 否 |
| src/akb2/quality/llm_router.py | 234 | 否 |
| 其余文件 | < 200 | 否 |

### 超长函数 (>100 行)

| 文件 | 函数 | 行数 |
|------|------|------|
| promote.py | `finalize_promotion` | 166 |
| repair.py | `run_repair_loop` | 161 |
| calibration.py | `_process_corpus_entry` | 114 |
| checks_structural.py | `check_a2_frontmatter` | 109 |
| runner.py | `main` | 107 |
| distill.py | `extract_claims` | 103 |

### TODO/FIXME 残留

无。`grep -rn 'TODO\|FIXME\|HACK\|XXX' src/akb2/loops/ src/akb2/quality/` 无命中。

### 硬编码凭证

无。`llm_router.py:151` 的 `api_key=api_key` 使用函数参数变量，非硬编码。`admission.py:173,399` 分别为注释和数据结构字段名，非凭证。

## 审查结论

VERDICT: APPROVED

### 裁决理由

6 个 step 的执行结果与 controller-report 完全一致，SC acceptance 全覆盖且 evidence 充实，forbidden_paths 零违规，无 TODO/FIXME/硬编码凭证残留。所有测试绿 (293 passed, 0 failed)，ruff 零问题。

### Findings

**F1 (MEDIUM) -- Owned Paths 越界: 共享依赖未声明**

`tests/quality/conftest.py`、`tests/quality/fixtures/grader_result_pass.json`、`tests/quality/fixtures/grader_result_fail.json` 不在对应 step 的 owned_paths 内。根因是 dispatch_map 未预留 "shared fixtures" 或 "common test infra" 路径槽位。建议在 dispatch_map 中新增 `shared_paths` 字段，或在 owned_paths 中显式列出跨 step 共享的测试基础设施文件。

**F2 (LOW) -- Python 包标记文件隐式越界**

`src/akb2/quality/__init__.py`、`tests/quality/__init__.py`、`tests/quality/fixtures/__init__.py` 三个 `__init__.py` 不在对应 step 的 owned_paths 内。这些是新 Python 包的必要文件。建议 dispatch_map 生成逻辑自动将新建目录的 `__init__.py` 纳入 owned_paths。

**F3 (LOW) -- 文件行数超标**

`promote.py` (1078 行) 和 `runner.py` (911 行) 超过 800 行阈值。`finalize_promotion` (166 行)、`run_repair_loop` (161 行) 等 6 个函数超过 100 行阈值。建议后续迭代中拆分，但不阻塞当前交付。

**F4 (LOW) -- e-step6 声称修改 pyproject.toml 但未落地**

e-step6 result 的 deliverables 声称 `pyproject.toml: modified (新增 pytest markers)`，但 `git diff d9af73f..HEAD` 中无此文件。pytest markers (`proposal_mode`, `post_amendment`) 在文件中实际存在，表明 markers 已在 base commit 中或通过其他方式写入。声称与 git 记录不一致。

**F5 (LOW) -- result YAML 格式不统一**

e-step2 使用 `sc_id` 而非 `sc_ref`，e-step3 使用 Markdown 表格而非 YAML 列表，e-step4 使用 `result: PASS` 而非 `exec_status: completed`。建议后续统一为 `exec_status + sc_ref + YAML 列表` 的单一格式。

---

以上 findings 均不阻塞 APPROVED 裁决。F1/F2 建议在下次 dispatch_map 生成时修复，F3 为代码卫生建议，F4 为记录准确性建议，F5 为格式统一建议。