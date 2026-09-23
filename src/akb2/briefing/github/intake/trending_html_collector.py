"""Layer 2: curl GitHub Trending HTML → BeautifulSoup parse → normalize URLs."""

import re
import subprocess
import time
from datetime import UTC, datetime

from . import _ZERO_RECENCY_STATS, Candidate, CollectorResult

TRENDING_URL = "https://github.com/trending"
CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 抓取重试：单次 curl 失败（瞬时网络 / GitHub 临时拦截）不直接判 L2 失败。
FETCH_ATTEMPTS = 2
FETCH_RETRY_SLEEP_SEC = 30
MIN_HTML_BYTES = 1000

_REPO_LINK_RE = re.compile(r'href="/([\w.-]+)/([\w.-]+)"')


class TrendingHTMLCollector:
    def collect(self) -> CollectorResult:
        t0 = time.monotonic()

        html = None
        fetch_errors: list[str] = []
        for attempt in range(FETCH_ATTEMPTS):
            if attempt:
                time.sleep(FETCH_RETRY_SLEEP_SEC)
            html = self._fetch_trending()
            if html is not None:
                break
            fetch_errors.append(self._last_fetch_error or "unknown")
        if html is None:
            return CollectorResult("layer2", [], [], time.monotonic() - t0, False,
                                   "HTTP request to GitHub Trending failed "
                                   f"({FETCH_ATTEMPTS} attempts: {'; '.join(fetch_errors)})",
                                   _ZERO_RECENCY_STATS)

        repos = self._parse_repos(html)
        if len(repos) < 5:
            return CollectorResult("layer2", [], [], time.monotonic() - t0, False,
                                   f"parsed only {len(repos)} repos, need >= 5",
                                   _ZERO_RECENCY_STATS)

        now = datetime.now(UTC).isoformat()
        candidates = [
            Candidate(
                repo_url=f"https://github.com/{owner}/{repo}",
                mentioner="@github_trending",
                tweet_id=None,
                mentioned_at=now,
                tweet_text=None,
            )
            for owner, repo in repos
        ]

        return CollectorResult("layer2", candidates, [], time.monotonic() - t0, True, None,
                               _ZERO_RECENCY_STATS)

    _last_fetch_error: str | None = None

    def _fetch_trending(self) -> str | None:
        self._last_fetch_error = None
        try:
            result = subprocess.run(
                ["curl", "-sL", "--max-time", "30",
                 "-H", f"User-Agent: {CHROME_UA}",
                 TRENDING_URL],
                capture_output=True, text=True, timeout=35,
            )
            if result.returncode == 0 and len(result.stdout) > MIN_HTML_BYTES:
                return result.stdout
            self._last_fetch_error = (
                f"curl rc={result.returncode} bytes={len(result.stdout)} "
                f"stderr={(result.stderr or '').strip()[:120]}"
            )
            return None
        except Exception as exc:
            self._last_fetch_error = f"{type(exc).__name__}: {exc}"
            return None

    def _parse_repos(self, html: str) -> list[tuple[str, str]]:
        """Extract (owner, repo) pairs from trending HTML."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            articles = soup.find_all("article", class_="Box-row")
            repos = []
            for article in articles:
                h2 = article.find("h2", class_="h3")
                if not h2:
                    continue
                link = h2.find("a")
                if not link:
                    continue
                href = link.get("href", "").strip()
                # href looks like "/owner/repo"
                parts = href.lstrip("/").split("/")
                if len(parts) >= 2:
                    repos.append((parts[0].lower(), parts[1].lower()))
            return repos
        except ImportError:
            # Fallback: regex extraction
            matches = _REPO_LINK_RE.findall(html)
            seen = set()
            repos = []
            for owner, repo in matches:
                # Filter out non-repo paths
                if owner in ("settings", "explore", "notifications", "login", "signup",
                             "marketplace", "sponsors", "codespaces", "features",
                             "mobile", "enterprise", "pricing", "topics", "collections",
                             "events", "readme", "security", "account"):
                    continue
                key = (owner.lower(), repo.lower())
                if key not in seen:
                    seen.add(key)
                    repos.append(key)
            return repos


def _main(argv: list[str]) -> int:
    """CLI: --report json 输出 {ok, repos:[url...], duration_sec, error}。"""
    import json

    result = TrendingHTMLCollector().collect()
    report = {
        "ok": result.ok,
        "repos": [c.repo_url for c in result.candidates],
        "duration_sec": round(result.duration_sec, 3),
        "error": result.error,
    }
    if "--report" in argv:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"ok={result.ok} repos={len(result.candidates)} "
              f"duration={report['duration_sec']}s error={result.error}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
