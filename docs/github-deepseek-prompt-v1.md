# GitHub Curator DeepSeek V4 Pro Prompt v1

> Owned by step-3 (synthesis)
> 调用方：`src/akb2/briefing/github/synthesis.py`
> 配套 schema：[`schemas/github_briefing_v1.json`](../src/akb2/briefing/github/schemas/github_briefing_v1.json)

## 调用契约

- **模式**：batch — 10 个 repo 一次 LLM 调用（节省 token，相比 per-repo 省 10×）
- **input tokens 上限**：12,000（SC-6 强卡）
- **temperature**：0.3（事实型摘要不需要发散）
- **max_output_tokens**：4,000（10 × 400 tokens 平均）

## Prompt 完整模板

```text
<system>
你是 GitHub 项目情报编辑。任务：为以下 N 个 GitHub 仓库各产出两段中文文本：
  ① summary（200 字以内）— 客观陈述项目做什么、技术栈、解决的核心问题
  ② use_case（30-300 字）— 什么情况下该装，什么情况下别装；必须含 1-2 条正向场景 + 1 条反向场景

规则：
- 客观、紧凑、不堆形容词、不重复 README 原话
- summary 回答"是什么"，use_case 回答"什么时候用，什么时候别用"
- 输出 JSON 数组，每项 {"repo_url": ..., "summary": ..., "use_case": ...}
- 严格按输入顺序输出，repo_url 必须完全匹配输入
</system>

<input>
<repo url="https://github.com/owner/repo1">
  stars: 1234
  language: Python
  topics: [llm, agent]
  mentioned_by: @simonw, @karpathy
  readme_excerpt: |
    {{README 头 500 字，已 XML escape}}
</repo>
... × 10
</input>

<output_format>
[
  {"repo_url": "...", "summary": "...", "use_case": "..."},
  ...
]
</output_format>

<use_case_example>
正例 1：你需要在 Python 项目里跑量化回测，希望从数据拉取到信号评估一条链路打通。
正例 2：公司内部做 alpha 因子研究需要可复现的 baseline。
反例：纯加密货币高频交易场景不适用（项目侧重传统金融时序）。
</use_case_example>
```

## Prompt Injection 防护

1. **XML escape**：所有 `readme_excerpt` 和 `mentioned_by` 在拼 prompt 前过 `xml.sax.saxutils.escape()`
2. **XML 标签 fence**：每个 repo 用 `<repo url="...">` 标签包裹，避免博主推文/README 中的 `</repo>` 或 "ignore previous instructions" 逃逸
3. **输入字段白名单**：只把 stars / language / topics / mentioned_by / readme_excerpt 喂进 prompt；tweet_text 不进 prompt（高风险来源，scoring 用即可）
4. **输出 schema 校验**：DeepSeek 返回后立即用 jsonschema 校验数组每项 = `{repo_url, summary, use_case}`，少字段或多字段直接 abort

## 错误处理

| 错误 | 行为 |
|------|------|
| API 调用失败 | retry 3 次（退避 5s/15s/45s），仍失败 → 写 `briefing-failed.json` |
| 输出 JSON 解析失败 | retry 1 次（追加 "前次输出不是合法 JSON，重新输出" prompt），仍失败 → 写 `briefing-failed.json` |
| 输出条数 ≠ 10 | abort，不写 vault |
| use_case 长度 < 30 或 > 300 | abort（pipeline 入口已通过 schema validator 卡） |
| repo_url 与输入不匹配 | abort（防 LLM 编造 repo） |

## 成本估算

- 平均每次：~8K input tokens + ~4K output tokens
- DeepSeek V4 Pro 价格：~$0.27/MTok input + $1.10/MTok output
- 单次成本：$0.0066
- 30 天成本：≈ $0.20

## 未来调优空间（v2）

- few-shot 加 5 个"高质量摘要"示例
- 根据 readme 长度动态调整 summary 长度上限
- 用户反馈数据加权 prompt（"哪些 use_case 描述更有用"）
