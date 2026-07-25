---
title: Wiki Sources — 主索引
akb_layer: wiki
doc_type: index
index_kind: source-index
document_status: ready
canonical: true
updated_at: 2026-05-08
---

# Wiki Sources — 主索引

> 承接 [[../../docs/AKB-2.0-FOLDER-STRUCTURE|FOLDER-STRUCTURE]] §1 主索引硬规则(目录主索引文件名固定 `00-Index.md`)。
> 本目录归档 AKB 2.0 的 source 类 active 页(论文 / 视频 / 课程 / 研究报告 / blog / docs / 工作笔记 / 论坛 / news)。
>
> 本索引由 reviewer 在 L4 提升时手工维护(C-2 Index updater 实施前)。

---

## §1. 分组规则(承接 PAGE-TEMPLATES §6 source-index)

- **一级分组**:`source_type`(video / paper / course / research / blog / docs / forum / reddit / news / work-note / tool)
- **二级分组**:`source_published_at`(年月倒序)
- **排序键**:`source_published_at` DESC

---

## §2. video

### 2023

- [[karpathy-intro-to-llms]] — Andrej Karpathy 1 小时 talk:LLM 三阶段训练 + OS kernel mental model + 2023-11 短板清单(2023-11-22 / 60min);LLM 入门 mental model 的共同基底,是 [[concept-large-language-model]] hub 的直接证据来源

---

## §3. research

### 2026-04

- [[harness-engineering-deep-eval]] — NLM Deep Research(35 源)系统评估 Harness Engineering 工程学科:PE/CE/HE 三层范式 + 7 大核心组件(tool / memory / guardrail / retry / planning / observability / permission)+ 3 大思想推动者(Fowler / Anthropic / OpenAI)+ Intent Met 新指标(2026-04-21)
- [[jenny-wen-ai-design-philosophy]] — NLM Deep Research(49 外部源 + 双视频)Jenny Wen + Boris Cherny 的 AI 设计观:设计师角色转型 + Anthropic 100% AI 编写 + 落地三件套(implementation-aware design + Claude Cowork + design tokens / Code Connect)+ NN/G 反对意见均衡(2026-04-18);**2026-05-04 quality repair 从 wiki/designs/ 拆移到本目录**(原 design 拆出独立设计原则页 [[implementation-aware-design]])

---

## §4. blog

### 2025-09

- [[anthropic-effective-context-engineering]] — Anthropic Applied AI 团队官方系统化文章(2025-09-29):CE 是"在 LLM 推理时 curate 最优 token 集"的工程问题;给出 CE vs PE 边界 + attention budget / context rot 限制理论 + 四大 anatomy(system prompt / tools / examples / just-in-time retrieval) + 长程任务三大技术(compaction / structured note-taking / sub-agent architectures);为 [[concept-context-engineering]] 提供 source_grounded 主体证据(Phase C2 BL-C1-003 闭环)

### 2025-06

- [[anthropic-multi-agent-research-system]] — Anthropic Engineering 多 agent 系统工程实录(2025-06-13):描述其 Research multi-agent 系统通过 orchestrator-worker pattern(lead agent 协调 + 多 subagents 并行委派)实现 agent collaboration;给出 4 大 agent 角色(LeadResearcher / parallel subagents / CitationAgent / dynamic adaptation)+ 8 条 prompt 原则 + production reliability 4 项(stateful checkpoints / production tracing / rainbow deployments / 同步执行瓶颈)+ multi-agent vs single-agent 内部评估约 90.2% 提升数据;为 BL-D-014 CC-008 agent-collaboration concept 提供 direct source(Phase D source-first lane A 闭环)

### 2026-05（发布时间不详，captured 2026-05-08）

