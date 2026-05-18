# Step 5: E2E 集成验证

日期：2026-05-14 | blocked_by：step 1-4, 6-7 | can_parallel_with：— | SC：SC-5

## 目标

创建 test_compound_e2e.py，在临时 vault 中验证 query-result 从创建到 lint→gate→index 的完整闭环。

## owned_paths

- `tests/test_compound_e2e.py`

## forbidden_paths

- `src/akb2/**`（只调用，不改实现）
- `${AKB2_ROOT}/**`（使用 pytest tmp_path 临时 vault，不改真实 vault）
- `~/.claude/**`
- `toolbox/**`

## blocked_by

step 1, step 2, step 3, step 4, step 6, step 7

## can_parallel_with

（无，E2E 是最后一个 wave）

## required_context

- `plans/gd/2026-05-13-knowledge-compounding/gd-master-knowledge-compounding-v3.md`（SC-5 完整验收要求）
- `plans/gd/2026-05-13-knowledge-compounding/gd-step-1-query-result-template.md`（schema）
- `plans/gd/2026-05-13-knowledge-compounding/gd-step-2-index-generator.md`（index 接口）
- `plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md`（save-query-result contract，SKILL.md artifact）
- `plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md`（search routing contract，SKILL.md artifact）

## deliverables

- `tests/test_compound_e2e.py`（含 test_save_trigger + test_search_routing）

## 实现步骤

1. **E2E 测试**
   - WHERE: `tests/test_compound_e2e.py`
   - WHAT: 用 pytest `tmp_path` 构造临时 vault，包含三个独立测试函数：
     1. `test_pipeline` — 3 个 active 页面 + 1 个 query-result 候选页；lint pass → gate pass → **将通过 gate 的候选页从 staging/candidate/ 复制到 wiki/ 目录** → index 生成含 query-result 页面标题（因为 index 只遍历 wiki/，不遍历 staging/）；断言：候选页先在 staging/candidate/ 存在，复制后在 wiki/ 存在，index 输出含该页标题
     2. `test_save_trigger`（**Effect-level E2E，使用 tmp staging；runtime trigger activation deferred**）— 在 tmp_path 下构造 `staging/candidate/` 目录；手动执行 save-query-result 的核心流程：给定 query/derived_from/confidence，构建候选页 Markdown 并写入 `staging/candidate/<slug>.md`；断言文件存在；再调 lint，按以下优先顺序选择模式：
         - **主路径（Step 4 位置参数已实现）**：`python3 src/akb2/lint/akb2_lint.py --version v0.3 <staging/candidate/slug.md>` exit 0
         - **fallback（Step 4 位置参数未实现）**：`python3 src/akb2/lint/akb2_lint.py --version v0.3 --vault-root <tmp_path>` 并断言无 P0/P1 针对 query-result 候选页；再调 `/Users/praise/.pyenv/versions/3.13.12/bin/python3 src/akb2/gate/akb2_gate_check.py check --akb-root str(tmp_path) --input staging/candidate/<slug>.md --dry-run` exit 0（注：--input 是相对 akb-root 的路径，不是绝对路径）；**注意：save-query-result 的触发词激活属于 runtime trigger activation，为 deferred 项（不在本步测试），此 E2E 只验证候选页生成和 lint/gate effect**
     3. `test_search_routing`（**Effect-level E2E，验证路由分流规则**）— 直接断言三类路由逻辑（可通过读 SKILL.md 或调用路由辅助函数）：工具信号词（如"怎么用 cc-akb-search"）→ 输出或配置中包含 `scope=toolbox` 或 `工具库`；知识信号词（如"什么是 MCP 协议"）→ 包含 `scope=knowledge` 或 `知识库`；无信号词 → 包含 `scope=all` 或全量搜；每类断言必须有具体输入/输出，不能只检查词频
   - VERIFY: `python3 -m pytest tests/test_compound_e2e.py -v` 输出包含 `test_save_trigger PASSED` 和 `test_search_routing PASSED`；`test_save_trigger` 的 staging/candidate 文件写入断言和 lint/gate 断言必须作为独立 assert 语句出现

## 验证

```bash
python3 -m pytest tests/test_compound_e2e.py -v
# 期望输出含：
#   test_pipeline PASSED
#   test_save_trigger PASSED   ← staging 写入 + lint/gate pass
#   test_search_routing PASSED ← 三类路由断言
```

## 非目标

- 不修改 src/akb2/（只调用不改）
- 不触碰真实 vault（用 tmp_path）
