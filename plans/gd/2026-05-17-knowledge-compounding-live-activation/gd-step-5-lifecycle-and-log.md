# Step 5: lifecycle 命令 + log 追踪

日期：2026-05-17 | blocked_by：step 1, step 2 | can_parallel_with：— | SC：SC-5, SC-6

## 目标

knowledge-ops skill 新增 3 个 lifecycle 子命令（update-query-result / deprecate-query-result / expire-low-confidence），并实装 wiki/log.md 自动追踪——save/promote/update/deprecate 任何操作后追加一行 `YYYY-MM-DD HH:MM | <action> | <slug> | <actor>`。

## owned_paths

- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（追加 lifecycle 段落 + log 段落）

## forbidden_paths

- `src/akb2/**`（lifecycle 用 Python subprocess 调 promote.py 提供的 slug→path 函数，不写新 Python 模块）
- `${WORKSPACE}/skills/akb-search/**`
- `~/.claude/**`（除日志写入 wiki/log.md，无 ~/.claude 写入）

## blocked_by

step 1（需要 save 基线），step 2（需要 promote.py 提供的 slug→path 解析）

## can_parallel_with

（无；step 5 是 W3 单独 wave）

## required_context

- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（step 1/3/4 后的累积版本）
- `src/akb2/promotion/promote.py`（提供 slug→path 解析）
- `${AKB2_ROOT}/wiki/log.md`（现有手动维护日志，本步切换到自动追加）

## deliverables

- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（新增 `## Lifecycle Commands` 和 `## Operation Log` 段落）

## 实现步骤

1. **新增 Lifecycle Commands 段落**
   - WHERE: `${WORKSPACE}/skills/knowledge-ops/SKILL.md` 末尾
   - WHAT: 新增 `## Lifecycle Commands` 段落，定义 3 个子命令：
     - `update-query-result <slug>`：读 wiki/synthesis/<slug>.md，AskUserQuestion 让用户选"改 query / 改 derived_from / 改正文 / 改 confidence / 取消"；修改后跑 lint 验证；写回原路径；更新 backlinks
     - `deprecate-query-result <slug>`：读目标页确认存在，AskUserQuestion 确认废弃；frontmatter 加 `page_status: deprecated`，移动到 `${AKB2_ROOT}/archive/<slug>.md`；更新 backlinks 移除该 slug 的引用
     - `expire-low-confidence`：扫描 wiki/synthesis/ 所有 query-result 页，筛选 `confidence: low` 且 frontmatter `created_at` >30 天的页面，打印列表 AskUserQuestion 确认；用户确认后批量执行 deprecate 流程
     - 每个命令操作前**必须**打印目标列表 + 用户确认（防止误归档）
   - VERIFY: `grep -c 'update-query-result\|deprecate-query-result\|expire-low-confidence' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 3

2. **新增 Operation Log 段落**
   - WHERE: `${WORKSPACE}/skills/knowledge-ops/SKILL.md` Lifecycle 段落之后
   - WHAT: 新增 `## Operation Log` 段落，定义：
     - 触发：save / promote / update / deprecate 任何操作完成后
     - 写入路径：`${AKB2_ROOT}/wiki/log.md`
     - 行格式：`<ISO 8601 datetime> | <action> | <slug> | <actor>`（actor 默认 `claude_code`）
     - 并发保护：使用 `fcntl.flock(fd, fcntl.LOCK_EX)` 包裹写入（描述并发场景：多个会话同时操作时不丢条目）
     - 不修改 log.md 现有手动条目（只追加，不重写）
   - VERIFY: `grep -c 'log.md\|fcntl' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 2

3. **在 save/promote/update/deprecate 段落插入 log 引用**
   - WHERE: `${WORKSPACE}/skills/knowledge-ops/SKILL.md` 各对应段落末尾
   - WHAT: 在 save-query-result（step 1）/ Lifecycle update/deprecate（本步）段落末尾加引用：`操作完成后自动追加一行到 wiki/log.md（详见 ## Operation Log 段落）`
   - VERIFY: `grep -c '自动追加' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 2

## 验证

```bash
WORKSPACE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Claude Code"
grep -c 'update-query-result\|deprecate-query-result\|expire-low-confidence' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"  # >= 3
grep -c 'log.md\|fcntl' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"  # >= 2
grep -c '自动追加' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"        # >= 2
```

## 非目标

- 不自动执行 expire-low-confidence（必须用户手动触发）
- 不实现 undeprecate（恢复归档页属未来）
- 不解析 log.md 现有内容做合并/排序（只追加）
- log 不写到 ~/.claude 或其他全局位置（只在 vault 内）
