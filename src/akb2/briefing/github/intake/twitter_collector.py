"""Layer 1: twitter-cli 跑 curator pool → 提取 GitHub URLs → 归一化 → 去重."""

import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from ....common.x_api_coordination import (
    acquire_priority_lease,
    is_rate_limited,
    release_priority_lease,
)
from ..url_normalizer import normalize
from . import _ZERO_RECENCY_STATS, Candidate, CollectorResult

BASE_SLEEP_BETWEEN_HANDLES = 5
FALLBACK_FAILURE_RATE_THRESHOLD = 0.5
DAILY_RUN_BUDGET_MIN = 12
HEALTH_CHECK_CMD = ["twitter", "status"]

_GITHUB_URL_PATTERN = re.compile(r"https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+", re.IGNORECASE)
_POOL_DB_PATH = Path.home() / ".local" / "share" / "akb2" / "github_dedup.db"

RECENCY_WINDOW_DAYS = 7

# 限流治理（2026-09-23 事故：与 intake-twitter 共用 X 账号，61 个账号全 429）。
# 连续 N 个账号 429 → 等一个 X 限流窗口，并把触发断路的这 N 个账号重新排回队首
# 重试（不白记失败）；等待次数用尽后再连续 N 个 429 → 放弃 L1，交 fallback_judge
# 走 L2。等待次数有上限，L1 总耗时有上界。
# 09-24 实测：L1 自身 61 账号 × 2 页 UserTweets 就超出一个窗口配额（第 18 个账号起
# 429），一次等待不够，fail_rate 以 30/61≈0.49 擦线过闸。
RATE_LIMIT_CONSECUTIVE_BREAKER = 3
RATE_LIMIT_WINDOW_WAIT_SEC = 900
MAX_RATE_LIMIT_WAITS = 3
# 单账号抓取条数。twitter-cli 每页最多 40 条：≤35 只打 1 页 UserTweets，50 要打 2 页。
# 历史上约 70% 的 URL 因超出 7 天窗口被丢弃，40 条已足够覆盖 7 天。
USER_POSTS_MAX = 35
# 优先租约：每个账号前、每次窗口等待前续租，TTL 只需覆盖「一次窗口等待 + 一个慢账号」；
# 持有方崩溃后最多 TTL 内自动失效。
PRIORITY_LEASE_HOLDER = "github_briefing_l1"
PRIORITY_LEASE_TTL_SEC = 30 * 60


def _parse_tweet_time(raw: str | None) -> datetime | None:
    """Parse tweet timestamp, normalizing to UTC-aware datetime.

    Rules:
      - None / empty → None
      - fromisoformat (Z→+00:00 first for RFC 3339 compat)
      - naive datetime → assume UTC
      - any exception → None
    """
    if not raw:
        return None
    if not isinstance(raw, str):
        return None
    try:
        # RFC 3339 "Z" suffix not standard ISO; replace with explicit offset
        s = raw.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


