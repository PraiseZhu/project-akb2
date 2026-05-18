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

### Step 2: 执行搜索

- 调用 `akb_search` MCP 工具
- 根据 scope 过滤结果：
  - `scope=toolbox`：仅返回 toolbox/ 路径下的条目
  - `scope=knowledge`：仅返回 wiki/ 路径下的条目
  - `scope=all`：返回所有匹配条目

### Step 3: 合并结果

- 去重
- 按相关度排序

### Step 4: 呈现给用户

- 每条结果标注来源类型：
  - 路径含 `toolbox/` → 标注 `[工具库]`
  - 路径含 `wiki/` → 标注 `[知识库]`
  - 其他 → 标注 `[其他]`
- 格式：`[知识库] 标题 — 摘要` 或 `[工具库] 工具名 — 用途`

## Dependencies

- akb-search MCP server: 提供底层搜索能力
- AKB2.0 vault: 知识库数据源

## Safety

- 只读操作，不修改任何文件
- 不修改 MCP server.py（路由逻辑在 skill 层）
- 搜索路由判断在 skill 执行时完成，不改变 MCP 接口

## Source Notes

- Original Claude skill: `akb-search`
- 知识复利增强：搜索路由分流（scope=toolbox / scope=knowledge / scope=all）
