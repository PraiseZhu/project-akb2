"""Layer 1: twitter-cli 跑 curator pool → 提取 GitHub URLs → 归一化 → 去重."""

import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ..url_normalizer import normalize
from . import Candidate, CollectorResult

BASE_SLEEP_BETWEEN_HANDLES = 5
BACKOFF_429 = [60, 120, 300]
MAX_RETRIES_PER_HANDLE = 3
FALLBACK_FAILURE_RATE_THRESHOLD = 0.5
DAILY_RUN_BUDGET_MIN = 12
HEALTH_CHECK_CMD = ["twitter", "status"]

_GITHUB_URL_PATTERN = re.compile(r"https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+", re.IGNORECASE)
_POOL_DB_PATH = Path.home() / ".local" / "share" / "akb2" / "github_dedup.db"


class TwitterCollector:
    def __init__(self, twitter_bin: str = "twitter", db_path: Path | None = None):
        self._twitter_bin = twitter_bin
        self._db_path = db_path or _POOL_DB_PATH

    def collect(self, pool_yaml_path: Path) -> CollectorResult:
        t0 = time.monotonic()
        pool = self._load_pool(pool_yaml_path)
        handles = [h["handle"] for h in pool]

        # Health check
        if not self._health_check():
            return CollectorResult("layer1", [], handles, time.monotonic() - t0, False,
                                   "twitter-cli health check failed (not authenticated)")

        candidates: list[Candidate] = []
        failed_handles: list[str] = []

        # 去重策略：仅 per-handle 本地去重（同一博主同 repo 多条推文只算 1 次提及），
        # 跨博主同 repo 必须保留 —— 否则 mention_count 永远 = 1，"几个博主提了"
        # 这个核心热度信号会被抹掉（真实链路验收发现）。跨天 novelty 去重移到
        # pipeline 聚合后统一做（见 pipeline._aggregate 后的 DedupStore 过滤）。
        for handle in handles:
            tweets = self._fetch_user_posts(handle)
            if tweets is None:
                failed_handles.append(handle)
                continue
            seen_in_handle: set[str] = set()
            for tweet in tweets:
                if tweet.get("isRetweet", False):
                    continue
                text = tweet.get("full_text") or tweet.get("text") or ""
                # GitHub 链接主要在展开的 urls 字段（list[str]），text 多为 t.co 短链；
                # 两处都提，normalize() 会拒非 github 并剥尾巴。
                raw_urls = [u for u in (tweet.get("urls") or []) if isinstance(u, str)]
                raw_urls += _GITHUB_URL_PATTERN.findall(text)
                mentioned_at = (
                    tweet.get("createdAtISO") or tweet.get("createdAt")
                    or tweet.get("created_at") or datetime.now(UTC).isoformat()
                )
                for url in raw_urls:
                    normalized = normalize(url)
                    if normalized is None:
                        continue
                    if normalized in seen_in_handle:
                        continue
                    seen_in_handle.add(normalized)
                    candidates.append(Candidate(
                        repo_url=normalized,
                        mentioner=handle,
                        tweet_id=str(tweet.get("id", "")),
                        mentioned_at=mentioned_at,
                        tweet_text=text[:500],
                        metrics=tweet.get("metrics"),  # 推文热度信号（engagement 打分维）
                    ))
            time.sleep(BASE_SLEEP_BETWEEN_HANDLES)

        duration = time.monotonic() - t0
        fail_rate = len(failed_handles) / len(handles) if handles else 1.0
        # Fail if failure rate exceeds threshold OR no candidates
        ok = fail_rate < FALLBACK_FAILURE_RATE_THRESHOLD and len(candidates) >= 5
        error = None if ok else f"fail_rate={fail_rate:.2f}, candidates={len(candidates)}"

        return CollectorResult("layer1", candidates, failed_handles, duration, ok, error)

    def _load_pool(self, path: Path) -> list[dict]:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data.get("pool", [])

    def _health_check(self) -> bool:
        try:
            result = subprocess.run(
                [self._twitter_bin] + HEALTH_CHECK_CMD[1:],
                capture_output=True, text=True, timeout=30,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _fetch_user_posts(self, handle: str) -> list[dict] | None:
        """Fetch recent tweets for a handle. Returns None on failure."""
        clean_handle = handle.lstrip("@")
        for attempt in range(MAX_RETRIES_PER_HANDLE):
            try:
                # twitter-cli user-posts 的选项是 -n/--max（不是 --limit）。
                # 历史 bug：硬编码 --limit 50 → CLI 报 "No such option" → 每个 handle
                # 瞬间失败 → fail_rate=1.0（SC-2 全量真跑暴露）。
                result = subprocess.run(
                    [self._twitter_bin, "user-posts", clean_handle, "--max", "50", "--json"],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0:
                    import json
                    try:
                        parsed = json.loads(result.stdout)
                    except json.JSONDecodeError:
                        return []
                    # twitter-cli 返回 {ok, schema_version, data:[...]}；tweet 列表在 data 键。
                    # 历史 bug：直接返回顶层 dict → collect() 迭代得到 key 字符串 → tweet.get 崩。
                    if isinstance(parsed, dict):
                        return parsed.get("data", [])
                    return parsed
                # 429 rate limit → backoff
                if "429" in result.stderr or "rate limit" in result.stderr.lower():
                    if attempt < len(BACKOFF_429):
                        time.sleep(BACKOFF_429[attempt])
                        continue
                return None
            except Exception:
                if attempt < MAX_RETRIES_PER_HANDLE - 1:
                    time.sleep(BASE_SLEEP_BETWEEN_HANDLES)
                else:
                    return None
        return None