- [[andrej-karpathy-skills]] — GitHub 仓库 forrestchang/andrej-karpathy-skills（89.8k ⭐）;提炼自 Andrej Karpathy 公开观察，定义 4 条 LLM 编码行为准则：Think Before Coding / Simplicity First / Surgical Changes / Goal-Driven Execution;以 CLAUDE.md Plugin 格式分发，已安装于用户全局 rules;为 [[concept-harness-engineering]] 行为约束层提供社区级 source_grounded 证据;Stage 2 manual intake 首个真实候选（2026-05-08 入库）

### 2024-02

- [[langchain-plan-and-execute-agents]] — LangChain Blog LangGraph agent 架构综述(2024-02-13;原 URL 已 301 → langchain.com/blog):统一描述 3 种 plan-and-execute 风格 agent 设计(基础 Plan-and-Execute / ReWOO / LLMCompiler);planner + executor(s) 分离架构相对 ReAct(thought-act-observation loop)在 speed/cost/performance 三方面的收益;LLMCompiler 用 task DAG 最大化并行;为 BL-D-014 CC-005 plan-execute-loop concept 提供 direct source(Phase D source-first lane B 闭环)

---

## §5. docs

### 2026-05

- [[mcp-spec]] — Model Context Protocol 官方规范与文档(modelcontextprotocol.io 三页核心:introduction / architecture / server-concepts;2026-05-07 抓取);MCP 是 Anthropic 主导的开放协议,把 host / client / server 三角色 + JSON-RPC 数据层 + stdio/Streamable HTTP 两种 transport + Tools/Resources/Prompts 三类 server primitives 标准化;为 [[concept-mcp-tool-use]] hub 提供 spec 级证据(Phase C2 BL-C1-002 闭环)

---

## §6. 修订历史

| 日期 | 改动 | 作者 |
|---|---|---|
| 2026-05-04 | 初始本索引 — Phase A→D 第一批 active(L4 Pilot 2 source)正式入索引 | Claude Code |
| 2026-05-04 | quality repair v2 — 索引行扩写到一句人读摘要(不只是文件名);新增 [[jenny-wen-ai-design-philosophy]] 行(从 wiki/designs/ 拆移)| praise + Claude Code |
| 2026-05-07 | Phase C2 Query Gap Closure — 新增 §4 blog 分组 + [[anthropic-effective-context-engineering]] 行;新增 §5 docs 分组 + [[mcp-spec]] 行;BL-C1-002 / BL-C1-003 闭环 | praise + Claude Code |
| 2026-05-08 | Phase D BL-D-014 source-first lane A/B 闭环 — §4 blog 新增 [[anthropic-multi-agent-research-system]](2025-06)+ [[langchain-plan-and-execute-agents]](2024-02);为 CC-008 agent-collaboration / CC-005 plan-execute-loop 提供 direct source | praise + Claude Code |
| 2026-05-08 | Stage 2 manual intake — §4 blog 新增 [[andrej-karpathy-skills]];首个真实 manual intake 候选；runbook Stage 2 验证 | praise + Claude Code |

