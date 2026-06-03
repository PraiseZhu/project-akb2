"""discover：缺失静默、解析失败上报、日期按文件名匹配。"""

from __future__ import annotations

from akb2.feishu_sync.discover import discover

from .conftest import write_ai_trends, write_github


def test_both_present(vault):
    write_ai_trends(vault, "2026-06-03")
    write_github(vault, "2026-06-03")
    briefings, errors = discover(vault, "2026-06-03")
    kinds = sorted(b.kind for b in briefings)
    assert kinds == ["ai_trends", "github"]
    assert errors == []


def test_missing_is_silent(vault):
    # 当天什么都没有 → 空列表、无错误（SC-2）
    briefings, errors = discover(vault, "2026-06-03")
    assert briefings == []
    assert errors == []


def test_only_one_present(vault):
    write_ai_trends(vault, "2026-06-03")
    briefings, errors = discover(vault, "2026-06-03")
    assert [b.kind for b in briefings] == ["ai_trends"]
    assert errors == []


def test_date_match_by_filename(vault):
    write_ai_trends(vault, "2026-06-02")  # 昨天
    briefings, errors = discover(vault, "2026-06-03")  # 找今天
    assert briefings == []
    assert errors == []


def test_corrupt_existing_file_is_error_not_silent(vault):
    # 已存在但 frontmatter 损坏 → 进 errors（SC-5），不静默跳过
    write_ai_trends(vault, "2026-06-03", raw="no frontmatter here\njust text")
    briefings, errors = discover(vault, "2026-06-03")
    assert briefings == []
    assert len(errors) == 1
    assert "ai_trends" in errors[0]


def test_github_json_loaded(vault):
    write_github(vault, "2026-06-03")
    briefings, _ = discover(vault, "2026-06-03")
    gh = next(b for b in briefings if b.kind == "github")
    assert gh.json_data is not None
    assert gh.json_data["top_repos"][0]["repo_url"].startswith("https://github.com/")
