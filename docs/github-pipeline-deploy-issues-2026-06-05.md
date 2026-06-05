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
| **P0** | 8 | Layer1 Twitter 产出不稳定：61 handles 跑完只产出 8 个 candidates（需 ≥10），直接 skip | 今日 09:29 UTC 实测：L1 跑完 → 8 candidates → `too_few_aggregated_repos` skip → 退到 L2 兜底。3 天 recency 窗口 + novelty 去重双重过滤，博主池的有效 GitHub 推荐量不足以稳定填满 10 个 | 当日简报可能不发（L2 也不可靠时全链路 skip） |
| **P1** | 6 | Layer2 TrendingHTMLCollector 硬要求 ≥10 个 repo，GitHub 页面偶尔只返回 4 个 → 整批丢弃 | 降级路线不可靠：L1 失败 + L2 也失败 = 直接 skip，当天无简报 | L2 是 L1 的唯一兜底，不可靠时全链路脆弱 |
| **P2** | 7 | 本机没配 GitHub PAT（用户手动补了） | env.example 没有 PAT 字段，新机器部署时 enrich 全失败 → 简报 skip | 首次部署体验差，需人工介入 |

## 架构备注

- 三层 fallback：L1 Twitter 博主池 → L2 GitHub Trending → L3 手动 JSON
- L1 跑一次 ~8-10 分钟（100+ handle × 5s pacing）
- 当前分支 `feature/automation-pipeline-v2`，已合并 `plan/akb2-gd-github-curator`
- launchd 每天 5:30 触发，与社媒链路（7:30）错峰