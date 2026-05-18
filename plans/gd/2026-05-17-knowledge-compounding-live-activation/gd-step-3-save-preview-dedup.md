# Step 3: save 预览 + 去重

日期：2026-05-17 | blocked_by：step 1 | can_parallel_with：step 4 | SC：SC-3

## 目标

knowledge-ops skill save-query-result 子命令在写入 staging 前向用户展示 frontmatter + 正文 diff preview，并执行 slug 冲突检查（已存在则提示"替换/合并/取消"）、query 字符串完全匹配检查（已存在类似条目则提示）。

## owned_paths

- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（新增 Preview 和 Deduplication 段落）

## forbidden_paths

- `src/akb2/**`
- `${WORKSPACE}/skills/akb-search/**`
- `~/.claude/rules/**`（触发词表 step 1 已改）

## blocked_by

step 1（需要 save-query-result 基线已存在）

## can_parallel_with

step 4（修改 SKILL.md 不同段落，无内容冲突）

## required_context

- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（step 1 交付后的 save-query-result 段落）

## deliverables

- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（追加 `## Preview` 和 `## Deduplication` 段落）

## 实现步骤

1. **新增 Preview 段落**
   - WHERE: `${WORKSPACE}/skills/knowledge-ops/SKILL.md` save-query-result 段落之后
   - WHAT: 新增 `## Preview` 段落，定义：
     - 触发时机：save-query-result 提取完 query/derived_from/confidence 后、写文件前
     - 展示内容：完整 frontmatter（template_type/derived_from/query/confidence/page_status/title/tldr）+ 正文 markdown 预览（含 source pages 列表）
     - 用户操作：用 AskUserQuestion 提供"确认写入 / 修改 derived_from / 修改 confidence / 取消"4 个选项
     - 取消则 skill exit 0，不写任何文件
   - VERIFY: `grep -c '## Preview' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 1

2. **新增 Deduplication 段落**
   - WHERE: `${WORKSPACE}/skills/knowledge-ops/SKILL.md` Preview 段落之后
   - WHAT: 新增 `## Deduplication` 段落，定义：
     - slug 冲突检查：写入前用 `ls ${AKB2_ROOT}/staging/candidate/<slug>.md ${AKB2_ROOT}/wiki/synthesis/<slug>.md` 检测；已存在 → AskUserQuestion 提供"替换 / 合并 derived_from / 取消"3 选项
     - query 字符串匹配检查：grep 所有 query-result 页面 frontmatter `query:` 字段，与新 query 字符串完全匹配 → 提示"已存在类似条目: <slug>"并请用户确认是否仍要新建
     - 合并 derived_from 选项：读旧页 frontmatter derived_from，与新条目合并去重后写回原 slug
   - VERIFY: `grep -c '## Deduplication' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 1

3. **save-query-result 流程引用**
   - WHERE: `${WORKSPACE}/skills/knowledge-ops/SKILL.md` save-query-result 段落内
   - WHAT: 在"自动校验"步骤之前插入引用：`先执行 Preview 段落定义的展示和确认流程，再执行 Deduplication 段落定义的冲突检查`
   - VERIFY: `grep -c 'Preview\|Deduplication' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 4

## 验证

```bash
WORKSPACE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Claude Code"
grep -c '## Preview' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"        # >= 1
grep -c '## Deduplication' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"  # >= 1
grep -c 'Preview\|Deduplication' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"  # >= 4
```

## 非目标

- 不做 query 语义相似度（本期只字符串匹配，避免误合并）
- 不做正文级 diff（只展示完整内容，不和源页对比 diff）
- 不实现合并算法（合并 derived_from 是简单去重并集）
