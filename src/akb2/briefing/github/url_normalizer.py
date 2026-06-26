"""GitHub URL normalization — lowercase owner/repo, strip tails."""

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


def normalize(url: str) -> str | None:
    """Normalize a GitHub URL. Returns lowercase https://github.com/owner/repo or None."""
    m = _GITHUB_URL_RE.match(url.strip())
    if not m:
        return None
    owner, repo = m.group(1).lower(), m.group(2).lower()
    # Strip trailing .git from repo name
    if repo.endswith(".git"):
        repo = repo[:-4]
    if len(owner) > _MAX_NAME_LEN or len(repo) > _MAX_NAME_LEN:
        return None
    return f"https://github.com/{owner}/{repo}"
