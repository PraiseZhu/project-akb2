# Step 7: 搜索路由分流

日期：2026-05-14 | blocked_by：— | can_parallel_with：step 6 | SC：SC-7

## 目标

修改 akb-search toolbox skill，搜索前根据用户意图关键词判断路由到知识库（scope=knowledge）、工具库（scope=toolbox）或全量（scope=all），搜索结果标注来源类型 `[知识库]/[工具库]`。

## owned_paths

- `toolbox/cc-akb-search/SKILL.md`（akb-search 权威源；与 Step 6 的 `toolbox/cc-knowledge-ops/SKILL.md` 独立，可并行）

## forbidden_paths

- `~/.claude/**`（触发词表不改）
- `toolbox/cc-knowledge-ops/**`（属 step 6）
- `${AKB2_ROOT}/**`
- `src/akb2/**`（路由在 skill 层，不改 MCP server）

## blocked_by

（无，step 7 无数据依赖）

## can_parallel_with

step 6（Step 6 owned_paths 是 `toolbox/cc-knowledge-ops/SKILL.md`，与本步无冲突）

## required_context

- `plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md`

## deliverables

- `toolbox/cc-akb-search/SKILL.md`（新增 `### Step 0: 搜索路由判断`；三个 scope 分支均明确定义；`[知识库]/[工具库]` 标注）

## verify（SC-7，分 scope 分 token 检查）

Step 7 verify 必须分别证明三个 scope 分支均存在：

```bash
grep -c 'scope=toolbox' toolbox/cc-akb-search/SKILL.md   # >= 1
grep -c 'scope=knowledge' toolbox/cc-akb-search/SKILL.md  # >= 1
grep -c 'scope=all' toolbox/cc-akb-search/SKILL.md        # >= 1
grep -c '知识库' toolbox/cc-akb-search/SKILL.md            # >= 2
grep -c '工具库' toolbox/cc-akb-search/SKILL.md            # >= 2
```

## 实现步骤

1. **搜索路由判断**
   - WHERE: `toolbox/cc-akb-search/SKILL.md`，在 Step 1 "构造搜索 query" 之前
   - WHAT: 新增 `### Step 0: 搜索路由判断`：
     - 工具库信号词：具体工具名(cc-akb-search/cc-review 等)、"怎么用"、"有什么工具"、"工具"、"tool"
     - 知识库信号词："什么是"、"概念"、"框架"、"对比"、"区别"、"设计模式"、"协议"
     - 路由规则：
       - 命中工具 → `scope=toolbox`；搜索来源限定工具库，合并结果仅含工具条目，标注 `[工具库]`
       - 命中知识 → `scope=knowledge`；搜索来源限定知识库，合并结果仅含知识条目，标注 `[知识库]`
       - 都不命中 → `scope=all`；工具库 + 知识库全量搜，结果混合标注来源
   - VERIFY（分 token）：
     - `grep -c 'scope=toolbox' toolbox/cc-akb-search/SKILL.md` >= 1
     - `grep -c 'scope=knowledge' toolbox/cc-akb-search/SKILL.md` >= 1
     - `grep -c 'scope=all' toolbox/cc-akb-search/SKILL.md` >= 1

2. **结果标注**
   - WHERE: `toolbox/cc-akb-search/SKILL.md` Step 4 "呈现给用户"
   - WHAT: 每条结果标注 `[知识库]` 或 `[工具库]`，判据为 item_id/url 含 tool 关键词
   - VERIFY: `grep -c '工具库' toolbox/cc-akb-search/SKILL.md` >= 2 且 `grep -c '知识库' toolbox/cc-akb-search/SKILL.md` >= 2

## 非目标

- 不改 MCP server.py（路由在 skill 层）
- 不改触发词表（akb-search 已有触发词）
