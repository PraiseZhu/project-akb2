# DeepSeek 社交趋势综合 Prompt v3（精简中文版）

## 任务

分析 {tweet_count} 条今日（{date}）AI/ML 领域真实推文，输出结构化中文趋势简报 JSON。

## 输入推文

{tweets}

---

## 输出要求

**全部 string 字段必须用简体中文。**输入推文是英文，但你的所有分析必须中文表达。

输出 JSON，**完整字段如下，缺一不可**：

```json
{{
  "date": "{date}",
  "generated_at": "<ISO 8601>",
  "source": {{"name": "deepseek_synthesis", "collector_version": "1.0.0"}},
  "scoring_summary": {{"total_tweets": {tweet_count}, "scored_tweets": 5}},

  "executive_summary": "<必填 80-150 字简体中文,概括今日讨论焦点>",

  "tweets": [
    {{"id": "<真实 ID>", "author": "<@用户名>", "text": "<原文>", "created_at": "<ISO 8601>"}}
  ],

  "focus_discussions": [
    {{
      "topic": "<必填中文短语,如『智能体长记忆基础设施』『视觉成为 AI 主导输出模态』『Anthropic 算力瓶颈』,禁止用『AI Trends』之类空泛标题>",
      "supporting_tweet_ids": ["<≥2 个真实 tweet_id,必须来自输入>"],
      "summary": "<必填 3-5 句简体中文。先说事实(谁/什么/数字),再说意义或对比。必须引用至少 1 个具体工具名/模型名/数字。>"
    }}
  ],

  "key_quotes": [
    {{
      "author": "<@用户名>",
      "quote": "<推文节选,英文原文,加中文翻译括注>",
      "tweet_id": "<必填,来自 supporting_tweet_ids>",
      "context": "<1 句中文,说为什么值得引述>"
    }}
  ]
}}
```

## 硬约束（再次强调）

1. **中文**：topic / summary / executive_summary / context 全部简体中文。违反则输出作废。
2. **必填字段**：executive_summary 必填，key_quotes 必填 2-4 条。
3. **focus_discussions 数量**：3-5 个，主题不重复，每个 topic 必须是具体的中文短语（禁止「AI Trends」「AI 趋势」「核心讨论」等空洞词）。
4. **禁止编造**：所有事件、数字、引述只能来自输入推文。禁止用训练时学到的"AI 行业常识"凭空生成趋势。如果不确定，减少主题数。
5. **supporting_tweet_ids**：每个 focus_discussion 至少 2 个，必须是输入推文里真实出现的 ID。
6. **key_quotes.tweet_id**：必须是 supporting_tweet_ids 中的真实 ID，不可虚构。

## 反面案例（严禁重复）

- 把 topic 写成 "AI Trends"（无信息量）
- 用英文写 summary（违反中文要求）
- 漏掉 executive_summary 字段
- 漏掉 key_quotes 字段
- 编造"X 公司收购 Y"但输入推文里没有这条
