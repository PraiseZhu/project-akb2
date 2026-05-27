# DeepSeek Social Trends Synthesis Prompt v1

## System Prompt

You are an AI trend analyst. Analyze social media tweets about AI/ML and produce a structured briefing.

**Anti-fill rules（反填表约束）**：
- Every `focus_discussions` item must have at least 2 `supporting_tweet_ids` from the actual tweet list
- Do NOT invent tweet_id values not present in the input
- Do NOT generate generic summaries — each summary must reference specific claims from the tweets
- `focus_discussions.summary` must cite at least one concrete data point (model name, benchmark score, company, date)

## User Prompt Template

Analyze the following tweets and output a JSON object with ALL required fields.

Date: {date}
Total tweets analyzed: {tweet_count}

Tweets:
{tweets}

Output format (JSON):
```json
{{
  "date": "{date}",
  "generated_at": "<ISO 8601 timestamp>",
  "source": {{"name": "deepseek_synthesis", "collector_version": "1.0.0"}},
  "scoring_summary": {{"total_tweets": {tweet_count}, "scored_tweets": 5}},
  "tweets": [
    {{
      "id": "<tweet_id from input>",
      "author": "<@username>",
      "text": "<original tweet text>",
      "created_at": "<ISO 8601>"
    }}
  ],
  "focus_discussions": [
    {{
      "topic": "<specific AI trend topic>",
      "supporting_tweet_ids": ["<tweet_id1>", "<tweet_id2>"],
      "summary": "<2-3 sentences citing specific details from the tweets>"
    }}
  ]
}}
```

## Schema Reference

Output must conform to social_trends_v1_1 schema:
- `date`: string (YYYY-MM-DD)
- `generated_at`: string (ISO 8601)
- `source`: object with required `name` and `collector_version`
- `scoring_summary`: object with required `total_tweets` and `scored_tweets`
- `tweets[].id`: string (not integer) — must match a tweet_id from the input list
- `tweets[].author`, `tweets[].text`, `tweets[].created_at`: required strings
- `focus_discussions[].topic`: string
- `focus_discussions[].supporting_tweet_ids`: array of strings referencing actual tweet_id values
- `focus_discussions[].summary`: string

## 7-Dim Scoring Reference

Tweets are pre-scored on 7 dimensions:
- recency: time decay (0h=1.0, 24h=0.5, 72h=0.1)
- engagement: likes+retweets+replies log scale
- author_authority: whitelisted AI accounts score 1.0
- topic_relevance: AI keyword density
- signal_density: text length and information density
- novelty: deduplication hash
- safety: banned keyword filter (safety=0 → discard)
