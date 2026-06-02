# Scoring 语义 vs 关键词 离线对比 + 标定 (P1-2)

- 锚点数：5（多语言，max cosine 归一化）
- 样本推文：12；对照探针：7
- 嵌入模型：text-embedding-3-small

## 标定结论（推荐写入 scoring.py 常量）

```python
_RELEVANCE_COS_LOW = 0.1132   # 离题内容 cosine 地板（max offtopic = 0.1132）
_RELEVANCE_COS_HIGH = 0.3444  # 相关内容归一化上界（relevant 低分位）
# relevance = clamp((max_cos - LOW) / (HIGH - LOW), 0, 1)
```

- 离题 cosine 范围：0.0642 ~ 0.1132
- AI 探针 cosine 范围：0.2929 ~ 0.4063
- 样本 cosine 范围：0.3074 ~ 0.5057

## 对照探针：关键词窄门 vs 语义（核心证据）

| 探针 | 关键词 relevance | 语义 max cosine | 文本 |
|------|----------------|----------------|------|
| zh_ai_1 | 0.00 | 0.3792 | 国产大模型今天发布，推理能力大幅提升，中文理解超过上一代，价格还降了一半。 |
| zh_ai_2 | 0.00 | 0.3916 | 我们用智能体重构了整个客服流程，自动处理率超过八成，人工只需复核。 |
| nobuzz_ai_1 | 0.00 | 0.4063 | Spent the weekend getting FP8 quantization working on our se |
| nobuzz_ai_2 | 0.00 | 0.2929 | Stop chunking documents and bolting on a vector DB; let the  |
| offtopic_1 | 0.00 | 0.1132 | Just made the best homemade pizza with fresh basil and mozza |
| offtopic_2 | 0.00 | 0.0884 | The Lakers won last night in overtime, incredible game and a |
| offtopic_3 | 0.00 | 0.0642 | Stock market hit a new record high today after the central b |

> 关键：中文 AI / 无 buzzword AI 探针关键词 relevance≈0（被窄门漏掉），但语义 cosine 明显高于离题 → 语义版能捞回。

## 样本推文：关键词 vs 语义并排

| 作者 | 关键词 relevance | 语义 max cosine | 文本(截断) |
|------|----------------|----------------|-----------|
| @sama | 1.00 | 0.5057 | GPT-5 is now available in ChatGPT. The reasoning improvement |
| @AnthropicAI | 1.00 | 0.4575 | Claude 4 Opus now achieves 72% on SWE-bench verified. We're  |
| @GoogleDeepMind | 0.67 | 0.4482 | The multimodal gap is closing fast. Gemini 2.5 Ultra process |
| @karpathy | 1.00 | 0.4792 | LLMs as operating systems: the new paradigm. Your model does |
| @ylecun | 0.67 | 0.4679 | AGI timelines just compressed again. o3 scores 96.7 on MMLU- |
| @AIatMeta | 0.33 | 0.3254 | We open-sourced Llama 4 Maverick weights today — 400B MoE, A |
| @paulfchristiano | 1.00 | 0.4557 | Hot take: the 'alignment tax' is gone. The top RLHF-tuned mo |
| @mckaywrigley | 0.67 | 0.3849 | MCP (Model Context Protocol) now has 50k+ registered servers |
| @github | 0.67 | 0.3782 | Coding agents crossed a threshold: our internal benchmarks s |
| @AnthropicAI | 0.67 | 0.4409 | Just published our paper on 'Constitutional AI at Scale' — w |
| @deepseek_ai | 0.67 | 0.3729 | DeepSeek V4 Pro benchmark results: 96.4% HumanEval, 91.2% MA |
| @swyx | 1.00 | 0.3074 | The 'vibe coding' era is producing real production software  |