- [[replit-blog--37328f908399]] — replit-blog--37328f908399 · unknown · (source_grounded) Replit 将 Agent 评估从一次性发布闸门改造为持续改进循环：离线基准（ViBench）在发布前筛选候选变更，在线 A/B 测试与生产痕迹衡量真实用户影响，Telescope 聚类失败轨迹解释根因 · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-09 | Added replit-blog--37328f908399 | gate: gate: active (five-gate auto-promote) |
- [[jason-liu--fec48e00b834]] — jason-liu--fec48e00b834 · unknown · (source_grounded) Durable threads feeds Memory system [raw §H_HOW_STEPS]. (source_grounded) 机制组件（4 个）：Durable threads, M · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-10 | Added jason-liu--fec48e00b834 | gate: gate: active (five-gate auto-promote) |
- [[sshh-blog--fc39241ec253]] — sshh-blog--fc39241ec253 · unknown · (source_grounded) AI Agents empowers Loop Owner [raw §F_EVIDENCE]. (source_grounded) 机制组件（4 个）：Loop, Loop Owner, Platfor · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-10 | Added sshh-blog--fc39241ec253 | gate: gate: active (five-gate auto-promote) |
- [[deno-blog--49aa95c8e7ac]] — deno-blog--49aa95c8e7ac · unknown · (source_grounded) 使用 deno init dino-runner 命令可以快速创建包含 deno；json 配置文件的 Deno 项目目录 [raw §D_ARGUMENT]. (source_grounded) 可以使 · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-10 | Added deno-blog--49aa95c8e7ac | gate: gate: active (five-gate auto-promote) |
- [[langchain-blog--87e401200410]] — langchain-blog--87e401200410 · unknown · (source_grounded) 机制组件（4 个）：Deep Agents Code, Nemotron 3 Ultra, NemoClaw, OpenShell [raw §H_HOW_STEPS]. (source_grounded · serves agent, loop_engineering · basis: gate: active (five-gate auto-promote)
| 2026-07-11 | Added langchain-blog--87e401200410 | gate: gate: active (five-gate auto-promote) |
- [[deno-blog--1f8a9bdb323a]] — deno-blog--1f8a9bdb323a · unknown · (source_grounded) [Read the post](https://deno；com/blog/clawpatrol) > This series of blog posts will guide you through b · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-11 | Added deno-blog--1f8a9bdb323a | gate: gate: active (five-gate auto-promote) |
- [[phil-schmid--65441c0c9b4b]] — phil-schmid--65441c0c9b4b · unknown · (source_grounded) March 16, 2026 6 minute read [Nano Banana 2](https://blog；google/innovation-and-ai/technology/ai/nano- · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-11 | Added phil-schmid--65441c0c9b4b | gate: gate: active (five-gate auto-promote) |
- [[huggingface-blog--77d64de25479]] — huggingface-blog--77d64de25479 · unknown · (source_grounded) 机制组件（4 个）：Hugging Face, Action Buttons (Customize on SageMaker AI / Deploy on SageMaker AI), AmazonSag · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-11 | Added huggingface-blog--77d64de25479 | gate: gate: active (five-gate auto-promote) |
- [[langchain-blog--4ae2f1f40e73]] — langchain-blog--4ae2f1f40e73 · unknown · (source_grounded) LangGraph state feeds Harness [raw §U_BENCHMARKS]. (source_grounded) 机制组件（4 个）：WebAssembly, QuickJS, H · serves langgraph, loop_engineering · basis: gate: active (five-gate auto-promote)
| 2026-07-12 | Added langchain-blog--4ae2f1f40e73 | gate: gate: active (five-gate auto-promote) |
- [[phil-schmid--5aa59a0e4054]] — phil-schmid--5aa59a0e4054 · unknown · (source_grounded) Function calling call built-in tools [raw §B_INTRODUCTION]. (source_grounded) 机制组件（4 个）：Interactions A · serves agent, loop_engineering · basis: gate: active (five-gate auto-promote)
| 2026-07-12 | Added phil-schmid--5aa59a0e4054 | gate: gate: active (five-gate auto-promote) |
- [[phil-schmid--9a1dacc7fa70]] — phil-schmid--9a1dacc7fa70 · unknown · (source_grounded) January 9, 2026 7 minute read > **Updated January 2026 for v0；3；0** — New 3-subcommand architecture (` · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-12 | Added phil-schmid--9a1dacc7fa70 | gate: gate: active (five-gate auto-promote) |
- [[phil-schmid--a936eb222e52]] — phil-schmid--a936eb222e52 · unknown · (source_grounded) January 14, 2026 9 minute read [View Code](https://github；com/philschmid/gemini-samples/blob/main/exam · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-12 | Added phil-schmid--a936eb222e52 | gate: gate: active (five-gate auto-promote) |
- [[arize-blog--5f3b46dd2c27]] — arize-blog--5f3b46dd2c27 · unknown · (source_grounded) TrueFoundry AI Gateway calls Arize [raw §B_INTRODUCTION]. (source_grounded) 机制组件（4 个）：TrueFoundry AI G · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-12 | Added arize-blog--5f3b46dd2c27 | gate: gate: active (five-gate auto-promote) |
- [[arize-blog--90f13546e5c5]] — arize-blog--90f13546e5c5 · unknown · (source_grounded) ## Published July 9, 2026 Your agent does more than call a model [raw §A]. · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-12 | Added arize-blog--90f13546e5c5 | gate: gate: active (five-gate auto-promote) |
- [[aws-ml-blog--c982ac7adca1]] — aws-ml-blog--c982ac7adca1 · unknown · (source_grounded) Case Creator enables Case Processor [raw §A]. (source_grounded) 机制组件（4 个）：Case Creator, Case Processor · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-13 | Added aws-ml-blog--c982ac7adca1 | gate: gate: active (five-gate auto-promote) |
- [[dvc-blog--cea9100789bf]] — dvc-blog--cea9100789bf · unknown · (source_grounded) 机制组件（4 个）：DVC, Git repository, Pipeline, .dvc/cache [raw §H_HOW_STEPS]. (source_grounded) 作者早期项目因缺乏可复现 · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-13 | Added dvc-blog--cea9100789bf | gate: gate: active (five-gate auto-promote) |
- [[marimo-blog--7c5945e47d9e]] — marimo-blog--7c5945e47d9e · unknown · (source_grounded) marimo 笔记本存储为纯 Python 而非 JSON，因此比 Jupyter 等传统笔记本更适合 LLM 生成代码 [raw §J_DISCUSSION]. (source_grounded) Ju · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-13 | Added marimo-blog--7c5945e47d9e | gate: gate: active (five-gate auto-promote) |
- [[ollama-blog--19762e0cd26c]] — ollama-blog--19762e0cd26c · unknown · (source_grounded) Nemotron 3 Ultra 为长运行工作流提供了快速且经济实惠的性能，适用于数百次工具调用 [raw §E_CLAIMS]. (source_grounded) 该模型具有百万token上下文窗口， · serves token, loop_engineering · basis: gate: active (five-gate auto-promote)
| 2026-07-13 | Added ollama-blog--19762e0cd26c | gate: gate: active (five-gate auto-promote) |
- [[promptfoo--3e13ed07206e]] — promptfoo--3e13ed07206e · unknown · (source_grounded) Phase 1: Capability Discovery then Phase 2: Artifact Creation [raw §I_LIMITATIONS]. (source_grounded)  · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-13 | Added promptfoo--3e13ed07206e | gate: gate: active (five-gate auto-promote) |
- [[arize-blog--f7b3eb1b096c]] — arize-blog--f7b3eb1b096c · unknown · (source_grounded) ## Published July 7, 2026 If youâ€™re struggling to get started with evals, youâ€™re not alone [raw §A · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-13 | Added arize-blog--f7b3eb1b096c | gate: gate: active (five-gate auto-promote) |
- [[ollama-blog--b19003a4c8ef]] — ollama-blog--b19003a4c8ef · unknown · (source_grounded) 机制组件（1 个）：MiniMax M2 [raw §B_INTRODUCTION]. (source_grounded) MiniMax-M2 的 composite score 在全球开源模型中排名第 · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-15 | Added ollama-blog--b19003a4c8ef | gate: gate: active (five-gate auto-promote) |
- [[portkey-blog--432bf8f8b295]] — portkey-blog--432bf8f8b295 · unknown · (source_grounded) Skill (Prompt Partial) if Version History [raw §M_PARAMS]. (source_grounded) 机制组件（4 个）：Skill (Prompt P · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-15 | Added portkey-blog--432bf8f8b295 | gate: gate: active (five-gate auto-promote) |
- [[apple-ml-research--58a3b7a34aed]] — apple-ml-research--58a3b7a34aed · unknown · (source_grounded) target-domain performance requires constrained-domain losses [raw §M_PARAMS]. (source_grounded) 机制组件（4 · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-15 | Added apple-ml-research--58a3b7a34aed | gate: gate: active (five-gate auto-promote) |
- [[steipete--5d9b3deb817c]] — steipete--5d9b3deb817c · unknown · (source_grounded) • 4 min read [Edit on GitHub](https://github；com/steipete/steipete；me/edit/main/src/content/blog/2025/ · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-15 | Added steipete--5d9b3deb817c | gate: gate: active (five-gate auto-promote) |
- [[phil-schmid--554e1f3cd722]] — phil-schmid--554e1f3cd722 · unknown · (source_grounded) 机制组件（4 个）：Code Sandbox MCP, Container Session, Host Machine, run_python_code [raw §B_INTRODUCTION]. (s · serves mcp, loop_engineering · basis: gate: active (five-gate auto-promote)
| 2026-07-15 | Added phil-schmid--554e1f3cd722 | gate: gate: active (five-gate auto-promote) |
- [[explosion-ai--868a0be9cc34]] — explosion-ai--868a0be9cc34 · unknown · (source_grounded) [Go to top](/blog/modal-prodigy-serverless-nlp#) ；[](/_next/image；url=%2Fblog%2Fmodal；png&w=3840&q=75) · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-15 | Added explosion-ai--868a0be9cc34 | gate: gate: active (five-gate auto-promote) |
- [[arize-blog--50b0b15016c2]] — arize-blog--50b0b15016c2 · unknown · (source_grounded) ## Published July 15, 2026 *Co-authored by Rich Young, Director of Partner Solutions Architecture, and · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-16 | Added arize-blog--50b0b15016c2 | gate: gate: active (five-gate auto-promote) |
- [[aws-ml-blog--b516225b6b26]] — aws-ml-blog--b516225b6b26 · unknown · (source_grounded) three-layer validation approach allows AI Judges [raw §G_EXAMPLES]. (source_grounded) 机制组件（4 个）：AI Jud · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-16 | Added aws-ml-blog--b516225b6b26 | gate: gate: active (five-gate auto-promote) |
- [[marimo-blog--54ea704fab9c]] — marimo-blog--54ea704fab9c · unknown · (source_grounded) marimo鼓励教育者通过邮件或Discord寻求帮助，并推荐查看作业笔记本以了解pytest集成如何改善学习体验 [raw §H_HOW_STEPS]. (source_grounded) molab现 · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-16 | Added marimo-blog--54ea704fab9c | gate: gate: active (five-gate auto-promote) |
- [[ollama-blog--8cba42e14be6]] — ollama-blog--8cba42e14be6 · unknown · (source_grounded) ## Thinking ## May 30, 2025 Ollama now has the ability to enable or disable thinking [raw §A]. · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-16 | Added ollama-blog--8cba42e14be6 | gate: gate: active (five-gate auto-promote) |
- [[runpod-blog--035dc17f716e]] — runpod-blog--035dc17f716e · unknown · (source_grounded) Blog Learn how to deploy Meta's Llama 3；1 8B Instruct model using the vLLM inference engine on Runpod  · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-16 | Added runpod-blog--035dc17f716e | gate: gate: active (five-gate auto-promote) |
- [[huggingface-blog--7f36308856b0]] — huggingface-blog--7f36308856b0 · unknown · (source_grounded) [Back to Articles](/blog) Published July 15, 2026 [Update on GitHub](https://github；com/huggingface/bl · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-16 | Added huggingface-blog--7f36308856b0 | gate: gate: active (five-gate auto-promote) |
- [[together-ai-blog--d3159581f347]] — together-ai-blog--d3159581f347 · unknown · (source_grounded) KV cache addresses DeepSeek-V4 [raw §D_ARGUMENT]. (source_grounded) 机制组件（4 个）：Compressed Sparse Attent · serves token, loop_engineering · basis: gate: active (five-gate auto-promote)
| 2026-07-16 | Added together-ai-blog--d3159581f347 | gate: gate: active (five-gate auto-promote) |
- [[weaviate-blog--11ed3df980c0]] — weaviate-blog--11ed3df980c0 · unknown · (source_grounded) [← Back to Blogs](/blog) It has never been easier to build software [raw §A]. · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-17 | Added weaviate-blog--11ed3df980c0 | gate: gate: active (five-gate auto-promote) |
- [[langchain-blog--92884e6fce9a]] — langchain-blog--92884e6fce9a · unknown · (source_grounded) satisfied terminates max_iterations_reached [raw §H_HOW_STEPS]. (source_grounded) 机制组件（4 个）：grader sub · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-17 | Added langchain-blog--92884e6fce9a | gate: gate: active (five-gate auto-promote) |
- [[openrouter-blog--04b931093448]] — openrouter-blog--04b931093448 · unknown · (source_grounded) ## Introducing the Unified Image API Brian Thomas · ；[Introducing the Unified Image API](/blog/images/ · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-17 | Added openrouter-blog--04b931093448 | gate: gate: active (five-gate auto-promote) |
- [[ollama-blog--1d914294b487]] — ollama-blog--1d914294b487 · unknown · (source_grounded) 机制组件（4 个）：OpenJarvis, Intelligence Per Watt, morning-digest-mac, deep-research [raw §B_INTRODUCTION].  · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-17 | Added ollama-blog--1d914294b487 | gate: gate: active (five-gate auto-promote) |
- [[huggingface-blog--3ce97f6a8c6c]] — huggingface-blog--3ce97f6a8c6c · unknown · (source_grounded) 机制组件（4 个）：transformers vLLM backend, torch.fx, ast, Fused operations [raw §B_INTRODUCTION]. (source_gr · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-17 | Added huggingface-blog--3ce97f6a8c6c | gate: gate: active (five-gate auto-promote) |
- [[phil-schmid--23c513d396b6]] — phil-schmid--23c513d396b6 · unknown · (source_grounded) 机制组件（4 个）：Gemini, custom function, Google Search, URL Context [raw §H_HOW_STEPS]. (source_grounded) 过去 · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-17 | Added phil-schmid--23c513d396b6 | gate: gate: active (five-gate auto-promote) |
- [[qdrant-blog--927cfbf31653]] — qdrant-blog--927cfbf31653 · unknown · (source_grounded) Qdrant skills 向 AI 智能体注入方案架构师级别的诊断知识，使其在向量搜索的工程权衡空间（内存、延迟、召回率、吞吐量、成本）中做出正确配置决策，而非仅调用检索 API。该技能将 Claude · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-17 | Added qdrant-blog--927cfbf31653 | gate: gate: active (five-gate auto-promote) |
- [[openrouter-blog--1975e7e54c29]] — openrouter-blog--1975e7e54c29 · unknown · (source_grounded) ## Guardrails: Protect your Agents, Data, and Costs Cailee Moberg · [raw §A]. (source_grounded) OpenRo · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-17 | Added openrouter-blog--1975e7e54c29 | gate: gate: active (five-gate auto-promote) |
- [[huggingface-blog--b0d7df1028b0]] — huggingface-blog--b0d7df1028b0 · unknown · (source_grounded) 机制组件（4 个）：AI Drafting Model, Deterministic Python Scripts, Human Reviewer, GitHub Actions [raw §F_EVID · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-18 | Added huggingface-blog--b0d7df1028b0 | gate: gate: active (five-gate auto-promote) |
- [[google-research-ai--c1e4f65b1ad4]] — google-research-ai--c1e4f65b1ad4 · unknown · (source_grounded) 该工作通过 LLM 生成地图描述提示词，text-to-image 模型据此渲染地图；Mask Critic 与 Path Critic 分别对候选掩码和最终路径进行质量筛选，产出大规模高保真路径标注数据 · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-18 | Added google-research-ai--c1e4f65b1ad4 | gate: gate: active (five-gate auto-promote) |
- [[vllm-blog--459e0435ec11]] — vllm-blog--459e0435ec11 · unknown · (source_grounded) Menu Theme **TL DR:** Prefill and decode fight over the same GPUs, causing ITL spikes under load [raw  · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-18 | Added vllm-blog--459e0435ec11 | gate: gate: active (five-gate auto-promote) |
- [[phil-schmid--7239f688a564]] — phil-schmid--7239f688a564 · unknown · (source_grounded) 机制组件（4 个）：langchain-google-genai, LangChain, LangGraph, Google AI Studio [raw §A]. (source_grounded) l · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-18 | Added phil-schmid--7239f688a564 | gate: gate: active (five-gate auto-promote) |
- [[openrouter-blog--5b4eb5d6ea90]] — openrouter-blog--5b4eb5d6ea90 · unknown · (source_grounded) 机制组件（4 个）：Agent SDK, EU AI Act, Article 14, onToolCalled, requireApproval [raw §B_INTRODUCTION]. (sour · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-18 | Added openrouter-blog--5b4eb5d6ea90 | gate: gate: active (five-gate auto-promote) |
- [[openrouter-blog--bdab7c95bfd8]] — openrouter-blog--bdab7c95bfd8 · unknown · (source_grounded) ## Response Caching: Zero Cost for Identical Requests Brian Thomas · [raw §A]. (source_grounded) ## Wh · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-18 | Added openrouter-blog--bdab7c95bfd8 | gate: gate: active (five-gate auto-promote) |
- [[langchain-blog--008bbefc919e]] — langchain-blog--008bbefc919e · unknown · (source_grounded) AI Agent Service enables Amazon ECS Fargate [raw §S_CODE]. (source_grounded) 机制组件（4 个）：AI Agent Servic · serves langgraph, loop_engineering · basis: gate: active (five-gate auto-promote)
| 2026-07-20 | Added langchain-blog--008bbefc919e | gate: gate: active (five-gate auto-promote) |
- [[openrouter-blog--8e3239a63785]] — openrouter-blog--8e3239a63785 · unknown · (source_grounded) ## Keep Your Agent Running When Models Disappear Kenny Rogers and Alex Atallah · Providers retire and  · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-20 | Added openrouter-blog--8e3239a63785 | gate: gate: active (five-gate auto-promote) |
- [[phil-schmid--7117aa0a4295]] — phil-schmid--7117aa0a4295 · unknown · (source_grounded) 机制组件（4 个）：Hugging Face LLM Inf2 Container, Amazon SageMaker, Text Generation Inference (TGI), Optimum  · serves token, loop_engineering · basis: gate: active (five-gate auto-promote)
| 2026-07-20 | Added phil-schmid--7117aa0a4295 | gate: gate: active (five-gate auto-promote) |
- [[addy-osmani--b2b55b857089]] — addy-osmani--b2b55b857089 · unknown · (source_grounded) automated checks required for review gate [raw §H_HOW_STEPS]. (source_grounded) 机制组件（4 个）：loop, harnes · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-21 | Added addy-osmani--b2b55b857089 | gate: gate: active (five-gate auto-promote) |
- [[huggingface-blog--13c5d54df9fe]] — huggingface-blog--13c5d54df9fe · unknown · (source_grounded) 机制组件（4 个）：Llama-Nemotron-Embed-1B-v2, nvidia/nemotron-3-nano-30b-a3b, Synthetic Data Generation (SDG)  · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-21 | Added huggingface-blog--13c5d54df9fe | gate: gate: active (five-gate auto-promote) |
- [[together-ai-blog--b4bf4ba72cff]] — together-ai-blog--b4bf4ba72cff · unknown · (source_grounded) 原文结论（must_cover 代码直出）：Therefore the response of this request will return a payload with an identifier  · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-21 | Added together-ai-blog--b4bf4ba72cff | gate: gate: active (five-gate auto-promote) |
- [[together-ai-blog--e09b1f02b098]] — together-ai-blog--e09b1f02b098 · unknown · DSGym 是一个统一的数据科学 agent 评测与训练框架，通过标准化 API 将多种评测套件整合到单一接口中，并提供实时执行容器以安全运行代码。[raw §E_CLAIMS] 其核心机制包括：执行容器提供安全代码执行环境，精炼数据集（D · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-21 | Added together-ai-blog--e09b1f02b098 | gate: gate: active (five-gate auto-promote) |
- [[zed-blog--ead0a0400895]] — zed-blog--ead0a0400895 · unknown · Zed AI 的助手面板是一个完整的文本编辑器，直接暴露整个 LLM 请求，让用户可观察、编辑和优化提示的每一部分，提供完全的透明度和控制力（[raw §E_CLAIMS]）。通过 slash commands 系统注入上下文、inline · serves workflow, loop_engineering · basis: gate: active (five-gate auto-promote)
| 2026-07-22 | Added zed-blog--ead0a0400895 | gate: gate: active (five-gate auto-promote) |
- [[vercel-blog--149284f5a166]] — vercel-blog--149284f5a166 · unknown · (source_grounded) [Vercel Blob](https://vercel；com/docs/vercel-blob) now supports consistent reads on private storage [r · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-22 | Added vercel-blog--149284f5a166 | gate: gate: active (five-gate auto-promote) |
- [[css-irl--9621eb52be7a]] — css-irl--9621eb52be7a · unknown · (source_grounded) 机制组件（4 个）：theme.js, index.js, mapTheme function, buildTheme function [raw §A]. (source_grounded) 为Java · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-22 | Added css-irl--9621eb52be7a | gate: gate: active (five-gate auto-promote) |
- [[css-irl--e667743045fb]] — css-irl--e667743045fb · unknown · (source_grounded) Popover attribute is_adjacent_to Popover target button [raw §H_HOW_STEPS]. (source_grounded) 机制组件（4 个） · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-22 | Added css-irl--e667743045fb | gate: gate: active (five-gate auto-promote) |
- [[qdrant-blog--943dae022e8f]] — qdrant-blog--943dae022e8f · unknown · (source_grounded) 0 Filip Makraduli, David Myriel · April 24, 2025 AI has transformed how we find products, services, an · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-23 | Added qdrant-blog--943dae022e8f | gate: gate: active (five-gate auto-promote) |
- [[aws-ml-blog--c5a957fca16c]] — aws-ml-blog--c5a957fca16c · unknown · (source_grounded) ## Artificial Intelligence Fast-growing companies and enterprise supply-chain teams often have enough  · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-24 | Added aws-ml-blog--c5a957fca16c | gate: gate: active (five-gate auto-promote) |
- [[smashing-magazine--fa0b3ac1fdf6]] — smashing-magazine--fa0b3ac1fdf6 · unknown · (source_grounded) [Start reading the article](#article__start) [Jump to list of all articles](/articles/) [Jump to all t · serves loop_engineering, quality_gate, agent_workflow · basis: gate: active (five-gate auto-promote)
| 2026-07-24 | Added smashing-magazine--fa0b3ac1fdf6 | gate: gate: active (five-gate auto-promote) |
- [[phil-schmid--4458cb45b2df]] — phil-schmid--4458cb45b2df · unknown · (source_grounded) Flash Attention 通过重排注意力计算并利用分块与重计算技术，可将训练速度提升最多 3 倍 [raw §D_ARGUMENT]. (source_grounded) QLoRA 是一种通过量化 · serves loop_engineering, quality_gate · basis: gate: active (five-gate auto-promote)
| 2026-07-24 | Added phil-schmid--4458cb45b2df | gate: gate: active (five-gate auto-promote) |