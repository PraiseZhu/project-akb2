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

from .base import BaseCollector, REPO_ROOT, load_config

FIXTURE_PATH = REPO_ROOT / "tests/fixtures/intake/dry-run-2026-05-27/sample.json"

TWITTER_BIN = shutil.which("twitter") or str(Path.home() / ".local" / "bin" / "twitter")
TWITTER_TIMEOUT_SEC = 60


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

    def collect(self, dry_run: bool = False) -> list[dict]:
        if dry_run:
            self.provenance = "fixture_dryrun"
            return self._read_fixture()
        return self._fetch_real()

    def _read_fixture(self) -> list[dict]:
        with open(FIXTURE_PATH) as f:
            data = json.load(f)
        return data.get("tweets", [])

    def _fallback_to_fixture(self, reason: str) -> list[dict]:
        """抓取失败时降级到 fixture，标记 provenance 并大声警告。"""
        self.provenance = "fixture_fallback"
        sys.stderr.write(f"WARNING: {reason} — fallback to fixture\n")
        return self._read_fixture()

    def _fetch_real(self) -> list[dict]:
        """通过 twitter-cli 拉真实推文;失败降级到 fixture。"""
        cfg = (self.config or load_config()).get("sources", {}).get("l0", {})
        query = cfg.get("query", "AI OR LLM OR AGI")
        # 拆掉配置 query 里 inline 的 lang/exclude 标志,统一通过 flag 传
        query_clean = (
            query.replace("lang:en", "").replace("-is:retweet", "").strip()
        )
        max_results = int(cfg.get("max_results", 20))

        if not Path(TWITTER_BIN).exists():
            return self._fallback_to_fixture(f"twitter-cli not found at {TWITTER_BIN}")

        cmd = [
            TWITTER_BIN, "search", query_clean,
            "--lang", "en",
            "--exclude", "retweets",
            "-n", str(max_results),
            "--json",
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=TWITTER_TIMEOUT_SEC
            )
        except subprocess.TimeoutExpired:
            return self._fallback_to_fixture(f"twitter-cli timeout ({TWITTER_TIMEOUT_SEC}s)")

        if result.returncode != 0:
            stderr_excerpt = (result.stderr or "")[:300].replace("\n", " | ")
            return self._fallback_to_fixture(
                f"twitter-cli exit {result.returncode}: {stderr_excerpt}"
            )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            return self._fallback_to_fixture(f"twitter-cli output not JSON ({e})")

        if not payload.get("ok"):
            return self._fallback_to_fixture("twitter-cli payload.ok=false")

        raw = payload.get("data") or []
        tweets = _twitter_cli_to_akb2(raw)
        if not tweets:
            return self._fallback_to_fixture("twitter-cli returned 0 tweets")
        return tweets


def main():
    parser = argparse.ArgumentParser(description="L0 public timeline collector")
    parser.add_argument("--output", default="/tmp/l0-out.json", help="Output JSON path")
    parser.add_argument(
        "--dry-run", action="store_true", default=False, help="Use fixture data instead of twitter-cli"
    )
    args = parser.parse_args()
    collector = L0Collector()
    collector.run_cli(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
