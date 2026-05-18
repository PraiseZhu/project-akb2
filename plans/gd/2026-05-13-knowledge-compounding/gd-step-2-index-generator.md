# Step 2: 00-Index.md 生成器

日期：2026-05-14 | blocked_by：— | can_parallel_with：step 1 | SC：SC-2

## 目标

新建 src/akb2/index/generate_index.py，遍历 vault wiki/ 按 template_type 分组生成 00-Index.md，让 LLM 通过 Read 一个文件发现全部 wiki 页面。

## owned_paths

- `src/akb2/index/`
- `tests/test_index.py`

## forbidden_paths

- `${AKB2_ROOT}/**`（除 `wiki/00-Index.md` by --apply；--apply 只在临时 vault 执行证明，不改真实 vault）
- `src/akb2/gate/**`
- `src/akb2/lint/**`
- `~/.claude/**`
- `toolbox/**`

## blocked_by

（无）

## can_parallel_with

step 1

## required_context

- `plans/gd/2026-05-13-knowledge-compounding/gd-step-2-index-generator.md`

## deliverables

- `src/akb2/index/generate_index.py`
- `src/akb2/index/__init__.py`
- `tests/test_index.py`

## 实现步骤

1. **创建包结构**
   - WHERE: `src/akb2/index/__init__.py`
   - WHAT: 空文件
   - VERIFY: `test -f src/akb2/index/__init__.py`

2. **实现生成器**
   - WHERE: `src/akb2/index/generate_index.py`
   - WHAT: argparse 设计：`--vault-root`（必填普通参数，与输出模式无关）；`--dry-run` 和 `--apply` 互斥（二选一，不传时默认 `--dry-run`）；遍历 vault-root/wiki/ 子目录读取 .md frontmatter(title/tldr/tags/template_type/page_status)；排除 `00-Index.md` 自身（自排除）；按 template_type 分组；输出 `## <类型>` + 页面列表(title + TLDR + tags)；`--dry-run`：输出 stdout，不写文件；`--apply`：写 `wiki/00-Index.md`（含 `<!-- AUTO-GENERATED -->` 标记），写成功 `exit 0`，写失败 `exit 1`
   - VERIFY: `python3 src/akb2/index/generate_index.py --dry-run --vault-root "${AKB2_ROOT}" | head -20`

3. **编写测试**
   - WHERE: `tests/test_index.py`
   - WHAT: 3 个 pytest 测试——空 wiki/ 不崩溃、单页面输出含标题、多模板类型有多个 `##` 分组
   - VERIFY: `python3 -m pytest tests/test_index.py -v`

## 验证

```bash
# dry-run 验证
python3 src/akb2/index/generate_index.py --dry-run --vault-root "${AKB2_ROOT}" | head -20
python3 -m pytest tests/test_index.py -v
```

## Non-Dry-Run Proof（临时 vault，机器可验证）

SC-2 non-dry-run 实写证明，使用临时 vault 隔离，不写入真实 `${AKB2_ROOT}`：

```bash
# Step 2 临时 vault 实写证明
TMPVAULT=$(mktemp -d)
mkdir -p "${TMPVAULT}/wiki"

# 创建一个测试页面
cat > "${TMPVAULT}/wiki/test-page.md" << 'MDEOF'
---
title: Test Page
tldr: A test wiki page
tags: [test]
template_type: concept
page_status: live
---
Test content.
MDEOF

# 使用 --apply 实际写入 00-Index.md
python3 src/akb2/index/generate_index.py --vault-root "${TMPVAULT}" --apply

# 验证写入成功（条件 echo，失败则 exit 1）
test -f "${TMPVAULT}/wiki/00-Index.md" && echo "WROTE wiki/00-Index.md" || { echo "FAIL: 00-Index.md not written"; exit 1; }

# 验证 00-Index.md 自身不出现在 index 中（连续运行两次）
python3 src/akb2/index/generate_index.py --vault-root "${TMPVAULT}" --apply
! grep -q '00-Index.md' "${TMPVAULT}/wiki/00-Index.md" && echo "EXCLUDED self" || { echo "WARN: SELF INCLUDED — index must exclude itself"; exit 1; }

# 清理临时 vault
rm -rf "${TMPVAULT}"
```

期望输出（按行）：
- `WROTE wiki/00-Index.md` — 实写成功（exit 1 表示失败）
- `EXCLUDED self` — 00-Index.md 自身不出现在 index 内容中（exit 1 表示自排除未实现）

## 非目标

- 不写入真实 vault（--apply 仅在临时 vault 执行）
- 不解析 derived_from/query/confidence（只读通用字段）
