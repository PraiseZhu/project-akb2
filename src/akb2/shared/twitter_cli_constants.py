"""twitter-cli 上游故障签名 / fail-fast 阈值 / payload 适配器。

briefing（l0 / account collector）与 quality（x_account discovery）共用同一组
具名常量，避免两处各写一份后漂移。本模块不得 import `akb2.briefing`。

`l0_collector` 对这些名字做 re-export，既有 `from ...l0_collector import …`
路径保持等值（同一对象）。
"""

from __future__ import annotations

# 上游 twitter-cli / x_client_transaction 的**全局性**故障签名。
#
# 这类错误与账号无关：它在 twitter-cli 构造 client 时就已发生，对每个账号都会
# 逐字复现。2026-07-28 事故实测：47 个账号全部返回
# "Failed to init ClientTransaction: 'NoneType' object has no attribute 'group'"，
# 逐账号重试烧掉 793s 并把 IP 推进 429（后半段账号叠加 "Rate limited (429)"），
# 最终仍然 0 条 → 降级 fixture。逐账号重试对这类故障零收益，必须整轮 fail-fast
# （constitution 原则 1「有界自动化」：循环要有显式停止条件）。
UPSTREAM_FATAL_SIGNATURES: tuple[str, ...] = (
    # x_client_transaction 无法从 x.com 首页推导 ondemand bundle → GraphQL 返回 404。
    # 登录态本身是好的（twitter status/whoami 仍返回 authenticated: true），
    # 所以这不是 auth 过期，重新登录不解决。
    "Failed to init ClientTransaction",
)

# 连续命中同一全局签名多少个账号后中止整轮。
# 取 3 而非 1：留一次性抖动的余地；代价是 3 × TWITTER_TIMEOUT_SEC 上限，
# 相对 47 账号 × 17s ≈ 793s 的实测浪费仍是两个量级的改善。
UPSTREAM_FATAL_FAILFAST_STREAK = 3

# 「无签名的硬失败」连续多少个账号后中止整轮。
#
# 为什么需要它：签名匹配（上面那条）只覆盖 stderr 里出现已登记字符串的失败。
# 但 twitter-cli 还有两条失败路径**不产生任何签名**：
#   - 超时（`subprocess.TimeoutExpired`，每次赔满 TWITTER_TIMEOUT_SEC=60s）
#   - 输出不是 JSON（工具坏了/被中间层改写，stdout 无法解析）
# 2026-07-28 对抗复核实测：全账号超时的场景下签名护栏与 preflight 都不开火，
# 47 个账号跑满 = **2820s**，且把 IP 一路推进 429。
#
# 与 429 限流的区别（刻意不合并）：限流是**可重试**的瞬时问题，账号间 pacing
# 就是为它设计的，必须继续遍历（回归见 test_upstream_failfast.py 的
# test_transient_per_account_failure_does_not_trigger_failfast）；而连续超时/连续
# 输出非 JSON 说明上游整体无响应，继续遍历零收益。
#
# 取 3 与签名护栏对齐：单个账号偶发超时（网络抖动、某账号内容特别大）是正常的，
# 连续 3 个才判整体。代价上限 3 × 60s = 180s。
UPSTREAM_HARD_FAILURE_FAILFAST_STREAK = 3

# 无签名硬失败的伪签名（进入 UpstreamCollectorBroken 的 signature 位，供运维辨认）。
HARD_FAILURE_TIMEOUT = "连续超时（上游无响应）"
HARD_FAILURE_BAD_OUTPUT = "连续输出非 JSON（上游工具异常）"


def twitter_cli_to_akb2(raw_data: list[dict]) -> list[dict]:
    """twitter-cli 输出的 tweet list → AKB2 schema list."""
    out = []
    for t in raw_data:
        author_obj = t.get("author") or {}
        screen = author_obj.get("screenName", "") if isinstance(author_obj, dict) else ""
        tweet_id = str(t.get("id", ""))
        metrics_in = t.get("metrics") or {}
        out.append({
            "id": tweet_id,
            "text": t.get("text", ""),
            "author": f"@{screen}" if screen else "",
            "created_at": t.get("createdAtISO", ""),
            "url": f"https://x.com/{screen}/status/{tweet_id}" if screen and tweet_id else "",
            "metrics": {
                "likes": int(metrics_in.get("likes", 0) or 0),
                "retweets": int(metrics_in.get("retweets", 0) or 0),
                "replies": int(metrics_in.get("replies", 0) or 0),
            },
        })
    return out


# 既有 briefing 调用点用的私有名；与 twitter_cli_to_akb2 是同一对象。
_twitter_cli_to_akb2 = twitter_cli_to_akb2
