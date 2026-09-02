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

### LLM 系统机制(F39 扩锚 2026-07-11)

15. **LLM Architecture & Training** — LLM 架构、训练机制、微调、对齐方法
16. **LLM Inference & Interpretability** — 推理优化、量化、可解释性、机制分析
17. **RAG & Retrieval Engineering** — 检索增强生成、embedding、重排、知识管道

### 工程基础设施

18. **Knowledge Engineering** — 知识工程、知识图谱、信息抽取
19. **Tool Design** — 工具设计、API 设计、开发者体验
20. **Protocol Design** — 协议设计（MCP、A2A 等）
21. **MCP** — Model Context Protocol 相关
22. **A2A** — Agent-to-Agent 协议相关
23. **LangGraph** — LangGraph 框架相关

### AI 广域（loop2 扩锚 2026-09-01）

24. **Foundation Models** — 基础模型、模型发布、能力边界、多模态底座
25. **Model Training** — 训练数据、微调、对齐、蒸馏与持续训练
26. **AI Evals & Benchmarks** — 评测集、基准、回归闸、能力切片
27. **AI Infrastructure** — 推理集群、调度、配额、观测与成本
28. **AI Tooling** — 开发者工具、SDK、IDE 插件、工作流脚手架
29. **Agent Products** — 面向用户的 agent 产品形态与交付面
30. **AI Product** — AI 产品设计、增长、定价与落地形态
31. **AI Research** — 论文、技术报告、可复现实验与开放研究

## 内容车道锚点（intake-rebuild p5，sc-p5-taxonomy-sources）

内容车道（content_lane）是准入段对通过态内容的四选一分类，与上方领域 taxonomy 是两个
维度：领域 taxonomy 判「是否在收录面内」（topic_relevance 锚点），内容车道判「以什么
形态入池、走哪条内芯」。车道集合与 `src/akb2/quality/admission.py` 的 `LANE_*` 常量
（`LANE_PASSABLE`）exact 对齐，本节是文档侧锚点：

1. **concept** — 机制/架构概念阐述（过渡保留车道）
2. **tutorial** — 教程/操作指南（canonical 值；`procedural` 为兼容别名，入口归一为 tutorial，不产生 procedural 终值）
3. **pattern** — 可复用模式集（内芯为模式条目，不含概念机制组件）
4. **design** — 设计呈现（内芯为设计决策呈现，不含概念机制组件）

另有 `reject`（非知识，一票拒收不下传，不混入通过态车道）。未知车道值 fail-closed
（拒收+落账），不静默归车道。pattern/design 不默认落 concept 桶，各自独立车道；
完整性门对非 concept 车道走 integrity_floor 的 H1≥1 分支，不误用 concept 组件阈值。

### 车道与源标注的对应（main_sources.yaml 同步锚点）

`config/main_sources.yaml` 的 `lane` 字段按本节车道集合标注源的内容形态预期
（未标注 = 不预设，由 admission 逐篇判定）：

- 教程型源（how-to/操作指南/实操 walkthrough 为主）标 `tutorial`，**不得标 `concept`**
- 模式集型源（设计模式/架构模式/可复用方法论为主）标 `pattern`
- 设计呈现型源（设计决策/视觉呈现/设计系统案例为主）标 `design`
- 概念阐述型源（机制/架构原理拆解为主）标 `concept`

两份文件的车道值集合必须与 `admission.py` 常量保持 exact 一致；改车道枚举时三处
同步（代码常量 / 本节 / main_sources.yaml schema 注释）。

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
代码中 `admission.py` 的 `_DEFAULT_DOMAIN_TAXONOMY` 常量为本文件（领域 taxonomy 节）
的代码化版本，两端必须保持同步。内容车道锚点同理：`admission.py` 的 `LANE_*`
常量与本文「内容车道锚点」节、`config/main_sources.yaml` 的 `lane` 字段值域
三处 exact 对齐，改任一处必须同步其余两处。