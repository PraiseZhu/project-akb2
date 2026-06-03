"""render：卡片结构、长正文拆块、GitHub 用 json 渲染。"""

from __future__ import annotations

from akb2.feishu_sync.render import render_cards
from akb2.feishu_sync.state import Briefing


def _card_of(b):
    cards = render_cards(b)
    assert len(cards) >= 1
    return cards[0]


def test_ai_trends_card_structure():
    b = Briefing(
        kind="ai_trends",
        date="2026-06-03",
        path=None,
        title="AI 趋势日报 · 2026-06-03",
        frontmatter={},
        body="# 标题\n\n正文一段。",
    )
    card = _card_of(b)
    assert card["header"]["title"]["content"] == "AI 趋势日报 · 2026-06-03"
    assert card["header"]["template"] == "blue"
    assert all(e["tag"] == "markdown" for e in card["elements"])
    # 标题 # 应被转成加粗
    assert "**标题**" in card["elements"][0]["content"]


def test_long_body_splits_into_multiple_elements():
    big = "\n".join(f"行 {i} " + "x" * 50 for i in range(400))  # 远超 3000 字符
    b = Briefing(
        kind="ai_trends", date="d", path=None, title="t", frontmatter={}, body=big
    )
    card = _card_of(b)
    assert len(card["elements"]) > 1


def test_github_rendered_from_json():
    b = Briefing(
        kind="github",
        date="2026-06-03",
        path=None,
        title="GitHub 推荐 · 2026-06-03",
        frontmatter={},
        body="（应忽略此正文，用 json）",
        json_data={
            "top_repos": [
                {
                    "repo_url": "https://github.com/foo/bar",
                    "stars": 999,
                    "language": "Rust",
                    "summary": "做 X",
                    "use_case": "用于 Y",
                }
            ]
        },
    )
    card = _card_of(b)
    content = "\n".join(e["content"] for e in card["elements"])
    assert "foo/bar" in content
    assert "999" in content
    assert card["header"]["template"] == "turquoise"


def test_github_falls_back_to_body_without_json():
    b = Briefing(
        kind="github",
        date="d",
        path=None,
        title="t",
        frontmatter={},
        body="纯正文回退",
        json_data=None,
    )
    card = _card_of(b)
    content = "\n".join(e["content"] for e in card["elements"])
    assert "纯正文回退" in content
