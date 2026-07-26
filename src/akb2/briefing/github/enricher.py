"""GitHub API enrichment — description, stars, language, topics, README excerpt, created_at.

Grounding 契约（缺陷 #7）：LLM 只能基于 `grounding_excerpt` 写 summary。
`description`（GitHub 侧最短最准的一句话）此前从未被取用，readme 取失败时
`enrich_ok` 仍为 True → 空 grounding 进 LLM，模型只能靠 repo 名 + language 编
（实证：`mattpocock/skills` 真实描述 "Skills for Real Engineers…" 被写成
「定制 GitHub 个人主页技能展示的模板仓库」）。因此：

- `description` 纳入结果并进 prompt；
- `grounding_source` / `grounding_excerpt` 显式记录 grounding 来源与原文（可审计）；
- **`enrich_ok` 反映真实 grounding**：无任何 grounding → False，由 pipeline 剔除。

身份契约（缺陷 #8）：`urlopen` 默认跟随 301（`codedrobe/codex-skill` →
`CodeDrobe/skills`），但旧实现从不回填 → 简报挂旧 URL/旧 stars 却描述新仓内容，
且旧名与新名成为两个去重键（同仓双份入库）。现在：

- `repo_url` = `full_name` 派生的**归一化去重键**（小写稳定）；
- `repo_full_name` = GitHub 权威大小写，供显示；
- `requested_repo_url` = 原始请求 URL，保留可审计。
"""

import json
import os
import time
from typing import Any
from urllib.request import Request, urlopen

from .url_normalizer import normalize

GITHUB_API_BASE = "https://api.github.com"
GITHUB_PAT_ENV = "GITHUB_PAT"
RATE_LIMIT_MAX = 4500  # abort at 4500/h (headroom below 5000)

# grounding 摘要长度上限（进 JSON 产物供事后审计幻觉率，不进 Markdown 正文）
GROUNDING_EXCERPT_MAX = 400
# grounding 摘要里 readme 部分的上限（description 优先，readme 补充）
_GROUNDING_README_CHARS = 300


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


def _build_grounding(description: str | None,
                     readme: str | None) -> tuple[str | None, str | None]:
    """返回 (grounding_source, grounding_excerpt)；两者皆空 → (None, None)。

    description 优先（最短最准），readme 截断补充。空白串不算 grounding。
    """
    desc = (description or "").strip()
    body = (readme or "").strip()
    if not desc and not body:
        return None, None
    if desc and body:
        source = "description+readme"
    elif desc:
        source = "description"
    else:
        source = "readme"
    parts = []
    if desc:
        parts.append(desc)
    if body:
        parts.append(body[:_GROUNDING_README_CHARS])
    excerpt = "\n".join(parts)[:GROUNDING_EXCERPT_MAX]
    return source, excerpt


def enrich(repo_url: str, pat: str | None = None) -> dict[str, Any]:
    """Enrich a repository URL with GitHub API data."""
    # Parse owner/repo from URL
    parts = repo_url.rstrip("/").split("/")
    owner, repo = parts[-2], parts[-1]

    repo_data = _github_api(f"/repos/{owner}/{repo}", pat)
    if repo_data is None:
        # API 失败/限流/404：显式标记 enrich_ok=False，让 pipeline 能区分
        # "调用失败" 与 "真无数据"，避免静默产出全 0 但 schema 合法的简报。
        return {
            "repo_url": repo_url,
            "requested_repo_url": repo_url,
            "repo_full_name": None,
            "description": None,
            "stars": 0,
            "language": None,
            "topic_tags": [],
            "created_at": None,
            "readme_excerpt": None,
            "grounding_source": None,
            "grounding_excerpt": None,
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

    # 301 重定向身份回填：full_name 是 API 跟随重定向后的真身。
    # 去重键走 normalize()（小写稳定），显示名保留 API 的权威大小写。
    full_name = (repo_data.get("full_name") or "").strip() or None
    canonical_url = normalize(f"https://github.com/{full_name}") if full_name else None
    if canonical_url is None:
        canonical_url = normalize(repo_url) or repo_url

    description = repo_data.get("description")
    grounding_source, grounding_excerpt = _build_grounding(description, readme)

    return {
        "repo_url": canonical_url,
        "requested_repo_url": repo_url,
        "repo_full_name": full_name,
        "description": description,
        "stars": repo_data.get("stargazers_count", 0),
        "language": repo_data.get("language"),
        "topic_tags": repo_data.get("topics", []),
        "created_at": created_at,
        "readme_excerpt": readme,
        "grounding_source": grounding_source,
        "grounding_excerpt": grounding_excerpt,
        "commits_7d": commits_7d,
        # grounding 为空即视为 enrich 失败：没有原文可依据时，LLM 只能编。
        "enrich_ok": grounding_source is not None,
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
