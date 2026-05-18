# Step 2: staging→wiki 自动提升

日期：2026-05-17 | blocked_by：— | can_parallel_with：step 1 | SC：SC-2

## 目标

新建 `src/akb2/promotion/promote.py`：读 staging/candidate/ 通过 gate 的页面，按 frontmatter slug 复制到 wiki/<template_type>/，更新 page_status=active，写 promotion log。--dry-run 默认，--apply 模式 slug 冲突直接 exit 1 不覆盖。

## owned_paths

- `src/akb2/promotion/`
- `tests/test_promotion.py`

## forbidden_paths

- `${WORKSPACE}/skills/**`（属 step 1/3/4/5）
- `src/akb2/lint/**`、`src/akb2/gate/**`、`src/akb2/index/**`（Plan 2 v3 已覆盖）
- `${AKB2_ROOT}/wiki/**`（--dry-run 不写；--apply 写入由测试覆盖临时 vault）

## blocked_by

（无）

## can_parallel_with

step 1

## required_context

- `src/akb2/gate/akb2_gate_check.py`（读 gate 决策结果格式）
- `${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md`（template_type 与目录对应规则）

## deliverables

- `src/akb2/promotion/__init__.py`（空包标记）
- `src/akb2/promotion/promote.py`（提升脚本，--dry-run/--apply 双模式）
- `tests/test_promotion.py`（4 个 pytest 测试）

## 实现步骤

1. **创建包结构**
   - WHERE: `src/akb2/promotion/__init__.py`
   - WHAT: 空文件
   - VERIFY: `test -f src/akb2/promotion/__init__.py`

2. **实现 promote.py**
   - WHERE: `src/akb2/promotion/promote.py`
   - WHAT: argparse 设计：`--staging-root`（必填）、`--wiki-root`（必填）、`--dry-run`（默认）/`--apply` 互斥
     - 遍历 staging-root 下所有 .md 文件，跳过非 candidate 状态
     - 调 `akb2_gate_check.check` 接口判断 gate_decision；只处理 `accept` 决策
     - 按 frontmatter `template_type` 决定目标子目录（query-result → wiki/synthesis/）
     - --dry-run：输出 `would promote <slug>: <staging-path> → <wiki-path>`，不写文件
     - --apply：复制文件，修改 page_status frontmatter 为 active，写到目标路径；slug 冲突 exit 1
     - 写 promotion log 到 stdout JSON：`{"slug": ..., "from": ..., "to": ..., "action": "promoted|skipped|conflict"}`
   - VERIFY: `python3 src/akb2/promotion/promote.py --help` exit 0 且输出含 `--dry-run` `--apply` `--staging-root` `--wiki-root`

3. **写测试**
   - WHERE: `tests/test_promotion.py`
   - WHAT: 4 个 pytest 测试：
     - `test_dry_run_outputs_would_promote`：候选页通过 gate → dry-run 输出 "would promote"
     - `test_apply_copies_to_wiki`：--apply 后 wiki/<type>/<slug>.md 存在且 page_status=active
     - `test_apply_rejects_slug_conflict`：wiki/ 已有同 slug 文件 → --apply exit 1
     - `test_dry_run_skips_gate_rejected`：候选页 gate decision=reject → dry-run 输出不含该 slug
   - VERIFY: `python3 -m pytest tests/test_promotion.py -v`

## 验证

```bash
python3 src/akb2/promotion/promote.py --help
python3 src/akb2/promotion/promote.py --dry-run --staging-root /tmp/test/staging/candidate --wiki-root /tmp/test/wiki
python3 -m pytest tests/test_promotion.py -v
```

## 非目标

- 不改 gate 决策逻辑（Plan 2 v3 已锁定）
- 不实现 wiki→staging 回退（rollback 属 future）
- --apply 不自动调用 backlinks 更新（属 step 4）
- 不写 log.md（属 step 5）
