# GitHub 链路部署问题清单

> 2026-06-05 · 首次部署到 praise-stack Mac

## 已修复

| # | 问题 | 修复 |
|---|------|------|
| 1 | env 文件 `VAULT_ROOT` 含空格未加引号 → source 报错 | 加双引号 |
| 2 | env 变量未 `export` → 子进程拿不到 | 加 `export` |
| 3 | plist 缺 PATH/PYTHONPATH/WorkingDirectory → `python: command not found` | 对齐 social briefing plist 补上 |
| 4 | plist PATH 漏了 `~/.local/bin` → twitter CLI 找不到，Layer1 静默失败 | 补 `.local/bin` |
| 5 | bootstrap 脚本从 `/tmp` 加载 plist 报 I/O error | 改为 `~/Library/LaunchAgents/` |

## 待处理（按优先级）

| 优先级 | # | 问题 | 现在哪里在痛 | 影响 |
|--------|---|------|-------------|------|
| **P0** | 8 | Layer1 Twitter 产出不稳定：61 handles 跑完只产出 8 个 candidates | 今日 09:29 UTC：L1 → 8 candidates → skip → 退到 L2 兜底 | 当日简报可能不发 |
| **P1** | 6 | Layer2 TrendingHTMLCollector 硬要求 ≥10 个 repo | GitHub Trending 偶尔只返回 4 个 → L2 也失败 → 全链路 skip | 降级路线不可靠 |
| **P2** | 7 | 本机没配 GitHub PAT（用户手动补了） | env.example 没有 PAT 字段 | 首次部署体验差 |

---

## 深层根因分析

### P0 — Layer1 产出不稳定（5 层根因）

**实测数据**：

| 测试 | 结果 |
|------|------|
| 9 handle spot check（无 pacing） | 94 raw URLs，avg 10.4/handle，成功 9/9 |
| 5 handle paced test（5s pacing） | 2/5 被 429 rate limit 拒绝（40% 失败率） |
| 3 handle 去重测试 | 75 raw → 52 unique repos（per-handle 去重损失 31%） |
| 今日 09:29 UTC 全量跑 | 61 handles → 8 candidates（聚合 + novelty 后） |

**根因层级**（从症状到根源）：

```
L1: 症状    → 61 handles → 8 candidates → pipeline skip
L2: 直接    → L1 collector 产出低 + pipeline gate 太严 (≥10)
L3: 机制    → ① Twitter API 429 限流导致 ~40% handles 失败
              ② Pool 信号密度呈幂律分布：少数博主贡献大部分 GitHub URL
              ③ 3 天 recency 窗口对低频博主过于激进
              ④ Per-handle 去重额外损失 ~31%
L4: 架构    → ① Fallback judge (≥5) 与 pipeline gate (≥10) 阈值不对齐
                 → L1 产出 5-9 个时 judge 认为 OK，不触发 L2，pipeline 却 skip
                 → 这是「死区」：L1 "可用"但不够用，L2 从未被尝试
              ② 429 重试双层叠加：CLI 内置重试 (~38s) + collector 外部重试 (60s/120s/300s)
                 → 单 handle 最坏耗时 ~8.7 分钟
              ③ 三层架构无「合并」模式：L1 8 个 + L2 6 个 = 14 个，但各层独立评估
L5: 设计假设 → 假设 pool 中大部分 handles 是高频 GitHub repo 推荐者
              实际 pool 中大部分是通用 AI/tech 博主，GitHub 推荐是副业
              61 个 handles 的有效 GitHub 信号 ≈ 10-15 个专门推荐 GitHub 的博主
```

**Pool 信号密度实测**（9 handle 样本，3 天内推文）：

| Handle | 原创推文 | GitHub URLs | 信号密度 |
|--------|---------|-------------|---------|
| @simonw | 46 | 26 | 🔴 高 |
| @lencx_ | 50 | 22 | 🔴 高 |
| @cassidoo | 28 | 25 | 🔴 高 |
| @swyx | 33 | 15 | 🟡 中 |
| @rasbt | 46 | 24 | 🔴 高 |
| @addyosmani | 29 | 10 | 🟡 中 |
| @dotey | 38 | 7 | 🟡 中 |
| @svpino | 44 | 7 | 🟡 中 |
| @omarsar0 | 37 | 7 | 🟡 中 |
| @matthewberman | 48 | 4 | 🟢 低 |
| @jerryjliu0 | 22 | 3 | 🟢 低 |
| @hwchase17 | 8 | 3 | 🟢 低 |

