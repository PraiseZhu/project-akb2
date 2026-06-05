# GitHub Curator 简报渲染模板 v1

> Owned by step-4 (pipeline)
> 调用方：`src/akb2/briefing/github/pipeline.py` 内 `render_briefing(briefing_json: dict) -> str`
> 配套 schema：[`schemas/github_briefing_v1.json`](../src/akb2/briefing/github/schemas/github_briefing_v1.json)

## 模板规范

每日产出 1 份 `.md` 文件 + 1 份同名 `.json` sidecar（schema-validated）：

- `.md` — 给人读，按本模板渲染
- `.json` — 给程序读 / SC 验证用，内容 = github_briefing_v1 schema 实例

## 完整模板

````text
---
date: <YYYY-MM-DD>
type: github-briefing
source: github-curator
fallback_layer: layer1|layer2|layer3
candidate_pool_size: <int>
active_curators_today: <int>
generated_at: <ISO>
tags:
  - github
  - ai-trends
  - daily
---

# GitHub 项目日报 · <YYYY-MM-DD>

> 今日活跃博主 <N>/<pool_size>，走 <Layer X>。Top 10 涵盖：<根据 topic_tags 聚合分类的一句话>。
> （fallback_layer != layer1 时显式提示："今日走兜底链路，质量需人工 review"）

## 1. [owner/repo](https://github.com/owner/repo) ⭐ <stars> · <language>

`topic1` `topic2` `topic3` · **<N> mentions**(@h1 · @h2 · @h3)
score <0.xx> · 推荐于 <first_mention_at 短格式>

**做什么**: <summary 200 字>

**适用场景**: <use_case 30-300 字，含正向 + 反向>

---

## 2. [owner/repo-2](...) ...

（同上结构，1-10 编号 + score 降序）
````

## 渲染规则

| 项 | 规则 |
|----|------|
| 编号 | 1-10，按 score 降序 |
| repo 链接 | markdown `[owner/repo](URL)`（不用 Obsidian wikilink，避免污染 vault） |
| stars 显示 | `⭐ 12k` / `⭐ 1.5k` / `⭐ 234`（1000+ 自动 k 化）|
| language | 直接显示 `Python` / `TypeScript`；null 时省略 ` · <language>` 段 |
| topic_tags | inline code 显示，超过 5 个截断 + `…` |
| mentioners | `@h1 · @h2 · @h3`；超过 5 个截断 + `· …` |
| first_mention_at | 短格式 `YYYY-MM-DDTHH:MM`（去秒和时区） |
| score | 保留 2 位小数 `score 0.87`（用户参考用，不强卡）|
| 做什么 / 适用场景 | 加粗 label + 一段文本，段间空一行 |
| repo 之间 | `---` 分隔（视觉断开）|
| fallback_layer | `≠ layer1` 时 TL;DR 第一句必须显式说明，例如"今日走 Layer 2（GitHub Trending 兜底）" |

## frontmatter 字段说明

| 字段 | 来源 | 用途 |
|------|------|------|
| `date` | briefing.date | Obsidian dataview 按日期查询 |
| `type` | 固定 `github-briefing` | 区分不同链路简报 |
| `source` | 固定 `github-curator` | 区分内部 sub-source |
| `fallback_layer` | briefing.fallback_layer_used | dataview 统计各 layer 使用频次 |
| `candidate_pool_size` | briefing.candidate_pool_size | 历史观察池规模变化 |
| `active_curators_today` | briefing.active_curators_today 长度 | 历史观察活跃度 |
| `generated_at` | briefing.generated_at | 审计/追溯 |
| `tags` | 固定 `[github, ai-trends, daily]` | Obsidian 标签体系 |

## 渲染示例（完整一条）

````markdown
---
date: 2026-05-28
type: github-briefing
source: github-curator
fallback_layer: layer1
candidate_pool_size: 100
active_curators_today: 23
generated_at: 2026-05-28T14:03:21+08:00
tags:
  - github
  - ai-trends
  - daily
---

# GitHub 项目日报 · 2026-05-28

> 今日活跃博主 23/100，走 Layer 1。Top 10 涵盖：AI agent 框架 3、UI 组件 2、building tools 5。

## 1. [microsoft/qlib](https://github.com/microsoft/qlib) ⭐ 15k · Python

`quant` `ml` `finance` · **3 mentions**(@simonw · @karpathy · @GitTrend0x)
score 0.87 · 推荐于 2026-05-28T13:22

**做什么**: Qlib 是微软开源的量化金融研究平台，覆盖从原始数据采集、特征工程、模型训练到回测评估的完整链路。内置 ML baseline、benchmark dataset 和 alpha 因子库，支持自定义模型插件。

**适用场景**: 你需要在 Python 项目里搭建量化研究 pipeline，希望从数据到信号评估一条链路打通；或公司内做 alpha 因子研究需要可复现 baseline。**不适用于**纯加密货币高频交易（项目侧重传统金融时序）。

---

## 2. [owner/repo-2](https://github.com/owner/repo-2) ⭐ 5.2k · TypeScript

...
````

## fallback layer 提示词

| Layer | TL;DR 第一句模板 |
|-------|------------------|
| layer1 | "今日活跃博主 <N>/<pool_size>，走 Layer 1。Top 10 涵盖：..." |
| layer2 | "今日 Layer 1 X curator 抓取失败 / 候选不足，走 Layer 2（GitHub Trending HTML 兜底），mentioner 统一为 @github_trending，质量需人工 review。" |
| layer3 | "今日 Layer 1/2 均无法产出，使用 Layer 3（人工 JSON 注入）兜底，来源由用户在 `data/manual/<date>.json` 自填。" |

## 未来调优空间（v2）

- 加 Obsidian Dataview 查询块（"过去 7 天 mention_count > 5 的 repo"）
- 按 topic 分组（"AI agent 类" / "UI 组件类" / ...）
- 每周末追加一份 "本周精选 top 5"
