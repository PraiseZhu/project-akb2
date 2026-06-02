# 打分排序 eval 报告 — 2026-06-02

GOAL_SOURCE: Project AKB2 — 社交日报系统（P1-7 harness / P1-8 验收）
标注集：45 条（label=1: 30，关键词受害者: 14）
K = 30

## 指标对比（语义版 vs 关键词版）

| 指标 | 关键词版 | 语义版 | 语义优于? |
|------|----------|--------|-----------|
| precision@30 | 0.9333 | 1.0000 | ✓ |
| nDCG@30 | 0.9511 | 1.0000 | ✓ |
| victim_rescue@30 | 12/14 | 14/14 | ✓ |

**验收（SC-8）**：✅ APPROVED — 语义版 nDCG 与 precision 均 ≥ 关键词版

## 关键词版 top-30 排序

| # | label | score | topic_rel | author | text |
|---|-------|-------|-----------|--------|------|
| 1 | 1 | 0.8382 | 1.000 | @karpathy | LLMs as operating systems: the model orc |
| 2 | 1 | 0.8346 | 1.000 | @sama | GPT-5 reasoning improvements are real —  |
| 3 | 1 | 0.8117 | 1.000 | @paulfchristiano | Alignment update: top RLHF models now ou |
| 4 | 1 | 0.6966 | 0.667 | @AnthropicAI | Claude 4 Opus hits 72% on SWE-bench veri |
| 5 | 1 | 0.6956 | 0.667 | @github | Coding agents cross 60% autonomous fix r |
| 6 | 1 | 0.6835 | 0.667 | @AnthropicAI | Constitutional AI at scale: steer 405B m |
| 7 | 1 | 0.6033 | 0.667 | @sec_ml | We hacked our own model in a red-team sp |
| 8 | 1 | 0.5940 | 0.667 | @research_dev | Fine-tuning small models on synthetic se |
| 9 | 1 | 0.5856 | 0.667 | @ml_eng | Multimodal inference at 2M context over  |
| 10 | 1 | 0.5798 | 0.333 | @AIatMeta | Open-sourced Llama 4 Maverick: 400B MoE, |
| 11 | 1 | 0.5695 | 0.333 | @deepseek_ai | DeepSeek V4 Pro: 96.4% HumanEval at $0.1 |
| 12 | 1 | 0.5544 | 0.333 | @swyx | Vibe coding ships real production softwa |
| 13 | 1 | 0.5513 | 0.333 | @ylecun | o3 scores 96.7 on MMLU-Pro and 84% on AR |
| 14 | 1 | 0.4735 | 0.333 | @redteam_ai | New paper on prompt-injection exploits i |
| 15 | 1 | 0.4670 | 0.333 | @ai_phd | Transformer attention variants keep gett |
| 16 | 1 | 0.4647 | 0.333 | @data_eng | Synthetic data flywheels are the real mo |
| 17 | 0 | 0.4161 | 0.333 | @tvfan | New season of the cooking show drops Fri |
| 18 | 1 | 0.4012 | 0.000 | @mckaywrigley | MCP now has 50k+ registered servers; eve |
| 19 | 1 | 0.3472 | 0.000 | @builder | Stop bolting a vector DB onto everything |
| 20 | 1 | 0.3436 | 0.000 | @cn_ai_lab | 国产大模型今天发布，推理能力大幅提升，中文理解明显超过上一代，价格还降了一半。 |
| 21 | 1 | 0.3400 | 0.000 | @infra_dev | Spent the weekend getting FP8 quantizati |
| 22 | 1 | 0.3347 | 0.000 | @doctor_ml | 实测下来，新一代多模态模型在医学影像早筛上已经超过不少放射科住院医。 |
| 23 | 1 | 0.3317 | 0.000 | @perf_eng | Speculative decoding plus paged KV cache |
| 24 | 1 | 0.3275 | 0.000 | @arch_dev | Mixture-of-experts routing finally stabl |
| 25 | 1 | 0.3252 | 0.000 | @distill_dev | Distillation from a frontier teacher int |
| 26 | 1 | 0.3204 | 0.000 | @edge_ai_zh | 推理成本又降了一个数量级，端侧跑 70B 量化模型已经不是梦。 |
| 27 | 0 | 0.3200 | 0.000 | @hoops | The Lakers won last night in overtime, w |
| 28 | 1 | 0.3178 | 0.000 | @agent_zh | 智能体编排框架现在能稳定跑多轮工具调用，长任务的成功率比半年前翻倍。 |
| 29 | 1 | 0.3162 | 0.000 | @founder_zh | 我们团队用智能体重构了整个客服流程，现在自动处理率超过八成，人工只需复核异常。 |
| 30 | 1 | 0.3134 | 0.000 | @safety_zh | 对齐研究的中文社区终于热起来了，红队测试和越狱防护的讨论越来越扎实。 |

