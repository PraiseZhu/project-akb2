---
name: akb-search
description: >-
  搜索 AKB 知识库获取 AI Agent 相关技术文档。当用户提到 Agent 框架、工具、模式、
  协议或遇到 agentic 相关技术问题时使用。触发词：akb、知识库、agent 框架对比、
  什么是 MCP/A2A/LangGraph 等。
category: knowledge
priority: high
tools_used:
  - Shell
  - Read
  - Grep
project: AKB
---
# AKB 知识库搜索

通过 AKB (Agent Knowledge Base) 搜索 AI Agent 生态的技术知识，包括框架对比、
工具用法、设计模式、论文摘要、最佳实践等。

## 使用场景

- 用户问「LangGraph 和 CrewAI 哪个好」→ 搜索对比内容
- 用户在项目中需要选型 Agent 框架 → 搜索相关评测
- 用户提到 MCP / A2A / Tool Use 等概念 → 搜索相关笔记
- 用户说「帮我查一下」「知识库里有没有」→ 直接搜索

## 执行步骤

### Step 1: 构造搜索 query

从用户消息中提取搜索关键词。优先使用英文技术术语（如 LangGraph, MCP, ReAct），
中文补充。

### Step 2: 执行搜索

在项目根目录运行：

```bash
python3 scripts/main.py search "<关键词>" --limit 5 --output json
```

AKB 项目路径: `~/AI-Agent/Claude/archive/projects/Project AKB`

如需语义搜索（Ollama 在线时）：

```bash
python3 scripts/main.py search "<关键词>" --semantic --limit 5 --output json
```

### Step 3: 解读结果

搜索结果是 JSON 数组，每条包含：
- `title`: 文章标题
- `source`: 来源 (reddit / github / blog / arxiv 等)
- `date`: 日期
- `url`: 原文链接
- `snippet`: 内容摘要（含 `<b>` 高亮标记）
- `note_path`: 笔记文件路径（如有）

### Step 4: 呈现给用户

- 按相关性排序呈现 top 3-5 结果
- 如有 `note_path`，读取笔记摘要提供更详细的信息
- 附上原文链接供深入阅读
- 如果搜索无结果，建议用户尝试不同关键词

## 可选: 导出主题报告

```bash
python3 scripts/main.py export "<主题>" --limit 10
```

生成的 Markdown 报告保存在 `exports/` 目录。

## 注意事项

- AKB 知识库聚焦 AI Agent 生态，非 Agent 相关问题不适合使用
- 搜索支持 FTS5 语法：`word1 OR word2`、`"exact phrase"` 等
- 按标签过滤：`--tags Agent_Framework,Tool`