> 结论：Pool 中约 40% 的 handles 是低信号（≤4 GitHub URLs / 50 推文），高信号 handles 不足 10 个。

**429 速率限制实测**（5 handle paced test，5s 间隔）：

| Handle | 耗时 | 结果 |
|--------|------|------|
| @karpathy | 40.4s | ❌ 429 × 3（CLI 内置重试全部耗尽） |
| @rauchg | 41.2s | ❌ 429 × 3 |
| @rasbt | 47.5s | ✅ （有重试后成功） |
| @addyosmani | 9.1s | ✅ |
| @cassidoo | 10.0s | ✅ |

> CLI 的重试策略：5.9s → 11.6s → 20.4s（~38s 后放弃）。Collector 检测到 stderr 中的 "429" 后又做自己的 60s → 120s → 300s 重试，**双层重试叠加**使单 handle 最坏耗时达 8.7 分钟。

### P1 — Layer2 硬阈值（3 层根因）

```
L1: 症状    → 阈值 ≥10 偶尔导致 L2 失败
L2: 直接    → GitHub Trending 页面解析不稳定（CSS 选择器脆弱）
L3: 架构    → ① 阈值硬编码在 3 个地方无数据驱动
                 - TrendingHTMLCollector.collect(): >= 10
                 - fallback_judge.decide(): MIN_CANDIDATES_LAYER2 = 10
                 - pipeline.run_pipeline(): len(aggregated) < 10
              ② 阈值与 P0 的架构死区共振：L1 产出 5-9 时 L2 不触发，L2 自己 < 10 时
                 L3 也不触发 → 任何一层 < 10 都导致全链路 skip
```

### P2 — GitHub PAT 未预配（1 层根因）

```
L1: 症状    → 新机器部署需手动填 PAT
L2: 直接    → env.example 缺少 GITHUB_PAT 字段，bootstrap 脚本无检查
```

---

## 修复建议（按投入产出比排序）

| 建议 | 现在哪里在痛 | 价值 | 改动量 |
|------|-------------|------|--------|
| 1. 修复 fallback judge 死区：L1 产出 5-9 时**也尝试 L2**，合并结果 | 今日 8 个全丢，L2 有 14 个但没被用 | **高** — 立即消除最大单点故障 | 小（~20 行） |
| 2. 降低 L1 recency 窗口：3 天 → 7 天 | 低频博主（每周 1-2 个 repo）在 3 天窗口内产出接近 0 | **高** — 提升 L1 有效产出 2-3× | 极小（1 行） |
| 3. 移除 collector 的 429 重试（CLI 已内置重试） | 双层重试使单 handle 失败耗时 8.7 分钟 | **中** — 减少管线运行时间 | 小（~10 行） |
| 4. 降低 trending 阈值：10 → 5 | L2 偶尔只返回 4-6 个，丢掉可惜 | **中** — 让 L2 兜底更可靠 | 极小（3 行） |
| 5. env.example 加 GITHUB_PAT + bootstrap 检查 | 新机器部署 enrich 全失败 | **低** — 一次性的部署体验 | 极小（2 行） |
| 6. 清理低信号 handles（GitHub URLs ≤ 2 / 50 推文） | Pool 质量参差不齐，低信号 handle 浪费 API 配额 | **中** — 长期提升信号密度 | 中（需人工 review） |

## 架构备注

- 三层 fallback：L1 Twitter 博主池 → L2 GitHub Trending → L3 手动 JSON
- L1 跑一次 ~8-10 分钟（61 handle × 5s pacing，不含 429 重试）
- 当前分支 `feature/automation-pipeline-v2`，已合并 `plan/akb2-gd-github-curator`
- launchd 每天 5:30 触发，与社媒链路（7:30）错峰