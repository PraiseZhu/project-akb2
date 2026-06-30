"""discover：缺失静默、解析失败上报、日期按文件名匹配。"""

from __future__ import annotations

from akb2.feishu_sync.discover import discover

from .conftest import write_ai_trends, write_github


def test_both_present(vault):
    write_ai_trends(vault, "2026-06-03")
    write_github(vault, "2026-06-03")
    briefings, errors, missing = discover(vault, "2026-06-03")
    kinds = sorted(b.kind for b in briefings)
    assert kinds == ["ai_trends", "github"]
    assert errors == []
    assert missing == []


def test_missing_is_silent(vault):
    # 当天什么都没有 → 空列表、无错误（SC-2）；无 expected 时不报缺失
    briefings, errors, missing = discover(vault, "2026-06-03")
    assert briefings == []
    assert errors == []
    assert missing == []


def test_only_one_present(vault):
    write_ai_trends(vault, "2026-06-03")
    briefings, errors, missing = discover(vault, "2026-06-03")
    assert [b.kind for b in briefings] == ["ai_trends"]
    assert errors == []
    assert missing == []  # 未传 expected → 不校验缺失


def test_date_match_by_filename(vault):
    write_ai_trends(vault, "2026-06-02")  # 昨天
    briefings, errors, missing = discover(vault, "2026-06-03")  # 找今天
    assert briefings == []
    assert errors == []
    assert missing == []


def test_corrupt_existing_file_is_error_not_silent(vault):
    # 已存在但 frontmatter 损坏 → 进 parse_errors（SC-5），不静默跳过
    write_ai_trends(vault, "2026-06-03", raw="no frontmatter here\njust text")
    briefings, errors, missing = discover(vault, "2026-06-03")
    assert briefings == []
    assert len(errors) == 1
    assert "ai_trends" in errors[0]
    assert missing == []


def test_github_json_loaded(vault):
    write_github(vault, "2026-06-03")
    briefings, _, _ = discover(vault, "2026-06-03")
    gh = next(b for b in briefings if b.kind == "github")
    assert gh.json_data is not None
    assert gh.json_data["top_repos"][0]["repo_url"].startswith("https://github.com/")


# ---------------------------------------------------------------------------
# SC-7: strict 模式期望简报齐全 — 缺失计入 missing_expected
# ---------------------------------------------------------------------------


def test_missing_expected_github_is_error_in_strict_mode(vault):
    """SC-7: strict(expected=[ai_trends,github]) 下 github 缺失 → missing_expected 含 github+期望路径。"""
    write_ai_trends(vault, "2026-06-03")
    briefings, errors, missing = discover(
        vault, "2026-06-03", expected=["ai_trends", "github"]
    )

    assert [b.kind for b in briefings] == ["ai_trends"]
    assert errors == []  # 缺失非 parse error，由上层 strict 转 error
    assert len(missing) == 1
    kind, path = missing[0]
    assert kind == "github"
    assert "GitHub Briefing.md" in path
    assert "2026-06-03" in path


def test_missing_expected_both_when_nothing_present(vault):
    """SC-7: 两份都缺 → missing_expected 含 ai_trends + github。"""
    _, _, missing = discover(vault, "2026-06-03", expected=["ai_trends", "github"])
    kinds = sorted(k for k, _ in missing)
    assert kinds == ["ai_trends", "github"]


def test_no_expected_no_missing_check(vault):
    """SC-7: 未传 expected → 即使缺失也不报 missing_expected（开发/历史日期）。"""
    write_ai_trends(vault, "2026-06-03")
    _, _, missing = discover(vault, "2026-06-03")  # 无 expected
    assert missing == []
