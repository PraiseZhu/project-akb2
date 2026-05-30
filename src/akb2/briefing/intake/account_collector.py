"""Curator account collector — 逐 AI 策展账号抓取（替代泛关键词搜索）。

CLI: python -m akb2.briefing.intake.account_collector [--output <path>]
     [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--tier en|zh] [--dry-run]

真实抓取链路（替代 l0_collector 的泛关键词搜索，解决内容噪音）：
  对 config/curator-accounts.yaml 的 fetch_tier.<tier> 每个账号执行：
    twitter search "" --from <h> -t latest --exclude retweets --exclude replies
                  --since <s> --until <u> -n <cap> --json
  聚合所有账号结果 + 按 id 去重。

  - 单个账号失败/0 条 → 跳过（warn），不影响其他账号
  - 全部账号 0 条 → 降级 fixture，标 _fallback_used
  - 账号间 pacing（默认 3s）规避限流（实测 ~34 账号触 429，en 层 19 个安全）

v1 默认 tier=en（19 账号）。pipeline 是「英文合成→翻译中文」链路，仅干净支持英文输入；
zh 层需 per-tweet lang 处理（en 翻译/zh 跳翻），属后续工作。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

from .base import REPO_ROOT
from .l0_collector import (
    FIXTURE_PATH,
    TWITTER_BIN,
    TWITTER_TIMEOUT_SEC,
    _twitter_cli_to_akb2,
)

CURATOR_CONFIG = REPO_ROOT / "config" / "curator-accounts.yaml"

_DEFAULT_TIER = "en"
_DEFAULT_CAP = 10           # 每账号最多取最新 N 条
_DEFAULT_PACING_SEC = 3.0   # 账号间停顿，规避限流


def _load_fetch_tier(tier: str = _DEFAULT_TIER) -> list[str]:
    """从 curator-accounts.yaml 读取 fetch_tier.<tier> 账号列表（去掉行内注释/空白）。"""
    try:
        with open(CURATOR_CONFIG) as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        sys.stderr.write(f"WARNING: curator config not found at {CURATOR_CONFIG}\n")
        return []
    raw = ((cfg.get("fetch_tier") or {}).get(tier)) or []
    # yaml 已剥注释；再防御性 strip + 去空
    return [str(h).strip() for h in raw if str(h).strip()]


class AccountCollector:
    source_name = "curator_accounts"

    def __init__(
        self,
        tier: str = _DEFAULT_TIER,
        per_account_cap: int = _DEFAULT_CAP,
        pacing_sec: float = _DEFAULT_PACING_SEC,
    ):
        self.tier = tier
        self.per_account_cap = per_account_cap
        self.pacing_sec = pacing_sec
        self.used_fallback: bool = False

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

    def _fetch_one(
        self,
        handle: str,
        since_date: str | None,
        until_date: str | None,
    ) -> list[dict]:
        """抓单个账号；失败/0 条返回 []（不抛、不降级，由调用方聚合处理）。"""
        cmd = [
            TWITTER_BIN, "search", "",
            "--from", handle,
            "-t", "latest",
            "--exclude", "retweets",
            "--exclude", "replies",
            "-n", str(self.per_account_cap),
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
            sys.stderr.write(f"WARNING: @{handle} timeout ({TWITTER_TIMEOUT_SEC}s) — skip\n")
            return []

        if result.returncode != 0:
            excerpt = (result.stderr or "")[:200].replace("\n", " | ")
            sys.stderr.write(f"WARNING: @{handle} twitter-cli exit {result.returncode}: {excerpt} — skip\n")
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            sys.stderr.write(f"WARNING: @{handle} output not JSON — skip\n")
            return []
        if not payload.get("ok"):
            sys.stderr.write(f"WARNING: @{handle} payload.ok=false — skip\n")
            return []
        return _twitter_cli_to_akb2(payload.get("data") or [])

    def _fetch_real(
        self,
        since_date: str | None = None,
        until_date: str | None = None,
    ) -> list[dict]:
        if not Path(TWITTER_BIN).exists():
            sys.stderr.write(
                f"WARNING: twitter-cli not found at {TWITTER_BIN} — fallback to fixture\n"
            )
            self.used_fallback = True
            return self._read_fixture()

        handles = _load_fetch_tier(self.tier)
        if not handles:
            sys.stderr.write(
                f"WARNING: fetch_tier.{self.tier} empty in curator config — fallback to fixture\n"
            )
            self.used_fallback = True
            return self._read_fixture()

        seen: set[str] = set()
        aggregated: list[dict] = []
        ok_accounts = 0
        for i, handle in enumerate(handles):
            if i > 0 and self.pacing_sec > 0:
                time.sleep(self.pacing_sec)  # 限流防护
            tweets = self._fetch_one(handle, since_date, until_date)
            if tweets:
                ok_accounts += 1
            for t in tweets:
                tid = str(t.get("id", ""))
                if tid and tid not in seen:
                    seen.add(tid)
                    aggregated.append(t)

        if not aggregated:
            sys.stderr.write(
                f"WARNING: curator fetch_tier.{self.tier} returned 0 tweets across "
                f"{len(handles)} accounts — fallback to fixture\n"
            )
            self.used_fallback = True
            return self._read_fixture()

        sys.stderr.write(
            f"INFO [account_collector]: tier={self.tier} {ok_accounts}/{len(handles)} accounts "
            f"yielded {len(aggregated)} unique tweets\n"
        )
        return aggregated


def main():
    parser = argparse.ArgumentParser(description="Curator account collector (per-account twitter fetch)")
    parser.add_argument("--output", default="/tmp/account-out.json", help="Output JSON path")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Use fixture instead of twitter-cli")
    parser.add_argument("--since", default=None, help="Collect tweets since YYYY-MM-DD")
    parser.add_argument("--until", default=None, help="Collect tweets until YYYY-MM-DD")
    parser.add_argument("--tier", default=os.environ.get("AKB2_FETCH_TIER", _DEFAULT_TIER), help="curator fetch_tier (en|zh)")
    args = parser.parse_args()

    cap = int(os.environ.get("AKB2_ACCOUNT_CAP", _DEFAULT_CAP))
    pacing = float(os.environ.get("AKB2_ACCOUNT_PACING_SEC", _DEFAULT_PACING_SEC))

    collector = AccountCollector(tier=args.tier, per_account_cap=cap, pacing_sec=pacing)
    tweets = collector.collect(
        dry_run=args.dry_run,
        since_date=args.since,
        until_date=args.until,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output: dict = {"tweets": tweets}
    if collector.used_fallback:
        output["_fallback_used"] = True
    with open(out_path, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
