# AKB-2.0-CONTENT-STANDARD (Test Fixture)

> Simplified fixture for knowledge-compounding Step 1 verification.

## 3. PAGE-TEMPLATES

### 3.1 Template Type Enumeration

| template_type | Description |
|---------------|-------------|
| concept       | 概念解释页   |
| source        | 信息源页     |
| query-result  | 查询综合结果页 |

### §4.1 concept

- `template_type: concept` — 概念解释页
- Required fields: `title`, `slug`, `page_status`

### §4.2 source

- `template_type: source` — 信息源页
- Required fields: `title`, `slug`, `page_status`, `source_url`

### §4.13 query-result

- `template_type: query-result` — 查询综合结果页

**Required fields:**

| Field         | Type                         | Description                          |
|---------------|------------------------------|--------------------------------------|
| `derived_from`| array of wiki page slugs     | 综合引用的源页面 slug 列表            |
| `query`       | string                       | 原始查询文本                          |
| `confidence`  | enum: high / medium / low    | 综合结果置信度                        |

**Frontmatter example:**

```yaml
---
title: "MCP vs A2A Protocol Comparison"
template_type: query-result
page_status: active
slug: mcp-vs-a2a-comparison
derived_from:
  - existing-source
  - mcp-protocol-overview
query: "MCP 和 A2A 协议有什么区别？"
confidence: high
created: 2026-05-17
---
```

## 5. Template Type Summary

| template_type | 中文名称       |
|---------------|---------------|
| concept       | 概念解释页      |
| source        | 信息源页        |
| query-result  | 查询综合结果页   |
