# twitter-cli ClientTransaction 本地补丁 + 采集端点切换（2026-07-28）

## 事故

X 前端改版后，社媒简报采集全瘫（07-27 / 07-28 连续两天 `intake blocked, exit=3`）。
两层叠加故障：

1. **ClientTransaction 初始化失败**：`x_client_transaction` 从**未登录**的
   `https://x.com` 落地页推导 ondemand bundle，X 已把该标记从落地页移除
   （实测：未登录 34KB / `ondemand` 0 次；带 cookie 的 `/home` 276KB / 240 次）。
   → 每次调用打 `Failed to init ClientTransaction: 'NoneType' object has no
   attribute 'group'`，GraphQL 全部 404。
2. **search 端点 queryId 失效**：修好 CT 后，`twitter search` 仍恒 404
   （X 轮换了 SearchTimeline 的 persisted queryId），而 `user-posts` /
   `user` / `status` 等端点正常。

登录态全程正常（`twitter status` → `authenticated: true`），与 auth 无关。
`twitter-cli 0.8.5` 与 `xclienttransaction 1.0.3` 均为 PyPI 最新，上游无修复版。

## 修复 1：本地补丁（工作区外，会被 upgrade 覆盖）

文件：`~/.local/share/uv/tools/twitter-cli/lib/python3.12/site-packages/twitter_cli/client.py`
位置：`_ensure_client_transaction` 内（原 `:1073` 附近），改动：

- 抓取页从 `https://x.com` → `https://x.com/home`
- 请求头补 `Cookie`（复用该文件 GraphQL 请求同款组装：`self._cookie_string`
  或 `auth_token=…; ct0=…`）

原件备份：同目录 `client.py.bak-pre-patch-20260728`。
补丁内有 `LOCAL PATCH 2026-07-28 (AKB2)` 注释块可 grep 定位。

**失效条件**：`uv tool upgrade twitter-cli`（或 reinstall）会覆盖补丁。
升级后若简报再次全账号失败并出现上述签名，按本文重打补丁，或确认新版已自带修复。
仓库侧已有 fail-fast 护栏（`intake/l0_collector.py` 的
`UPSTREAM_FATAL_SIGNATURES`），补丁失效时会连续 3 账号命中即中止并显式报错，
不会再烧 13 分钟、也不会降级 fixture 假数据。

## 修复 2：采集端点切换（仓库内，随 repo 走）

`src/akb2/briefing/intake/account_collector.py` 的 `_fetch_one`：
`search "" --from <h>` → `user-posts <h>`。

user-posts 无 `--exclude` / `--since` / `--until`，三类过滤移到 raw 层
`_post_filter`（RT 用 `isRetweet`、回复用 text 前缀、日期窗用 `createdAtISO`；
缺时间戳条目剔除）。抓取量放大 cap×2 补偿过滤损耗、产出仍截断到 cap。
测试：`tests/briefing/test_account_collector_userposts.py`。

注意：`l0_collector`（keyword 回退模式）仍用 search 端点，**当前同样是坏的**；
它不在生产路径（默认 account 模式），未随本次修复。若要启用 keyword 模式需先做
同样的端点评估。

## 验证方式

```bash
twitter status                                   # 登录态（应 authenticated: true）
twitter user-posts karpathy -n 2 --json          # 采集能力（应 ok: true 且有 data）
twitter search "" --from karpathy -n 1 --json    # search 端点（截至 2026-07-28 仍 404，属预期）
```
