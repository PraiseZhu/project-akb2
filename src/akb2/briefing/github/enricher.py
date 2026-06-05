"""GitHub API enrichment — stars, language, topics, README excerpt, created_at."""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

GITHUB_API_BASE = "https://api.github.com"
GITHUB_PAT_ENV = "GITHUB_PAT"
RATE_LIMIT_MAX = 4500  # abort at 4500/h (headroom below 5000)


def _github_api(path: str, pat: str | None = None) -> dict | None:
    token = pat or os.environ.get(GITHUB_PAT_ENV, "")
    url = f"{GITHUB_API_BASE}{path}"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "akb2-github-curator"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=15) as resp:
            remaining = int(resp.headers.get("X-RateLimit-Remaining", 9999))
            if remaining < 100:
                return None  # rate limit critically low
            return json.loads(resp.read())
    except Exception:
        return None


def _get_readme(owner: str, repo: str, pat: str | None = None) -> str | None:
    data = _github_api(f"/repos/{owner}/{repo}/readme", pat)
    if data is None:
        return None
    try:
        import base64
        content = data.get("content", "")
        decoded = base64.b64decode(content).decode("utf-8", errors="replace")
        return decoded[:1500]
    except Exception:
        return None


def enrich(repo_url: str, pat: str | None = None) -> dict[str, Any]:
    """Enrich a repository URL with GitHub API data."""
    # Parse owner/repo from URL
    parts = repo_url.rstrip("/").split("/")
    owner, repo = parts[-2], parts[-1]

    repo_data = _github_api(f"/repos/{owner}/{repo}", pat)
    if repo_data is None:
        # API 失败/限流：显式标记 enrich_ok=False，让 pipeline 能区分
        # "调用失败" 与 "真无数据"，避免静默产出全 0 但 schema 合法的简报。
        return {
            "repo_url": repo_url,
            "stars": 0,
            "language": None,
            "topic_tags": [],
            "created_at": None,
            "readme_excerpt": None,
            "commits_7d": None,  # None = unknown (API failed)
            "enrich_ok": False,
        }

    # Always attempt readme fetch (pat or env fallback) — no short-circuit
    readme = _get_readme(owner, repo, pat)

    created_at = repo_data.get("created_at")

    # 7-day commits via stats/commit_activity
    # None = unknown (API error / 202 / empty); int 0 = API succeeded, truly 0 commits
    commits_7d: int | None = None
    try:
        commit_data = _github_api(f"/repos/{owner}/{repo}/stats/commit_activity", pat)
        if commit_data and isinstance(commit_data, list) and len(commit_data) > 0:
            # API returned data: sum the most recent week
            commits_7d = sum(week.get("total", 0) for week in commit_data[-1:])
    except Exception:
        pass  # stays None

    return {
        "repo_url": repo_url,
        "stars": repo_data.get("stargazers_count", 0),
        "language": repo_data.get("language"),
        "topic_tags": repo_data.get("topics", []),
        "created_at": created_at,
        "readme_excerpt": readme,
        "commits_7d": commits_7d,
        "enrich_ok": True,
    }


def enrich_batch(repos: list[str], pat: str | None = None) -> list[dict]:
    """Enrich multiple repos with rate-limit awareness."""
    results = []
    for i, url in enumerate(repos):
        enriched = enrich(url, pat)
        results.append(enriched)
        if i < len(repos) - 1:
            time.sleep(0.3)  # ~3 req/s, well within 5000/h
    return results