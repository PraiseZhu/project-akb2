"""L0 公共时间线 collector。

CLI: python -m akb2.briefing.intake.l0_collector [--output <path>] [--dry-run]
     默认走真实抓取（twitter-cli）。--dry-run 强制读 fixture。

真实抓取链路：
  twitter-cli (cookie auth) → twitter_cli_to_akb2() adapter → AKB2 schema
  失败时降级到 fixture，stderr 打 WARNING。
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .base import REPO_ROOT, BaseCollector, load_config

FIXTURE_PATH = REPO_ROOT / "tests/fixtures/intake/dry-run-2026-05-27/sample.json"

TWITTER_BIN = shutil.which("twitter") or str(Path.home() / ".local" / "bin" / "twitter")
TWITTER_TIMEOUT_SEC = 60

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


class UpstreamCollectorBroken(RuntimeError):
    """twitter-cli 侧全局故障 —— 整轮抓取无意义，立即中止且**不降级 fixture**。

    降级 fixture 会把「上游工具坏了」伪装成「今天没素材」，这正是 2026-06/07
    连续 12 天假数据冒充真实抓取的成因。这里选择显式失败（constitution 原则 4）。
    """

    def __init__(self, signature: str, accounts_tried: int, sample: str = "") -> None:
        self.signature = signature
        self.accounts_tried = accounts_tried
        self.sample = sample
        super().__init__(
            f"twitter-cli 全局故障（连续 {accounts_tried} 个账号命中同一签名）: "
            f"{signature}；登录态与账号配置均非成因，需修复/更换上游采集工具"
        )


def match_upstream_fatal(stderr_text: str) -> str | None:
    """命中全局故障签名则返回该签名，否则 None（= 可能只是单账号问题）。"""
    if not stderr_text:
        return None
    for sig in UPSTREAM_FATAL_SIGNATURES:
        if sig in stderr_text:
            return sig
    return None


def _twitter_cli_to_akb2(raw_data: list[dict]) -> list[dict]:
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


class L0Collector(BaseCollector):
    source_name = "l0_public_timeline"

    def __init__(self, config=None):
        super().__init__(config)
        self.used_fallback: bool = False  # set True when fixture was used as fallback

    def collect(
        self,
        dry_run: bool = False,
        since_date: str | None = None,
        until_date: str | None = None,
    ) -> list[dict]:
        if dry_run:
            self.used_fallback = True
            return self._read_fixture()
        return self._fetch_real(since_date=since_date, until_date=until_date)

    def _read_fixture(self) -> list[dict]:
        with open(FIXTURE_PATH) as f:
            data = json.load(f)
        return data.get("tweets", [])

    def _fetch_real(
        self,
        since_date: str | None = None,
        until_date: str | None = None,
    ) -> list[dict]:
        """通过 twitter-cli 拉真实推文;失败降级到 fixture。"""
        cfg = (self.config or load_config()).get("sources", {}).get("l0", {})
        query = cfg.get("query", "AI OR LLM OR AGI")
        # 拆掉配置 query 里 inline 的 lang/exclude 标志,统一通过 flag 传
        query_clean = (
            query.replace("lang:en", "").replace("-is:retweet", "").strip()
        )
        max_results = int(cfg.get("max_results", 20))

        if not Path(TWITTER_BIN).exists():
            sys.stderr.write(
                f"WARNING: twitter-cli not found at {TWITTER_BIN} — fallback to fixture\n"
            )
            self.used_fallback = True
            return self._read_fixture()

        cmd = [
            TWITTER_BIN, "search", query_clean,
            "-t", "latest",           # 按时间倒序，确保近期推文优先
            "--lang", "en",
            "--exclude", "retweets",
            "-n", str(max_results),
            "--json",
        ]
        if since_date:
            cmd += ["--since", since_date]
        if until_date:
            cmd += ["--until", until_date]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=TWITTER_TIMEOUT_SEC
            )
        except subprocess.TimeoutExpired:
            sys.stderr.write(
                f"WARNING: twitter-cli timeout ({TWITTER_TIMEOUT_SEC}s) — fallback to fixture\n"
            )
            self.used_fallback = True
            return self._read_fixture()

        if result.returncode != 0:
            stderr_excerpt = (result.stderr or "")[:300].replace("\n", " | ")
            sys.stderr.write(
                f"WARNING: twitter-cli exit {result.returncode}: {stderr_excerpt} "
                "— fallback to fixture\n"
            )
            self.used_fallback = True
            return self._read_fixture()

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            sys.stderr.write(
                f"WARNING: twitter-cli output not JSON ({e}) — fallback to fixture\n"
            )
            self.used_fallback = True
            return self._read_fixture()

        if not payload.get("ok"):
            sys.stderr.write(
                "WARNING: twitter-cli payload.ok=false — fallback to fixture\n"
            )
            self.used_fallback = True
            return self._read_fixture()

        raw = payload.get("data") or []
        tweets = _twitter_cli_to_akb2(raw)
        if not tweets:
            sys.stderr.write(
                "WARNING: twitter-cli returned 0 tweets — fallback to fixture\n"
            )
            self.used_fallback = True
            return self._read_fixture()
        return tweets


def main():
    parser = argparse.ArgumentParser(description="L0 public timeline collector")
    parser.add_argument("--output", default="/tmp/l0-out.json", help="Output JSON path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Use fixture data instead of twitter-cli",
    )
    parser.add_argument("--since", default=None, help="Collect tweets since YYYY-MM-DD")
    parser.add_argument("--until", default=None, help="Collect tweets until YYYY-MM-DD")
    args = parser.parse_args()
    collector = L0Collector()
    tweets = collector.collect(
        dry_run=args.dry_run,
        since_date=args.since,
        until_date=args.until,
    )
    # 顶层写入 _fallback_used 标记供 pipeline 层跳过日期过滤（SC-7）
    import json as _json
    from pathlib import Path as _Path
    out_path = _Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output = {"tweets": tweets}
    if collector.used_fallback:
        output["_fallback_used"] = True
    with open(out_path, "w") as f:
        _json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