## 语义版 top-30 排序

| # | label | score | topic_rel | author | text |
|---|-------|-------|-----------|--------|------|
| 1 | 1 | 0.8382 | 1.000 | @karpathy | LLMs as operating systems: the model orc |
| 2 | 1 | 0.8346 | 1.000 | @sama | GPT-5 reasoning improvements are real —  |
| 3 | 1 | 0.8299 | 1.000 | @AnthropicAI | Claude 4 Opus hits 72% on SWE-bench veri |
| 4 | 1 | 0.8289 | 1.000 | @github | Coding agents cross 60% autonomous fix r |
| 5 | 1 | 0.8169 | 1.000 | @AnthropicAI | Constitutional AI at scale: steer 405B m |
| 6 | 1 | 0.8117 | 1.000 | @paulfchristiano | Alignment update: top RLHF models now ou |
| 7 | 1 | 0.8016 | 0.888 | @AIatMeta | Open-sourced Llama 4 Maverick: 400B MoE, |
| 8 | 1 | 0.7638 | 0.819 | @deepseek_ai | DeepSeek V4 Pro: 96.4% HumanEval at $0.1 |
| 9 | 1 | 0.7589 | 0.845 | @swyx | Vibe coding ships real production softwa |
| 10 | 1 | 0.7436 | 1.000 | @cn_ai_lab | 国产大模型今天发布，推理能力大幅提升，中文理解明显超过上一代，价格还降了一半。 |
| 11 | 1 | 0.7401 | 1.000 | @redteam_ai | New paper on prompt-injection exploits i |
| 12 | 1 | 0.7400 | 1.000 | @infra_dev | Spent the weekend getting FP8 quantizati |
| 13 | 1 | 0.7336 | 1.000 | @ai_phd | Transformer attention variants keep gett |
| 14 | 1 | 0.7314 | 1.000 | @data_eng | Synthetic data flywheels are the real mo |
| 15 | 1 | 0.7301 | 0.996 | @perf_eng | Speculative decoding plus paged KV cache |
| 16 | 1 | 0.7275 | 1.000 | @arch_dev | Mixture-of-experts routing finally stabl |
| 17 | 1 | 0.7273 | 1.000 | @research_dev | Fine-tuning small models on synthetic se |
| 18 | 1 | 0.7204 | 1.000 | @edge_ai_zh | 推理成本又降了一个数量级，端侧跑 70B 量化模型已经不是梦。 |
| 19 | 1 | 0.7190 | 1.000 | @ml_eng | Multimodal inference at 2M context over  |
| 20 | 1 | 0.7178 | 1.000 | @agent_zh | 智能体编排框架现在能稳定跑多轮工具调用，长任务的成功率比半年前翻倍。 |
| 21 | 1 | 0.7097 | 0.984 | @founder_zh | 我们团队用智能体重构了整个客服流程，现在自动处理率超过八成，人工只需复核异常。 |
| 22 | 1 | 0.7049 | 1.000 | @nlp_zh | 微调一个小模型做垂直领域问答，效果出乎意料，关键是数据清洗而不是参数量。 |
| 23 | 1 | 0.7025 | 0.985 | @rag_zh | 把检索从「先分块再塞向量库」改成让模型自己决定何时取什么，准确率明显上来了。 |
| 24 | 1 | 0.6981 | 0.700 | @ylecun | o3 scores 96.7 on MMLU-Pro and 84% on AR |
| 25 | 1 | 0.6778 | 0.692 | @mckaywrigley | MCP now has 50k+ registered servers; eve |
| 26 | 1 | 0.6259 | 0.697 | @builder | Stop bolting a vector DB onto everything |
| 27 | 1 | 0.6194 | 0.712 | @doctor_ml | 实测下来，新一代多模态模型在医学影像早筛上已经超过不少放射科住院医。 |
| 28 | 1 | 0.5318 | 0.488 | @sec_ml | We hacked our own model in a red-team sp |
| 29 | 1 | 0.5217 | 0.521 | @safety_zh | 对齐研究的中文社区终于热起来了，红队测试和越狱防护的讨论越来越扎实。 |
| 30 | 1 | 0.4683 | 0.358 | @distill_dev | Distillation from a frontier teacher int |
