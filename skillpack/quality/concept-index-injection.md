version: v1
date: 2026-06-13
status: active

# 概念索引注入文件

本文件定义 AKB 知识库的领域 taxonomy 锚点与概念索引，供准入段主题相关性判定与提炼段概念引用。

## 领域 Taxonomy（准入判定锚点）

以下领域为第二大脑的知识覆盖面。准入段主题相关性判定以此清单为锚点：
新内容必须命中至少一个锚点，否则拒收。

### 核心领域

1. **AI Agent** — 自主代理架构、决策循环、工具使用、Agent 设计模式
2. **Loop Engineering** — 工程化循环设计、收敛条件、预算控制、防跑飞
3. **Context Engineering** — 上下文管理、压缩策略、信息密度优化
4. **Harness Engineering** — 代理运行时、工具定义、观测面设计
5. **Eval Engineering** — 评估框架、基准设计、回归测试、质量度量
6. **Multi-Agent Orchestration** — 多代理编排、协作模式、任务分解与派发
7. **AI Coding Workflow** — AI 辅助编码工作流、代码审查、TDD 与 AI 协作

### 设计与前端

8. **Design System & Tokens** — 设计系统、Token 架构、组件规范
9. **Generative UI** — 生成式 UI、AI 驱动界面生成、设计到代码
10. **UI/UX Design** — 用户界面与体验设计、交互模式
11. **Frontend Architecture** — 前端架构、状态管理、渲染策略
12. **AI Animation** — AI 驱动的动画生成与控制
13. **AI UI Animation** — AI 生成 UI 动画

### 游戏与交互

14. **Game Design** — 游戏设计方法论、机制设计、关卡设计

### 工程基础设施

15. **Knowledge Engineering** — 知识工程、知识图谱、信息抽取
16. **Tool Design** — 工具设计、API 设计、开发者体验
17. **Protocol Design** — 协议设计（MCP、A2A 等）
18. **MCP** — Model Context Protocol 相关
19. **A2A** — Agent-to-Agent 协议相关
20. **LangGraph** — LangGraph 框架相关

## 概念索引（提炼段概念引用）

以下概念为知识库中已建立的概念节点。提炼段 `referenced_concepts` 字段应尽量引用
已有概念，以便图谱回链。

### 已建立概念（vault wiki/concepts/ 下）

- `harness-engineering` — 代理运行时/工具/观测面设计
- `agent-collaboration` — 多代理协作模式
- `context-engineering` — 上下文管理与压缩
- `loop-engineering` — 工程化循环设计
- `eval-engineering` — 评估框架与基准
- `multi-agent-orchestration` — 多代理编排
- `ai-coding-workflow` — AI 编码工作流
- `design-system` — 设计系统
- `generative-ui` — 生成式 UI
- `protocol-design` — 协议设计（MCP/A2A）
- `knowledge-engineering` — 知识工程
- `tool-design` — 工具设计

### 概念匹配规则

1. 提炼段从 claims 中识别概念关键词
2. 优先匹配已有概念节点（可建立图谱回边）
3. 无匹配时保留概念名作为 pending 概念
4. ≥3 篇共指同一 pending 概念 → 触发建节点任务（循环 5）

## 运行时注入

本文件在准入段与提炼段启动时加载，作为 prompt 的 taxonomy 锚点部分。
代码中 `admission.py` 的 `_DEFAULT_DOMAIN_TAXONOMY` 常量为本文件的代码化版本，
两端必须保持同步。