class TwitterCollector:
    def __init__(self, twitter_bin: str = "twitter", db_path: Path | None = None):
        self._twitter_bin = twitter_bin
        self._db_path = db_path or _POOL_DB_PATH
        # 最近一次 _fetch_user_posts 是否因 429 失败（供限流断路器判定）。
        self._last_fetch_rate_limited = False

    def collect(self, pool_yaml_path: Path) -> CollectorResult:
        t0 = time.monotonic()
        pool = self._load_pool(pool_yaml_path)
        handles = [h["handle"] for h in pool]

        # Health check
        if not self._health_check():
            return CollectorResult("layer1", [], handles, time.monotonic() - t0, False,
                                   "twitter-cli health check failed (not authenticated)",
                                   _ZERO_RECENCY_STATS)

        # 让 intake-twitter 在本层采集期间让出 X 限流窗口。
        acquire_priority_lease(PRIORITY_LEASE_HOLDER, PRIORITY_LEASE_TTL_SEC)
        try:
            return self._collect_handles(handles, t0)
        finally:
            release_priority_lease(PRIORITY_LEASE_HOLDER)

    def _collect_handles(self, handles: list[str], t0: float) -> CollectorResult:
        candidates: list[Candidate] = []
        failed_handles: list[str] = []
        rate_limited_handles = 0
        rate_limit_waits = 0
        aborted_reason: str | None = None
        cutoff = datetime.now(UTC) - timedelta(days=RECENCY_WINDOW_DAYS)

        # SC-7 计数器
        candidates_before = 0
        candidates_after = 0
        recency_dropped = 0
        timestamp_missing = 0
        timestamp_parse_failed = 0

        # 去重策略：仅 per-handle 本地去重（同一博主同 repo 多条推文只算 1 次提及），
        # 跨博主同 repo 必须保留 —— 否则 mention_count 永远 = 1，"几个博主提了"
        # 这个核心热度信号会被抹掉（真实链路验收发现）。跨天 novelty 去重移到
        # pipeline 聚合后统一做（见 pipeline._aggregate 后的 DedupStore 过滤）。
        queue = list(handles)
        streak: list[str] = []  # 当前连续 429 的账号（等窗口后重试）
        while queue:
            handle = queue.pop(0)
            acquire_priority_lease(PRIORITY_LEASE_HOLDER, PRIORITY_LEASE_TTL_SEC)  # 续租
            self._last_fetch_rate_limited = False
            tweets = self._fetch_user_posts(handle)
            if tweets is None:
                failed_handles.append(handle)
                if not self._last_fetch_rate_limited:
                    streak = []
                    continue
                rate_limited_handles += 1
                streak.append(handle)
                if len(streak) < RATE_LIMIT_CONSECUTIVE_BREAKER or not queue:
                    # 未到阈值，或已是最后一个账号（再等窗口也没账号可打）。
                    continue
                done = len(handles) - len(queue)
                if rate_limit_waits >= MAX_RATE_LIMIT_WAITS:
                    # 等待次数用尽仍连续 429：继续打只是白耗时间，剩余账号记失败交 L2。
                    failed_handles.extend(queue)
                    aborted_reason = (
                        f"rate_limited: {len(streak)} consecutive 429 "
                        f"after {rate_limit_waits} window wait(s)"
                    )
                    sys.stderr.write(
                        f"WARNING [github-l1]: {aborted_reason}; abort L1 at "
                        f"{done}/{len(handles)} handles\n"
                    )
                    break
                rate_limit_waits += 1
                sys.stderr.write(
                    f"WARNING [github-l1]: {len(streak)} consecutive 429 "
                    f"at {done}/{len(handles)} handles; waiting "
                    f"{RATE_LIMIT_WINDOW_WAIT_SEC}s for X rate-limit window "
                    f"({rate_limit_waits}/{MAX_RATE_LIMIT_WAITS}), then retry them\n"
                )
                acquire_priority_lease(PRIORITY_LEASE_HOLDER, PRIORITY_LEASE_TTL_SEC)
                time.sleep(RATE_LIMIT_WINDOW_WAIT_SEC)
                # 触发断路的账号重新排回队首：它们不是真失败，只是撞在窗口上。
                for h in streak:
                    failed_handles.remove(h)
                queue[:0] = streak
                streak = []
                continue
            streak = []
            seen_in_handle: set[str] = set()
            for tweet in tweets:
                if tweet.get("isRetweet", False):
                    continue
                text = tweet.get("full_text") or tweet.get("text") or ""
                # GitHub 链接主要在展开的 urls 字段（list[str]），text 多为 t.co 短链；
                # 两处都提，normalize() 会拒非 github 并剥尾巴。
                raw_urls = [u for u in (tweet.get("urls") or []) if isinstance(u, str)]
                raw_urls += _GITHUB_URL_PATTERN.findall(text)

                # --- 时间戳提取与 recency 判窗 ---
                raw_ts = (
                    tweet.get("createdAtISO") or tweet.get("createdAt")
                    or tweet.get("created_at")
                )
                parsed_time = _parse_tweet_time(raw_ts)
                if raw_ts is None:
                    timestamp_missing += 1
                elif parsed_time is None:
                    timestamp_parse_failed += 1

                mentioned_at = raw_ts if raw_ts else datetime.now(UTC).isoformat()

                # 判窗：解析成功且 < cutoff → 跳过整条 tweet
                candidates_before += len(raw_urls)
                if parsed_time is not None and parsed_time < cutoff:
                    recency_dropped += len(raw_urls)  # 该 tweet 中所有 URL 都算丢弃
                    continue

                for url in raw_urls:
                    normalized = normalize(url)
                    if normalized is None:
                        continue
                    if normalized in seen_in_handle:
                        continue
                    seen_in_handle.add(normalized)
                    candidates_after += 1
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
        error = None if ok else (
            f"fail_rate={fail_rate:.2f}, candidates={len(candidates)}, "
            f"rate_limited_handles={rate_limited_handles}"
            + (f", {aborted_reason}" if aborted_reason else "")
        )

        # 每次都打印，擦线过闸（如 09-24 的 30/61≈0.49）事后可见。
        sys.stderr.write(
            f"INFO [github-l1]: ok={ok} fail_rate={fail_rate:.2f} "
            f"failed={len(failed_handles)}/{len(handles)} candidates={len(candidates)} "
            f"rate_limited_fetches={rate_limited_handles} window_waits={rate_limit_waits}\n"
        )

        recency_stats = {
            "candidates_before_recency": candidates_before,
            "candidates_after_recency": candidates_after,
            "recency_dropped": recency_dropped,
            "timestamp_missing": timestamp_missing,
            "timestamp_parse_failed": timestamp_parse_failed,
        }

        return CollectorResult("layer1", candidates, failed_handles, duration, ok, error,
                               recency_stats)

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
        """Fetch recent tweets for a handle. Returns None on failure.

        twitter-cli 已内置 429 重试（5.9s→11.6s→20.4s），collector 不再叠加外部重试。
        """
        clean_handle = handle.lstrip("@")
        try:
            result = subprocess.run(
                [self._twitter_bin, "user-posts", clean_handle,
                 "--max", str(USER_POSTS_MAX), "--json"],
                capture_output=True, text=True, timeout=60,
            )
            if is_rate_limited((result.stderr or "") + (result.stdout or "")):
                self._last_fetch_rate_limited = True
            if result.returncode == 0:
                import json
                try:
                    parsed = json.loads(result.stdout)
                except json.JSONDecodeError:
                    return []
                if isinstance(parsed, dict):
                    # CLI 对 API 层失败也可能 exit 0，只在 payload 里写 ok=false；
                    # 当成「0 条」会让该账号漏记进 failed_handles、fail_rate 失真。
                    if parsed.get("ok") is False:
                        return None
                    return parsed.get("data", [])
                return parsed
            # CLI 已处理 429，非 0 返回码直接视为失败（不重试）
            return None
        except Exception:
            return None
