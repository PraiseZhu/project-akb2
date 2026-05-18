---
title: LLM Agent 架构模式综合
template_type: query-result
page_status: candidate
slug: llm-agent-architecture-patterns-synthesis
derived_from: [existing-source]
query: "LLM Agent 有哪些常见架构模式？"
confidence: high
future_questions:
  - 不同架构模式在生产环境中的可靠性对比如何？
  - ReAct 与 Plan-and-Execute 在多步任务中的性能差异？
  - 多 Agent 协作模式的通信开销如何量化？
---

## TLDR

LLM Agent 常见架构模式包括 ReAct（推理-行动交替）、Plan-and-Execute（先规划再执行）、
Multi-Agent（多代理协作）三大类。ReAct 适合简单工具调用场景，Plan-and-Execute 适合
复杂多步任务，Multi-Agent 适合需要专业化分工的大型系统。

## 核心论点

1. ReAct 模式通过交替推理和行动降低幻觉率
2. Plan-and-Execute 通过预先规划提升多步任务成功率
3. Multi-Agent 通过角色分工实现复杂任务分解
