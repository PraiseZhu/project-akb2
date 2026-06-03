"""Tests for GitHub API enricher."""

from unittest import mock

from akb2.briefing.github.enricher import enrich, enrich_batch


SAMPLE_REPO_DATA = {
    "stargazers_count": 15234,
    "language": "Python",
    "topics": ["quant", "ml", "finance"],
    "created_at": "2020-05-15T12:00:00Z",
}


def test_enrich_parses_owner_repo_from_url():
    with mock.patch("akb2.briefing.github.enricher._github_api", return_value=SAMPLE_REPO_DATA):
        with mock.patch("akb2.briefing.github.enricher._get_readme", return_value="# Qlib\n```python\nprint('hello')\n```" * 20):
            result = enrich("https://github.com/microsoft/qlib")

    assert result["stars"] == 15234
    assert result["language"] == "Python"
    assert result["topic_tags"] == ["quant", "ml", "finance"]
    assert result["created_at"] == "2020-05-15T12:00:00Z"
    assert result["repo_url"] == "https://github.com/microsoft/qlib"


def test_enrich_handles_api_failure():
    with mock.patch("akb2.briefing.github.enricher._github_api", return_value=None):
        result = enrich("https://github.com/owner/repo")

    assert result["stars"] == 0
    assert result["language"] is None
    assert result["topic_tags"] == []


def test_enrich_batch_returns_all_results():
    repos = [
        "https://github.com/a/b",
        "https://github.com/c/d",
        "https://github.com/e/f",
    ]
    with mock.patch("akb2.briefing.github.enricher.enrich", return_value={"repo_url": "x", "stars": 100}):
        with mock.patch("time.sleep", return_value=None):
            results = enrich_batch(repos)
    assert len(results) == 3
    for r in results:
        assert r["stars"] == 100


def test_enrich_handles_no_pat():
    with mock.patch("akb2.briefing.github.enricher._github_api", return_value=SAMPLE_REPO_DATA):
        result = enrich("https://github.com/microsoft/qlib", pat=None)
    assert result["stars"] == 15234