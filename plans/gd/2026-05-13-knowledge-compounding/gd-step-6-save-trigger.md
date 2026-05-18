# Step 6: 对话触发存储入口

日期：2026-05-14 | blocked_by：step 1, 3, 4 | can_parallel_with：step 7 | SC：SC-6

## 目标

在 knowledge-ops toolbox skill 新增 save-query-result workflow 定义，让用户说"存一下"可触发候选页自动生成和校验。

**注意**：runtime trigger activation（触发词注册到 `~/.claude`）为 deferred 项，需用户授权后独立安装；本步只交付 `toolbox/cc-knowledge-ops/SKILL.md` 的 workflow 文档定义。

## owned_paths

- `toolbox/cc-knowledge-ops/SKILL.md`（知识 ops workflow 权威源；与 Step 7 的 `toolbox/cc-akb-search/SKILL.md` 独立，可并行）

## forbidden_paths

- `~/.claude/**`（runtime trigger activation deferred；本步不触碰此路径）
- `${AKB2_ROOT}/**`（save-query-result 调用但不改 vault 结构）
- `src/akb2/**`（调用 lint/gate，不改实现）

## blocked_by

step 1, step 3, step 4

## can_parallel_with

step 7（Step 7 owned_paths 是 `toolbox/cc-akb-search/SKILL.md`，与本步无冲突）

## required_context

- `plans/gd/2026-05-13-knowledge-compounding/gd-master-knowledge-compounding-v3.md`（SC-6 目标）
- `plans/gd/2026-05-13-knowledge-compounding/gd-step-1-query-result-template.md`（derived_from/query/confidence schema）
- `plans/gd/2026-05-13-knowledge-compounding/gd-step-3-gate-simplification.md`（gate CLI 接口）
- `plans/gd/2026-05-13-knowledge-compounding/gd-step-4-lint-extension.md`（lint v0.3 接口）

## deliverables

- `toolbox/cc-knowledge-ops/SKILL.md`（**Plan 3 future target**；Plan 2 不创建该文件，不得 test -f 或 grep 其内容；本计划只定义 workflow 规格，文件由 Plan 3 创建。deliverable 状态：`future_target`，must_exist: false）

## 实现步骤

1. **skill workflow 定义**
   - WHERE: `toolbox/cc-knowledge-ops/SKILL.md`
   - WHAT: 新增 `## save-query-result` 段落，定义 workflow：
     - 触发词（文档描述，runtime activation deferred）：`存一下、保存到知识库、save to wiki、这个值得记住`
     - 提取 query：取当前对话中用户最近的问句
     - 提取 derived_from：取当前对话中 akb_search 返回的 item_id 列表，转为 wiki slug
     - 确认 confidence：询问用户选 high/medium/low
     - 生成候选页：Write 到 `staging/candidate/<slug>.md`（effect-level：文件需存在）
     - 自动校验：调 `lint v0.3` exit 0 + `gate check --akb-root <staging_root> --input staging/candidate/<slug>.md --dry-run` exit 0
   - VERIFY（Step 6 plan-level — 验证本 plan 文档自身，不 grep 不存在的 future target）：`grep -c 'save-query-result' plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md` >= 3

2. **runtime trigger activation（deferred）**
   - 约束：`~/.claude/**` 路径受保护，需用户明确授权方可修改
   - 本步交付：在 SKILL.md 内写明触发词列表（供后续授权 install flow 追加到 skill-trigger-scan.md）
   - VERIFY：`grep -iE 'deferred|runtime trigger activation deferred|授权等待' plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md` >= 1

## 验证

```bash
# Step 6 plan-level verify（验证本 plan 文档自身，不 grep 不存在的 Plan 3 future target）
grep -c 'save-query-result' plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md
# 期望: >= 3（save-query-result workflow 定义存在于本 plan 文档）

grep -iE 'deferred|runtime trigger activation deferred|授权等待' plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md
# 期望: >= 1（runtime trigger activation deferred 声明存在于本 plan 文档）
```

## 非目标

- 不注册触发词到 `~/.claude/rules/skill-trigger-scan.md`（deferred，需用户明确授权）
- 不建独立 CLI（workflow 在 skill 内定义）
- 不自动判断 confidence（必须问用户）
- 不自动推送到 wiki/（停留在 staging/candidate/）
