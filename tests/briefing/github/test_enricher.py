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


# --- 缺陷 #7：description 是最短最准的 grounding，此前从未被取用 ---
# 实证幻觉：mattpocock/skills 真实描述 "Skills for Real Engineers. Straight from
# my .agents directory."，2026-07-25 简报却写成「定制 GitHub 个人主页技能展示的
# 模板仓库…」——纯从 repo 名 + language=Shell 猜的。

def test_enrich_returns_description_as_grounding():
    data = {
        **SAMPLE_REPO_DATA,
        "full_name": "mattpocock/skills",
        "description": "Skills for Real Engineers. Straight from my .agents directory.",
    }
    with mock.patch("akb2.briefing.github.enricher._github_api", return_value=data):
        with mock.patch("akb2.briefing.github.enricher._get_readme", return_value=None):
            result = enrich("https://github.com/mattpocock/skills")

    assert result["description"] == "Skills for Real Engineers. Straight from my .agents directory."
    assert "Real Engineers" in result["grounding_excerpt"]
    assert result["grounding_source"] == "description"
    assert result["enrich_ok"] is True


def test_enrich_ok_false_when_no_grounding_at_all():
    """readme 取不到 + description 为空 → 无任何 grounding，不得标 enrich_ok=True。"""
    data = {**SAMPLE_REPO_DATA, "full_name": "owner/repo", "description": None}
    with mock.patch("akb2.briefing.github.enricher._github_api", return_value=data):
        with mock.patch("akb2.briefing.github.enricher._get_readme", return_value=None):
            result = enrich("https://github.com/owner/repo")

    assert result["enrich_ok"] is False
    assert result["grounding_source"] is None
    assert not (result["grounding_excerpt"] or "")


def test_enrich_ok_false_when_grounding_is_whitespace_only():
    data = {**SAMPLE_REPO_DATA, "full_name": "owner/repo", "description": "   "}
    with mock.patch("akb2.briefing.github.enricher._github_api", return_value=data):
        with mock.patch("akb2.briefing.github.enricher._get_readme", return_value="\n \n"):
            result = enrich("https://github.com/owner/repo")

    assert result["enrich_ok"] is False


def test_enrich_grounding_excerpt_is_bounded():
    data = {**SAMPLE_REPO_DATA, "full_name": "owner/repo", "description": "d" * 900}
    with mock.patch("akb2.briefing.github.enricher._github_api", return_value=data):
        with mock.patch("akb2.briefing.github.enricher._get_readme", return_value="r" * 1500):
            result = enrich("https://github.com/owner/repo")

    from akb2.briefing.github.enricher import GROUNDING_EXCERPT_MAX

    assert 0 < len(result["grounding_excerpt"]) <= GROUNDING_EXCERPT_MAX


def test_enrich_api_failure_marks_no_grounding():
    with mock.patch("akb2.briefing.github.enricher._github_api", return_value=None):
        result = enrich("https://github.com/features/ai")

    assert result["enrich_ok"] is False
    assert result["grounding_source"] is None
    assert not (result["grounding_excerpt"] or "")


# --- 缺陷 #8：301 重定向后必须回填身份，且新旧名归一到同一去重键 ---
# 实证：codedrobe/codex-skill → CodeDrobe/skills（231 stars）；简报仍挂旧 URL/旧
# stars，描述的却是新仓内容。urlopen 默认跟随重定向，但 full_name 从不回填。

def test_enrich_backfills_repo_url_from_full_name_on_redirect():
    data = {
        "stargazers_count": 231,
        "language": "Python",
        "topics": [],
        "created_at": "2026-01-01T00:00:00Z",
        "full_name": "CodeDrobe/skills",
        "description": "Agent skills collection.",
    }
    with mock.patch("akb2.briefing.github.enricher._github_api", return_value=data):
        with mock.patch("akb2.briefing.github.enricher._get_readme", return_value=None):
            result = enrich("https://github.com/codedrobe/codex-skill")

    # 去重键 = 回填后的规范身份（小写稳定）
    assert result["repo_url"] == "https://github.com/codedrobe/skills"
    # 原始请求保留可审计
    assert result["requested_repo_url"] == "https://github.com/codedrobe/codex-skill"
    # 显示名保留 GitHub 权威大小写
    assert result["repo_full_name"] == "CodeDrobe/skills"


def test_redirect_and_new_name_share_one_dedup_key():
    """旧名与新名 enrich 后必须产出同一 repo_url（否则同仓双份入库）。"""
    data = {
        "stargazers_count": 231,
        "language": "Python",
        "topics": [],
        "created_at": "2026-01-01T00:00:00Z",
        "full_name": "CodeDrobe/skills",
        "description": "Agent skills collection.",
    }
    with mock.patch("akb2.briefing.github.enricher._github_api", return_value=data):
        with mock.patch("akb2.briefing.github.enricher._get_readme", return_value=None):
            old = enrich("https://github.com/codedrobe/codex-skill")
            new = enrich("https://github.com/CodeDrobe/skills")

    assert old["repo_url"] == new["repo_url"]
