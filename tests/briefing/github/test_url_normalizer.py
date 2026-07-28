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


# --- 缺陷 #8：GitHub 保留路径不是 repo，必须拒收 ---
# 实证：https://github.com/features/ai（3 天）与 /features/copilot（2 天）被当 repo
# 收录，GitHub API 返回 404（营销页），stars=0/lang=null，却仍产出完整中文简介，
# 并挤掉 5 个真实名额（2026-06-25 一份简报同时收了两条）。

@pytest.mark.parametrize("url", [
    "https://github.com/features/ai",
    "https://github.com/features/copilot",
    "https://github.com/about/careers",
    "https://github.com/pricing/team",
    "https://github.com/topics/machine-learning",
    "https://github.com/sponsors/simonw",
    "https://github.com/marketplace/actions/checkout",
    "https://github.com/orgs/anthropics",
    "https://github.com/apps/dependabot",
    "https://github.com/settings/profile",
    "https://github.com/explore/trending",
    "https://github.com/collections/ai",
    "https://github.com/trending/python",
    "https://github.com/login/oauth",
    "https://github.com/signup/free",
    # 大小写与尾巴变体都得拒
    "https://github.com/Features/AI",
    "https://www.github.com/features/copilot?ref=nav",
])
def test_normalize_rejects_reserved_paths(url):
    assert normalize(url) is None


def test_reserved_set_is_exposed_for_audit():
    from akb2.briefing.github.url_normalizer import RESERVED_OWNER_PATHS

    assert "features" in RESERVED_OWNER_PATHS
    assert all(p == p.lower() for p in RESERVED_OWNER_PATHS)


# --- 缺陷 #8：去重键保持小写稳定，显示名保留原始大小写 ---

def test_dedup_key_stays_lowercase():
    """去重键必须继续小写归一（GitHub 侧大小写不敏感，键分裂 = 同仓双份入库）。"""
    assert normalize("https://github.com/id-Software/Quake") == "https://github.com/id-software/quake"


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/id-Software/Quake", "id-Software/Quake"),
    ("https://github.com/xianfei/SysMocap", "xianfei/SysMocap"),
    ("https://github.com/owner/repo.git", "owner/repo"),
    ("https://github.com/Microsoft/Qlib/issues/123", "Microsoft/Qlib"),
])
def test_display_slug_preserves_case(url, expected):
    from akb2.briefing.github.url_normalizer import display_slug

    assert display_slug(url) == expected


def test_display_slug_none_for_invalid():
    from akb2.briefing.github.url_normalizer import display_slug

    assert display_slug("https://github.com/features/ai") is None
    assert display_slug("not a url") is None
