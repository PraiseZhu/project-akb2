---
name: "cc-akb-search"
description: "AKB 知识库搜索 skill，支持搜索路由分流（知识库/工具库/全量）"
---

# akb-search

## Trigger

Use this skill only when the user explicitly invokes it or clearly asks for this specific integration/workflow.

Aliases: `akb-search`, `cc-akb-search`, `akb`, `search`, `知识库`, `知识库搜索`

## Purpose

搜索 AKB 知识库，支持三种搜索路由分流：工具库查询、知识库查询、全量搜索。
知识库查询走 AKB2 只读检索，按概念图谱 `[[链接]]` 读完相关正文，不用条数卡检索。

## Workflow

### Step 0: 搜索路由判断

根据用户意图关键词判断搜索 scope：

**工具库信号词** → `scope=toolbox`
- 具体工具名（cc-akb-search / cc-review / cc-executable-plan 等）
- "怎么用"、"有什么工具"、"工具"、"tool"、"toolbox"
- 搜索来源限定**工具库**，合并结果仅含工具条目，标注 `[工具库]`

**知识库信号词** → `scope=knowledge`
- "什么是"、"概念"、"框架"、"对比"、"区别"、"设计模式"、"协议"
- "architecture"、"pattern"、"concept"、"comparison"
- 搜索来源限定**知识库**，合并结果仅含知识条目，标注 `[知识库]`

**都不命中** → `scope=all`
- 工具库 + 知识库全量搜索
- 结果混合标注来源：`[知识库]` 或 `[工具库]`

### Step 1: 构造搜索 query

- 从用户消息提取核心关键词
- 中英文均可搜索
- 优先英文技术术语（LangGraph, MCP, ReAct），中文补充

### Step 2: 执行搜索

**知识库 / 全量里的知识部分**：走 `akb2_retrieval_search`，不要用条数卡。
MCP 默认 5、CLI/引擎默认 10；`limit<=0` 会空结果。显式传 `limit=1000` 顶掉截断。
只有用户明确说「只要 N 条」才改 limit。

```
mcp__akb2-retrieval__akb2_retrieval_search(query="<关键词>", limit=1000)
```

MCP 不可用时，在 Project AKB2 仓根：

```bash
cd ~/AI-Agent/Claude/projects/Project\ AKB2
PYTHONPATH=src python3 -m akb2.retrieval.cli search "<关键词>" --limit 1000 --output json
```

vault：`VAULT_ROOT` / `AKB2_ROOT` 优先。Mac mini 生产库是 `/Volumes/AKB2/Obsidian`
（env 会规范化到 AKB2.0 子根）。主力机通常没有该卷，不要把 Obsidian 库根传给
`--vault-root`（该参数不规范化，会静默搜空）。用下面任一：

```bash
VAULT_ROOT=/Users/praise/Obsidian PYTHONPATH=src python3 -m akb2.retrieval.cli search "<关键词>" --limit 1000 --output json
AKB2_ROOT="/Users/praise/Obsidian/Praise Space/AKB2.0" PYTHONPATH=src python3 -m akb2.retrieval.cli search "<关键词>" --limit 1000 --output json
PYTHONPATH=src python3 -m akb2.retrieval.cli search "<关键词>" --limit 1000 --output json --vault-root "/Users/praise/Obsidian/Praise Space/AKB2.0"
```

**工具库**：仍调 `akb_search` MCP，按 `scope=toolbox` 只留 toolbox/ 路径。

**全量**：知识库走上一节，工具库走 `akb_search`，再合并。

### Step 3: 抽链接，派子代理读（知识库）

检索 JSON 只是索引。概念图谱要读有链接的正文，不要只拿 `how_density` 交差。

从每条命中抽链接（去重）：

- frontmatter `related_concepts` / `aliases` 里的 `[[wikilink]]`
- 正文 `[[wikilink]]`
- 只跟这篇相关的概念/设计/来源；跳过 `00-Index`、staging、archive

解析：

- `[[concept-harness-engineering]]` → `<AKB2.0>/wiki/concepts/concept-harness-engineering.md`
- `[[slug]]` 先 `wiki/concepts/`、再 `wiki/sources/`、再 `wiki/designs/` 找 `<slug>.md`
- 解析不到的记一笔，不要假装读过

读法：

- 1–2 个链接：主会话自己 Read
- ≥3 个链接：派子代理并行读，回报标题、相关要点、又链出的 `[[wikilink]]`
- 外扩跟图谱走，读到没有未读相关链接为止；环用已读集合打破

子代理只读，不改 vault，不改工程仓。

### Step 4: 呈现给用户

- 先给结论，再按相关性列读过的笔记
- 每条标注来源：`[知识库]` / `[工具库]`
- How 类问题优先展示可执行步骤
- 概念/对比/选型：把图谱上读到的相邻概念一并说清
- 无结果换词；有链接没读到就如实说

## Dependencies

- akb2-retrieval MCP：知识库只读检索（`akb2_retrieval_search`）
- akb-search MCP：工具库 / 旧检索兜底
- AKB2.0 vault：知识库数据源

## Safety

- 只读操作，不修改任何文件
- 不修改 MCP server.py（路由逻辑在 skill 层）
- 搜索路由判断在 skill 执行时完成，不改变 MCP 接口

## Source Notes

- Original Claude skill: `akb-search`
- 知识复利增强：搜索路由分流（scope=toolbox / scope=knowledge / scope=all）
- 2026-08-16：知识库改走 akb2-retrieval，不按条数截断，跟图谱链接读
