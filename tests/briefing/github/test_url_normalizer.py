"""Tests for GitHub URL normalizer."""

import pytest

from akb2.briefing.github.url_normalizer import normalize

# --- Valid URLs — must normalize correctly ---

@pytest.mark.parametrize("url,expected", [
    # Standard HTTPS
    ("https://github.com/microsoft/qlib", "https://github.com/microsoft/qlib"),
    # HTTP → HTTPS
    ("http://github.com/owner/repo", "https://github.com/owner/repo"),
    # www prefix
    ("https://www.github.com/owner/repo", "https://github.com/owner/repo"),
    # Uppercase
    ("https://github.com/Microsoft/Qlib", "https://github.com/microsoft/qlib"),
    # Mixed case
    ("https://GitHub.Com/Owner/Repo", "https://github.com/owner/repo"),
    # Strip /issues subpath
    ("https://github.com/owner/repo/issues/123", "https://github.com/owner/repo"),
    # Strip /blob subpath
    ("https://github.com/owner/repo/blob/main/README.md", "https://github.com/owner/repo"),
    # Strip /tree subpath
    ("https://github.com/owner/repo/tree/dev/src", "https://github.com/owner/repo"),
    # Strip /pull subpath
    ("https://github.com/owner/repo/pull/42", "https://github.com/owner/repo"),
    # Strip /commit subpath
    ("https://github.com/owner/repo/commit/abc123", "https://github.com/owner/repo"),
    # Strip /releases subpath
    ("https://github.com/owner/repo/releases/tag/v1.0", "https://github.com/owner/repo"),
    # Strip /actions subpath
    ("https://github.com/owner/repo/actions", "https://github.com/owner/repo"),
    # Strip /projects subpath
    ("https://github.com/owner/repo/projects/1", "https://github.com/owner/repo"),
    # Strip /discussions subpath
    ("https://github.com/owner/repo/discussions/5", "https://github.com/owner/repo"),
    # Strip /wiki subpath
    ("https://github.com/owner/repo/wiki", "https://github.com/owner/repo"),
    # Strip /milestones subpath
    ("https://github.com/owner/repo/milestones/1", "https://github.com/owner/repo"),
    # Strip /labels subpath
    ("https://github.com/owner/repo/labels/bug", "https://github.com/owner/repo"),
    # Strip /settings subpath
    ("https://github.com/owner/repo/settings", "https://github.com/owner/repo"),
    # Strip /network subpath
    ("https://github.com/owner/repo/network/dependents", "https://github.com/owner/repo"),
    # Strip /graphs subpath
    ("https://github.com/owner/repo/graphs/contributors", "https://github.com/owner/repo"),
    # Strip /compare subpath
    ("https://github.com/owner/repo/compare/v1...v2", "https://github.com/owner/repo"),
    # Strip /raw subpath
    ("https://github.com/owner/repo/raw/main/file.txt", "https://github.com/owner/repo"),
    # Strip /archive subpath
    ("https://github.com/owner/repo/archive/refs/heads/main.zip", "https://github.com/owner/repo"),
    # Strip .git suffix
    ("https://github.com/owner/repo.git", "https://github.com/owner/repo"),
    # Strip query string
    ("https://github.com/owner/repo?tab=readme", "https://github.com/owner/repo"),
    # Strip fragment
    ("https://github.com/owner/repo#readme", "https://github.com/owner/repo"),
    # Strip trailing slash
    ("https://github.com/owner/repo/", "https://github.com/owner/repo"),
    # Dot in owner/repo (GitHub allows)
    ("https://github.com/owner.name/repo.name", "https://github.com/owner.name/repo.name"),
    # Dash in owner/repo
    ("https://github.com/owner-name/repo-name", "https://github.com/owner-name/repo-name"),
    # Underscore
    ("https://github.com/owner_name/repo_name", "https://github.com/owner_name/repo_name"),
    # Single char owner/repo
    ("https://github.com/a/b", "https://github.com/a/b"),
    # Leading/trailing whitespace
    ("  https://github.com/owner/repo  ", "https://github.com/owner/repo"),
])
def test_normalize_valid(url, expected):
    assert normalize(url) == expected


# --- Invalid URLs — must return None ---

@pytest.mark.parametrize("url", [
    # Not a GitHub domain
    "https://gitlab.com/owner/repo",
    "https://bitbucket.org/owner/repo",
    # No owner — only domain
    "https://github.com",
    # No repo — only owner
    "https://github.com/owner",
    # Empty string
    "",
    # Not a URL at all
    "not a url",
    # gist.github.com (different subdomain)
    "https://gist.github.com/owner/hash",
    # Too long owner (GitHub max 39)
    "https://github.com/" + "a" * 40 + "/repo",
    # Too long repo
    "https://github.com/owner/" + "a" * 40,
    # No scheme
    "github.com/owner/repo",
])
def test_normalize_invalid(url):
    assert normalize(url) is None


def test_normalize_idempotent():
    """Normalize is idempotent."""
    url = "https://github.com/Microsoft/Qlib/issues/123"
    first = normalize(url)
    assert first is not None
    second = normalize(first)
    assert second == first
