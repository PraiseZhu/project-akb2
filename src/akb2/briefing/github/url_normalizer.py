"""GitHub URL normalization — lowercase owner/repo（去重键）+ 保留大小写的显示名。

两点契约（缺陷 #8）：

- `normalize()` 产出**去重键**：小写、剥尾巴、无 `.git`。GitHub 侧 owner/repo 大小写
  不敏感，键必须小写才稳定（大小写分裂 = 同仓双份入库）。
- `display_slug()` 产出**显示名**：保留原始大小写。`normalize()` 无条件 `.lower()`
  曾把 `id-Software/Quake` 显示成 `id-software/quake`、`xianfei/SysMocap` 显示成
  `sysmocap`（pipeline 直接从 url 切名渲染标题）。权威大小写优先取 GitHub API 的
  `full_name`（见 enricher 的 `repo_full_name`），本函数是无 API 数据时的兜底。

保留路径 denylist：`github.com/features/ai`、`/features/copilot` 这类营销页曾被当
repo 收录（API 404 → stars=0/lang=null），仍产出完整中文简介并挤掉真实名额。
GitHub 保留这些顶层路径，不可能是真实 owner，因此在归一化层直接拒收。
"""

import re

_GITHUB_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+)"
    r"(?:/(?:issues|pull|blob|tree|commit|releases|actions|projects|discussions|wiki|milestones"
    r"|labels|settings|network|graphs|compare|raw|archive))?"
    r"(?:/[^?#]*)?"
    r"(?:\.git)?"
    r"(?:[?#].*)?$",
    re.IGNORECASE,
)

# Max GitHub owner/repo name length
_MAX_NAME_LEN = 39

# GitHub 保留顶层路径（不可能是 owner）。全小写，比对前先 lower()。
RESERVED_OWNER_PATHS = frozenset({
    "about", "account", "apps", "blog", "codespaces", "collections", "contact",
    "copilot", "customer-stories", "dashboard", "discussions", "downloads",
    "education", "enterprise", "events", "explore", "features", "home", "issues",
    "join", "login", "logout", "marketplace", "mobile", "new", "nonprofit",
    "notifications", "organizations", "orgs", "premium-support", "pricing",
    "pulls", "readme", "search", "security", "sessions", "settings", "signup",
    "site", "sitemap", "sponsors", "stars", "topics", "trending", "users",
    "watching",
})


def _split(url: str) -> tuple[str, str] | None:
    """匹配并返回 (owner, repo)，保留原始大小写；非 repo URL / 保留路径 → None。"""
    m = _GITHUB_URL_RE.match(url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    # Strip trailing .git from repo name
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if not repo:
        return None
    if len(owner) > _MAX_NAME_LEN or len(repo) > _MAX_NAME_LEN:
        return None
    if owner.lower() in RESERVED_OWNER_PATHS:
        return None
    return owner, repo


def normalize(url: str) -> str | None:
    """去重键：lowercase `https://github.com/owner/repo`，非 repo URL → None。"""
    parts = _split(url)
    if parts is None:
        return None
    owner, repo = parts
    return f"https://github.com/{owner.lower()}/{repo.lower()}"


def display_slug(url: str) -> str | None:
    """显示名：`Owner/Repo`（保留原始大小写），非 repo URL → None。"""
    parts = _split(url)
    if parts is None:
        return None
    return f"{parts[0]}/{parts[1]}"
