# Step 1: query-result 模板定义

日期：2026-05-14 | blocked_by：— | can_parallel_with：step 2 | SC：SC-1

## 目标

在 AKB-2.0-CONTENT-STANDARD.md PAGE-TEMPLATES §4 追加 query-result 模板，新增 derived_from/query/confidence 三个必填 frontmatter 字段，使 gate 和 lint 有明确 schema 依据。

## owned_paths

- `${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md`

## forbidden_paths

- `src/akb2/**`（代码属 step 2-4）
- `tests/**`（属 step 3-5）
- `~/.claude/**`
- `toolbox/**`

## blocked_by

（无）

## can_parallel_with

step 2

## required_context

- `plans/gd/2026-05-13-knowledge-compounding/gd-step-1-query-result-template.md`
- `${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md`（现有 PAGE-TEMPLATES §4）

## deliverables

- `${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md`（修改后：+§4.13 query-result 模板段落 + 枚举汇总表追加行）

## 实现步骤

1. **§4 追加模板段落**
   - WHERE: `${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md` PAGE-TEMPLATES §4（第 12 模板之后）
   - WHAT: 新增 §4.13 query-result，定义 `derived_from`（数组，引用源 wiki 页 slug）、`query`（字符串，原始查询文本）、`confidence`（枚举 high/medium/low）
   - VERIFY: `grep -c 'derived_from' "${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md"` >= 1

2. **枚举汇总表追加**
   - WHERE: `${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md` template_type 枚举汇总表
   - WHAT: 末尾新增 `| query-result | 查询综合结果页 |`
   - VERIFY: `grep -F '| query-result | 查询综合结果页 |' "${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md"` → exit 0

## 验证

```bash
# SC-1 全字段验收
TARGET="${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md"

grep -c 'query-result' "$TARGET"
# 期望: >= 3（模板名多次出现）

grep -c 'derived_from' "$TARGET"
# 期望: >= 1（必填字段 derived_from 存在于 §4.13）

grep -c 'query' "$TARGET"
# 期望: >= 1（必填字段 query 存在于 §4.13）

grep -c 'confidence' "$TARGET"
# 期望: >= 1（必填字段 confidence 存在于 §4.13）

grep -E 'high.*medium.*low|high|medium|low' "$TARGET" | grep -c 'confidence'
# 期望: >= 1（confidence 枚举值 high/medium/low 在文件中存在）

grep -F '| query-result | 查询综合结果页 |' "$TARGET"
# 期望: exit 0（枚举汇总表中存在 query-result 行）
```

## Future Targets (Plan 3 — Step 1 声明占位，本 Plan 2 不创建)

- `docs/AKB-2.0-PAGE-TEMPLATES.md` — 独立 PAGE-TEMPLATES 参考文档，由 Plan 3 创建；Step 1 的内容变更写入 `AKB-2.0-CONTENT-STANDARD.md`，不写 PAGE-TEMPLATES.md
  - deliverables 声明：**不在本步交付**，本计划期间文件不存在，不得 `test -f` 或 `cat` 此路径

## 非目标

- 不修改现有 12 模板字段
- 不修改 gate/lint 代码（属 step 3/4）
- 不创建 `docs/AKB-2.0-PAGE-TEMPLATES.md`（Plan 3 future target）
