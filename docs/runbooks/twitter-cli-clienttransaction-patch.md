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

仓库侧护栏（两层，均在 `intake/`）：

| 层 | 触发 | 代价 |
|----|------|------|
| preflight 探针（`account_collector.PREFLIGHT_PROBE_ACCOUNTS = 1`） | **第 1 个账号**就命中 `UPSTREAM_FATAL_SIGNATURES` → 立即 `UpstreamCollectorBroken` | 1 次账号调用（该调用同时是第一个正式账号的抓取，正常路径不多付） |
| 连击护栏（`UPSTREAM_FATAL_FAILFAST_STREAK = 3`） | 探针放行后**跑到中途**才坏 → 连续 3 次命中同一签名即中止 | ≤ 3 × 60s |

两层都**不降级 fixture**（`used_fallback` 保持 False），异常里直接带成因判定。
对比事故当天：47 账号 × ~17s ≈ 793s 后仍 0 条并降级 fixture 假数据。

## 修复 2：采集端点切换（仓库内，随 repo 走）

`src/akb2/briefing/intake/account_collector.py` 的 `_fetch_one`：
`search "" --from <h>` → `user-posts <h>`。

user-posts 无 `--exclude` / `--since` / `--until`，三类过滤移到 raw 层
`_post_filter`（RT 用 `isRetweet`、回复用 text 前缀、日期窗用 `createdAtISO`；
缺时间戳条目剔除）。抓取量放大 cap×2 补偿过滤损耗、产出仍截断到 cap。
测试：`tests/briefing/test_account_collector_userposts.py`。

## 修复 3：keyword 回退模式 fail-closed（2026-07-28 补）

`l0_collector`（`AKB2_INTAKE_COLLECTOR=keyword`）仍用 search 端点 —— keyword 是
**跨账号关键词搜索**，`user-posts`（单账号时间线）没有等价能力，换端点这条路不存在。
所以这里走的是 fail-closed：真实抓取的每个失败分支（binary 缺失 / 超时 / exit≠0 /
非 JSON / `ok=false` / 0 条）一律抛 `KeywordSearchEndpointUnavailable`，CLI 退出码 4、
**不写 output 文件**，pipeline 见非零即中止。

原实现这些分支全是 `used_fallback = True; _read_fixture()`：一旦有人打开 keyword
模式，抓取失败会静默变成 12 条 fixture 假推文冒充真实抓取（正是 2026-06/07 连续
12 天假日报的成因）。fixture 现在只保留给显式 `--dry-run`。

命中 `UPSTREAM_FATAL_SIGNATURES` 时，报错会额外带上签名与成因判定（与 account
模式共用 `diagnose_upstream_fatal`）。

「先探测可用性再决定跑不跑」在这里等于那唯一一次 search 调用本身
（`KEYWORD_SEARCH_EXTRA_PROBE_CALLS = 0`）：keyword 整轮只有一次调用，单独再发一次
`-n 1` 探测拿不到额外信息，只会让端点多挨一次。

## 验证方式

```bash
twitter status                                   # 登录态（应 authenticated: true）
twitter user-posts karpathy -n 2 --json          # 生产端点采集能力（应 ok: true 且有 data）
twitter search "" --from karpathy -n 1 --json    # keyword 回退模式用的 search 端点
```

⚠ **2026-07-28 16:29 复测更正**：上面三条**全部返回 `ok: true`**，包括 search
（`search "AI" -n 1`、`search "" --from karpathy -n 1`、`search "AI OR LLM OR AGI"
-t latest --lang en --exclude retweets -n 3` 三种形态都通）。也就是本文原先
「search 端点恒 404，属预期」的结论**已不成立**（是 X 侧又转回来、还是当天的 404
本就是 ClientTransaction 故障的下游表现，未定）。保留 `user-posts` 作为 account
模式的端点不是因为 search 坏，而是它才是"按账号取时间线"的对应端点。

## 自检：怎么知道补丁还在 / 采集还活着

人手敲或塞 cron 都可以，全部只读、不触仓库状态：

```bash
# 1. 补丁在位性（判据是补丁注释块 marker，不是"文件存在"——upgrade 后文件仍在)
grep -l "LOCAL PATCH 2026-07-28 (AKB2)" \
  ~/.local/share/uv/tools/twitter-cli/lib/*/site-packages/twitter_cli/client.py \
  || echo "补丁已丢失 —— 按上面「修复 1」重打"

# 2. 登录态
twitter status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["authenticated"])'

# 3. 生产端点采集能力（account 模式实际用的那条）
twitter user-posts karpathy -n 2 --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["ok"], len(d.get("data") or []))'

# 4. 仓库侧同一判据（1+2 与报错文案共用同一实现，避免自检与线上结论打架）
cd "<repo>" && python3 -c "
from akb2.briefing.intake.l0_collector import _patch_state, _login_state, diagnose_upstream_fatal
print(_patch_state()); print(_login_state())
print(diagnose_upstream_fatal('(自检，非真实故障)'))" 2>/dev/null \
  || PYTHONPATH=src python3 -c "
from akb2.briefing.intake.l0_collector import diagnose_upstream_fatal
print(diagnose_upstream_fatal('(自检，非真实故障)'))"
```

### 报错文案 → 成因 → 下一步

采集中止时 stderr 会打一段 `成因判定: …`。三种成因与判据、动作的对应：

| 报错里的成因 | 判据（自动探测） | 下一步 |
|-------------|----------------|--------|
| `登录态失效` | `twitter status --json` 的 `data.authenticated` 为 false | `twitter login` 重新登录后重跑 |
| `本地补丁缺失（很可能被 uv tool upgrade 冲掉）` | 登录正常 + client.py 里找不到 `LOCAL PATCH 2026-07-28 (AKB2)` marker | 按「修复 1」重打补丁 |
| `补丁在位但上游又变了` | 登录正常 + marker 在位，但探针仍命中全局签名 | 跑上面「验证方式」三条命令，定位新的失效层（CT? 端点? queryId?） |
| `成因未能自动判定` | 登录态或安装路径探测失败（如 twitter-cli 不在 PATH、安装目录变了） | 先手跑「自检」1–3 条 |

判据分工：**是否中止**只看"探针调用返回了全局故障签名"这一可观测行为；补丁在位性
与登录态只用来给上面这张表选行（工作区外文件的只读探测失败一律回报
`unknown`，不改变中止判定）。
