# DeepSeek 社交趋势综合 Prompt v4（核心讨论骨架版）

## 任务

分析 {tweet_count} 条今日（{date}）AI/ML 领域真实推文，输出结构化趋势简报 JSON。

## 输入推文

{tweets}

---

## 输出要求

输出 JSON，**完整字段如下，缺一不可**：

```json
{{
  "date": "{date}",
  "generated_at": "<ISO 8601>",
  "source": {{"name": "deepseek_synthesis", "collector_version": "1.0.0"}},
  "scoring_summary": {{"total_tweets": {tweet_count}, "scored_tweets": 5}},

  "executive_summary": "<必填，80-150 字简体中文，概括今日最重要的 2-3 个讨论焦点>",

  "tweets": [
    {{"id": "<真实 ID>", "author": "<@用户名>", "text": "<原文>", "created_at": "<ISO 8601>"}}
  ],

  "focus_discussions": [
    {{
      "topic": "<必填中文短语，如『智能体长记忆基础设施』『视觉成为 AI 主导输出模态』，禁止用『AI Trends』之类空泛标题>",
      "supporting_tweet_ids": ["<≥2 个真实 tweet_id，必须是输入推文里的真实 ID；把本主题所有分数靠前的重要推文都包含进来，不要只放 2 个>"],
      "summary": "<必填。必须写满 3 至 5 句完整简体中文。第1句：说谁/什么/具体数字（至少引用 1 个工具名/模型名/数字）。第2句：说为什么重要。第3句：说影响或趋势。如果有更多细节，继续第4-5句。3句未满不得停止。>"
    }}
  ]
}}
```

## 硬约束（再次强调）

1. **中文**：topic / summary / executive_summary 全部简体中文。违反则输出作废。
2. **必填字段**：executive_summary 必填；focus_discussions 每条 summary 必须 ≥3 句。
3. **focus_discussions 数量**：3-5 个，主题不重复，每个 topic 必须是具体的中文短语（禁止「AI Trends」「AI 趋势」「核心讨论」等空洞词）。
4. **禁止编造**：所有事件、数字、引述只能来自输入推文。禁止用训练时学到的"AI 行业常识"凭空生成趋势。如果不确定，减少主题数。
5. **supporting_tweet_ids 要求**：
   - 必须是输入推文里真实出现的 ID，禁止伪造。
   - 每个主题至少 2 个，但不要人为限制——凡是对该主题有贡献的高分推文都应放入。
   - 高分推文（score 前 10）至少要出现在某个主题的 supporting_tweet_ids 里，不要遗漏。
   - 一条推文可以属于多个主题（如果它真的同时支撑两个主题）。

## 反面案例（严禁重复）

- 把 topic 写成 "AI Trends"（无信息量）
- summary 只有 1-2 句就停止（不够 3 句）
- supporting_tweet_ids 只放 2 个但实际有 6 条相关推文（遗漏了高分推文）
- 编造"X 公司收购 Y"但输入推文里没有这条
- supporting_tweet_ids 里放了不存在的 tweet_id
